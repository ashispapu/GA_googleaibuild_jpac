"""Retriever (AG-3): hybrid BM25 + vectors over P4's corpus (../../p4-ml-data/corpus/).
Story query "spindle vibration rising bearing" must return MAN-SPINDLE-200 §7.4 first."""
from plantguard_contracts.schemas.engineering import DocSearchRequest, DocSearchResponse


class HybridRetriever:  # implements interfaces.Retriever
    def search(self, req: DocSearchRequest) -> DocSearchResponse:
        raise NotImplementedError
