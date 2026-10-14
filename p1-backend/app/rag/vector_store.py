"""Manuals vector store: FAISS index over document chunks (manuals + completed work orders).

- Chunks and documents live in the IT database (documents, doc_chunks); the FAISS index stores one vector per chunk
  under the chunk's integer id, so a hit maps straight back to the catalogue.
- Embeddings: hashed TF-IDF (word + bigram features, signed hashing, sublinear tf, L2-normalised). It runs offline,
  needs no model download and is deterministic. Swap `HashingEmbedder` for any other embedder with the same
  `embed(texts) -> np.ndarray` method.
- Every completed repair is added to the index the moment it is closed ("the solution used").
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import Counter
from pathlib import Path

import faiss
import numpy as np

TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
STOP = set("the a an and or of to in on at for with is are be by it this that as from if when then than into".split())
# words that carry no meaning in a search request ("find the manual guidance for ...")
QUERY_FILLER = set("find search look show give tell me please manual manuals guidance guide section sections about what "
                   "why how which should we do does is there any high low eq".split())


def tokens(text: str) -> list[str]:
    out = []
    for t in TOKEN.findall(text.lower()):
        if t in STOP:
            continue
        out.append(t)
        if "-" in t:  # SKU-like tokens: also index their parts (skf-6205 -> skf, 6205)
            out.extend(p for p in t.split("-") if p and p not in STOP)
    return out


def _stem(t: str) -> str:
    """Tiny suffix stripper: rising/rises/rise -> ris, bearings/bearing -> bear, worn stays worn."""
    if t.isdigit() or "-" in t:
        return t
    for suf in ("ings", "ing", "ies", "es", "ed", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            t = t[: -len(suf)]
            break
    if t.endswith("e") and len(t) > 4:
        t = t[:-1]
    return t


class HashingEmbedder:
    def __init__(self, dim: int = 1024, idf: dict[str, float] | None = None, default_idf: float = 3.0):
        self.dim, self.idf, self.default_idf = dim, idf or {}, default_idf

    @staticmethod
    def features(text: str) -> list[str]:
        toks = [_stem(t) for t in tokens(text)]
        return toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]

    def fit(self, texts: list[str]) -> "HashingEmbedder":
        df = Counter()
        for t in texts:
            df.update(set(self.features(t)))
        n = max(1, len(texts))
        self.idf = {f: math.log((1 + n) / (1 + c)) + 1 for f, c in df.items()}
        self.default_idf = math.log(1 + n) + 1
        return self

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype="float32")
        for i, text in enumerate(texts):
            for f, c in Counter(self.features(text)).items():
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
                w = (1 + math.log(c)) * self.idf.get(f, self.default_idf)
                out[i, h % self.dim] += w if (h >> 63) & 1 else -w
            norm = float(np.linalg.norm(out[i]))
            if norm:
                out[i] /= norm
        return out

    def to_json(self) -> str:
        return json.dumps({"dim": self.dim, "idf": self.idf, "default_idf": self.default_idf})

    @classmethod
    def from_json(cls, s: str) -> "HashingEmbedder":
        d = json.loads(s)
        return cls(d["dim"], d["idf"], d["default_idf"])


class FaissStore:
    """IndexIDMap over inner product (cosine on normalised vectors). Thread-safe; saved after every change."""

    def __init__(self, directory: Path, embedder: HashingEmbedder):
        self.dir, self.embedder = directory, embedder
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        idx = self.dir / "manuals.faiss"
        if idx.exists():
            self.index = faiss.read_index(str(idx))
        else:
            self.index = faiss.IndexIDMap2(faiss.IndexFlatIP(embedder.dim))

    @classmethod
    def open(cls, directory: Path) -> "FaissStore | None":
        emb = directory / "embedder.json"
        if not emb.exists():
            return None
        return cls(directory, HashingEmbedder.from_json(emb.read_text()))

    def add(self, ids: list[int], texts: list[str]) -> None:
        if not ids:
            return
        vecs = self.embedder.embed(texts)
        with self._lock:
            self.index.remove_ids(np.array(ids, dtype="int64"))
            self.index.add_with_ids(vecs, np.array(ids, dtype="int64"))
            self.save()

    def search(self, query: str, k: int) -> list[tuple[int, float]]:
        vec = self.embedder.embed([query])
        with self._lock:
            if self.index.ntotal == 0:
                return []
            scores, ids = self.index.search(vec, min(k, self.index.ntotal))
        return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i >= 0]

    @property
    def size(self) -> int:
        return int(self.index.ntotal)

    def save(self) -> None:
        faiss.write_index(self.index, str(self.dir / "manuals.faiss"))
        (self.dir / "embedder.json").write_text(self.embedder.to_json())
