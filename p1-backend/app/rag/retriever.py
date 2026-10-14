"""FaissRetriever (implements interfaces.Retriever): semantic search over manuals and past work orders, scoped to the
machine being asked about, with cited chunks (doc_id + section + page)."""
from __future__ import annotations

import math

from plantguard_contracts.schemas.engineering import DocChunk, DocSearchRequest, DocSearchResponse

from app.rag.corpus import Chunk, ManualDoc, work_order_text
from app.rag.vector_store import QUERY_FILLER, FaissStore, HashingEmbedder, _stem, tokens
import re
from app.sim.db import Database


def calibrated(cos: float) -> float:
    """Cosine of hashed TF-IDF vectors -> 0..1 (0.35 cosine ~ 0.88, 0.1 ~ 0.25)."""
    return 1 / (1 + math.exp(-(cos - 0.18) * 13))


def overlap(query_stems: set[str], text: str, idf: dict[str, float] | None = None, default_idf: float = 3.0) -> float:
    """Share of the query's words the chunk contains, rare words counting more (the lexical half of the hybrid)."""
    if not query_stems:
        return 0.0
    have = {_stem(t) for t in tokens(text)}
    w = (lambda t: (idf or {}).get(t, default_idf))  # noqa: E731
    return sum(w(t) for t in query_stems & have) / sum(w(t) for t in query_stems)


def hybrid(cos: float, lex: float) -> float:
    return min(0.99, round(0.45 * calibrated(cos) + 0.55 * lex, 2))


class FaissRetriever:
    def __init__(self, it: Database, store: FaissStore):
        self.it, self.store = it, store

    # ---- indexing ----
    @classmethod
    def build(cls, it: Database, directory, manuals: list[ManualDoc], now_iso: str) -> "FaissRetriever":
        """Fresh catalogue + index from the manual files and every done work order already in the CMMS."""
        rows = []
        for doc in manuals:
            it.x("INSERT OR REPLACE INTO documents(id, kind, title, asset_type, equipment_model, asset_id, source, "
                 "created_at) VALUES(?,?,?,?,?,?,?,?)", (doc.id, "manual", doc.title, doc.asset_type, doc.model, None,
                                                        doc.source, now_iso))
            for c in doc.chunks:
                cid = it.x("INSERT INTO doc_chunks(doc_id, section, heading, page, text) VALUES(?,?,?,?,?)",
                           (c.doc_id, c.section, c.heading, c.page, c.text))
                rows.append((cid, f"{doc.title} {c.heading}. {c.text}"))
        wo_rows = cls._insert_work_orders(it, now_iso, it.q("SELECT id FROM work_orders WHERE status='done'"))
        rows += wo_rows
        embedder = HashingEmbedder().fit([t for _, t in rows])
        store = FaissStore(directory, embedder)
        store.add([i for i, _ in rows], [t for _, t in rows])
        return cls(it, store)

    @staticmethod
    def _insert_work_orders(it: Database, now_iso: str, ids) -> list[tuple[int, str]]:
        rows = []
        names = {r["sku"]: r["description"] for r in it.q("SELECT sku, description FROM parts")}
        for r in ids:
            wo = dict(it.one("SELECT w.*, e.type AS asset_type FROM work_orders w JOIN equipment e "
                             "ON e.asset_id=w.asset_id WHERE w.id=?", (r["id"],)))
            wo["parts"] = [dict(p) for p in it.q("SELECT sku, quantity FROM work_order_parts WHERE work_order_id=?",
                                                 (wo["id"],))]
            text = work_order_text(wo, names)
            title = f"Work order: {wo['title']}"
            it.x("INSERT OR REPLACE INTO documents(id, kind, title, asset_type, equipment_model, asset_id, source, "
                 "created_at) VALUES(?,?,?,?,?,?,?,?)", (wo["id"], "work_order", title, wo["asset_type"], None,
                                                        wo["asset_id"], "cmms", now_iso))
            it.x("DELETE FROM doc_chunks WHERE doc_id=?", (wo["id"],))
            cid = it.x("INSERT INTO doc_chunks(doc_id, section, heading, page, text) VALUES(?,?,?,?,?)",
                       (wo["id"], "findings", title, 1, text))
            rows.append((cid, f"{title}. {text}"))
        return rows

    def index_work_order(self, wo_id: str, now_iso: str) -> None:
        """Called when a repair is closed: the fix becomes searchable for the next incident."""
        if self.it.one("SELECT 1 FROM work_orders WHERE id=?", (wo_id,)) is None:
            return
        rows = self._insert_work_orders(self.it, now_iso, [{"id": wo_id}])
        self.store.add([i for i, _ in rows], [t for _, t in rows])

    # ---- search ----
    def search(self, req: DocSearchRequest) -> DocSearchResponse:
        scope_type = scope_model = None
        if req.asset_id:
            eq = self.it.one("SELECT type, model FROM equipment WHERE asset_id=?", (req.asset_id,))
            if eq:
                scope_type, scope_model = eq["type"], eq["model"]
        hits = self.store.search(f"{req.query} {scope_model or ''}", max(req.top_k * 10, 60))
        q_stems = {_stem(t) for t in tokens(req.query)
                   if t not in QUERY_FILLER and not re.fullmatch(r"eq-\d+|\d{4}", t)}
        chunks: list[DocChunk] = []
        for cid, cos in hits:
            row = self.it.one("SELECT c.*, d.title, d.kind, d.asset_type, d.asset_id FROM doc_chunks c JOIN documents d "
                              "ON d.id=c.doc_id WHERE c.id=?", (cid,))
            if row is None:
                continue
            if scope_type and row["asset_type"] and row["asset_type"] != scope_type:
                continue  # another machine type's manual or repair
            score = hybrid(cos, overlap(q_stems, f"{row['heading']} {row['text']}", self.store.embedder.idf,
                                        self.store.embedder.default_idf))
            if req.asset_id and row["kind"] == "work_order" and row["asset_id"] == req.asset_id:
                score = min(0.99, round(score + 0.05, 2))  # this very machine's history
            chunks.append(DocChunk(doc_id=row["doc_id"], title=row["title"], section=row["section"], page=row["page"],
                                   text=row["text"], score=score))
        chunks.sort(key=lambda c: -c.score)
        return DocSearchResponse(query=req.query, chunks=chunks[: req.top_k])

    # ---- reading (manual viewer) ----
    def document(self, doc_id: str) -> dict:
        d = self.it.one("SELECT * FROM documents WHERE id=?", (doc_id,))
        if d is None:
            raise KeyError(doc_id)
        sections = [dict(r) for r in self.it.q("SELECT section, heading, page, text FROM doc_chunks WHERE doc_id=? "
                                               "ORDER BY id", (doc_id,))]
        used_by = [r["asset_id"] for r in self.it.q("SELECT asset_id FROM equipment WHERE manual_doc_id=? ORDER BY asset_id",
                                                    (doc_id,))]
        return {"id": d["id"], "kind": d["kind"], "title": d["title"], "asset_type": d["asset_type"],
                "equipment_model": d["equipment_model"], "asset_id": d["asset_id"], "sections": sections,
                "used_by": used_by}

    def documents(self, kind: str | None = None) -> list[dict]:
        rows = self.it.q("SELECT d.*, (SELECT COUNT(*) FROM doc_chunks c WHERE c.doc_id=d.id) AS n FROM documents d "
                         + ("WHERE kind=? " if kind else "") + "ORDER BY kind, id", (kind,) if kind else ())
        return [{"id": r["id"], "kind": r["kind"], "title": r["title"], "asset_type": r["asset_type"],
                 "equipment_model": r["equipment_model"], "asset_id": r["asset_id"], "sections": r["n"]} for r in rows]


__all__ = ["FaissRetriever", "Chunk"]
