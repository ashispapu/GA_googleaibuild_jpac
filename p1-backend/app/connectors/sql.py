"""Connectors over the simulated plant's databases (DATA_MODE=sim). Same Protocols and contract types as the fixture
connectors, so routers, tools and agents do not change. Line ids repeat across plants (L1 in Plant A and Plant B):
every line-based call takes an optional plant_id; the ToolGateway fills it from the incident for agents."""
from __future__ import annotations

from datetime import datetime, timedelta

from plantguard_contracts.schemas.common import Health, MachineState, Money, TimeWindow
from plantguard_contracts.schemas.energy import AssetConsumption, Co2Factor, LineConsumption, Tariff
from plantguard_contracts.schemas.engineering import AssetBom, BomItem
from plantguard_contracts.schemas.erp import (PurchaseOrder, PurchaseOrderDraft, PurchaseOrderStatus, Part, PlantStock, StockTransfer, StockTransferCreate, Supplier, TransferMode,
                                              TransferStatus)
from plantguard_contracts.schemas.maintenance import PartUse, WorkOrder, WorkOrderCreate, WorkOrderStatus
from plantguard_contracts.schemas.mes import LineOEE, OrderStatus, ProductionOrder, ScheduleSlot
from plantguard_contracts.schemas.ot import Alarm, AlarmSeverity, Signal, SignalPoint, SignalSeries
from plantguard_contracts.schemas.plants import Asset, Line, Plant, Station
from plantguard_contracts.schemas.quality import Defect, DefectSummary, Inspection
from plantguard_contracts.schemas.workforce import Shift, Technician

from app.sim.clock import iso, parse_ts
from app.sim.db import Database
from app.sim.physics import nominal

STANDARD_TRANSFER = (350.0, 20.0, 1.0)  # (EUR, hours to arrive, hours until the next truck leaves)
EXPRESS_TRANSFER = (900.0, 6.0, 0.25)
SUPPLIER_SHIPPING_EUR = 0.0       # same values as [policy.planning] supplier_shipping_eur / supplier_express_eur
SUPPLIER_EXPRESS_EUR = 250.0


def _line_filter(plant_id: str | None) -> tuple[str, tuple]:
    return (" AND plant_id=?", (plant_id,)) if plant_id else ("", ())


class SqlPlants:  # implements PlantConnector
    def __init__(self, ot: Database, it: Database, engine, scores):
        self.ot, self.it, self.engine, self.scores = ot, it, engine, scores

    def list_plants(self) -> list[Plant]:
        out = []
        states = {r["asset_id"]: r["state"] for r in self.ot.q("SELECT asset_id, state FROM machine_state")}
        crit_alarm = {r["asset_id"] for r in self.ot.q("SELECT DISTINCT asset_id FROM alarms WHERE cleared_at IS NULL "
                                                        "AND severity='critical'")}
        for p in self.it.q("SELECT * FROM plants ORDER BY id"):
            lines = []
            for l in self.it.q("SELECT * FROM lines WHERE plant_id=? ORDER BY id", (p["id"],)):
                stations: dict[str, Station] = {}
                worst = Health.OK
                for e in self.it.q("SELECT * FROM equipment WHERE plant_id=? AND line_id=? ORDER BY station_seq, asset_id",
                                   (p["id"], l["id"])):
                    h = self.scores.get(e["asset_id"])
                    risk = h.risk_72h if h else 0.04
                    health = h.health if h else Health.OK
                    state = states.get(e["asset_id"], "run")
                    if e["asset_id"] in crit_alarm or state == "fault":
                        health = Health.CRITICAL
                    if health == Health.CRITICAL or (health == Health.WARNING and worst == Health.OK):
                        worst = health
                    st = stations.setdefault(e["station_id"], Station(id=e["station_id"], name=e["station_name"],
                                                                      order=e["station_seq"], assets=[]))
                    st.assets.append(Asset(id=e["asset_id"], name=e["name"], type=e["type"],
                                           state=MachineState(state) if state in MachineState._value2member_map_ else MachineState.STOPPED,
                                           health=health, risk_72h=round(risk, 2), position=(e["pos_x"], e["pos_y"])))
                running = self.engine.line_running(p["id"], l["id"])
                order = self.it.val("SELECT id FROM production_orders WHERE plant_id=? AND line_id=? AND status!='completed' "
                                    "ORDER BY seq LIMIT 1", (p["id"], l["id"]))
                lines.append(Line(id=l["id"], plant_id=p["id"], name=l["name"], product=l["product"],
                                  state=MachineState.RUN if running else MachineState.STOPPED, health=worst,
                                  speed_pct=self.engine.line_speed(p["id"], l["id"]),
                                  oee=round(SqlMES.oee_value(self.it, self.engine.clock(), p["id"], l["id"]), 3),
                                  current_order_id=order, stations=sorted(stations.values(), key=lambda s: s.order)))
            out.append(Plant(id=p["id"], name=p["name"], location=p["location"], lines=lines))
        return out


class SqlOT:  # implements OTConnector
    def __init__(self, ot: Database, engine, clock):
        self.ot, self.engine, self.clock = ot, engine, clock

    def get_signals(self, asset_id: str, signals: list[Signal], minutes: int) -> list[SignalSeries]:
        if not self.ot.one("SELECT 1 FROM equipment WHERE asset_id=?", (asset_id,)):
            raise KeyError(asset_id)
        since = iso(self.clock() - timedelta(minutes=minutes))
        rows = self.ot.q("SELECT * FROM historian WHERE asset_id=? AND ts>=? ORDER BY ts", (asset_id, since))
        tags = {r["signal"]: r for r in self.ot.q("SELECT * FROM tags WHERE asset_id=?", (asset_id,))}
        out = []
        for s in (signals or list(Signal)):
            t = tags.get(s.value)
            if t is None:
                continue
            out.append(SignalSeries(asset_id=asset_id, signal=s, unit=t["unit"], source_tag=t["tag"],
                                    points=[SignalPoint(ts=parse_ts(r["ts"]), value=r[s.value]) for r in rows
                                            if r[s.value] is not None]))
        return out

    def get_alarms(self, plant_id: str | None, active: bool) -> list[Alarm]:
        sql = "SELECT a.* FROM alarms a JOIN equipment e ON e.asset_id=a.asset_id WHERE 1=1"
        args: list = []
        if plant_id:
            sql += " AND e.site_id=?"
            args.append(plant_id)
        if active:
            sql += " AND a.cleared_at IS NULL AND a.acknowledged=0"
        else:
            sql += " AND a.raised_at>=?"
            args.append(iso(self.clock() - timedelta(hours=24)))
        sql += " ORDER BY a.raised_at DESC LIMIT 200"
        return [Alarm(id=r["id"], asset_id=r["asset_id"], code=r["code"], severity=AlarmSeverity(r["severity"]),
                      message=r["message"], raised_at=parse_ts(r["raised_at"]), acknowledged=bool(r["acknowledged"]))
                for r in self.ot.q(sql, args)]

    def subscribe(self, plant_id: str, line_id: str):
        return self.engine.subscribe(plant_id, line_id)


class SqlERP:  # implements ERPConnector
    def __init__(self, it: Database, clock):
        self.it, self.clock = it, clock

    def get_part(self, sku: str) -> Part:
        p = self.it.one("SELECT p.*, s.lead_time_days FROM parts p JOIN suppliers s ON s.id=p.supplier_id WHERE sku=?", (sku,))
        if p is None:
            raise KeyError(sku)
        stock = [PlantStock(plant_id=r["plant_id"], quantity=r["quantity"], storage_location=r["location_id"])
                 for r in self.it.q("SELECT * FROM stock WHERE sku=? ORDER BY plant_id", (sku,))]
        return Part(sku=sku, description=p["description"], unit_cost=Money(amount=p["unit_cost"]), stock=stock,
                    supplier_id=p["supplier_id"], supplier_lead_time_days=p["lead_time_days"])

    def get_supplier(self, supplier_id: str) -> Supplier:
        s = self.it.one("SELECT * FROM suppliers WHERE id=?", (supplier_id,))
        if s is None:
            raise KeyError(supplier_id)
        return Supplier(id=s["id"], name=s["name"], lead_time_days=s["lead_time_days"],
                        express_available=bool(s["express_available"]), express_lead_time_days=s["express_lead_time_days"])

    def create_stock_transfer(self, req: StockTransferCreate) -> StockTransfer:
        cost, eta_h, depart_h = EXPRESS_TRANSFER if req.mode == TransferMode.EXPRESS else STANDARD_TRANSFER
        now = self.clock()
        tid = f"STO-{self.it.next_id('STO', 4500012)}"
        approval = self.it.val("SELECT id FROM approvals WHERE incident_id=? AND decision='approve' ORDER BY approved_at DESC "
                               "LIMIT 1", (req.incident_id,))
        self.it.x("INSERT INTO stock_transfers(id, sku, from_plant, to_plant, quantity, mode, status, created_at, departs_at, "
                  "eta, cost, incident_id, approval_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (tid, req.sku, req.from_plant, req.to_plant, req.quantity, req.mode.value, "created", iso(now),
                   iso(now + timedelta(hours=depart_h)), iso(now + timedelta(hours=eta_h)), cost, req.incident_id, approval))
        return self.transfer(tid)

    def create_purchase_order(self, d: PurchaseOrderDraft) -> PurchaseOrder:
        """Order from the supplier (v1.2). Arrives after the supplier's (express) lead time; the plant engine books the
        goods receipt into stock at `to_plant`."""
        p = self.it.one("SELECT p.unit_cost, s.* FROM parts p JOIN suppliers s ON s.id=p.supplier_id WHERE p.sku=?", (d.sku,))
        if p is None:
            raise KeyError(d.sku)
        days = p["express_lead_time_days"] if d.express and p["express_available"] and p["express_lead_time_days"] \
            else p["lead_time_days"]
        now = self.clock()
        pid = f"PO-{self.it.next_id('PO', 4500101)}"
        cost = p["unit_cost"] * d.quantity + (SUPPLIER_EXPRESS_EUR if d.express else SUPPLIER_SHIPPING_EUR)
        approval = self.it.val("SELECT id FROM approvals WHERE incident_id=? AND decision='approve' ORDER BY approved_at DESC "
                               "LIMIT 1", (d.incident_id,))
        self.it.x("INSERT INTO purchase_orders(id, sku, supplier_id, to_plant, quantity, express, status, created_at, eta, "
                  "cost, incident_id, approval_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (pid, d.sku, d.supplier_id, d.to_plant, d.quantity, int(d.express), "ordered", iso(now),
                   iso(now + timedelta(days=float(days))), cost, d.incident_id, approval))
        return self.purchase_order(pid)

    def purchase_order(self, pid: str) -> PurchaseOrder:
        r = self.it.one("SELECT * FROM purchase_orders WHERE id=?", (pid,))
        if r is None:
            raise KeyError(pid)
        return PurchaseOrder(id=r["id"], sku=r["sku"], supplier_id=r["supplier_id"], to_plant=r["to_plant"],
                             quantity=r["quantity"], express=bool(r["express"]), status=PurchaseOrderStatus(r["status"]),
                             eta=parse_ts(r["eta"]), cost=Money(amount=r["cost"]), incident_id=r["incident_id"])

    def purchase_orders(self, incident_id: str) -> list[dict]:
        return [dict(r) for r in self.it.q("SELECT * FROM purchase_orders WHERE incident_id=? ORDER BY created_at DESC",
                                           (incident_id,))]

    def transfer(self, tid: str) -> StockTransfer:
        r = self.it.one("SELECT * FROM stock_transfers WHERE id=?", (tid,))
        if r is None:
            raise KeyError(tid)
        return StockTransfer(id=r["id"], sku=r["sku"], from_plant=r["from_plant"], to_plant=r["to_plant"],
                             quantity=r["quantity"], mode=TransferMode(r["mode"]), status=TransferStatus(r["status"]),
                             eta=parse_ts(r["eta"]), cost=Money(amount=r["cost"]))

    def transfers(self, incident_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM stock_transfers" + (" WHERE incident_id=?" if incident_id else "") + " ORDER BY created_at DESC"
        return [dict(r) for r in self.it.q(sql, (incident_id,) if incident_id else ())]

    def reset(self) -> None:  # parity with FixtureERP (simulator calm/reset scenes are fixture-mode only)
        return None


class SqlMES:  # implements MESConnector
    def __init__(self, it: Database, clock, engine):
        self.it, self.clock, self.engine = it, clock, engine

    def get_orders(self, line_id: str | None, plant_id: str | None = None) -> list[ProductionOrder]:
        sql, args = "SELECT * FROM production_orders WHERE status!='completed'", []
        if line_id:
            sql += " AND line_id=?"
            args.append(line_id)
        if plant_id:
            sql += " AND plant_id=?"
            args.append(plant_id)
        return [ProductionOrder(id=r["id"], product=r["product"], quantity=r["quantity"], completed=r["completed"],
                                due_at=parse_ts(r["due_at"]), line_id=r["line_id"], status=OrderStatus(r["status"]))
                for r in self.it.q(sql + " ORDER BY due_at", args)]

    def _plant(self, line_id: str, plant_id: str | None) -> str:
        if plant_id:
            return plant_id
        r = self.it.one("SELECT plant_id FROM lines WHERE id=? ORDER BY plant_id LIMIT 1", (line_id,))
        if r is None:
            raise KeyError(line_id)
        return r["plant_id"]

    def get_schedule(self, line_id: str, hours: int, plant_id: str | None = None) -> list[ScheduleSlot]:
        plant_id = self._plant(line_id, plant_id)
        now = self.clock()
        rows = self.it.q("SELECT * FROM schedule_slots WHERE plant_id=? AND line_id=? AND end_ts>? AND start_ts<? "
                         "ORDER BY start_ts", (plant_id, line_id, iso(now), iso(now + timedelta(hours=hours))))
        return [ScheduleSlot(line_id=line_id, start=parse_ts(r["start_ts"]), end=parse_ts(r["end_ts"]),
                             order_id=r["order_id"], planned_speed_pct=r["planned_speed_pct"], kind=r["kind"]) for r in rows]

    @staticmethod
    def oee_value(it: Database, now: datetime, plant_id: str, line_id: str) -> float:
        r = it.one("SELECT SUM(planned_min) pm, SUM(run_min) rm, SUM(speed_sum) ss, SUM(units) u, SUM(defects) d "
                   "FROM production_log WHERE plant_id=? AND line_id=? AND hour_ts>=?",
                   (plant_id, line_id, iso(now - timedelta(hours=24))))
        if not r or not r["pm"]:
            return 0.0
        a = (r["rm"] or 0) / r["pm"]
        perf = ((r["ss"] or 0) / 100 / r["rm"]) if r["rm"] else 0.0
        q = 1 - (r["d"] or 0) / max(1.0, (r["u"] or 0))
        return max(0.0, a * perf * q)

    def get_oee(self, line_id: str, plant_id: str | None = None) -> LineOEE:
        plant_id = self._plant(line_id, plant_id)
        now = self.clock()
        r = self.it.one("SELECT SUM(planned_min) pm, SUM(run_min) rm, SUM(speed_sum) ss, SUM(units) u, SUM(defects) d "
                        "FROM production_log WHERE plant_id=? AND line_id=? AND hour_ts>=?",
                        (plant_id, line_id, iso(now - timedelta(hours=24))))
        pm, rm = (r["pm"] or 0.0), (r["rm"] or 0.0)
        a = rm / pm if pm else 0.0
        perf = (r["ss"] or 0) / 100 / rm if rm else 0.0
        q = 1 - (r["d"] or 0) / max(1.0, (r["u"] or 0))
        return LineOEE(line_id=line_id, window=TimeWindow(start=now - timedelta(hours=24), end=now),
                       availability=round(a, 3), performance=round(perf, 3), quality=round(q, 3),
                       oee=round(a * perf * q, 3), downtime_minutes=int(max(0.0, pm - rm)))

    def set_line_speed(self, line_id: str, speed_pct: int, start: datetime, end: datetime | None,
                       plant_id: str | None = None, incident_id: str | None = None) -> None:
        if not plant_id and incident_id:
            plant_id = self.it.val("SELECT plant_id FROM incidents WHERE id=?", (incident_id,))
        plant_id = self._plant(line_id, plant_id)
        approval = self.it.val("SELECT id FROM approvals WHERE incident_id=? AND decision='approve' ORDER BY approved_at "
                               "DESC LIMIT 1", (incident_id,)) if incident_id else None
        self.it.x("INSERT INTO line_speed_plan(plant_id, line_id, speed_pct, start_ts, end_ts, incident_id, approval_id, "
                  "created_at) VALUES(?,?,?,?,?,?,?,?)", (plant_id, line_id, speed_pct, iso(start), iso(end) if end else None,
                                                       incident_id, approval, iso(self.clock())))

    def clear_speed_plans(self, incident_id: str) -> None:
        """When the repair is finished the line goes back to normal speed (end the incident's plans now)."""
        now = iso(self.clock())
        self.it.x("UPDATE line_speed_plan SET end_ts=? WHERE incident_id=? AND (end_ts IS NULL OR end_ts>?)",
                  (now, incident_id, now))


class SqlMaintenance:  # implements MaintenanceConnector
    def __init__(self, it: Database, clock):
        self.it, self.clock = it, clock

    def _tech_name(self, tech_id: str | None) -> str:
        return self.it.val("SELECT name FROM technicians WHERE id=?", (tech_id,), tech_id) or "unassigned"

    def _wo(self, r) -> WorkOrder:
        parts = [PartUse(sku=p["sku"], quantity=p["quantity"])
                 for p in self.it.q("SELECT sku, quantity FROM work_order_parts WHERE work_order_id=?", (r["id"],))]
        return WorkOrder(id=r["id"], asset_id=r["asset_id"], type=r["type"], title=r["title"], root_cause=r["root_cause"],
                         findings=r["findings"], parts=parts, technician_id=r["technician_id"],
                         planned_start=parse_ts(r["planned_start"]) if r["planned_start"] else None,
                         duration_hours=r["duration_hours"], status=WorkOrderStatus(r["status"]),
                         incident_id=r["incident_id"], created_at=parse_ts(r["created_at"]))

    def get(self, wo_id: str) -> WorkOrder:
        r = self.it.one("SELECT * FROM work_orders WHERE id=?", (wo_id,))
        if r is None:
            raise KeyError(wo_id)
        return self._wo(r)

    def get_history(self, asset_id: str) -> list[WorkOrder]:
        if not self.it.one("SELECT 1 FROM equipment WHERE asset_id=?", (asset_id,)):
            raise KeyError(asset_id)
        return [self._wo(r) for r in self.it.q("SELECT * FROM work_orders WHERE asset_id=? ORDER BY "
                                               "COALESCE(completed_at, planned_start, created_at) DESC", (asset_id,))]

    def create_work_order(self, req: WorkOrderCreate) -> WorkOrder:
        now = self.clock()
        wid = f"WO-{now.year}-{self.it.next_id(f'WO-{now.year}', 153):04d}"
        approval = self.it.val("SELECT id FROM approvals WHERE incident_id=? AND decision='approve' ORDER BY approved_at "
                               "DESC LIMIT 1", (req.incident_id,))
        loc = self.it.one("SELECT plant_id, line_id FROM equipment WHERE asset_id=?", (req.asset_id,))
        with self.it.tx() as c:
            c.execute("INSERT INTO work_orders(id, asset_id, type, title, root_cause, technician_id, planned_start, "
                      "duration_hours, status, incident_id, approval_id, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (wid, req.asset_id, req.type.value, req.title, req.root_cause, req.technician_id,
                       iso(req.planned_start), req.duration_hours, WorkOrderStatus.SCHEDULED.value, req.incident_id,
                       approval, iso(now)))
            for p in req.parts:
                c.execute("INSERT INTO work_order_parts(work_order_id, sku, quantity) VALUES(?,?,?)", (wid, p.sku, p.quantity))
            c.execute("INSERT INTO work_order_updates(work_order_id, ts, author_id, kind, note) VALUES(?,?,?,?,?)",
                      (wid, iso(now), None, "created", f"Work order {wid} planned for "
                       f"{req.planned_start:%a %d %b %H:%M} (plant time), technician {self._tech_name(req.technician_id)}"))
            if loc:
                c.execute("INSERT INTO schedule_slots(plant_id, line_id, start_ts, end_ts, kind, order_id, planned_speed_pct, "
                          "work_order_id) VALUES(?,?,?,?,?,?,?,?)",
                          (loc["plant_id"], loc["line_id"], iso(req.planned_start),
                           iso(req.planned_start + timedelta(hours=req.duration_hours)), "maintenance", None, 0, wid))
        return self.get(wid)

    def reset(self) -> None:
        return None


class SqlQuality:  # implements InspectionConnector
    def __init__(self, it: Database, clock):
        self.it, self.clock = it, clock

    def get_defects(self, line_id: str, hours: int, plant_id: str | None = None) -> DefectSummary:
        now = self.clock()
        f, fa = _line_filter(plant_id)
        if not self.it.one("SELECT 1 FROM lines WHERE id=?" + f, (line_id, *fa)):
            raise KeyError(line_id)
        start, prev = now - timedelta(hours=hours), now - timedelta(hours=2 * hours)
        rows = self.it.q("SELECT * FROM defects WHERE line_id=?" + f + " AND ts>=? ORDER BY ts", (line_id, *fa, iso(start)))
        prev_n = self.it.val("SELECT COUNT(*) FROM defects WHERE line_id=?" + f + " AND ts>=? AND ts<?",
                             (line_id, *fa, iso(prev), iso(start)), 0)
        units = self.it.val("SELECT SUM(units) FROM production_log WHERE line_id=?" + f + " AND hour_ts>=?",
                            (line_id, *fa, iso(start)), 0.0) or 0.0
        inspected = max(1.0, units * 0.05)
        trend = ((len(rows) - prev_n) / prev_n * 100) if prev_n else (100.0 * len(rows) if rows else 0.0)
        return DefectSummary(line_id=line_id, window=TimeWindow(start=start, end=now),
                             defect_rate_pct=round(100 * len(rows) / inspected, 2), trend_pct=round(trend, 1),
                             items=[Defect(id=r["id"], line_id=line_id, asset_id=r["asset_id"], defect_type=r["defect_type"],
                                           measured=r["measured"], spec_min=r["spec_min"], spec_max=r["spec_max"],
                                           ts=parse_ts(r["ts"])) for r in rows[-50:]])

    def get_inspections(self, line_id: str, plant_id: str | None = None) -> list[Inspection]:
        f, fa = _line_filter(plant_id)
        return [Inspection(id=r["id"], line_id=line_id, lot_id=r["lot_id"], passed=bool(r["passed"]), ts=parse_ts(r["ts"]))
                for r in self.it.q("SELECT * FROM inspections WHERE line_id=?" + f + " ORDER BY ts DESC LIMIT 50",
                                   (line_id, *fa))]


class SqlEngineering:  # implements EngineeringConnector
    def __init__(self, it: Database):
        self.it = it

    def get_bom(self, asset_id: str) -> AssetBom:
        if not self.it.one("SELECT 1 FROM equipment WHERE asset_id=?", (asset_id,)):
            raise KeyError(asset_id)
        return AssetBom(asset_id=asset_id, items=[BomItem(sku=r["sku"], description=r["description"], quantity=r["quantity"])
                                                  for r in self.it.q("SELECT b.sku, b.quantity, p.description FROM bom_items b "
                                                                     "JOIN parts p ON p.sku=b.sku WHERE asset_id=? ORDER BY b.pos, b.sku",
                                                                     (asset_id,))])


class SqlWorkforce:  # implements WorkforceConnector
    def __init__(self, it: Database, clock):
        self.it, self.clock = it, clock

    def _tech(self, r, on: set[str]) -> Technician:
        return Technician(id=r["id"], name=r["name"],
                          skills=[s["skill"] for s in self.it.q("SELECT skill FROM technician_skills WHERE technician_id=? "
                                                                 "ORDER BY skill", (r["id"],))],
                          certifications=[s["certification"] for s in self.it.q(
                              "SELECT certification FROM technician_certifications WHERE technician_id=? ORDER BY certification",
                              (r["id"],))],
                          on_shift=r["id"] in on)

    def on_shift_now(self) -> set[str]:
        t = iso(self.clock())
        return {r["technician_id"] for r in self.it.q("SELECT a.technician_id FROM shift_assignments a JOIN shifts s "
                                                      "ON s.id=a.shift_id WHERE s.start_ts<=? AND s.end_ts>?", (t, t))}

    def get_shifts(self, plant_id: str) -> list[Shift]:
        now = self.clock()
        on = self.on_shift_now()
        out = []
        for s in self.it.q("SELECT * FROM shifts WHERE plant_id=? AND end_ts>? AND start_ts<? ORDER BY start_ts",
                           (plant_id, iso(now), iso(now + timedelta(hours=48)))):
            techs = [self._tech(r, on) for r in self.it.q("SELECT t.* FROM technicians t JOIN shift_assignments a ON "
                                                          "a.technician_id=t.id WHERE a.shift_id=? ORDER BY t.id", (s["id"],))]
            out.append(Shift(id=s["id"], name=s["name"], start=parse_ts(s["start_ts"]), end=parse_ts(s["end_ts"]),
                             plant_id=plant_id, technicians=techs))
        return out

    def find_technicians(self, skill: str | None, on_shift: bool | None, plant_id: str | None = None) -> list[Technician]:
        on = self.on_shift_now()
        sql, args = "SELECT * FROM technicians WHERE 1=1", []
        if plant_id:
            sql += " AND plant_id=?"
            args.append(plant_id)
        out = [self._tech(r, on) for r in self.it.q(sql + " ORDER BY id", args)]
        return [t for t in out if (not skill or skill in t.skills) and (on_shift is None or t.on_shift == on_shift)]

    def next_shift_of(self, tech_id: str) -> dict | None:
        r = self.it.one("SELECT s.* FROM shifts s JOIN shift_assignments a ON a.shift_id=s.id WHERE a.technician_id=? AND "
                        "s.end_ts>? ORDER BY s.start_ts LIMIT 1", (tech_id, iso(self.clock())))
        return dict(r) if r else None


class SqlEnergy:  # implements EnergyConnector
    def __init__(self, ot: Database, it: Database, clock, models: dict, tariffs: list[dict], co2: float):
        self.ot, self.it, self.clock, self.models = ot, it, clock, models
        self._tariffs, self._co2 = tariffs, co2

    def _price(self, hour: int) -> float:
        for t in self._tariffs:
            a, b = t["start_hour"], t["end_hour"]
            if (a <= hour < b) if a < b else (hour >= a or hour < b):
                return t["price"]
        return self._tariffs[0]["price"]

    def get_consumption(self, line_id: str, hours: int, plant_id: str | None = None) -> LineConsumption:
        now = self.clock()
        start = now - timedelta(hours=hours)
        sql = "SELECT asset_id, type FROM equipment WHERE line_id=?" + (" AND site_id=?" if plant_id else "")
        assets = self.ot.q(sql, (line_id, plant_id) if plant_id else (line_id,))
        if not assets:
            raise KeyError(line_id)
        by_asset, kwh_total, cost = [], 0.0, 0.0
        for a in assets:
            rows = self.ot.q("SELECT ts, power_kw, speed_pct, state FROM historian WHERE asset_id=? AND ts>=? ORDER BY ts",
                             (a["asset_id"], iso(start)))
            if not rows:
                continue
            step_h = hours / max(1, len(rows))
            kwh = sum(r["power_kw"] or 0 for r in rows) * step_h
            run = [r for r in rows if r["state"] == "run"]
            avg = sum(r["power_kw"] for r in run) / len(run) if run else 0.0
            base = sum(nominal(self.models[a["type"]]["baseline"], r["speed_pct"])["power_kw"] for r in run) / len(run) \
                if run else 0.0
            for r in rows:
                cost += (r["power_kw"] or 0) * step_h * self._price(parse_ts(r["ts"]).hour)
            kwh_total += kwh
            by_asset.append(AssetConsumption(asset_id=a["asset_id"], kwh=round(kwh, 1), avg_power_kw=round(avg, 2),
                                             baseline_power_kw=round(base, 2)))
        return LineConsumption(line_id=line_id, window=TimeWindow(start=start, end=now), kwh=round(kwh_total, 1),
                               cost=Money(amount=round(cost, 2)), co2_kg=round(kwh_total * self._co2, 1), by_asset=by_asset)

    def get_tariffs(self) -> list[Tariff]:
        return [Tariff(name=t["name"], start_hour=t["start_hour"], end_hour=t["end_hour"],
                       price_per_kwh=Money(amount=t["price"])) for t in self._tariffs]

    def get_co2_factor(self) -> Co2Factor:
        return Co2Factor(kg_per_kwh=self._co2, source="Grid average (plant config)")
