"""IncidentService (BE-7): open, read, approve/reject (token + executor) and feedback. Agent runs live in RunCoordinator."""
from plantguard_contracts.interfaces import AgentRuntime, AuditLogger, EventBus
from plantguard_contracts.schemas.approvals import (Approval, ApprovalRequest, DecisionKind, Feedback,
                                                    FeedbackRequest)
from plantguard_contracts.schemas.common import ErrorCode
from plantguard_contracts.schemas.incidents import (Incident, IncidentCreate, IncidentStatus, IncidentTrigger,
                                                    OptionsResponse)
from plantguard_contracts.schemas.security import AccessClaims

from app.core.common import Clock, IdSequence, utcnow
from app.services.plant_directory import PlantDirectory
from app.core.errors import ApiError
from app.services.scoring import severity_for
from app.core.ports import HealthProvider
from app.services.approval_tokens import ApprovalTokenService
from app.services.auth import AccessPolicy
from app.services.action_executor import ActionExecutor, ExecutionContext
from app.services.confidence_updater import ConfidenceUpdater
from app.repositories.incident_repository import IncidentRecord, IncidentRepository

SYSTEM_ACTOR = ("system", "health_watcher")


class IncidentService:
    """Incident life cycle: open, read, approve/reject (with execution) and feedback."""

    def __init__(self, repo: IncidentRepository, directory: PlantDirectory, health: HealthProvider,
                 audit: AuditLogger, bus: EventBus, tokens: ApprovalTokenService, executor: ActionExecutor,
                 runtime: AgentRuntime, policy: AccessPolicy, learning: ConfidenceUpdater,
                 approval_ids: IdSequence, feedback_ids: IdSequence, clock: Clock = utcnow):
        """Receives every collaborator (repo, directory, health, audit, bus, tokens, executor, runtime, policy, learning
        rule, id sequences, clock); builds nothing itself."""
        self._repo, self._dir, self._health, self._audit, self._bus = repo, directory, health, audit, bus
        self._tokens, self._executor, self._runtime, self._policy = tokens, executor, runtime, policy
        self._learning, self._approval_ids, self._feedback_ids, self._clock = learning, approval_ids, feedback_ids, clock

    # ---- open / read ----
    async def open(self, req: IncidentCreate, actor: tuple[str, str], claims: AccessClaims | None = None) -> Incident:
        """Idempotent per asset: while an incident is active, the same one is returned."""
        loc = self._dir.locate(req.asset_id)  # KeyError -> 404
        if claims:
            self._policy.check_plant(claims, loc.plant_id)
        existing = self._repo.find_active(req.asset_id)
        if existing:
            return existing.incident
        now = self._clock()
        try:
            risk = self._health.get_health(req.asset_id).risk_72h
        except KeyError:
            risk = 0.0
        inc = Incident(id=self._repo.next_id(), title=f"{loc.asset_name} ({loc.asset_id}) health risk",
                       plant_id=loc.plant_id, line_id=loc.line_id, asset_id=loc.asset_id, trigger=req.trigger,
                       severity=severity_for(risk) if req.trigger != IncidentTrigger.USER_QUESTION
                       else severity_for(max(risk, 0.3)),
                       status=IncidentStatus.ANALYSING, created_at=now, updated_at=now)
        self._repo.add(IncidentRecord(inc))
        self._audit.log(actor[0], actor[1], "open_incident", inc.id, {"asset_id": inc.asset_id,
                                                                      "trigger": req.trigger.value})
        await self._bus.publish(f"incident.{inc.id}.status", inc)
        return inc

    def get(self, incident_id: str, claims: AccessClaims) -> Incident:
        """The Incident, after the plant-scope check."""
        return self._scoped(incident_id, claims).incident

    def scoped_record(self, incident_id: str, claims: AccessClaims) -> IncidentRecord:
        """The full IncidentRecord (events, options ...), after the plant-scope check."""
        return self._scoped(incident_id, claims)

    def options(self, incident_id: str, claims: AccessClaims) -> OptionsResponse:
        """The ranked options; KeyError (-> 404) while the analysis is still running."""
        rec = self._scoped(incident_id, claims)
        if rec.options is None:
            raise KeyError(f"options for {incident_id} (analysis still running)")
        return rec.options

    # ---- decide ----
    async def decide(self, incident_id: str, req: ApprovalRequest, approver: AccessClaims) -> Approval:
        """Approve or reject an option. Only allowed in AWAITING_APPROVAL (else 409). Approve issues a token, runs the
        actions and ends EXECUTED, or stays APPROVED if an action failed. Audits and publishes the new status."""
        rec = self._scoped(incident_id, approver)
        inc = rec.incident
        if inc.status != IncidentStatus.AWAITING_APPROVAL or rec.options is None:
            raise ApiError(409, ErrorCode.CONFLICT, f"Incident is {inc.status.value}; it cannot be decided now")
        option = next((o for o in rec.options.options if o.id == req.option_id), None)
        if option is None:
            raise KeyError(f"option {req.option_id}")
        # state flips before any await, so a concurrent second approve sees 409 and nothing runs twice
        approve = req.decision == DecisionKind.APPROVE
        inc.status = IncidentStatus.APPROVED if approve else IncidentStatus.REJECTED
        inc.selected_option_id = req.option_id
        token, executed = None, []
        if approve:
            token = self._tokens.issue(approver.sub, incident_id, option.id, {a.kind for a in option.actions})
            executed = await self._executor.execute(option, ExecutionContext(incident_id, token, approver.sub))
            if all(r.ok for r in executed):
                inc.status = IncidentStatus.EXECUTED
        inc.updated_at = self._clock()
        approval = Approval(id=self._approval_ids.next(), incident_id=incident_id, option_id=option.id,
                            decision=req.decision, reason=req.reason, approver_id=approver.sub,
                            approver_role=approver.role, approval_token=token, approved_at=self._clock(),
                            executed_actions=executed)
        rec.approval = approval
        self._audit.log("user", approver.sub, req.decision.value, incident_id,
                        {"option_id": option.id, "incident_id": incident_id,
                         "failed_actions": [r.kind for r in executed if not r.ok]})
        await self._bus.publish(f"incident.{incident_id}.status", inc)
        await self._runtime.on_approval(inc, approval)
        return approval

    # ---- feedback ----
    async def feedback(self, incident_id: str, req: FeedbackRequest, user: AccessClaims) -> Feedback:
        """Record the technician's verdict (only after EXECUTED/REJECTED, once), update the similar-case confidence, mark
        RESOLVED, audit and notify the runtime."""
        rec = self._scoped(incident_id, user)
        inc = rec.incident
        if inc.status not in (IncidentStatus.EXECUTED, IncidentStatus.REJECTED) or rec.feedback is not None:
            raise ApiError(409, ErrorCode.CONFLICT, f"Incident is {inc.status.value}; feedback not accepted now")
        confidence = inc.diagnosis.confidence if inc.diagnosis else None
        fb = Feedback(id=self._feedback_ids.next(), incident_id=incident_id, confirmed=req.confirmed,
                      actual_root_cause=req.actual_root_cause, stored_for_retraining=True,
                      new_similar_case_confidence=self._learning.updated(confidence, req.confirmed),
                      created_at=self._clock())
        rec.feedback = fb
        inc.status, inc.updated_at = IncidentStatus.RESOLVED, self._clock()
        self._audit.log("user", user.sub, "feedback", incident_id,
                        {"confirmed": req.confirmed, "technician_id": req.technician_id, "incident_id": incident_id})
        await self._bus.publish(f"incident.{incident_id}.status", inc)
        await self._runtime.on_feedback(inc, fb)
        return fb

    # ---- simulator / lifecycle hooks ----
    def reset(self) -> None:
        """Delete all incidents (simulator calm/reset)."""
        self._repo.clear()

    async def resolve_active(self) -> None:
        """Mark every incident RESOLVED and publish the status (simulator `resolved`)."""
        for rec in self._repo.all():
            rec.incident.status, rec.incident.updated_at = IncidentStatus.RESOLVED, self._clock()
            await self._bus.publish(f"incident.{rec.incident.id}.status", rec.incident)

    def _scoped(self, incident_id: str, claims: AccessClaims) -> IncidentRecord:
        """Load a record (KeyError -> 404) and enforce that the user may see its plant."""
        rec = self._repo.get(incident_id)  # KeyError -> 404
        self._policy.check_plant(claims, rec.incident.plant_id)
        return rec
