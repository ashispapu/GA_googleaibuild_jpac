from fastapi import APIRouter, Depends

from plantguard_contracts.schemas.common import ErrorCode
from plantguard_contracts.schemas.incidents import IncidentStatus
from plantguard_contracts.schemas.security import AccessClaims, Permission
from plantguard_contracts.schemas.simulator import Scenario, ScenarioResponse
from plantguard_contracts.schemas.workflow import FaultRequest, FaultResponse, SimSpeedRequest, SimStatus

from app.container import Container
from app.core.errors import ApiError
from app.routers.deps import VIEW, container, require

router = APIRouter(prefix="/simulator", tags=["simulator"])
SIM = Depends(require(Permission.RUN_SIMULATOR))


@router.post("/scenario/{name}", response_model=ScenarioResponse)
async def scenario(name: Scenario, claims: AccessClaims = SIM, c: Container = Depends(container)):
    """POST /simulator/scenario/{name}: switch the demo scene (needs RUN_SIMULATOR); audited."""
    if c.engine is not None:
        raise ApiError(409, ErrorCode.CONFLICT, "Scenes belong to the scripted story (DATA_MODE=fixtures); the simulated "
                                                "plant runs on its own. Use /simulator/faults to start a fault.")
    await c.scene.set(name)
    c.audit.log("user", claims.sub, "set_scenario", name.value, {})
    return ScenarioResponse(scenario=name, started=True, message=f"Scene set to {name.value}")


@router.get("/status", response_model=SimStatus)
def status(claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """Data mode, plant time and speed (the UI shows plant time everywhere)."""
    open_n = sum(1 for r in c.incidents.visible(claims) if not r.incident.question and r.incident.status not in
                 (IncidentStatus.RESOLVED, IncidentStatus.REJECTED))
    if c.engine is None:
        return SimStatus(mode="fixtures", now=c.clock(), scale=1.0, random_faults=False, developing_faults=0,
                         open_incidents=open_n)
    faults = c.engine.ot.val("SELECT COUNT(*) FROM asset_conditions WHERE ended_at IS NULL", (), 0)
    return SimStatus(mode="sim", now=c.clock(), scale=c.engine.clock.scale, random_faults=c.engine.cfg.random_faults,
                     developing_faults=faults, open_incidents=open_n)


@router.post("/faults", response_model=FaultResponse)
async def fault(body: FaultRequest, claims: AccessClaims = SIM, c: Container = Depends(container)):
    """Start a fault now (random machine and mode unless given). The agents only find out through the sensors."""
    if c.engine is None:
        raise ApiError(501, ErrorCode.NOT_IMPLEMENTED, "Only on the simulated plant")
    if body.asset_id and body.asset_id not in c.engine.assets:
        raise KeyError(body.asset_id)
    row = c.engine.inject_fault(body.asset_id, body.failure_mode, progress=0.18)
    if row is None:
        raise ApiError(409, ErrorCode.CONFLICT, "That machine already has a developing fault (or none is free)")
    c.audit.log("user", claims.sub, "inject_fault", row["asset_id"], {})
    return FaultResponse(asset_id=row["asset_id"], message=f"A fault started developing on {row['asset_id']}")


@router.post("/speed", response_model=SimStatus)
def speed(body: SimSpeedRequest, claims: AccessClaims = SIM, c: Container = Depends(container)):
    """Plant clock speed (plant minutes per real minute); 0 pauses the plant."""
    if c.engine is None:
        raise ApiError(501, ErrorCode.NOT_IMPLEMENTED, "Only on the simulated plant")
    c.engine.clock.set_scale(min(600.0, max(0.0, body.scale)))
    c.audit.log("user", claims.sub, "set_plant_speed", "clock", {"scale": body.scale})
    return status(claims, c)
