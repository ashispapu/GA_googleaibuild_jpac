"""Manual files (p4-ml-data/corpus/manuals/*.md) -> documents + section chunks, and work orders -> chunks."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

HEADING = re.compile(r"^##\s+(\d+(?:\.\d+)*)\s+(.+?)\s*$")
PAGE = re.compile(r"<!--\s*page\s+(\d+)\s*-->")


@dataclass
class Chunk:
    doc_id: str
    section: str
    heading: str
    page: int
    text: str


@dataclass
class ManualDoc:
    id: str
    title: str
    model: str
    asset_type: str
    source: str
    chunks: list[Chunk]


def parse_manual(path: Path) -> ManualDoc:
    raw = path.read_text(encoding="utf-8")
    meta, body = {}, raw
    if raw.startswith("---"):
        _, fm, body = raw.split("---", 2)
        meta = yaml.safe_load(fm) or {}
    doc_id = meta.get("id") or path.stem
    chunks: list[Chunk] = []
    page, cur, buf = 1, None, []

    def flush():
        if cur is not None:
            text = " ".join(l.strip() for l in buf if l.strip())
            if text:
                chunks.append(Chunk(doc_id, cur[0], cur[1], cur[2], text))

    for line in body.splitlines():
        m = PAGE.search(line)
        if m:
            page = int(m.group(1))
            continue
        h = HEADING.match(line)
        if h:
            flush()
            cur, buf = (h.group(1), h.group(2), page), []
        elif cur is not None:
            buf.append(line)
    flush()
    return ManualDoc(doc_id, meta.get("title", doc_id), meta.get("model", ""), meta.get("asset_type", ""),
                     str(path), chunks)


def load_manuals(directory: Path) -> list[ManualDoc]:
    return [parse_manual(p) for p in sorted(directory.glob("*.md")) if p.name.lower() != "readme.md"]


def work_order_text(wo: dict, part_names: dict[str, str] | None = None) -> str:
    parts = ", ".join(f"{p['sku']}" + (f" ({part_names[p['sku']]})" if part_names and p["sku"] in part_names else "")
                      for p in wo.get("parts") or [])
    bits = [wo.get("title") or "", f"Root cause: {wo['root_cause']}." if wo.get("root_cause") else "",
            f"Findings: {wo['findings']}" if wo.get("findings") else "", f"Parts used: {parts}." if parts else "",
            f"Outcome: {wo['outcome']}." if wo.get("outcome") else ""]
    return " ".join(b for b in bits if b).strip()
