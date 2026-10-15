"""FaissRetriever (implements interfaces.Retriever): semantic search over manuals and past work orders, scoped to the
machine being asked about, with cited chunks (doc_id + section + page)."""
from __future__ import annotations

import math
import os
from pathlib import Path

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


class FaissBackend:
    """Single-process vector index on local disk (development, tests, approach-1 deployments)."""
    name = "faiss"

    def __init__(self, store: FaissStore):
        self.store, self.embedder = store, store.embedder

    def add(self, items: list[tuple[int, str, dict]]) -> None:
        self.store.add([i for i, _, _ in items], [t for _, t, _ in items])

    def search(self, query: str, k: int, plants: list[str] | None, asset_type: str | None) -> list[tuple[int, float]]:
        return self.store.search(query, k)  # plant / type scope is applied by the retriever on the catalogue rows

    def remove(self, ids: list[int], payload: dict) -> None:
        with self.store._lock:
            import numpy as np
            self.store.index.remove_ids(np.array(ids, dtype="int64"))
            self.store.save()

    def size(self) -> int:
        return self.store.size

    def ping(self) -> bool:
        return True


class QdrantBackend:
    """Qdrant service (production). Manuals in one collection, each plant's repairs in its own collection; a search
    only opens the collections of the plants it is allowed to see."""
    name = "qdrant"

    def __init__(self, store, plant_ids: list[str]):
        self.store, self.embedder, self.plant_ids = store, store.embedder, list(plant_ids)

    def _collection(self, payload: dict) -> str:
        return self.store.workorders(payload["plant_id"]) if payload.get("kind") == "work_order" else self.store.manuals()

    def add(self, items: list[tuple[int, str, dict]]) -> None:
        groups: dict[str, list] = {}
        for item in items:
            groups.setdefault(self._collection(item[2]), []).append(item)
        for name, group in groups.items():
            self.store.add(name, group)

    def remove(self, ids: list[int], payload: dict) -> None:
        self.store.delete(self._collection(payload), ids)

    def collections(self, plants: list[str] | None = None) -> list[str]:
        allowed = [p for p in (plants if plants is not None else self.plant_ids) if p in self.plant_ids]
        return [self.store.manuals(), *[self.store.workorders(p) for p in allowed]]

    def search(self, query: str, k: int, plants: list[str] | None, asset_type: str | None) -> list[tuple[int, float]]:
        return [(i, s) for i, s, _ in self.store.search(query, k, self.collections(plants), asset_type)]

    def size(self) -> int:
        return self.store.size(self.collections())

    def ping(self) -> bool:
        return self.store.ping()


class Retriever:
    """implements interfaces.Retriever over a vector backend (FaissBackend or QdrantBackend). The catalogue (documents,
    doc_chunks) lives in the IT database; the backend stores one vector per chunk under the chunk id."""

    def __init__(self, it: Database, backend):
        self.it, self.backend = it, backend

    @property
    def store(self):  # older callers
        return self.backend

    # ---- indexing ----
    @classmethod
    def build(cls, it: Database, manuals: list[ManualDoc], now_iso: str, make_backend) -> "Retriever":
        """Fresh catalogue + index from the manual files and every done work order already in the CMMS.
        `make_backend(embedder)` creates the (empty) vector backend once the vocabulary is known."""
        it.x("DELETE FROM doc_chunks")
        rows: list[tuple[int, str, dict]] = []
        for doc in manuals:
            it.x("INSERT INTO documents(id, kind, title, asset_type, equipment_model, asset_id, source, "
                 "created_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, title=excluded.title, "
                 "asset_type=excluded.asset_type, equipment_model=excluded.equipment_model, asset_id=excluded.asset_id, "
                 "source=excluded.source, created_at=excluded.created_at", (doc.id, "manual", doc.title, doc.asset_type, doc.model, None,
                                                        doc.source, now_iso))
            for c in doc.chunks:
                cid = it.insert_id("INSERT INTO doc_chunks(doc_id, section, heading, page, text) VALUES(?,?,?,?,?)",
                                   (c.doc_id, c.section, c.heading, c.page, c.text))
                rows.append((cid, f"{doc.title} {c.heading}. {c.text}",
                             {"doc_id": doc.id, "kind": "manual", "asset_type": doc.asset_type, "asset_id": None,
                              "plant_id": None}))
        rows += cls._insert_work_orders(it, now_iso, it.q("SELECT id FROM work_orders WHERE status='done' ORDER BY id"))
        embedder = HashingEmbedder().fit([t for _, t, _ in rows])
        it.set_meta("vdb_embedder", embedder.to_json())
        backend = make_backend(embedder)
        backend.add(rows)
        return cls(it, backend)

    @staticmethod
    def _insert_work_orders(it: Database, now_iso: str, ids) -> list[tuple[int, str, dict]]:
        rows = []
        names = {r["sku"]: r["description"] for r in it.q("SELECT sku, description FROM parts")}
        for r in ids:
            wo = dict(it.one("SELECT w.*, e.type AS asset_type, e.plant_id FROM work_orders w JOIN equipment e "
                             "ON e.asset_id=w.asset_id WHERE w.id=?", (r["id"],)))
            wo["parts"] = [dict(p) for p in it.q("SELECT sku, quantity FROM work_order_parts WHERE work_order_id=? "
                                                 "ORDER BY sku", (wo["id"],))]
            text = work_order_text(wo, names)
            title = f"Work order: {wo['title']}"
            it.x("INSERT INTO documents(id, kind, title, asset_type, equipment_model, asset_id, source, "
                 "created_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, title=excluded.title, "
                 "asset_type=excluded.asset_type, equipment_model=excluded.equipment_model, asset_id=excluded.asset_id, "
                 "source=excluded.source, created_at=excluded.created_at", (wo["id"], "work_order", title, wo["asset_type"], None,
                                                        wo["asset_id"], "cmms", now_iso))
            it.x("DELETE FROM doc_chunks WHERE doc_id=?", (wo["id"],))
            cid = it.insert_id("INSERT INTO doc_chunks(doc_id, section, heading, page, text) VALUES(?,?,?,?,?)",
                               (wo["id"], "findings", title, 1, text))
            rows.append((cid, f"{title}. {text}", {"doc_id": wo["id"], "kind": "work_order",
                                                   "asset_type": wo["asset_type"], "asset_id": wo["asset_id"],
                                                   "plant_id": wo["plant_id"]}))
        return rows

    def index_work_order(self, wo_id: str, now_iso: str) -> None:
        """Called when a repair is closed: the fix becomes searchable for the next incident (in its own plant)."""
        if self.it.one("SELECT 1 FROM work_orders WHERE id=?", (wo_id,)) is None:
            return
        old = [r["id"] for r in self.it.q("SELECT id FROM doc_chunks WHERE doc_id=?", (wo_id,))]
        rows = self._insert_work_orders(self.it, now_iso, [{"id": wo_id}])
        if old:
            self.backend.remove(old, rows[0][2])
        self.backend.add(rows)

    # ---- search ----
    def search(self, req: DocSearchRequest, plants: list[str] | None = None) -> DocSearchResponse:
        """`plants`: the plants the caller may see (None = no restriction, e.g. an agent working on one incident,
        which is already scoped by its asset). A machine-scoped search only sees that machine's plant."""
        scope_type = scope_model = None
        if req.asset_id:
            eq = self.it.one("SELECT type, model, plant_id FROM equipment WHERE asset_id=?", (req.asset_id,))
            if eq:
                scope_type, scope_model = eq["type"], eq["model"]
                plants = [eq["plant_id"]] if plants is None or eq["plant_id"] in plants else []
        hits = self.backend.search(f"{req.query} {scope_model or ''}", max(req.top_k * 10, 60), plants, scope_type)
        q_stems = {_stem(t) for t in tokens(req.query)
                   if t not in QUERY_FILLER and not re.fullmatch(r"eq-\d+|\d{4}", t)}
        emb = self.backend.embedder
        chunks: list[DocChunk] = []
        rows = {}
        ids = [cid for cid, _ in hits]
        for start in range(0, len(ids), 500):  # one query for every hit (not one per hit)
            part = ids[start:start + 500]
            for r in self.it.q("SELECT c.*, d.title, d.kind, d.asset_type, d.asset_id, e.plant_id FROM doc_chunks c "
                               "JOIN documents d ON d.id=c.doc_id LEFT JOIN equipment e ON e.asset_id=d.asset_id "
                               f"WHERE c.id IN ({','.join('?' * len(part))})", part):
                rows[r["id"]] = r
        for cid, cos in hits:
            row = rows.get(cid)
            if row is None:
                continue
            if scope_type and row["asset_type"] and row["asset_type"] != scope_type:
                continue  # another machine type's manual or repair
            if plants is not None and row["kind"] == "work_order" and row["plant_id"] not in plants:
                continue  # another plant's repair history
            score = hybrid(cos, overlap(q_stems, f"{row['heading']} {row['text']}", emb.idf, emb.default_idf))
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


FaissRetriever = Retriever  # older name


def vector_mode() -> str:
    """VECTOR_STORE=qdrant|faiss; default qdrant when QDRANT_URL is set, else faiss."""
    return (os.getenv("VECTOR_STORE") or ("qdrant" if os.getenv("QDRANT_URL") else "faiss")).lower()


def backend_factory(it: Database, data_dir: Path, recreate: bool = False):
    """embedder -> empty backend, for Retriever.build()."""
    plant_ids = [r["id"] for r in it.q("SELECT id FROM plants ORDER BY id")]
    if vector_mode() == "qdrant":
        from app.rag.qdrant_store import QdrantStore, make_client
        client = make_client(data_dir)

        def make(embedder):
            store = QdrantStore(client, embedder)
            store.ensure(plant_ids, recreate=recreate)
            return QdrantBackend(store, plant_ids)
        return make

    def make_faiss(embedder):
        directory = data_dir / "vdb"
        for f in ("manuals.faiss", "embedder.json"):
            (directory / f).unlink(missing_ok=True)
        return FaissBackend(FaissStore(directory, embedder))
    return make_faiss


def open_backend(it: Database, data_dir: Path):
    """The existing index, or None when it has to be (re)built."""
    if vector_mode() == "qdrant":
        raw = it.meta("vdb_embedder")
        if not raw:
            return None
        from app.rag.qdrant_store import QdrantStore, make_client
        plant_ids = [r["id"] for r in it.q("SELECT id FROM plants ORDER BY id")]
        store = QdrantStore(make_client(data_dir), HashingEmbedder.from_json(raw))
        store.ensure(plant_ids)
        backend = QdrantBackend(store, plant_ids)
        return backend if backend.size() > 0 else None
    store = FaissStore.open(data_dir / "vdb")
    return FaissBackend(store) if store is not None and store.size > 0 else None


__all__ = ["Retriever", "FaissRetriever", "FaissBackend", "QdrantBackend", "Chunk", "backend_factory", "open_backend",
           "vector_mode"]
