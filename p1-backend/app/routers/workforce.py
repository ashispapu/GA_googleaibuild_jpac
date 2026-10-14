from fastapi import APIRouter, Depends

from plantguard_contracts.schemas.security import AccessClaims
from plantguard_contracts.schemas.workforce import Shift, Technician

from app.container import Container
from app.routers.deps import VIEW, container

router = APIRouter(prefix="/workforce", tags=["workforce"])


@router.get("/shifts", response_model=list[Shift])
def shifts(plant_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /workforce/shifts?plant_id= (plant scope enforced)."""
    c.policy.check_plant(claims, plant_id)
    return c.workforce.get_shifts(plant_id)


@router.get("/technicians", response_model=list[Technician])
def technicians(skill: str | None = None, on_shift: bool | None = None, plant_id: str | None = None,
                claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /workforce/technicians (optionally ?skill=, ?on_shift=, ?plant_id=)."""
    return c.workforce.find_technicians(skill, on_shift, plant_id)
