"""Engineering tools: search_manuals, get_bom."""
from plantguard_contracts.schemas.incidents import Evidence, EvidenceKind

from harness.events import clip
from tools.plant.presenter import presenter


def chunk_evidence(c: dict) -> Evidence:
    if c["doc_id"].startswith("WO-"):
        return Evidence(kind=EvidenceKind.WORK_ORDER, ref_id=c["doc_id"], title=clip(c["title"], 80),
                        snippet=clip(c["text"], 140), link=f"/work-orders/{c['doc_id']}")
    return Evidence(kind=EvidenceKind.MANUAL, ref_id=f"{c['doc_id']}#{c['section']}",
                    title=f"Manual §{c['section']} ({c['title']})" if len(c["title"]) < 40 else f"Manual §{c['section']}",
                    snippet=clip(c["text"], 140), link=f"/docs/{c['doc_id']}?section={c['section']}")


def _manual_evidence(d, args, pol):
    min_score = pol.float("manual_min_score", 0.8)
    top = [c for c in d.get("chunks", []) if c.get("score", 0) >= min_score and not c["doc_id"].startswith("WO-")]
    evs = [chunk_evidence(c) for c in top[: pol.int("manual_max_evidence", 1)]]
    return evs, (f"Manual §{top[0]['section']} matches the pattern" if top else "")


presenter(
    "search_manuals",
    label=lambda a: "Searching manuals",
    summary=lambda d, a: f"{len(d.get('chunks', []))} passage(s) found",
    evidence=_manual_evidence,
    compact=lambda d: {"query": d.get("query"), "chunks": [
        {"doc_id": c["doc_id"], "section": c["section"], "title": c["title"], "score": c["score"], "text": c["text"]}
        for c in d.get("chunks", [])]},
)

presenter(
    "get_bom",
    label=lambda a: "Finding the part in the BOM",
    summary=lambda d, a: f"{len(d.get('items', []))} parts in the bill of materials",
)
