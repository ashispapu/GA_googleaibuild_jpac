"""Deterministic planning maths (C3: "costs and numbers are computed in code, not by the LLM").

Every business number comes from a Policy (config/agents.ini [policy.*] / [option.*]); every fact comes from tool
data. The LLM never touches these functions' outputs except to explain them. Each function is pure so it is easy to
unit-test against the golden story (C9).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from harness.config import Policy
from domain.timeline import (energy_cost, hours, next_shift_start, overlapping_slot_hours, parse_shift, round_to,
                             to_dt)
from agents.cost_model import CostModel


# ---------------------------------------------------------------- per failure mode / part (all from [policy.planning])
def policy_map(pol: Policy, key: str) -> dict[str, str]:
    """`a:b, c:d` -> {a: b, c: d}."""
    out = {}
    for item in pol.list(key, []):
        k, _, v = item.partition(":")
        if k.strip():
            out[k.strip()] = v.strip()
    return out


def required_skill(sku: str | None, failure_mode: str | None, pol: Policy) -> str:
    """Skill the repair needs: by part-number prefix, then by failure mode, then [policy.planning] required_skill."""
    if sku:
        by_part = policy_map(pol, "skill_by_part_prefix")
        prefix = sku.split("-")[0]
        if prefix in by_part:
            return by_part[prefix]
    by_mode = policy_map(pol, "skill_by_mode")
    if failure_mode and failure_mode in by_mode:
        return by_mode[failure_mode]
    return pol.str("required_skill", "bearing_replacement")


def mode_word(pol: Policy, key: str, failure_mode: str | None, default: str) -> str:
    return policy_map(pol, key).get(failure_mode or "", default)


# ---------------------------------------------------------------- spare parts
@dataclass
class SourcePlan:
    mode: str                 # express | standard | supplier | supplier_express | local
    plant_id: str | None
    supplier_id: str | None
    available_qty: int
    eta_hours: float
    cost_eur: float


def plan_part_sources(part: dict, supplier: dict | None, plant_id: str, pol: Policy) -> tuple[int, list[SourcePlan]]:
    """Local stock + every way to bring the part here, fastest first."""
    unit = float(part["unit_cost"]["amount"])
    local = sum(int(s["quantity"]) for s in part["stock"] if s["plant_id"] == plant_id)
    out: list[SourcePlan] = []
    if local > 0:
        out.append(SourcePlan("local", plant_id, None, local, 0.0, unit))
    for s in part["stock"]:
        if s["plant_id"] == plant_id or int(s["quantity"]) <= 0:
            continue
        for mode in pol.list("transfer_modes", ["express", "standard"]):
            out.append(SourcePlan(mode, s["plant_id"], None, int(s["quantity"]), pol.float(f"{mode}_transfer_eta_h"),
                                  unit + pol.float(f"{mode}_transfer_eur")))
    lead_days = (supplier or {}).get("lead_time_days", part.get("supplier_lead_time_days"))
    sup_id = (supplier or {}).get("id", part.get("supplier_id"))
    if lead_days is not None:
        out.append(SourcePlan("supplier", None, sup_id, pol.int("supplier_available_qty", 50), float(lead_days) * 24,
                              unit + pol.float("supplier_shipping_eur", 0)))
    if supplier and supplier.get("express_available") and supplier.get("express_lead_time_days"):
        out.append(SourcePlan("supplier_express", None, sup_id, pol.int("supplier_available_qty", 50),
                              float(supplier["express_lead_time_days"]) * 24,
                              unit + pol.float("supplier_express_eur", 0)))
    out.sort(key=lambda s: (s.eta_hours, s.cost_eur))
    return local, out


def recommend_source(sources: list[SourcePlan], rul_hours: float | None, pol: Policy) -> int:
    """Cheapest source that arrives within RUL x safety factor; otherwise the fastest one."""
    if not sources:
        return 0
    budget = (rul_hours or 0) * pol.float("eta_safety_factor", 0.5)
    ok = [i for i, s in enumerate(sources) if s.eta_hours <= budget]
    if not ok:
        return 0
    return min(ok, key=lambda i: (sources[i].cost_eur, sources[i].eta_hours))


# ---------------------------------------------------------------- scheduling
@dataclass
class ProductionPlan:
    order_id: str
    due: datetime
    remaining_units: int
    rate_per_h: float


@dataclass
class WindowPlan:
    start: datetime
    end: datetime
    shift: str
    technician_ids: list[str]
    speed_before_pct: int
    deadline_met: bool
    lost_output_units: int
    lost_hours: float


def pick_order(orders: list[dict], line_id: str) -> dict | None:
    mine = [o for o in orders if o["line_id"] == line_id and o.get("status") not in ("done", "completed")]
    return min(mine, key=lambda o: to_dt(o["due_at"])) if mine else None


def production_plan(order: dict, pol: Policy) -> ProductionPlan:
    return ProductionPlan(order["id"], to_dt(order["due_at"]), int(order["quantity"]) - int(order["completed"]),
                          pol.float("line_rate_units_per_h"))


def can_finish(prod: ProductionPlan, now: datetime, lost_hours: float) -> tuple[bool, float]:
    """(deadline met?, units short). Capacity = rate x (hours until due - hours lost)."""
    capacity = prod.rate_per_h * max(0.0, hours(now, prod.due) - lost_hours)
    short = max(0.0, prod.remaining_units - capacity)
    return short <= 0, short


def pick_technicians(techs: list[dict], skill: str, n: int, prefer_off_shift: bool) -> list[str]:
    able = [t for t in techs if skill in t.get("skills", [])]
    able.sort(key=lambda t: ((t.get("on_shift") is True) if prefer_off_shift else (t.get("on_shift") is False),
                             -len(t.get("certifications", [])), t["id"]))
    return [t["id"] for t in able[:n]]


def plan_repair_window(now: datetime, part_eta_h: float, prod: ProductionPlan, techs: list[dict],
                       risk_72h: float, pol: Policy, skill: str | None = None) -> WindowPlan:
    """First preferred shift after the part arrives; slow the line down meanwhile if the risk is high."""
    ready = now + timedelta(hours=part_eta_h)
    shifts = {k[len("shift."):]: parse_shift(v) for k, v in pol.items() if k.startswith("shift.")}
    preferred = pol.list("preferred_shifts", ["night"])
    candidates = [(next_shift_start(ready, shifts[s][0]), s) for s in preferred if s in shifts]
    if not candidates:
        candidates = [(ready, "any")]
    start, shift = min(candidates)
    end = start + timedelta(hours=pol.float("repair_hours"))
    speed = pol.int("reduced_speed_pct") if risk_72h >= pol.float("slow_down_risk_threshold", 0.5) else 100
    lost = hours(now, start) * (1 - speed / 100) + pol.float("repair_hours")
    met, _ = can_finish(prod, now, lost)
    tech_ids = pick_technicians(techs, skill or pol.str("required_skill"), pol.int("technicians_needed", 1),
                                pol.bool("prefer_off_shift_now", True))
    return WindowPlan(start, end, shift, tech_ids, speed, met, int(round(prod.rate_per_h * lost)), lost)


# ---------------------------------------------------------------- energy
@dataclass
class EnergyPlan:
    option_id: str
    kwh: float
    cost_eur: float
    co2_kg: float
    note: str


def excess_power_pct(asset: dict) -> float:
    base = float(asset["baseline_power_kw"]) or 1.0
    return (float(asset["avg_power_kw"]) - base) / base * 100


def estimate_energy(option_id: str, opt: Policy, asset: dict, tariffs: list[dict], co2_kg_per_kwh: float,
                    now: datetime, due: datetime, window: WindowPlan | None, pol: Policy) -> EnergyPlan:
    """kWh / € / kg CO2 of an option versus doing nothing special (negative = saving)."""
    avg, base = float(asset["avg_power_kw"]), float(asset["baseline_power_kw"])
    excess = max(0.0, avg - base)
    strategy = opt.str("strategy")
    kwh, eur = 0.0, 0.0
    if strategy == "slow_then_swap" and window is not None:
        frac = 1 - (window.speed_before_pct / 100) ** pol.float("power_speed_exponent", 1.0)
        slow_kw = avg * frac
        kwh -= slow_kw * hours(now, window.start)
        eur -= energy_cost(slow_kw, now, window.start, tariffs)
        kwh -= excess * hours(window.end, due)  # worn-bearing waste avoided after the swap
        eur -= energy_cost(excess, window.end, due, tariffs)
    elif strategy == "run_to_supplier":
        kwh += excess * hours(now, due)
        eur += energy_cost(excess, now, due, tariffs)
    elif strategy == "stop_now" and not pol.bool("restart_offsets_savings", True):
        stop_h = float(opt.get("downtime_h") or 0)
        kwh -= avg * stop_h
        eur -= energy_cost(avg, now, now + timedelta(hours=stop_h), tariffs)
    kwh_r = round_to(kwh, pol.float("kwh_round", 10)) + 0.0
    return EnergyPlan(option_id, kwh_r, float(round(eur)) + 0.0, round(kwh_r * co2_kg_per_kwh, 1) + 0.0,
                      opt.str("energy_note", ""))


# ---------------------------------------------------------------- options
@dataclass
class OptionPlan:
    id: str
    strategy: str
    label: str
    summary: str
    actions: list[dict]
    cost: dict[str, float]
    downtime_hours: float
    deadline_met: bool
    energy_kwh: float
    co2_kg: float
    failure_risk_until_repair: float
    confidence: float
    risks: list[str]
    recommended: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def _fmt_eur(x: float) -> str:
    return f"€{x:,.0f}"


def _weekday(t: datetime) -> str:
    return t.strftime("%A")


def build_option(option_id: str, opt: Policy, f: "PlanningFacts", pol: Policy, cm: CostModel) -> OptionPlan:
    """One option from its strategy. f carries every fact gathered by the earlier agents."""
    strategy = opt.str("strategy")
    v = dict(f.template_vars())
    actions: list[dict] = []
    risks: list[str] = []
    tech = (f.window.technician_ids[0] if f.window and f.window.technician_ids else None)
    buy = pol.bool("purchase_orders", False)
    if strategy == "stop_now":
        src = f.source("local") or f.source(opt.str("transfer_mode", "express"))
        if src is None and buy:  # no plant has it: buy it, as fast as the supplier can deliver
            src = f.source("supplier_express") or f.source("supplier")
        eta = src.eta_hours if src else 0.0
        downtime_h = eta + pol.float("swap_when_stopped_h")
        stop_end = f.now + timedelta(hours=downtime_h)
        lost = overlapping_slot_hours(f.now, stop_end, f.schedule) if pol.bool("stop_loses_whole_shifts", True) \
            else downtime_h
        met, short = can_finish(f.prod, f.now, lost)
        cost = cm.option(downtime_h=downtime_h, night=False, transfer=src.mode if src else None,
                         night_labour=False, late=not met, unit_cost=f.unit_cost)
        risk, conf = 0.0, pol.float("stop_now_confidence")
        actions.append(f.speed_action("Stop Line {line_num}".format(**v), 0, f.now, stop_end))
        if src and src.plant_id and src.plant_id != f.plant_id:
            actions.append(f.transfer_action(src, opt.str("transfer_description", "Express 1× {sku} {from_name} → {to_name}")))
        elif src and src.supplier_id and buy:
            actions.append(f.purchase_action(src))
        if tech:
            when = "now" if src and src.mode == "local" else "on arrival"
            actions.append(f.work_order_action(f"{v['repair_noun']} {when}", f.now + timedelta(hours=eta), tech))
        if not met:
            risks.append(f"Order {f.prod.order_id} misses {_weekday(f.prod.due)} by "
                         f"~{max(1, math.ceil(short / (f.prod.rate_per_h * pol.float('shift_hours', 8))))} shift")
            risks.append(f"{_fmt_eur(cm.late_penalty)} late penalty")
        energy_key = option_id
    elif strategy == "slow_then_swap":
        w = f.window
        src = f.source("local") or f.source(opt.str("transfer_mode", "standard"))
        if src is None and buy:
            src = f.source("supplier") or f.source("supplier_express")
        downtime_h = pol.float("repair_hours")
        night = w is not None and w.shift in pol.list("night_shifts", ["night"])
        met = bool(w and w.deadline_met)
        cost = cm.option(downtime_h=downtime_h, night=night, transfer=src.mode if src else None, night_labour=night,
                         late=not met, unit_cost=f.unit_cost)
        until_repair_h = hours(f.now, w.start) if w else 0.0
        risk = min(0.99, round(f.risk_72h * until_repair_h / 72 * pol.float("reduced_speed_risk_factor"), 2))
        conf = f.diagnosis_confidence
        if w:
            actions.append(f.speed_action(f"Line {v['line_num']} to {w.speed_before_pct}% until repair",
                                          w.speed_before_pct, f.now, w.start))
            if src and src.plant_id and src.plant_id != f.plant_id:
                actions.append(f.transfer_action(src, opt.str("transfer_description",
                                                              "Standard transfer 1× {sku} {from_name} → {to_name}")))
            elif src and src.supplier_id and buy:
                actions.append(f.purchase_action(src))
            if tech:
                actions.append(f.work_order_action(
                    f"{v['repair_noun']} {w.start.strftime('%a %H:%M')} ({w.shift} shift, {tech})", w.start, tech))
            risks.append(f"{round(risk * 100)}% chance {v['risk_noun']} fails before {_weekday(w.start)} {w.shift}")
        if conf < pol.float("review_threshold", 0.7):
            risks.append(f"Diagnosis only {round(conf * 100)}% certain")
        energy_key = option_id
    elif strategy == "run_to_supplier":
        src = f.source("supplier") or f.source("supplier_express")
        downtime_h = pol.float("failure_downtime_h")
        risk = f.risk_72h
        part_late = (src is None) or f.now + timedelta(hours=src.eta_hours) > f.prod.due
        met = risk < pol.float("expected_failure_threshold", 0.5) and not part_late
        cost = cm.option(downtime_h=downtime_h, night=False, transfer=None, night_labour=False, late=not met,
                         unit_cost=f.unit_cost)
        conf = pol.float("run_to_failure_confidence")
        if src and buy:  # order now; the swap is planned for when the part arrives
            actions.append(f.purchase_action(src))
            if tech:
                actions.append(f.work_order_action(f"{v['repair_noun']} when the part arrives",
                                                   f.now + timedelta(hours=src.eta_hours), tech))
        risks.append(f"{round(risk * 100)}% chance of seizure within 72 h")
        if f.similar_work_order:
            risks.append(f"Repeat of {f.similar_work_order} ({downtime_h:g} h stop)")
        energy_key = option_id
    else:
        raise ValueError(f"unknown option strategy '{strategy}' for {option_id}")
    en = f.energy.get(energy_key)
    return OptionPlan(
        id=option_id, strategy=strategy, label=opt.str("label").format(**v), summary=opt.str("summary").format(**v),
        actions=actions, cost=cost, downtime_hours=downtime_h, deadline_met=met,
        energy_kwh=en.kwh if en else 0.0, co2_kg=en.co2_kg if en else 0.0,
        failure_risk_until_repair=risk, confidence=conf, risks=risks)


def rank_options(options: list[OptionPlan]) -> None:
    """Recommend the cheapest option that meets the deadline (else the cheapest overall)."""
    pool = [o for o in options if o.deadline_met] or options
    if pool:
        best = min(pool, key=lambda o: o.cost["total"])
        for o in options:
            o.recommended = o is best


@dataclass
class PlanningFacts:
    """Everything the option builder needs, gathered from earlier agents' results and tool data."""
    incident_id: str
    asset_id: str
    plant_id: str
    line_id: str
    now: datetime
    prod: ProductionPlan
    sku: str
    part_name: str
    unit_cost: float
    root_cause: str
    risk_72h: float
    diagnosis_confidence: float
    sources: list[SourcePlan]
    window: WindowPlan | None
    schedule: list[dict]
    energy: dict[str, EnergyPlan]
    similar_work_order: str | None = None
    failure_mode: str = "bearing_wear"
    repair_noun: str = "Bearing swap"
    risk_noun: str = "bearing"
    repair_verb: str = "Replace"
    repair_word: str = "swap"

    def source(self, mode: str) -> SourcePlan | None:
        return next((s for s in self.sources if s.mode == mode), None)

    def template_vars(self) -> dict[str, str]:
        w, sup = self.window, self.source("supplier")
        other = next((s.plant_id for s in self.sources if s.plant_id and s.plant_id != self.plant_id), None)
        local = any(s.mode == "local" for s in self.sources)
        src_name = _plant_name(other) if other else None
        return {"repair_noun": self.repair_noun, "risk_noun": self.risk_noun, "failure_mode": self.failure_mode,
                "repair_word": self.repair_word,
                "get_fast": "take the part from local stock" if local else
                            (f"express the part from {src_name}" if src_name else "order the part from the supplier"),
                "get_standard": "use the part in local stock" if local else
                                (f"standard transfer from {src_name}" if src_name else "order the part from the supplier"),"line_id": self.line_id, "line_num": self.line_id.lstrip("L"), "asset_id": self.asset_id,
                "sku": self.sku, "part_name": self.part_name, "plant_id": self.plant_id,
                "speed_pct": str(w.speed_before_pct) if w else "100", "repair_day": _weekday(w.start) if w else "",
                "repair_shift": w.shift if w else "", "supplier_days": f"{sup.eta_hours / 24:g}" if sup else "?",
                "from_name": _plant_name(other) if other or not local else "local stock",
                "to_name": _plant_name(self.plant_id)}

    def speed_action(self, description: str, pct: int, start: datetime, end: datetime | None) -> dict:
        return {"kind": "speed_change", "description": description, "line_id": self.line_id, "speed_pct": pct,
                "start": start, "end": end}

    def transfer_action(self, src: SourcePlan, template: str) -> dict:
        v = self.template_vars() | {"from_name": _plant_name(src.plant_id)}
        return {"kind": "stock_transfer", "description": template.format(**v),
                "draft": {"sku": self.sku, "from_plant": src.plant_id, "to_plant": self.plant_id, "quantity": 1,
                          "mode": src.mode, "incident_id": self.incident_id}}

    def purchase_action(self, src: SourcePlan, quantity: int = 1) -> dict:
        express = src.mode == "supplier_express"
        days = src.eta_hours / 24
        return {"kind": "purchase_order",
                "description": f"Order {quantity}× {self.sku} from {src.supplier_id}"
                               f"{' (express)' if express else ''}, arrives in ~{days:g} day{'s' if days != 1 else ''}",
                "draft": {"sku": self.sku, "supplier_id": src.supplier_id, "to_plant": self.plant_id,
                          "quantity": quantity, "express": express, "incident_id": self.incident_id}}

    def work_order_action(self, description: str, start: datetime, tech: str, hours_: float | None = None) -> dict:
        dur = hours_ if hours_ is not None else (hours(self.window.start, self.window.end) if self.window else 2.5)
        return {"kind": "work_order", "description": description,
                "draft": {"asset_id": self.asset_id, "type": "predictive",
                          "title": f"{self.repair_verb} {self.part_name} {self.asset_id}".strip(),
                          "root_cause": self.root_cause, "parts": [{"sku": self.sku, "quantity": 1}] if self.sku else [],
                          "technician_id": tech, "planned_start": start, "duration_hours": dur,
                          "incident_id": self.incident_id}}


def _plant_name(plant_id: str | None) -> str:
    return f"Plant {plant_id.split('-')[-1]}" if plant_id else "supplier"
