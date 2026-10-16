"""Vector store on Qdrant (C10 v1.2). Production: a dedicated Qdrant service with persistent disk, API-key + JWT RBAC;
development: Qdrant's embedded local mode in `<data_dir>/qdrant` (no server, same API).

Collection isolation
  <prefix>_manuals                  equipment manuals, shared by every plant (read-only for the API and workers)
  <prefix>_workorders_<plant>       closed repairs of ONE plant (Plant A's history never mixes with Plant B's)
`<prefix>` = PLANTGUARD_VECTOR_PREFIX (default plantguard_<env>), so dev / staging / prod can share one cluster
without seeing each other. Each service gets a JWT that only names the collections it needs (deploy/qdrant).

Points: id = doc_chunks.id (IT database), vector = hashed TF-IDF (rag/vector_store.HashingEmbedder, cosine),
payload = {doc_id, kind, asset_type, asset_id, plant_id}. The text lives in the IT database; Qdrant holds vectors only.
The embedder's vocabulary (IDF) is stored in IT `meta.vdb_embedder` so every process embeds queries identically.
"""
from __future__ import annotations

import contextlib
import logging
import os
import threading
from pathlib import Path

from app.rag.vector_store import HashingEmbedder

log = logging.getLogger("plantguard.qdrant")


def vector_prefix() -> str:
    return os.getenv("PLANTGUARD_VECTOR_PREFIX") or f"plantguard_{os.getenv('PLANTGUARD_ENV', 'dev')}"


def make_client(data_dir: Path):
    from qdrant_client import QdrantClient
    url = os.getenv("QDRANT_URL")
    if url:  # QDRANT_CA_CERT: the private CA that signed the Qdrant server certificate (https://)
        extra = {"verify": os.environ["QDRANT_CA_CERT"]} if os.getenv("QDRANT_CA_CERT") else {}
        # Qdrant is an infrastructure service, not an outbound LLM call. Docker/office proxy settings must not
        # route internal service names (such as "qdrant") through a corporate proxy. Keep explicit TLS verification.
        return QdrantClient(url=url, api_key=os.getenv("QDRANT_API_KEY") or None, timeout=15, prefer_grpc=False,
                            https=url.startswith("https"), trust_env=False, **extra)
    path = data_dir / "qdrant"
    path.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(path))


class QdrantStore:
    def __init__(self, client, embedder: HashingEmbedder, prefix: str | None = None):
        self.client, self.embedder, self.prefix = client, embedder, prefix or vector_prefix()
        self.embedded = type(getattr(client, "_client", None)).__name__ == "QdrantLocal"
        # embedded (local) mode is not thread-safe; the server client is, and must not serialise requests
        self._lock = threading.Lock() if self.embedded else contextlib.nullcontext()
        self._known: set[str] = set()

    # ---- collections ----
    def manuals(self) -> str:
        return f"{self.prefix}_manuals"

    def workorders(self, plant_id: str) -> str:
        return f"{self.prefix}_workorders_{plant_id.lower().replace('-', '_')}"

    def ensure(self, plant_ids: list[str], recreate: bool = False) -> None:
        from qdrant_client.models import Distance, PayloadSchemaType, VectorParams
        for name in [self.manuals(), *[self.workorders(p) for p in plant_ids]]:
            with self._lock:
                exists = self.client.collection_exists(name)
                if exists and recreate:
                    self.client.delete_collection(name)
                    exists = False
                if not exists:
                    self.client.create_collection(name, vectors_config=VectorParams(size=self.embedder.dim,
                                                                                    distance=Distance.COSINE))
                    if not self.embedded:  # embedded (local) mode has no payload indexes
                        for field in ("asset_type", "asset_id", "kind", "doc_id"):
                            self.client.create_payload_index(name, field, PayloadSchemaType.KEYWORD)
                    log.info("created vector collection %s", name)
            self._known.add(name)

    # ---- write ----
    def add(self, collection: str, items: list[tuple[int, str, dict]]) -> None:
        """items = [(chunk id, text, payload)]; upsert = re-indexing the same chunk replaces it."""
        if not items:
            return
        from qdrant_client.models import PointStruct
        vecs = self.embedder.embed([t for _, t, _ in items])
        points = [PointStruct(id=int(i), vector=vecs[k].tolist(), payload=p) for k, (i, _, p) in enumerate(items)]
        with self._lock:
            for start in range(0, len(points), 256):
                self.client.upsert(collection, points[start:start + 256], wait=True)

    def delete(self, collection: str, ids: list[int]) -> None:
        from qdrant_client.models import PointIdsList
        with self._lock:
            self.client.delete(collection, points_selector=PointIdsList(points=[int(i) for i in ids]), wait=True)

    # ---- read ----
    def search(self, query: str, k: int, collections: list[str], asset_type: str | None = None) -> list[tuple[int, float, dict]]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        vec = self.embedder.embed([query])[0].tolist()
        flt = Filter(must=[FieldCondition(key="asset_type", match=MatchValue(value=asset_type))]) if asset_type else None
        out: list[tuple[int, float, dict]] = []
        for name in collections:
            if name not in self._known:
                with self._lock:
                    if not self.client.collection_exists(name):
                        continue
                self._known.add(name)
            with self._lock:
                res = self.client.query_points(name, query=vec, limit=k, query_filter=flt, with_payload=True)
            out += [(int(p.id), float(p.score), p.payload or {}) for p in res.points]
        out.sort(key=lambda x: -x[1])
        return out[:k]

    def size(self, collections: list[str]) -> int:
        n = 0
        for name in collections:
            with self._lock:
                if self.client.collection_exists(name):
                    n += self.client.count(name, exact=True).count
        return n

    def ping(self) -> bool:
        with self._lock:  # a collection-scoped JWT may not list collections; checking our own one always works
            return bool(self.client.collection_exists(self.manuals()))

    def snapshot(self, collections: list[str]) -> list[str]:
        """Server-side snapshots (stored on the Qdrant volume; deploy/qdrant copies them to Cloud Storage)."""
        names = []
        for name in collections:
            with self._lock:
                if self.client.collection_exists(name):
                    names.append(self.client.create_snapshot(name, wait=True).name)
        return names
