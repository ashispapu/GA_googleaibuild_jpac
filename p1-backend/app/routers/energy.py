from fastapi import APIRouter, Depends

from plantguard_contracts.schemas.energy import Co2Factor, LineConsumption, Tariff
from plantguard_contracts.schemas.security import AccessClaims

from app.container import Container
from app.routers.deps import VIEW, container

router = APIRouter(prefix="/energy", tags=["energy"])


@router.get("/lines/{line_id}/consumption", response_model=LineConsumption)
def consumption(line_id: str, hours: int = 24, plant_id: str | None = None, claims: AccessClaims = VIEW,
                c: Container = Depends(container)):
    """GET /energy/lines/{line_id}/consumption"""
    return c.energy.get_consumption(line_id, hours, plant_id)


@router.get("/tariffs", response_model=list[Tariff])
def tariffs(claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /energy/tariffs"""
    return c.energy.get_tariffs()


@router.get("/co2-factor", response_model=Co2Factor)
def co2_factor(claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /energy/co2-factor"""
    return c.energy.get_co2_factor()
