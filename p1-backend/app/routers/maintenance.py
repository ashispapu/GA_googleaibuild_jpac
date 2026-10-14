from fastapi import APIRouter, Depends

from plantguard_contracts.schemas.maintenance import WorkOrder, WorkOrderCreate
from plantguard_contracts.schemas.security import AccessClaims

from app.container import Container
from app.routers.deps import VIEW, container
from app.routers.write_guard import authorize_write

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


@router.get("/assets/{asset_id}/history", response_model=list[WorkOrder])
def history(asset_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /maintenance/assets/{asset_id}/history"""
    return c.maintenance.get_history(asset_id)


@router.post("/work-orders", response_model=WorkOrder, status_code=201)
def create_work_order(body: WorkOrderCreate, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """POST /maintenance/work-orders. Needs an approval token matching the approved draft; audited."""
    authorize_write(c, claims, body, "work_order")
    wo = c.maintenance.create_work_order(body)
    c.audit.log("user", claims.sub, "create_work_order", wo.id, {"incident_id": body.incident_id})
    return wo
