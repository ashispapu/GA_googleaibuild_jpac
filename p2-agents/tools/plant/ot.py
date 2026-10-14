"""OT tools: get_signals, get_alarms."""
from plantguard_contracts.schemas.incidents import Evidence, EvidenceKind

from tools.plant.presenter import presenter

LABELS = {"vibration_rms": "Vibration", "bearing_temp": "Bearing temperature", "vibration_kurtosis": "Kurtosis",
          "power_kw": "Power", "spindle_rpm": "Spindle speed"}


def _stats(series: dict) -> dict:
    vals = [p["value"] for p in series.get("points", [])]
    if not vals:
        return {"signal": series.get("signal"), "unit": series.get("unit"), "points": 0}
    first, last = vals[0], vals[-1]
    return {"signal": series["signal"], "unit": series.get("unit"), "points": len(vals), "first": round(first, 3),
            "last": round(last, 3), "min": round(min(vals), 3), "max": round(max(vals), 3),
            "change_pct": round((last - first) / first * 100, 1) if first else None,
            "from": series["points"][0]["ts"], "to": series["points"][-1]["ts"]}


def _signals_evidence(d, args, pol):
    out = []
    for s in d.get("items", []):
        st = _stats(s)
        if not st.get("points"):
            continue
        name = s["signal"]
        thr = pol.get(f"rule_threshold_{name}")
        span_h = max(1, round(len(s["points"]) * 5 / 60))
        trend = f"{'Up' if (st['change_pct'] or 0) > 0 else 'Down'} {abs(st['change_pct'] or 0):.0f}% in {span_h} h"
        snippet = trend
        if thr:
            thr = float(thr)
            snippet += f"; rule threshold {thr:g} {'not yet hit' if st['last'] < thr else 'exceeded'}"
        out.append(Evidence(kind=EvidenceKind.SENSOR, ref_id=f"{s['asset_id']}:{name}",
                            title=f"{LABELS.get(name, name)} {st['last']:.1f} {s.get('unit', '')}".strip(),
                            snippet=snippet, link=f"/assets/{s['asset_id']}?signal={name}"))
    return out[:1] if pol.bool("sensor_evidence_first_only", True) else out, (
        f"Sensor trend: {out[0].title}" if out else "")


presenter(
    "get_signals",
    label=lambda a: "Looking at the sensor history",
    summary=lambda d, a: "; ".join(f"{LABELS.get(st['signal'], st['signal'])} {st.get('first')}→{st.get('last')} "
                                   f"{st.get('unit') or ''}".strip()
                                   for st in (_stats(s) for s in d.get("items", [])) if st.get("points")),
    evidence=_signals_evidence,
    compact=lambda d: {"series": [_stats(s) for s in d.get("items", [])]},
)

presenter(
    "get_alarms",
    label=lambda a: "Checking machine alarms",
    summary=lambda d, a: f"{d.get('total', len(d.get('items', [])))} active alarm(s)",
    compact=lambda d: {"total": d.get("total"), "items": [
        {k: x.get(k) for k in ("id", "asset_id", "code", "severity", "message", "raised_at")} for x in d.get("items", [])]},
)
