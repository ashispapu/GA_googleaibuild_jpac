from fastapi import APIRouter, Depends

from plantguard_contracts.schemas.quality import DefectSummary, Inspection
from plantguard_contracts.schemas.security import AccessClaims

from app.container import Container
from app.routers.deps import VIEW, container

router = APIRouter(prefix="/quality", tags=["quality"])


@router.get("/lines/{line_id}/defects", response_model=DefectSummary)
def defects(line_id: str, hours: int = 24, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /quality/lines/{line_id}/defects"""
    return c.quality.get_defects(line_id, hours)


@router.get("/inspections", response_model=list[Inspection])
def inspections(line_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /quality/inspections?line_id="""
    return c.quality.get_inspections(line_id)
