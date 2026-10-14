"""Options chat (v1.2): an approver says what they would rather do ("repair tomorrow morning at 80% speed",
"buy it from the supplier instead", "use Meera"), and the Decision agent adds that plan as a new, fully costed option.

The words become a structured PlanRequest (the LLM when it is available, else keyword rules; both are merged with the
earlier requests of the same chat, so "make it 80 %" refines the previous plan). Every number then comes from the same
deterministic planning code as the original options (C3): live plant data through the tool gateway, the same cost
model, the same deadline maths. The AI's recommended option is never changed.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from pydantic import TypeAdapter

from plantguard_contracts.schemas.incidents import Incident, Option, OptionsResponse

from agents.cost_model import CostModel
from domain.planning import (PlanningFacts, WindowPlan, build_option, can_finish, estimate_energy, mode_word, pick_order,
                             pick_technicians, plan_part_sources, production_plan, recommend_source, required_skill)
from domain.timeline import hours, next_shift_start, parse_shift
from harness.config import Policy

log = logging.getLogger("plantguard.agents.refine")
_options = TypeAdapter(list[Option])

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
SOURCES = ("local", "express", "standard", "supplier", "supplier_express")


@dataclass
class PlanRequest:
    timing: str | None = None        # now | shift | keep_running
    shift: str | None = None         # morning | evening | night
    day_offset: int | None = None    # 0 = today, 1 = tomorrow ...
    speed_pct: int | None = None
    source: str | None = None        # local | express | standard | supplier | supplier_express
    technician: str | None = None    # TECH-xxx or a first name
    notes: list[str] = field(default_factory=list)

    def merged(self, newer: "PlanRequest") -> "PlanRequest":
        out = replace(self)
        for k, v in asdict(newer).items():
            if k != "notes" and v is not None:
                setattr(out, k, v)
        return out

    def empty(self) -> bool:
        return all(v is None for k, v in asdict(self).items() if k != "notes")


# ---------------------------------------------------------------- words -> request
def parse_rules(text: str, now: datetime, tech_names: dict[str, str]) -> PlanRequest:
    t = " " + text.lower() + " "
    r = PlanRequest()
    if re.search(r"\b(start over|new plan|from scratch)\b", t):
        r.notes.append("reset")
    if re.search(r"\b(keep running|run (it )?until|run to failure|wait for the (part|supplier))\b", t):
        r.timing = "keep_running"
    if re.search(r"\b(now|immediately|right away|asap|straight away|stop (the )?line|stop now)\b", t):
        r.timing = "now"
    for shift, rx in (("night", r"\b(night|tonight|overnight)\b"), ("morning", r"\b(morning|day shift|early shift)\b"),
                      ("evening", r"\b(evening|afternoon|late shift)\b")):
        if re.search(rx, t):
            r.shift, r.timing = shift, r.timing if r.timing == "keep_running" else "shift"
    if "day after tomorrow" in t:
        r.day_offset = 2
    elif re.search(r"\btomorrow\b", t):
        r.day_offset = 1
    elif re.search(r"\b(today|tonight)\b", t):
        r.day_offset = 0
    else:
        for i, d in enumerate(WEEKDAYS):
            if re.search(rf"\b{d}\b", t):
                r.day_offset = (i - now.weekday()) % 7
                break
    if r.day_offset is not None and r.timing is None:
        r.timing = "shift"
    m = re.search(r"(\d{2,3})\s*(%|percent)", t)
    if m:
        r.speed_pct = max(0, min(100, int(m.group(1))))
    elif re.search(r"\b(full speed|don'?t slow|no slow ?down|keep (the )?speed|100 ?%)\b", t):
        r.speed_pct = 100
    if re.search(r"\b(supplier|buy|purchase|order (a )?new|new part)\b", t):
        r.source = "supplier_express" if re.search(r"\b(express|fast|urgent|quick)\b", t) else "supplier"
    elif re.search(r"\b(our (own )?stock|local stock|from (the )?store)\b", t):
        r.source = "local"
    elif re.search(r"\b(express|courier)\b", t):
        r.source = "express"
    elif re.search(r"\b(standard|normal|regular) (transfer|truck|shipment)\b|\btruck\b", t):
        r.source = "standard"
    elif re.search(r"\bplant [ab]\b|\btransfer\b", t):
        r.source = r.source or "standard"
    m = re.search(r"\btech-\d{3}\b", t)
    if m:
        r.technician = m.group(0).upper()
    else:
        for first, tid in tech_names.items():
            if re.search(rf"\b{re.escape(first.lower())}\b", t):
                r.technician = tid
                break
    return r


PLAN_TOOL = {"type": "function", "function": {
    "name": "plan_request", "description": "The approver's requested plan as structured fields. Leave a field null when "
                                           "the approver did not say it.",
    "parameters": {"type": "object", "properties": {
        "timing": {"type": ["string", "null"], "enum": ["now", "shift", "keep_running", None],
                   "description": "now = stop the line and repair at once; shift = repair in a given shift; "
                                  "keep_running = run on and repair when the part arrives"},
        "shift": {"type": ["string", "null"], "enum": ["morning", "evening", "night", None]},
        "day_offset": {"type": ["integer", "null"], "description": "0 today, 1 tomorrow, ..."},
        "speed_pct": {"type": ["integer", "null"], "description": "line speed until the repair, 0-100"},
        "source": {"type": ["string", "null"], "enum": [*SOURCES, None],
                   "description": "where the part comes from"},
        "technician": {"type": ["string", "null"], "description": "TECH-xxx id of a technician the approver named"},
        "start_over": {"type": "boolean", "description": "the approver wants a fresh plan, ignoring earlier requests"},
    }, "required": []}}}


# ---------------------------------------------------------------- the refiner
class OptionRefiner:
    def __init__(self, config, gateway, llm, clock):
        self.config, self.gateway, self.llm, self.clock = config, gateway, llm, clock

    async def _tool(self, agent: str, tool: str, args: dict, incident_id: str) -> Any:
        return await self.gateway.call(agent, tool, args, incident_id)

    async def _llm_request(self, history: list[dict], message: str, techs: dict[str, str]) -> PlanRequest | None:
        cfg = self.config.llm
        if self.llm is None or (cfg.needs_key() and not cfg.api_key()):
            return None
        system = ("You turn a maintenance lead's instruction about a repair plan into fields. Only fill what they said. "
                  f"Technicians: {', '.join(f'{n} = {t}' for n, t in techs.items())}. Always call plan_request.")
        msgs = [{"role": "assistant" if h["role"] == "assistant" else "user", "content": h["text"]} for h in history[-8:]]
        msgs.append({"role": "user", "content": message})
        try:
            out = await self.llm.chat("decision", system, msgs, [PLAN_TOOL], cfg)
        except Exception as e:  # quota, network: the keyword rules still work
            log.info("options chat: LLM not used (%s)", str(e)[:120])
            return None
        call = next((c for c in out.get("tool_calls", []) if c["name"] == "plan_request"), None)
        if call is None:
            return None
        a = call["args"] if isinstance(call["args"], dict) else json.loads(call["args"] or "{}")
        r = PlanRequest(**{k: a.get(k) for k in ("timing", "shift", "day_offset", "speed_pct", "source", "technician")})
        if r.source not in (None, *SOURCES):
            r.source = None
        if a.get("start_over"):
            r.notes.append("reset")
        return r

    async def refine(self, incident: Incident, options: OptionsResponse, history: list[dict], message: str,
                     author: str) -> dict:
        """-> {"reply": str, "option": Option | None}"""
        pol: Policy = self.config.policy("planning")
        inc = incident
        diag = inc.diagnosis
        sku = (diag.required_parts or [None])[0] if diag else None
        if not diag or not sku:
            return {"reply": "I can only build a new option once the diagnosis has named the part to replace.", "option": None}
        now = self.clock()
        iid = inc.id
        # ---- live plant data (each read goes through the tool gateway as the teammate who owns it) ----
        health = await self._tool("decision", "get_asset_health", {"asset_id": inc.asset_id}, iid)
        part = await self._tool("spare_parts", "check_stock", {"sku": sku}, iid)
        supplier = await self._tool("spare_parts", "get_supplier", {"supplier_id": part["supplier_id"]}, iid)
        orders = (await self._tool("decision", "get_orders", {"line_id": inc.line_id, "plant_id": inc.plant_id}, iid))["items"]
        schedule = (await self._tool("scheduler", "get_schedule", {"line_id": inc.line_id, "plant_id": inc.plant_id}, iid))["items"]
        skill = required_skill(sku, diag.failure_mode, pol)
        all_techs = (await self._tool("scheduler", "find_technicians", {"plant_id": inc.plant_id}, iid))["items"]
        energy = await self._tool("energy", "get_energy", {"line_id": inc.line_id, "plant_id": inc.plant_id}, iid)
        tariffs = (await self._tool("energy", "get_tariffs", {}, iid))["items"]
        co2 = float((await self._tool("energy", "get_co2_factor", {}, iid)).get("kg_per_kwh", 0.0))
        tech_names = {t["name"].split(" ")[0]: t["id"] for t in all_techs}

        # ---- what they asked for (this message on top of the earlier ones) ----
        req = PlanRequest()
        for h in history:
            if h["role"] == "user":
                r = parse_rules(h["text"], now, tech_names)
                req = PlanRequest() if "reset" in r.notes else req.merged(r)
        newest = await self._llm_request(history, message, tech_names) or parse_rules(message, now, tech_names)
        if newest.empty() and "reset" not in newest.notes:
            newest = parse_rules(message, now, tech_names)
        if newest.empty():
            return {"reply": ("I couldn't turn that into a plan. Tell me what to change, for example: “repair tomorrow "
                              "morning at 80% speed”, “stop now and buy the part express from the supplier”, “use the "
                              "standard truck from Plant B”, or “let Meera do it on the night shift”."), "option": None}
        req = PlanRequest() .merged(newest) if "reset" in newest.notes else req.merged(newest)

        order = pick_order(orders, inc.line_id)
        if order is None:
            return {"reply": f"There is no open production order on line {inc.line_id}, so I can't check the deadline.",
                    "option": None}
        prod = production_plan(order, pol)
        local, sources = plan_part_sources(part, supplier, inc.plant_id, pol)
        notes: list[str] = []
        # ---- part source ----
        if req.source:
            src = next((s for s in sources if s.mode == req.source), None)
            near = {"supplier_express": "supplier", "supplier": "supplier_express", "express": "standard",
                    "standard": "express"}.get(req.source)
            if src is None and near:
                src = next((s for s in sources if s.mode == near), None)
                if src is not None:
                    notes.append(f"{req.source.replace('_', ' ').capitalize()} isn't offered for {sku}, so I used "
                                 f"{near.replace('_', ' ')} instead")
            if src is None:
                have = ", ".join(sorted({s.mode.replace("_", " ") for s in sources})) or "none"
                notes.append(f"{req.source.replace('_', ' ').capitalize()} isn't possible for {sku} (available: {have}), "
                             "so I used the best available source")
        else:
            src = None
        if src is None:
            src = sources[recommend_source(sources, health.get("rul_hours"), pol)] if sources else None
        if src is None:
            return {"reply": f"No plant has {sku} and no supplier is known, so I can't plan the repair.", "option": None}

        # ---- technician ----
        able = [t for t in all_techs if skill in t.get("skills", [])]
        tech_id = None
        if req.technician:
            chosen = next((t for t in all_techs if t["id"] == req.technician), None)
            if chosen and skill in chosen.get("skills", []):
                tech_id = chosen["id"]
            elif chosen:
                notes.append(f"{chosen['name']} doesn't have the {skill.replace('_', ' ')} skill this repair needs")
            else:
                notes.append(f"{req.technician} isn't a technician at {inc.plant_id.replace('PLANT-', 'Plant ')}")
        if not able:
            return {"reply": f"No technician at {inc.plant_id.replace('PLANT-', 'Plant ')} has the "
                             f"{skill.replace('_', ' ')} skill, so this repair can't be planned here.", "option": None}

        risk = float(health.get("risk_72h", 0.0))
        mode = diag.failure_mode
        timing = req.timing or ("shift" if req.shift or req.day_offset is not None or req.speed_pct is not None else "shift")
        strategy = {"now": "stop_now", "keep_running": "run_to_supplier"}.get(timing, "slow_then_swap")
        if strategy == "run_to_supplier" and src.mode not in ("supplier", "supplier_express"):
            src = next((s for s in sources if s.mode == "supplier"), src)

        # ---- repair window ----
        shifts = {k[len("shift."):]: parse_shift(v) for k, v in pol.items() if k.startswith("shift.")}
        ready = now + timedelta(hours=src.eta_hours)
        window: WindowPlan | None = None
        if strategy != "run_to_supplier":
            shift = req.shift or (pol.list("preferred_shifts", ["night"])[0] if strategy == "slow_then_swap" else None)
            if strategy == "stop_now":
                start, shift_name = ready, "now"
            else:
                base = ready
                if req.day_offset is not None:
                    day = (now + timedelta(days=req.day_offset)).replace(hour=0, minute=0, second=0, microsecond=0)
                    base = max(base, day)
                start_h = shifts.get(shift, (22, 6))[0]
                start, shift_name = next_shift_start(base, start_h), shift or "night"
                if req.day_offset is not None and start.date() > (now + timedelta(days=req.day_offset)).date() \
                        and start_h >= 1:
                    notes.append("the part only arrives " + ready.strftime("%a %H:%M") + ", so the repair moves to "
                                 + start.strftime("%a %H:%M"))
            speed = req.speed_pct if req.speed_pct is not None else (
                pol.int("reduced_speed_pct") if risk >= pol.float("slow_down_risk_threshold", 0.5) else 100)
            if strategy == "stop_now":
                speed = 0
            repair_h = pol.float("repair_hours")
            lost = hours(now, start) * (1 - speed / 100) + repair_h
            met, _ = can_finish(prod, now, lost)
            techs = [tech_id] if tech_id else pick_technicians(able, skill, pol.int("technicians_needed", 1),
                                                                pol.bool("prefer_off_shift_now", True))
            window = WindowPlan(start, start + timedelta(hours=repair_h), shift_name, techs, speed, met,
                                int(round(prod.rate_per_h * lost)), lost)
        elif tech_id or able:
            window = WindowPlan(ready, ready + timedelta(hours=pol.float("repair_hours")), "on arrival",
                                [tech_id or able[0]["id"]], 100, False, 0, 0.0)

        # ---- build it with the same code as the AI's options ----
        oid = next_option_id(options)
        opt = Policy({"strategy": strategy, "transfer_mode": src.mode,
                      "label": "Your plan", "summary": "x", "energy_note": "Your plan"})
        asset = next((a for a in energy.get("by_asset", []) if a["asset_id"] == inc.asset_id), None)
        en = {oid: estimate_energy(oid, opt, asset, tariffs, co2, now, prod.due, window, pol)} if asset else {}
        pf = PlanningFacts(
            incident_id=iid, asset_id=inc.asset_id, plant_id=inc.plant_id, line_id=inc.line_id, now=now, prod=prod,
            sku=sku, part_name=_part_name(part), unit_cost=float(part["unit_cost"]["amount"]), root_cause=diag.root_cause,
            risk_72h=risk, diagnosis_confidence=diag.confidence, sources=[src], window=window, schedule=schedule,
            energy=en, failure_mode=mode, repair_noun=mode_word(pol, "repair_noun", mode, "Repair"),
            risk_noun=mode_word(pol, "risk_noun", mode, "the machine"),
            repair_verb=mode_word(pol, "repair_verb", mode, "Replace"), repair_word=mode_word(pol, "repair_word", mode, "repair"))
        if strategy == "stop_now" and window:
            pf = replace(pf, window=window)
        plan = build_option(oid, opt, pf, pol, CostModel(pol))
        plan.label, plan.summary = describe(strategy, src, window, req, pf)
        # chance of failure before the repair: survival over the waiting time, wear scaled by line speed
        # (the AI's own options use the calibrated story formula; a custom plan can wait days, so use the general one)
        if strategy != "stop_now":
            wait_h = hours(now, window.start) if window else src.eta_hours
            speed = window.speed_before_pct if window and strategy == "slow_then_swap" else 100
            wear = (speed / 100) ** pol.float("speed_exponent", 1.5)
            p = round(min(0.99, 1 - (1 - min(risk, 0.99)) ** (wait_h / 72 * wear)), 2)
            plan.failure_risk_until_repair = p
            plan.risks = [r for r in plan.risks if "chance" not in r]
            plan.risks.insert(0, f"{round(p * 100)}% chance {pf.risk_noun} fails before the repair "
                                 f"({wait_h:.0f} h at {speed}% speed)")
        tech_used = window.technician_ids[0] if window and window.technician_ids else None
        who = next((t["name"].split(" (")[0] + f" ({t['id']})" for t in all_techs if t["id"] == tech_used), None)
        option = _options.validate_python([{
            "id": plan.id, "label": plan.label, "summary": plan.summary, "actions": plan.actions,
            "cost": {k: {"amount": v, "currency": "EUR"} for k, v in plan.cost.items()},
            "downtime_hours": plan.downtime_hours, "deadline_met": plan.deadline_met, "energy_kwh": plan.energy_kwh,
            "co2_kg": plan.co2_kg, "failure_risk_until_repair": plan.failure_risk_until_repair,
            "confidence": plan.confidence, "risks": plan.risks + [n[0].upper() + n[1:] for n in notes],
            "recommended": False, "origin": "approver", "suggested_by": author}])[0]
        same = next((o for o in options.options if _same_plan(o, option)), None)
        if same is not None:
            return {"reply": f"That is the same plan as option {same.id[-1]} ({same.label}), so I didn't add it again. "
                             "Tell me what to change.", "option": None}
        return {"reply": compare(option, options, notes, who), "option": option}


def next_option_id(options: OptionsResponse) -> str:
    used = {o.id for o in options.options}
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if f"OPT-{c}" not in used:
            return f"OPT-{c}"
    raise ValueError("no option letters left")


def _part_name(part: dict) -> str:
    d = part.get("description") or part.get("sku", "part")
    return re.sub(r"\s+", " ", d).strip()


def describe(strategy: str, src, window: WindowPlan | None, req: PlanRequest, pf: PlanningFacts) -> tuple[str, str]:
    where = {"local": "local stock", "express": f"express from Plant {(src.plant_id or '?')[-1]}",
             "standard": f"standard truck from Plant {(src.plant_id or '?')[-1]}",
             "supplier": f"order from {src.supplier_id}", "supplier_express": f"express order from {src.supplier_id}"
             }.get(src.mode, src.mode)
    if strategy == "stop_now":
        return (f"Your plan: stop now, {where}",
                f"As you asked: stop Line {pf.line_id.lstrip('L')} now, {where}, repair as soon as the part is here.")
    if strategy == "run_to_supplier":
        return (f"Your plan: keep running, {where}",
                f"As you asked: run on, {where} and repair when it arrives.")
    w = window
    when = f"{w.start:%a} {w.shift}" if w else "later"
    speed = f" at {w.speed_before_pct}%" if w and w.speed_before_pct < 100 else " at full speed"
    who = ""
    return (f"Your plan: run{speed}, repair {when}",
            f"As you asked: Line {pf.line_id.lstrip('L')}{speed} until {w.start:%a %H:%M}, {where}, repair on the "
            f"{w.shift} shift{who}." if w else "As you asked.")


def _same_plan(a: Option, b: Option) -> bool:
    def key(o: Option):  # what would actually happen; the start of "from now on" speed changes moves with the clock
        return [(x.kind, x.model_dump(mode="json", exclude={"description", "start"})) for x in o.actions]
    return key(a) == key(b) and abs(a.cost.total.amount - b.cost.total.amount) < 1


def compare(new: Option, options: OptionsResponse, notes: list[str], who: str | None = None) -> str:
    best = next((o for o in options.options if o.recommended), None)
    letter = new.id[-1]
    parts = [f"Added option {letter}: {new.label.replace('Your plan: ', '')}" + (f", repair by {who}." if who else "."),
             f"Total €{new.cost.total.amount:,.0f}" + (", order on time" if new.deadline_met else ", the order will be late") +
             (f", {round(new.failure_risk_until_repair * 100)}% chance of failure before the repair"
              if new.failure_risk_until_repair else "") + "."]
    if best:
        d = new.cost.total.amount - best.cost.total.amount
        if abs(d) < 1:
            parts.append(f"Same cost as my recommendation ({best.id[-1]}).")
        else:
            parts.append(f"That is €{abs(d):,.0f} {'more' if d > 0 else 'less'} than my recommendation "
                         f"(option {best.id[-1]}, €{best.cost.total.amount:,.0f}).")
        if best.deadline_met and not new.deadline_met:
            parts.append("Option " + best.id[-1] + " still meets the deadline; this one doesn't.")
    if notes:
        parts.append("Note: " + "; ".join(notes) + ".")
    parts.append("Approve it like any other option, or tell me what to change.")
    return " ".join(parts)
