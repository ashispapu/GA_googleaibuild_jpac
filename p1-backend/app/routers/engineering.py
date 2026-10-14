from fastapi import APIRouter, Depends

from plantguard_contracts.schemas.common import ErrorCode
from plantguard_contracts.schemas.engineering import AssetBom, DocSearchRequest, DocSearchResponse
from plantguard_contracts.schemas.workflow import DocumentDetail, DocumentInfo

from app.core.errors import ApiError
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


def _docs(c: Container):
    if not hasattr(c.retriever, "document"):
        raise ApiError(501, ErrorCode.NOT_IMPLEMENTED, "The document library runs on the simulated plant (DATA_MODE=sim)")
    return c.retriever


@router.get("/docs", response_model=list[DocumentInfo])
def documents(kind: str | None = None, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /engineering/docs: manuals and indexed repair records (kind=manual|work_order)."""
    return _docs(c).documents(kind)


@router.get("/docs/{doc_id}", response_model=DocumentDetail)
def document(doc_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /engineering/docs/{doc_id}: the full manual (every section) for reading."""
    return _docs(c).document(doc_id)
