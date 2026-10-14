"""IncidentService (BE-7): open, read, approve/reject (token + executor) and feedback. Agent runs live in RunCoordinator.

With a RepairWorkflow attached (simulated plant) the life cycle continues after approval: the work order goes to the
assigned technician, who starts and finishes the repair; the machine is checked; only the technician can close the
incident. Without it (fixtures mode, contract tests) the original v1 behaviour is kept unchanged."""
from typing import Callable

from plantguard_contracts.interfaces import AgentRuntime, AuditLogger, EventBus
from plantguard_contracts.schemas.approvals import (Approval, ApprovalRequest, DecisionKind, Feedback,
                                                    FeedbackRequest)
from plantguard_contracts.schemas.common import ErrorCode
from plantguard_contracts.schemas.incidents import (Incident, IncidentCreate, IncidentStatus, IncidentTrigger, Option,
                                                    OptionsResponse, SignoffState)
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
        self._repo, self._dir, self._health, self._audit, self._bus = repo, directory, health, audit, bus
        self._tokens, self._executor, self._runtime, self._policy = tokens, executor, runtime, policy
        self._learning, self._approval_ids, self._feedback_ids, self._clock = learning, approval_ids, feedback_ids, clock
        self.workflow = None  # RepairWorkflow (simulated plant) or None (v1 behaviour)
        self.approval_policy = None  # ApprovalPolicy (simulated plant): multi-signature approval chain
        self.on_opened: list[Callable[[IncidentRecord], None]] = []
        self.on_status: list[Callable[[IncidentRecord], None]] = []

    @property
    def repo(self) -> IncidentRepository:
        return self._repo

    async def publish_status(self, rec: IncidentRecord) -> None:
        self._repo.save(rec)
        for fn in self.on_status:
            fn(rec)
        await self._bus.publish(f"incident.{rec.incident.id}.status", rec.incident)

    # ---- open / read ----
    async def open(self, req: IncidentCreate, actor: tuple[str, str], claims: AccessClaims | None = None) -> Incident:
        """Idempotent per asset: while an incident is active, the same one is returned. A question always opens its
        own thread; with parent_id it is about that incident (same machine)."""
        loc = self._dir.locate(req.asset_id)  # KeyError -> 404
        if claims:
            self._policy.check_plant(claims, loc.plant_id)
        question = (req.question or "").strip() if req.trigger == IncidentTrigger.USER_QUESTION else ""
        parent = None
        if req.parent_id:
            parent = self._repo.get(req.parent_id)  # KeyError -> 404
            if parent.incident.asset_id != req.asset_id:
                raise ApiError(422, ErrorCode.VALIDATION, "parent_id is an incident on another machine")
        existing = self._repo.find_active(req.asset_id)
        if existing and not question:  # a follow-up question always gets its own thread (and agent run)
            return existing.incident
        now = self._clock()
        try:
            risk = self._health.get_health(req.asset_id).risk_72h
        except KeyError:
            risk = 0.0
        title = f"Question about {loc.asset_id}: {question[:80]}" if question else f"{loc.asset_name} ({loc.asset_id}) health risk"
        if req.trigger == IncidentTrigger.ALARM and not question:
            title = f"{loc.asset_name} ({loc.asset_id}) alarm"
        inc = Incident(id=self._repo.next_id(), title=title, question=question or None,
                       plant_id=loc.plant_id, line_id=loc.line_id, asset_id=loc.asset_id, trigger=req.trigger,
                       severity=severity_for(risk) if req.trigger != IncidentTrigger.USER_QUESTION
                       else severity_for(max(risk, 0.3)),
                       status=IncidentStatus.ANALYSING, created_at=now, updated_at=now,
                       parent_id=parent.incident.id if parent else None,
                       opened_by=actor[1] if actor[0] == "user" else "system")
        rec = IncidentRecord(inc)
        self._repo.add(rec)
        self._audit.log(actor[0], actor[1], "open_incident", inc.id, {"asset_id": inc.asset_id, "incident_id": inc.id,
                                                                      "trigger": req.trigger.value,
                                                                      "parent_id": inc.parent_id})
        await self._bus.publish(f"incident.{inc.id}.status", inc)
        for fn in self.on_opened:
            fn(rec)
        return inc

    def get(self, incident_id: str, claims: AccessClaims) -> Incident:
        return self._scoped(incident_id, claims).incident

    def scoped_record(self, incident_id: str, claims: AccessClaims) -> IncidentRecord:
        return self._scoped(incident_id, claims)

    def options(self, incident_id: str, claims: AccessClaims) -> OptionsResponse:
        """The ranked options; KeyError (-> 404) while the analysis is still running."""
        rec = self._scoped(incident_id, claims)
        if rec.options is None:
            raise KeyError(f"options for {incident_id} (analysis still running)")
        if self.approval_policy is not None:
            return self.approval_policy.annotate(rec.incident, rec.options)
        return rec.options

    # ---- decide ----
    async def decide(self, incident_id: str, req: ApprovalRequest, approver: AccessClaims) -> Approval:
        """Approve or reject an option. Only allowed in AWAITING_APPROVAL (else 409). Approve issues a token, runs the
        actions and ends EXECUTED, or stays APPROVED if an action failed. Audits and publishes the new status."""
        rec = self._scoped(incident_id, approver)
        inc = rec.incident
        if self.approval_policy is not None and (inc.status == IncidentStatus.AWAITING_SIGNOFF or
                                                 (req.decision == DecisionKind.APPROVE and inc.status == IncidentStatus.AWAITING_APPROVAL)):
            return await self._decide_chain(rec, req, approver)
        if inc.status != IncidentStatus.AWAITING_APPROVAL or rec.options is None:
            raise ApiError(409, ErrorCode.CONFLICT, f"Incident is {inc.status.value}; it cannot be decided now")
        option = next((o for o in rec.options.options if o.id == req.option_id), None)
        if option is None:
            raise KeyError(f"option {req.option_id}")
        approve = req.decision == DecisionKind.APPROVE
        token, executed = None, []
        if not approve and self.workflow is not None:
            # A rejected option is recorded; the incident keeps waiting for one of the other options.
            approval = Approval(id=self._approval_ids.next(), incident_id=incident_id, option_id=option.id,
                                decision=req.decision, reason=req.reason, approver_id=approver.sub,
                                approver_role=approver.role, approval_token=None, approved_at=self._clock(),
                                executed_actions=[])
            self._repo.save_approval(rec, approval)
            self._audit.log("user", approver.sub, "reject", incident_id, {"option_id": option.id, "incident_id": incident_id})
            return approval
        return await self._execute(rec, option, req, approver)

    async def _execute(self, rec: IncidentRecord, option: Option, req: ApprovalRequest, approver: AccessClaims,
                       signoffs: list | None = None) -> Approval:
        """Final approval (or v1 reject): issue the token, run the option's actions, record and publish."""
        inc, incident_id = rec.incident, rec.incident.id
        approve = req.decision == DecisionKind.APPROVE
        token, executed = None, []
        # state flips before any await, so a concurrent second approve sees 409 and nothing runs twice
        inc.status = IncidentStatus.APPROVED if approve else IncidentStatus.REJECTED
        inc.selected_option_id = req.option_id
        # with an approval chain, "approved by" is the person who chose the option (the others signed it off)
        inc.approved_by = (signoffs[0].signed_by if signoffs and signoffs[0].signed_by else approver.sub) if approve else None
        approval_id = self._approval_ids.next()
        pending = Approval(id=approval_id, incident_id=incident_id, option_id=option.id, decision=req.decision,
                           reason=req.reason, approver_id=approver.sub, approver_role=approver.role,
                           approval_token=None, approved_at=self._clock(), executed_actions=[],
                           signoffs=signoffs or [])
        self._repo.save(rec)
        self._repo.save_approval(rec, pending)  # writes reference the approval
        if approve:
            token = self._tokens.issue(approver.sub, incident_id, option.id, {a.kind for a in option.actions})
            executed = await self._executor.execute(option, ExecutionContext(incident_id, token, approver.sub))
            if all(r.ok for r in executed):
                inc.status = IncidentStatus.EXECUTED
        inc.updated_at = self._clock()
        approval = pending.model_copy(update={"approval_token": token, "executed_actions": executed})
        rec.approval = approval
        self._repo.save_approval(rec, approval)
        if approve and self.workflow is not None:
            self.workflow.after_approval(rec, approval)
        self._audit.log("user", approver.sub, req.decision.value, incident_id,
                        {"option_id": option.id, "incident_id": incident_id,
                         "failed_actions": [r.kind for r in executed if not r.ok]})
        await self.publish_status(rec)
        await self._runtime.on_approval(inc, approval)
        return approval

    async def _decide_chain(self, rec: IncidentRecord, req: ApprovalRequest, signer: AccessClaims) -> Approval:
        """v1.2 approval chain: the first approval starts it, every required person signs, the last signature runs
        the actions. A decline by any of them sends the incident back to awaiting approval with the reason."""
        pol, inc, now = self.approval_policy, rec.incident, self._clock()
        approve = req.decision == DecisionKind.APPROVE
        if inc.status == IncidentStatus.AWAITING_SIGNOFF and inc.signoff:
            if req.option_id != inc.signoff.option_id:
                raise ApiError(409, ErrorCode.CONFLICT, f"Option {inc.signoff.option_id[-1]} is being signed off; it must be "
                                                         "approved or declined first")
            option = self._option(rec, req.option_id)
            steps = list(inc.signoff.steps)
        else:
            option = self._option(rec, req.option_id)
            steps = pol.steps_for(inc, option)
        signed = pol.sign(steps, signer, "approved" if approve else "declined", req.reason, now)
        if not signed:
            waiting = ", ".join(s.label for s in pol.pending(steps)) or "nobody"
            raise ApiError(403, ErrorCode.FORBIDDEN, f"Not yours to sign. This option is waiting for: {waiting}")
        started_by = inc.signoff.started_by if inc.status == IncidentStatus.AWAITING_SIGNOFF and inc.signoff else signer.sub
        started_at = inc.signoff.started_at if inc.status == IncidentStatus.AWAITING_SIGNOFF and inc.signoff else now
        state = SignoffState(option_id=option.id, status="in_progress", steps=steps, started_by=started_by,
                             started_at=started_at)
        base = dict(incident_id=inc.id, option_id=option.id, reason=req.reason, approver_id=signer.sub,
                    approver_role=signer.role, approval_token=None, approved_at=now, executed_actions=[], signoffs=steps)
        if not approve:
            inc.signoff = state.model_copy(update={"status": "declined"})
            inc.status, inc.selected_option_id, inc.updated_at = IncidentStatus.AWAITING_APPROVAL, None, now
            self._audit.log("user", signer.sub, "decline_option", inc.id,
                            {"incident_id": inc.id, "option_id": option.id, "steps": signed, "reason": req.reason,
                             "labels": [s.label for s in steps if s.key in signed]})
            await self.publish_status(rec)
            return Approval(id=self._approval_ids.next(), decision=DecisionKind.REJECT, **base)
        self._audit.log("user", signer.sub, "sign_option", inc.id,
                        {"incident_id": inc.id, "option_id": option.id, "steps": signed, "reason": req.reason,
                         "labels": [s.label for s in steps if s.key in signed]})
        if pol.pending(steps):
            inc.signoff = state
            inc.status, inc.selected_option_id, inc.updated_at = IncidentStatus.AWAITING_SIGNOFF, option.id, now
            await self.publish_status(rec)
            return Approval(id=self._approval_ids.next(), decision=DecisionKind.APPROVE, **base)
        inc.signoff = state.model_copy(update={"status": "complete"})
        first_reason = steps[0].note or req.reason
        return await self._execute(rec, option, ApprovalRequest(option_id=option.id, decision=DecisionKind.APPROVE,
                                                                reason=first_reason), signer, signoffs=steps)

    @staticmethod
    def _option(rec: IncidentRecord, option_id: str) -> Option:
        option = next((o for o in (rec.options.options if rec.options else []) if o.id == option_id), None)
        if option is None:
            raise KeyError(f"option {option_id}")
        return option

    # ---- feedback ----
    async def feedback(self, incident_id: str, req: FeedbackRequest, user: AccessClaims) -> Feedback:
        """Record the technician's verdict (only after EXECUTED/REJECTED, once), update the similar-case confidence, mark
        RESOLVED, audit and notify the runtime. With the repair workflow this is the technician's close-out."""
        if self.workflow is not None:
            from plantguard_contracts.schemas.workflow import ResolveRequest
            return await self.workflow.resolve(incident_id, ResolveRequest(
                confirmed=req.confirmed, actual_root_cause=req.actual_root_cause, notes=req.notes), user)
        rec = self._scoped(incident_id, user)
        inc = rec.incident
        if inc.status not in (IncidentStatus.EXECUTED, IncidentStatus.REJECTED) or rec.feedback is not None:
            raise ApiError(409, ErrorCode.CONFLICT, f"Incident is {inc.status.value}; feedback not accepted now")
        fb = self.make_feedback(rec, req.confirmed, req.actual_root_cause)
        rec.feedback = fb
        inc.status, inc.updated_at, inc.resolved_at = IncidentStatus.RESOLVED, self._clock(), self._clock()
        self._repo.save_feedback(rec, fb, req.technician_id, user.sub)
        self._audit.log("user", user.sub, "feedback", incident_id,
                        {"confirmed": req.confirmed, "technician_id": req.technician_id, "incident_id": incident_id})
        await self.publish_status(rec)
        await self._runtime.on_feedback(inc, fb)
        return fb

    def make_feedback(self, rec: IncidentRecord, confirmed: bool, actual_root_cause: str) -> Feedback:
        confidence = rec.incident.diagnosis.confidence if rec.incident.diagnosis else None
        return Feedback(id=self._feedback_ids.next(), incident_id=rec.incident.id, confirmed=confirmed,
                        actual_root_cause=actual_root_cause, stored_for_retraining=True,
                        new_similar_case_confidence=self._learning.updated(confidence, confirmed),
                        created_at=self._clock())

    @property
    def runtime(self) -> AgentRuntime:
        return self._runtime

    # ---- simulator / lifecycle hooks ----
    def reset(self) -> None:
        """Delete all incidents (simulator calm/reset)."""
        self._repo.clear()

    async def resolve_active(self) -> None:
        """Mark every incident RESOLVED and publish the status (simulator `resolved`)."""
        for rec in self._repo.all():
            rec.incident.status, rec.incident.updated_at = IncidentStatus.RESOLVED, self._clock()
            await self.publish_status(rec)

    def visible(self, claims: AccessClaims) -> list[IncidentRecord]:
        return [r for r in self._repo.all() if r.incident.plant_id in claims.plant_ids or self._signs_for(r, claims)]

    @staticmethod
    def _signs_for(rec: IncidentRecord, claims: AccessClaims) -> bool:
        """v1.2: a lead of another plant asked to release a part may see that incident (and only while it concerns
        their plant's step)."""
        so = rec.incident.signoff
        return bool(so) and any(s.plant_id in claims.plant_ids and s.key.startswith("source_plant:") for s in so.steps)

    def _scoped(self, incident_id: str, claims: AccessClaims) -> IncidentRecord:
        """Load a record (KeyError -> 404) and enforce that the user may see its plant."""
        rec = self._repo.get(incident_id)  # KeyError -> 404
        if self._signs_for(rec, claims):
            return rec
        self._policy.check_plant(claims, rec.incident.plant_id)
        return rec
