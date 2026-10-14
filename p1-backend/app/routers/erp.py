from fastapi import APIRouter, Depends

from plantguard_contracts.schemas.erp import Part, StockTransfer, StockTransferCreate, Supplier
from plantguard_contracts.schemas.security import AccessClaims

from app.container import Container
from app.routers.deps import VIEW, container
from app.routers.write_guard import authorize_write

router = APIRouter(prefix="/erp", tags=["erp"])


@router.get("/parts/{sku}", response_model=Part)
def part(sku: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /erp/parts/{sku}"""
    return c.erp.get_part(sku)


@router.get("/suppliers/{supplier_id}", response_model=Supplier)
def supplier(supplier_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /erp/suppliers/{supplier_id}"""
    return c.erp.get_supplier(supplier_id)


@router.post("/stock-transfers", response_model=StockTransfer, status_code=201)
def create_transfer(body: StockTransferCreate, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """POST /erp/stock-transfers. Needs an approval token matching the approved draft; audited."""
    authorize_write(c, claims, body, "stock_transfer")
    transfer = c.erp.create_stock_transfer(body)
    c.audit.log("user", claims.sub, "create_stock_transfer", transfer.id, {"incident_id": body.incident_id})
    return transfer
