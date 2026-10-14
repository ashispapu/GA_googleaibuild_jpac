"""Energy tools: get_energy, get_tariffs, get_co2_factor."""
from plantguard_contracts.schemas.incidents import Evidence, EvidenceKind

from tools.plant.presenter import presenter


def _excess(d, asset_id=None):
    rows = [r for r in d.get("by_asset", []) if not asset_id or r["asset_id"] == asset_id]
    return [(r["asset_id"], (r["avg_power_kw"] - r["baseline_power_kw"]) / r["baseline_power_kw"] * 100) for r in rows
            if r.get("baseline_power_kw")]


def _evidence(d, a, pol):
    worst = max(_excess(d), key=lambda x: x[1], default=None)
    if not worst or worst[1] < pol.float("excess_power_min_pct", 3):
        return [], ""
    return [Evidence(kind=EvidenceKind.ENERGY, ref_id=f"{worst[0]}:power_kw", title=f"{worst[0]} draws {worst[1]:.0f}% above baseline",
                     snippet="Extra friction shows up as extra power", link=f"/assets/{worst[0]}?signal=power_kw")], ""


presenter(
    "get_energy",
    label=lambda a: "Reading line energy",
    summary=lambda d, a: f"{d.get('kwh'):,.0f} kWh in 24 h; " + ", ".join(f"{x} {p:+.0f}% vs baseline" for x, p in _excess(d)),
    evidence=_evidence,
)
presenter("get_tariffs", label=lambda a: "Reading electricity prices",
          summary=lambda d, a: ", ".join(f"{t['name']} €{t['price_per_kwh']['amount']}/kWh" for t in d.get("items", [])))
presenter("get_co2_factor", label=lambda a: "Reading the grid CO2 factor",
          summary=lambda d, a: f"{d.get('kg_per_kwh')} kg CO2 per kWh")
