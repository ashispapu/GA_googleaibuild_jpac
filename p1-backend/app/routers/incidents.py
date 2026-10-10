from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from plantguard_contracts.schemas.approvals import Approval, ApprovalRequest, Feedback, FeedbackRequest
from plantguard_contracts.schemas.incidents import AgentEvent, Incident, IncidentCreate, OptionsResponse
from plantguard_contracts.schemas.security import AccessClaims, Permission

from app.container import Container
from app.routers.deps import VIEW, container, require, ws_user
from app.core.errors import ApiError

router = APIRouter(prefix="/incidents", tags=["incidents"])
USER_ACTOR = "user"


@router.post("", response_model=Incident, status_code=201)
async def create_incident(body: IncidentCreate, claims: AccessClaims = Depends(require(Permission.RUN_AGENTS)),
                          c: Container = Depends(container)):
    """POST /incidents: open an incident for an asset (returns the active one if it exists)."""
    return await c.incidents.open(body, (USER_ACTOR, claims.sub), claims)


@router.get("/{incident_id}", response_model=Incident)
def get_incident(incident_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /incidents/{id}"""
    return c.incidents.get(incident_id, claims)


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


@router.post("/{incident_id}/approve", response_model=Approval)
async def approve(incident_id: str, body: ApprovalRequest,
                  claims: AccessClaims = Depends(require(Permission.APPROVE)), c: Container = Depends(container)):
    """POST /incidents/{id}/approve: approve or reject an option; approving executes its actions."""
    return await c.incidents.decide(incident_id, body, claims)


@router.post("/{incident_id}/feedback", response_model=Feedback)
async def feedback(incident_id: str, body: FeedbackRequest,
                   claims: AccessClaims = Depends(require(Permission.SUBMIT_FEEDBACK)),
                   c: Container = Depends(container)):
    """POST /incidents/{id}/feedback: technician's verdict on the diagnosis."""
    return await c.incidents.feedback(incident_id, body, claims)
