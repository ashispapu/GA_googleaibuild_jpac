from typing import Literal

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from plantguard_contracts.schemas.approvals import Approval, ApprovalRequest, Feedback, FeedbackRequest
from plantguard_contracts.schemas.common import ErrorCode, Page
from plantguard_contracts.schemas.incidents import AgentEvent, Incident, IncidentCreate, IncidentStatus, OptionsResponse
from plantguard_contracts.schemas.security import AccessClaims, Permission
from plantguard_contracts.schemas.workflow import (IncidentContext, IncidentOverview, JobView, MaintenanceLogEntry,
                                                   OptionChatMessage, OptionChatRequest, OptionChatResponse,
                                                   ReanalyseRequest, ResolveRequest, SignoffTask, Stage,
                                                   WorkCompleteRequest, WorkNoteRequest, WorkStartRequest, WorkUpdate)

from app.container import Container
from app.routers.deps import VIEW, container, require, ws_user
from app.core.errors import ApiError

router = APIRouter(prefix="/incidents", tags=["incidents"])
USER_ACTOR = "user"
WORK = Depends(require(Permission.PERFORM_WORK))


def _flow(c: Container):
    if c.workflow is None:
        raise ApiError(501, ErrorCode.NOT_IMPLEMENTED, "The repair workflow runs on the simulated plant (DATA_MODE=sim)")
    return c.workflow


def _basic_overview(c: Container, rec) -> IncidentOverview:
    """Fixtures mode: what can be said without the plant databases."""
    inc = rec.incident
    loc = c.directory.locate(inc.asset_id)
    stage = {IncidentStatus.ANALYSING: Stage.AI_ANALYSING, IncidentStatus.OPEN: Stage.AI_ANALYSING,
             IncidentStatus.AWAITING_APPROVAL: Stage.AWAITING_APPROVAL, IncidentStatus.APPROVED: Stage.WAITING_TECHNICIAN,
             IncidentStatus.AWAITING_SIGNOFF: Stage.AWAITING_SIGNOFF,
             IncidentStatus.EXECUTED: Stage.WAITING_TECHNICIAN, IncidentStatus.REJECTED: Stage.REJECTED,
             IncidentStatus.RESOLVED: Stage.ANSWERED if inc.question else Stage.RESOLVED}.get(inc.status, Stage.AI_ANALYSING)
    return IncidentOverview(incident=inc, asset_name=loc.asset_name, line_name=inc.line_id, stage=stage,
                            status_tag="done" if inc.status in (IncidentStatus.RESOLVED, IncidentStatus.REJECTED) else "in_progress",
                            ai_working=inc.status == IncidentStatus.ANALYSING, option_id=inc.selected_option_id)


@router.get("", response_model=Page[IncidentOverview])
def list_incidents(scope: Literal["open", "done", "all"] = "all", mine: bool = False,
                   kind: Literal["incident", "question", "all"] = "incident", plant_id: str | None = None,
                   parent_id: str | None = None, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /incidents: every incident you may see, newest activity first, each with its stage tag (AI analysing,
    awaiting approval, technician working, ...). `mine=true`: a technician's assigned jobs (or your own questions
    with kind=question)."""
    if c.workflow is not None:
        items = c.workflow.list(claims, scope, mine, kind, plant_id, parent_id)
    else:
        items = []
        for rec in c.incidents.visible(claims):
            inc = rec.incident
            if (kind == "incident" and inc.question) or (kind == "question" and not inc.question):
                continue
            if plant_id and inc.plant_id != plant_id:
                continue
            if parent_id and inc.parent_id != parent_id:
                continue
            ov = _basic_overview(c, rec)
            if (scope == "open" and ov.status_tag == "done") or (scope == "done" and ov.status_tag != "done"):
                continue
            items.append(ov)
        items.sort(key=lambda o: o.incident.updated_at, reverse=True)
    return Page[IncidentOverview](items=items, total=len(items))


@router.get("/log", response_model=list[MaintenanceLogEntry])
def maintenance_log(limit: int = Query(100, ge=1, le=500), claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """Closed incidents with the solution that fixed them (the 'done' log)."""
    return _flow(c).maintenance_log(claims, limit)


@router.get("/signoffs", response_model=list[SignoffTask])
def my_signoffs(claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """v1.2: approval steps waiting for you (release a part to another plant, co-sign as plant manager, ...)."""
    pol = c.incidents.approval_policy
    if pol is None:
        return []
    out = []
    for rec in c.incidents.repo.all():
        inc = rec.incident
        for step in pol.waits_for(inc, claims):
            opt = next((o for o in (rec.options.options if rec.options else []) if o.id == inc.signoff.option_id), None)
            who = c.workflow.user(inc.signoff.started_by) if c.workflow else None
            out.append(SignoffTask(incident_id=inc.id, asset_id=inc.asset_id, plant_id=inc.plant_id,
                                   option_id=inc.signoff.option_id, option_label=opt.label if opt else inc.signoff.option_id,
                                   step=step, requested_by=who.name if who else inc.signoff.started_by,
                                   requested_at=inc.signoff.started_at))
    return sorted(out, key=lambda t: t.requested_at)


@router.post("", response_model=Incident, status_code=201)
async def create_incident(body: IncidentCreate, claims: AccessClaims = Depends(require(Permission.RUN_AGENTS)),
                          c: Container = Depends(container)):
    """POST /incidents: open an incident for an asset (returns the active one if it exists). A question
    (trigger=user_question) always opens its own thread; parent_id ties it to an incident."""
    return await c.incidents.open(body, (USER_ACTOR, claims.sub), claims)


@router.get("/{incident_id}", response_model=Incident)
def get_incident(incident_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /incidents/{id}"""
    return c.incidents.get(incident_id, claims)


@router.get("/{incident_id}/overview", response_model=IncidentOverview)
def overview(incident_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """The incident with its stage, approver, technician, work order and repair check."""
    rec = c.incidents.scoped_record(incident_id, claims)
    return c.workflow.overview(rec) if c.workflow is not None else _basic_overview(c, rec)


@router.get("/{incident_id}/timeline", response_model=list[WorkUpdate])
def timeline(incident_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """Everything that happened to the incident (opened, options, approvals, work order updates, parts, check)."""
    return _flow(c).timeline(incident_id, claims)


@router.get("/{incident_id}/context", response_model=IncidentContext)
def context(incident_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """What the agents may know about the incident (get_incident tool)."""
    if c.workflow is None:  # fixtures mode: same answer the get_incident tool gives
        from plantguard_contracts.tools import IncidentArgs
        c.incidents.repo.get(incident_id) if incident_id != "INC-0042" else None  # 404 for unknown ids
        return c.tool_gateway.handler("get_incident")(IncidentArgs(incident_id=incident_id))
    c.incidents.scoped_record(incident_id, claims)
    return c.workflow.context(incident_id)


@router.get("/{incident_id}/job", response_model=JobView)
def job(incident_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """Everything the technician needs: chosen option and who approved it, work order, parts (stock / transfer ETA),
    manual sections, updates, and which actions are allowed now."""
    return _flow(c).job(incident_id, claims)


@router.post("/{incident_id}/work/start", response_model=JobView)
async def work_start(incident_id: str, body: WorkStartRequest, claims: AccessClaims = WORK,
                     c: Container = Depends(container)):
    """Assigned technician starts the repair (machine locked out; needs the parts in stock)."""
    return await _flow(c).start_work(incident_id, claims, body.note)


@router.post("/{incident_id}/work/note", response_model=JobView)
async def work_note(incident_id: str, body: WorkNoteRequest, claims: AccessClaims = WORK, c: Container = Depends(container)):
    """Technician adds a note to the work order."""
    return await _flow(c).add_note(incident_id, claims, body.note)


@router.post("/{incident_id}/work/complete", response_model=JobView)
async def work_complete(incident_id: str, body: WorkCompleteRequest, claims: AccessClaims = WORK,
                        c: Container = Depends(container)):
    """Technician reports the repair done: parts are consumed, the machine restarts and is checked."""
    return await _flow(c).complete_work(incident_id, claims, body)


@router.post("/{incident_id}/resolve", response_model=Feedback)
async def resolve(incident_id: str, body: ResolveRequest, claims: AccessClaims = WORK, c: Container = Depends(container)):
    """Assigned technician closes the incident after the check passed (only they can)."""
    return await _flow(c).resolve(incident_id, body, claims)


@router.post("/{incident_id}/reanalyse", response_model=IncidentOverview)
async def reanalyse(incident_id: str, body: ReanalyseRequest, claims: AccessClaims = Depends(require(Permission.RUN_AGENTS)),
                    c: Container = Depends(container)):
    """After a repair that did not work: the AI team analyses again (the failed cause excluded, findings passed on)."""
    return await _flow(c).reanalyse(incident_id, body, claims)


@router.get("/{incident_id}/events", response_model=list[AgentEvent])
def events(incident_id: str, after_seq: int = Query(0, ge=0), claims: AccessClaims = VIEW,
           c: Container = Depends(container)):
    """GET /incidents/{id}/events: stored agent events after `after_seq` (REST catch-up)."""
    c.incidents.get(incident_id, claims)  # existence + plant scope
    return c.runs.events(incident_id, after_seq)


@router.websocket("/{incident_id}/stream")
async def stream(ws: WebSocket, incident_id: str, token: str, speed: float = Query(1.0, ge=0.1, le=1000),
                 after_seq: int = Query(0, ge=0)):
    """AgentEvent frames in seq order; the server closes after the orchestrator sends `finished`.
    The first viewer starts the agent run (at `speed`); later viewers and reconnects replay the recorded events."""
    claims = await ws_user(ws, token)
    if claims is None:
        return
    c: Container = ws.app.state.container
    try:
        c.incidents.get(incident_id, claims)
    except (KeyError, ApiError):
        await ws.close(code=4404)
        return
    await ws.accept()
    c.runs.ensure_started(incident_id, speed)
    try:
        async for ev in c.runs.follow(incident_id, after_seq):
            await ws.send_text(ev.model_dump_json())
        await ws.close()
    except (WebSocketDisconnect, RuntimeError):
        pass


@router.get("/{incident_id}/options", response_model=OptionsResponse)
def options(incident_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /incidents/{id}/options: ranked options; 404 until the analysis has produced them."""
    return c.incidents.options(incident_id, claims)


@router.get("/{incident_id}/options/chat", response_model=list[OptionChatMessage])
def option_chat(incident_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """v1.2: the approvers' conversation with the Decision agent about this round's options."""
    if c.option_chat is None:
        return []
    return c.option_chat.history(incident_id, claims)


@router.post("/{incident_id}/options/chat", response_model=OptionChatResponse)
async def option_chat_post(incident_id: str, body: OptionChatRequest,
                           claims: AccessClaims = Depends(require(Permission.APPROVE)), c: Container = Depends(container)):
    """v1.2: an approver suggests a change ("repair tomorrow morning at 80 %"); the Decision agent answers and, when the
    plan is workable, adds it as a new costed option. The AI's options stay as they are."""
    if c.option_chat is None:
        raise ApiError(409, ErrorCode.CONFLICT, "The options chat needs the simulated plant (DATA_MODE=sim)")
    return await c.option_chat.post(incident_id, body.message, claims)


@router.post("/{incident_id}/approve", response_model=Approval)
async def approve(incident_id: str, body: ApprovalRequest,
                  claims: AccessClaims = Depends(require(Permission.APPROVE)), c: Container = Depends(container)):
    """POST /incidents/{id}/approve: approve or reject an option; approving executes its actions."""
    return await c.incidents.decide(incident_id, body, claims)


@router.post("/{incident_id}/feedback", response_model=Feedback)
async def feedback(incident_id: str, body: FeedbackRequest,
                   claims: AccessClaims = Depends(require(Permission.SUBMIT_FEEDBACK)),
                   c: Container = Depends(container)):
    """POST /incidents/{id}/feedback: technician's verdict on the diagnosis (on the simulated plant: the assigned
    technician's close-out, after the machine check passed)."""
    return await c.incidents.feedback(incident_id, body, claims)
