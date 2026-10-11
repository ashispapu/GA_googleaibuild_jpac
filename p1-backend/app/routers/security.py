from fastapi import APIRouter, Depends

from plantguard_contracts.schemas.security import AccessClaims, RedactionReport

from app.container import Container
from app.routers.deps import VIEW, container

router = APIRouter(prefix="/security", tags=["security"])


@router.get("/redactions", response_model=list[RedactionReport])
def redactions(claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /security/redactions: redaction reports, newest first."""
    return c.redactions.list()
