from fastapi import APIRouter, Depends, Query

from plantguard_contracts.schemas.audit import AuditEntry, AuditVerify
from plantguard_contracts.schemas.common import Page
from plantguard_contracts.schemas.security import AccessClaims, Permission

from app.container import Container
from app.routers.deps import container, require

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=Page[AuditEntry])
def audit_list(incident_id: str | None = None, limit: int = Query(100, ge=1, le=1000), cursor: int | None = None,
               claims: AccessClaims = Depends(require(Permission.VIEW_AUDIT)), c: Container = Depends(container)):
    """Newest first. Pass `next_cursor` back as `cursor` for the next page."""
    items, total, next_seq = c.audit.list(incident_id, limit, cursor)
    return Page[AuditEntry](items=items, total=total, next_cursor=str(next_seq) if next_seq else None)


@router.get("/verify", response_model=AuditVerify)
def audit_verify(claims: AccessClaims = Depends(require(Permission.VIEW_AUDIT)), c: Container = Depends(container)):
    """Recompute the hash chain; reports whether it is intact and where it breaks (VIEW_AUDIT permission)."""
    valid, checked, broken = c.audit.verify()
    return AuditVerify(valid=valid, entries_checked=checked, broken_at_seq=broken)
