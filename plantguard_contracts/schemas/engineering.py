from .common import Contract


class BomItem(Contract):
    sku: str
    description: str
    quantity: int


class AssetBom(Contract):
    asset_id: str
    items: list[BomItem]


class DocSearchRequest(Contract):
    query: str
    asset_id: str | None = None
    top_k: int = 5


class DocChunk(Contract):
    doc_id: str
    title: str
    section: str  # e.g. 7.4
    page: int
    text: str
    score: float


class DocSearchResponse(Contract):
    query: str
    chunks: list[DocChunk]
