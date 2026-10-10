from fastapi import APIRouter, Depends

from plantguard_contracts.schemas.security import AccessClaims, Permission
from plantguard_contracts.schemas.simulator import Scenario, ScenarioResponse

from app.container import Container
from app.routers.deps import container, require

router = APIRouter(prefix="/simulator", tags=["simulator"])


@router.post("/scenario/{name}", response_model=ScenarioResponse)
async def scenario(name: Scenario, claims: AccessClaims = Depends(require(Permission.RUN_SIMULATOR)),
                   c: Container = Depends(container)):
    """POST /simulator/scenario/{name}: switch the demo scene (needs RUN_SIMULATOR); audited."""
    await c.scene.set(name)
    c.audit.log("user", claims.sub, "set_scenario", name.value, {})
    return ScenarioResponse(scenario=name, started=True, message=f"Scene set to {name.value}")
