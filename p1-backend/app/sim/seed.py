"""Seeds a fresh plant into the OT and IT databases from config/plant.yaml (+ the story's maintenance history).
Everything is created relative to the plant clock's `now`, so orders, shifts and history are always current."""
from __future__ import annotations

import hashlib
import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

from app.rag.corpus import load_manuals
from app.rag.retriever import Retriever, backend_factory
from app.sim.clock import iso
from app.sim.db import Database
from app.sim.physics import PROTOCOL, SIGNALS, TAG_SUFFIX, UNITS, Condition, readings, signal_range
from app.sim.settings import SimSettings

SHIFT_CODES = {"morning": "M", "evening": "E", "night": "N"}


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 60_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def check_password(password: str, stored: str) -> bool:
    import hmac
    try:
        _, salt, digest = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, bytes.fromhex(salt)).split("$")[2], digest)


def assets(cfg: SimSettings):
    """(plant, line, station, asset) for every machine in plant.yaml."""
    for plant in cfg.plant["plants"]:
        for line in plant["lines"]:
            for seq, st in enumerate(line["stations"], start=1):
                for a in st["assets"]:
                    yield plant, line, seq, st, a


def shift_bounds(day: datetime, name: str, hours: list[int]) -> tuple[datetime, datetime]:
    start = day.replace(hour=hours[0], minute=0, second=0, microsecond=0)
    end = day.replace(hour=hours[1], minute=0, second=0, microsecond=0)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def ensure_shifts(it: Database, cfg: SimSettings, first_day: datetime, days: int) -> None:
    """Rota: crew c works shift (c + day) % 3. Idempotent, so the engine can top it up every plant day."""
    techs = it.q("SELECT id, plant_id, crew FROM technicians")
    names = list(cfg.plant["shifts"])
    for d in range(days):
        day = (first_day + timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_no = day.toordinal()
        for plant in cfg.plant["plants"]:
            for si, name in enumerate(names):
                sid = f"SH-{plant['id'][-1]}-{day:%Y%m%d}-{SHIFT_CODES.get(name, name[0].upper())}"
                if it.one("SELECT 1 FROM shifts WHERE id=?", (sid,)):
                    continue
                start, end = shift_bounds(day, name, cfg.plant["shifts"][name])
                it.x("INSERT INTO shifts(id, plant_id, name, start_ts, end_ts) VALUES(?,?,?,?,?)",
                     (sid, plant["id"], name, iso(start), iso(end)))
                for t in techs:
                    if t["plant_id"] == plant["id"] and (t["crew"] + day_no) % 3 == si:
                        it.x("INSERT INTO shift_assignments(shift_id, technician_id) VALUES(?,?)", (sid, t["id"]))


def ensure_schedule(it: Database, cfg: SimSettings, now: datetime, hours_ahead: float = 72) -> None:
    """Production slots per shift for every line, assigned to the order that will be running then."""
    rate = cfg.line_rate_units_per_h
    for line in it.q("SELECT plant_id, id FROM lines"):
        last = it.val("SELECT MAX(end_ts) FROM schedule_slots WHERE plant_id=? AND line_id=? AND kind='production'",
                      (line["plant_id"], line["id"]))
        start = datetime.fromisoformat(last.replace("Z", "+00:00")) if last else now.replace(minute=0, second=0, microsecond=0)
        orders = it.q("SELECT id, quantity - completed AS left FROM production_orders WHERE plant_id=? AND line_id=? "
                      "AND status != 'completed' ORDER BY seq", (line["plant_id"], line["id"]))
        # hours already planned before `start` (to know which order runs then)
        queue = [[o["id"], o["left"] / rate] for o in orders]
        elapsed = max(0.0, (start - now).total_seconds() / 3600)
        while queue and elapsed > 0:
            take = min(elapsed, queue[0][1])
            queue[0][1] -= take
            elapsed -= take
            if queue[0][1] <= 1e-6:
                queue.pop(0)
        t = start
        while t < now + timedelta(hours=hours_ahead):
            # align to shift boundaries (06, 14, 22)
            nxt = t + timedelta(hours=8 - ((t.hour - 6) % 8) if (t.hour - 6) % 8 else 8)
            nxt = nxt.replace(minute=0, second=0, microsecond=0)
            order = queue[0][0] if queue else None
            it.x("INSERT INTO schedule_slots(plant_id, line_id, start_ts, end_ts, kind, order_id, planned_speed_pct) "
                 "VALUES(?,?,?,?,?,?,?)", (line["plant_id"], line["id"], iso(t), iso(nxt), "production", order, 100))
            h = (nxt - t).total_seconds() / 3600
            while queue and h > 0:
                take = min(h, queue[0][1])
                queue[0][1] -= take
                h -= take
                if queue[0][1] <= 1e-6:
                    queue.pop(0)
            t = nxt


def seed(ot: Database, it: Database, cfg: SimSettings, now: datetime, rng: random.Random | None = None) -> None:
    rng = rng or random.Random(42)
    P = cfg.plant
    models = P["models"]
    # ---------------------------------------------------------------- OT
    for plant in P["plants"]:
        ot.x("INSERT INTO sites(id, name) VALUES(?,?)", (plant["id"], plant["name"]))
        for line in plant["lines"]:
            ot.x("INSERT INTO lines(site_id, id, name) VALUES(?,?,?)", (plant["id"], line["id"], line["name"]))
    for plant, line, seq, st, a in assets(cfg):
        m = models[a["type"]]
        if not ot.one("SELECT 1 FROM stations WHERE site_id=? AND id=?", (plant["id"], st["id"])):
            ot.x("INSERT INTO stations(site_id, line_id, id, name, seq) VALUES(?,?,?,?,?)",
                 (plant["id"], line["id"], st["id"], st["name"], seq))
        ot.x("INSERT INTO equipment(asset_id, site_id, line_id, station_id, name, type, controller, pos_x, pos_y) "
             "VALUES(?,?,?,?,?,?,?,?,?)", (a["id"], plant["id"], line["id"], st["id"], a["name"], a["type"],
                                           m["controller"], a["position"][0], a["position"][1]))
        dev = m["device"] + (a["id"][-1] if a["type"] == "spindle" else "")
        for s in SIGNALS:
            lo, hi = signal_range(m["baseline"] if s not in ("vibration_peak", "vibration_kurtosis") else
                                  {**m["baseline"]}, s)
            ot.x("INSERT INTO tags(tag, asset_id, signal, unit, protocol, range_min, range_max) VALUES(?,?,?,?,?,?,?)",
                 (f"{plant['id'][-1]}.{st['id'].replace('-', '.')}.{dev}.{TAG_SUFFIX[s]}", a["id"], s, UNITS[s],
                  PROTOCOL[s], lo, hi))
        ot.x("INSERT INTO machine_state(asset_id, state, reason, since) VALUES(?,?,?,?)",
             (a["id"], "run", "production", iso(now - timedelta(hours=8))))
        ot.x("INSERT INTO machine_state_log(asset_id, state, reason, start_ts) VALUES(?,?,?,?)",
             (a["id"], "run", "production", iso(now - timedelta(hours=8))))
    for code, signal, sev, text in [
            ("VIB-HI-W", "vibration_rms", "warning", "Vibration RMS above warning band"),
            ("VIB-HI-C", "vibration_rms", "critical", "Vibration RMS above trip limit"),
            ("TMP-HI-W", "motor_temp", "warning", "Motor temperature above warning band"),
            ("TMP-HI-C", "motor_temp", "critical", "Motor temperature above trip limit"),
            ("MCH-FLT", "state", "critical", "Machine stopped on fault")]:
        ot.x("INSERT INTO alarm_defs(code, signal, severity, text) VALUES(?,?,?,?)", (code, signal, sev, text))

    # initial (story) faults + historian backfill that already shows them developing
    conds: dict[str, tuple[Condition, datetime, float]] = {}
    for asset_id, (mode, progress) in cfg.initial_faults.items():
        typ = next(a["type"] for _, _, _, _, a in assets(cfg) if a["id"] == asset_id)
        spec = models[typ]["modes"][mode]
        T = rng.uniform(cfg.breakdown_h_min, cfg.breakdown_h_max)
        started = now - timedelta(hours=progress * T)
        ot.x("INSERT INTO asset_conditions(asset_id, fault_mode, variant, root_cause, fix_part, progress, breakdown_h, "
             "started_at) VALUES(?,?,?,?,?,?,?,?)", (asset_id, mode, mode, spec["root_cause"], spec["part"], progress,
                                                    T, iso(started)))
        conds[asset_id] = (Condition(mode, mode, progress), started, T)
    step = timedelta(minutes=cfg.history_interval_min)
    t0 = now - timedelta(hours=cfg.history_backfill_h)
    rows = []
    for _, _, _, _, a in assets(cfg):
        b = models[a["type"]]["baseline"]
        t = t0
        while t <= now:
            cond = None
            if a["id"] in conds:
                c, started, T = conds[a["id"]]
                p = max(0.0, (t - started).total_seconds() / 3600 / T)
                cond = Condition(c.mode, c.variant, p) if p > 0 else None
            v = readings(b, P["signatures"], cond, 100, "run", rng, cfg.noise)
            rows.append((a["id"], iso(t), "run", 100, *[v[s] for s in SIGNALS], "good"))
            t += step
    ot.many("INSERT INTO historian(asset_id, ts, state, speed_pct, vibration_rms, vibration_peak, vibration_kurtosis, "
            "bearing_temp, motor_temp, spindle_rpm, motor_current, power_kw, quality) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows)
    ot.set_meta("seeded_at", iso(now))

    # ---------------------------------------------------------------- IT master data
    for plant in P["plants"]:
        it.x("INSERT INTO plants(id, name, location) VALUES(?,?,?)", (plant["id"], plant["name"], plant["location"]))
        it.x("INSERT INTO storage_locations(id, plant_id, name) VALUES(?,?,?)",
             (plant["storage"], plant["id"], f"{plant['name']} MRO store"))
        for line in plant["lines"]:
            it.x("INSERT INTO lines(plant_id, id, name, product, rate_units_per_h) VALUES(?,?,?,?,?)",
                 (plant["id"], line["id"], line["name"], line["product"], cfg.line_rate_units_per_h))
    for sid, s in P["suppliers"].items():
        it.x("INSERT INTO suppliers(id, name, lead_time_days, express_available, express_lead_time_days) VALUES(?,?,?,?,?)",
             (sid, s["name"], s["lead_time_days"], int(s.get("express_available", False)), s.get("express_lead_time_days")))
    storage = {p["id"]: p["storage"] for p in P["plants"]}
    for sku, pt in P["parts"].items():
        it.x("INSERT INTO parts(sku, description, unit_cost, supplier_id, repair_skill) VALUES(?,?,?,?,?)",
             (sku, pt["description"], pt["cost"], pt["supplier"], P["skills"].get(sku, "general")))
        for plant_id, qty in pt["stock"].items():
            it.x("INSERT INTO stock(sku, plant_id, location_id, quantity) VALUES(?,?,?,?)",
                 (sku, plant_id, storage[plant_id], qty))

    # manuals first (equipment points at its manual)
    manuals = load_manuals(cfg.root / "p4-ml-data" / "corpus" / "manuals")
    known_docs = {d.id for d in manuals}
    for plant, line, seq, st, a in assets(cfg):
        m = models[a["type"]]
        it.x("INSERT INTO equipment(asset_id, plant_id, line_id, station_id, station_name, station_seq, name, type, model, "
             "manufacturer, manual_doc_id, pos_x, pos_y) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
             (a["id"], plant["id"], line["id"], st["id"], st["name"], seq, a["name"], a["type"], m["model"],
              m["manufacturer"], None, a["position"][0], a["position"][1]))
        for pos, sku in enumerate(m["bom"]):
            it.x("INSERT INTO bom_items(asset_id, sku, quantity, pos) VALUES(?,?,?,?)",
                 (a["id"], sku, 2 if sku == "SKF-7008" else 1, pos))

    for t in P["technicians"]:
        it.x("INSERT INTO technicians(id, plant_id, name, crew) VALUES(?,?,?,?)", (t["id"], t["plant"], t["name"], t["crew"]))
        for s in t["skills"]:
            it.x("INSERT INTO technician_skills(technician_id, skill) VALUES(?,?)", (t["id"], s))
        for c in t.get("certifications", []):
            it.x("INSERT INTO technician_certifications(technician_id, certification) VALUES(?,?)", (t["id"], c))
    ensure_shifts(it, cfg, now - timedelta(days=2), 6)

    pw = P.get("default_password", "demo")
    for u in P["users"]:
        uid = f"USR-{u['username']}"
        it.x("INSERT INTO users(id, username, name, role, password_hash, technician_id) VALUES(?,?,?,?,?,?)",
             (uid, u["username"], u["name"], u["role"], hash_password(pw), u.get("technician")))
        for p_id in u["plants"]:
            it.x("INSERT INTO user_plants(user_id, plant_id) VALUES(?,?)", (uid, p_id))

    # ---------------------------------------------------------------- MES
    for i, o in enumerate(P["orders"]):
        status = "running" if o["completed"] > 0 else "planned"
        it.x("INSERT INTO production_orders(id, plant_id, line_id, product, quantity, completed, due_at, status, seq, "
             "started_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
             (o["id"], o["plant"], o["line"], o["product"], o["quantity"], o["completed"],
              iso(now + timedelta(hours=o["due_in_h"])), status, i, iso(now - timedelta(days=2)) if o["completed"] else None))
    ensure_schedule(it, cfg, now)
    for line in it.q("SELECT plant_id, id FROM lines"):
        order = it.val("SELECT id FROM production_orders WHERE plant_id=? AND line_id=? AND status='running' ORDER BY seq",
                       (line["plant_id"], line["id"]))
        for h in range(int(cfg.history_backfill_h), 0, -1):
            hour = (now - timedelta(hours=h)).replace(minute=0, second=0, microsecond=0)
            units = cfg.line_rate_units_per_h * rng.uniform(0.94, 1.0)
            it.x("INSERT INTO production_log(plant_id, line_id, hour_ts, order_id, planned_min, run_min, "
                 "speed_sum, units, defects) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                 (line["plant_id"], line["id"], iso(hour), order, 60, 60 * rng.uniform(0.95, 1.0), 6000, units,
                  int(rng.random() < 0.2)))

    # ---------------------------------------------------------------- CMMS history (story + generated)
    techs_by_plant: dict[str, list] = {}
    for t in P["technicians"]:
        techs_by_plant.setdefault(t["plant"], []).append(t)
    story = json.loads((cfg.root / "fixtures" / "story" / "history_EQ-3012.json").read_text())
    for w in story:
        _insert_wo(it, w)
    n = 0
    for plant, line, seq, st, a in assets(cfg):
        m = models[a["type"]]
        if a["id"] == "EQ-3012":
            continue
        n += 1
        tech = rng.choice(techs_by_plant[plant["id"]])
        when = now - timedelta(days=rng.randint(30, 140))
        _insert_wo(it, {"id": f"WO-{when.year}-{n:04d}", "asset_id": a["id"], "type": "preventive",
                        "title": f"{a['name']} inspection and lubrication", "root_cause": None, "findings": "OK",
                        "parts": [], "technician_id": tech["id"], "planned_start": iso(when), "duration_hours": 1.0,
                        "status": "done", "incident_id": None, "created_at": iso(when - timedelta(days=1))})
        if rng.random() < 0.55:
            mode = rng.choice(list(m["modes"]))
            spec = m["modes"][mode]
            skill = P["skills"].get(spec["part"])
            tech = next((t for t in techs_by_plant[plant["id"]] if skill in t["skills"]), tech)
            when = now - timedelta(days=rng.randint(200, 600))
            n += 1
            _insert_wo(it, {"id": f"WO-{when.year}-{n:04d}", "asset_id": a["id"], "type": "corrective",
                            "title": f"{a['name']} {mode.replace('_', ' ')}", "root_cause": spec["root_cause"],
                            "findings": _finding(mode, rng), "parts": [{"sku": spec["part"], "quantity": 1}],
                            "technician_id": tech["id"], "planned_start": iso(when),
                            "duration_hours": 2.5 if mode == "bearing_wear" else 2.0, "status": "done",
                            "incident_id": None, "created_at": iso(when - timedelta(hours=4))})

    # ---------------------------------------------------------------- id counters (golden story numbers)
    for name, start in (("INC", 42), (f"WO-{now.year}", 153), ("STO", 4500012), ("APR", 7), ("FB", 31),
                        ("ALM", 9001), ("DEF", 800), ("INS", 5000)):
        it.x("INSERT INTO id_sequences(name, next) VALUES(?,?) ON CONFLICT DO NOTHING", (name, start))

    # ---------------------------------------------------------------- manuals + past repairs -> vector index
    Retriever.build(it, manuals, iso(now), backend_factory(it, cfg.data_dir, recreate=True))
    for plant, line, seq, st, a in assets(cfg):
        doc = models[a["type"]]["manual"]
        if doc in known_docs:
            it.x("UPDATE equipment SET manual_doc_id=? WHERE asset_id=?", (doc, a["id"]))
    it.set_meta("seeded_at", iso(now))
    it.set_meta("plant_time", iso(now))


def _finding(mode: str, rng: random.Random) -> str:
    return {
        "bearing_wear": f"Vibration rose ~{rng.randint(80, 140)}% over {rng.randint(2, 4)} days; rolling elements pitted, "
                        "outer race worn. Bearing replaced, vibration back to baseline.",
        "misalignment": "High vibration with normal kurtosis; coupling offset 0.12 mm. Realigned, vibration back to baseline.",
        "overheating": f"Motor temperature reached {rng.randint(78, 86)} °C; cooling path blocked / failing. "
                       "Component replaced, temperature normal after 30 min.",
    }[mode]


def _insert_wo(it: Database, w: dict) -> None:
    it.x("INSERT INTO work_orders(id, asset_id, type, title, root_cause, findings, technician_id, planned_start, "
         "duration_hours, status, incident_id, created_at, started_at, completed_at, outcome) "
         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
         (w["id"], w["asset_id"], w["type"], w["title"], w.get("root_cause"), w.get("findings"), w.get("technician_id"),
          w.get("planned_start"), w.get("duration_hours"), w["status"], w.get("incident_id"), w["created_at"],
          w.get("planned_start"), w.get("planned_start"), "fixed" if w["status"] == "done" else None))
    for p in w.get("parts") or []:
        it.x("INSERT INTO work_order_parts(work_order_id, sku, quantity) VALUES(?,?,?)", (w["id"], p["sku"], p["quantity"]))


def wipe(data_dir: Path) -> None:
    for name in ("ot.db", "ot.db-wal", "ot.db-shm", "it.db", "it.db-wal", "it.db-shm"):
        (data_dir / name).unlink(missing_ok=True)
    vdb = data_dir / "vdb"
    if vdb.exists():
        for f in vdb.iterdir():
            f.unlink()
