from fastapi import APIRouter, Depends

from plantguard_contracts.schemas.engineering import AssetBom, DocSearchRequest, DocSearchResponse
from plantguard_contracts.schemas.security import AccessClaims

from app.container import Container
from app.routers.deps import VIEW, container

router = APIRouter(prefix="/engineering", tags=["engineering"])


@router.get("/assets/{asset_id}/bom", response_model=AssetBom)
def bom(asset_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /engineering/assets/{asset_id}/bom"""
    return c.engineering.get_bom(asset_id)


@router.post("/docs/search", response_model=DocSearchResponse)
def doc_search(body: DocSearchRequest, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """POST /engineering/docs/search: search manuals and documents."""
    return c.retriever.search(body)
