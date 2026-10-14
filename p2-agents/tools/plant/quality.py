"""Quality tools: get_defects."""
from collections import Counter
from datetime import datetime

from plantguard_contracts.schemas.incidents import Evidence, EvidenceKind

from tools.plant.presenter import presenter


def _evidence(d, args, pol):
    items = d.get("items", [])
    if not items or d.get("trend_pct", 0) < pol.float("defect_trend_min_pct", 100):
        return [], ""
    kind, n = Counter(i["defect_type"] for i in items).most_common(1)[0]
    since = min(i["ts"] for i in items if i["defect_type"] == kind)
    hhmm = datetime.fromisoformat(since.replace("Z", "+00:00")).strftime("%H:%M")
    label = kind.replace("_", " ").split(" ")[0].capitalize()
    ev = Evidence(kind=EvidenceKind.QUALITY, ref_id=f"{d['line_id']}:{kind}", title=f"{label} defects +{d['trend_pct']:.0f}%",
                  snippet=f"{n} parts out of tolerance since {hhmm}", link=f"/quality/{d['line_id']}")
    return [ev], f"{label} defects up {d['trend_pct']:.0f}%"


presenter(
    "get_defects",
    label=lambda a: "Checking quality",
    summary=lambda d, a: f"Defect rate {d.get('defect_rate_pct')}%, trend {d.get('trend_pct'):+.0f}%",
    evidence=_evidence,
    compact=lambda d: {"line_id": d.get("line_id"), "defect_rate_pct": d.get("defect_rate_pct"),
                       "trend_pct": d.get("trend_pct"), "count": len(d.get("items", [])),
                       "types": dict(Counter(i["defect_type"] for i in d.get("items", []))),
                       "first": (d.get("items") or [{}])[0].get("ts"), "last": (d.get("items") or [{}])[-1].get("ts")},
)
