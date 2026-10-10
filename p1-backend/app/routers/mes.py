from fastapi import APIRouter, Depends

from plantguard_contracts.schemas.mes import LineOEE, ProductionOrder, ScheduleSlot
from plantguard_contracts.schemas.security import AccessClaims

from app.container import Container
from app.routers.deps import VIEW, container

router = APIRouter(prefix="/mes", tags=["mes"])


@router.get("/orders", response_model=list[ProductionOrder])
def orders(line_id: str | None = None, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /mes/orders (optionally ?line_id=)"""
    return c.mes.get_orders(line_id)


@router.get("/lines/{line_id}/schedule", response_model=list[ScheduleSlot])
def schedule(line_id: str, hours: int = 72, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /mes/lines/{line_id}/schedule"""
    return c.mes.get_schedule(line_id, hours)


@router.get("/lines/{line_id}/oee", response_model=LineOEE)
def oee(line_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /mes/lines/{line_id}/oee"""
    return c.mes.get_oee(line_id)
