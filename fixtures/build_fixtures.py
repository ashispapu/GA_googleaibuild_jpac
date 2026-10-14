"""Golden story data. Run: python fixtures/build_fixtures.py
Every object is built through the contract models, so fixtures can never drift from schemas.
Owner: ML & Data. Everyone reads these; change numbers here only."""
import json, math, random, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "p4-ml-data"))
from data_gen import story  # EQ-3012 story curve, shared with the training data generator  # noqa: E402

from plantguard_contracts.schemas.common import *  # noqa
from plantguard_contracts.schemas.plants import Asset, Line, Plant, Station
from plantguard_contracts.schemas.ot import Alarm, AlarmSeverity, Signal, SignalPoint, SignalSeries
from plantguard_contracts.schemas.erp import *  # noqa
from plantguard_contracts.schemas.mes import *  # noqa
from plantguard_contracts.schemas.maintenance import *  # noqa
from plantguard_contracts.schemas.quality import Defect, DefectSummary
from plantguard_contracts.schemas.engineering import *  # noqa
from plantguard_contracts.schemas.workforce import Shift, Technician
from plantguard_contracts.schemas.energy import *  # noqa
from plantguard_contracts.schemas.ml import AssetHealth, ModelInfo, ShapFeature, FEATURES
from plantguard_contracts.schemas.incidents import *  # noqa
from plantguard_contracts.schemas.approvals import *  # noqa
from plantguard_contracts.schemas.audit import ActorType, AuditEntry

OUT = Path(__file__).parent / "story"
OUT.mkdir(exist_ok=True)
UTC = timezone.utc
NOW = datetime(2026, 10, 13, 21, 40, tzinfo=UTC)      # Tuesday night, alert time
DUE = datetime(2026, 10, 16, 18, 0, tzinfo=UTC)       # Friday deadline
NIGHT = datetime(2026, 10, 14, 22, 0, tzinfo=UTC)     # Wednesday night shift start
EUR = lambda x: Money(amount=round(x, 2))
random.seed(7)


def dump(name, obj):
    def ser(o):
        return o.model_dump(mode="json") if hasattr(o, "model_dump") else o
    data = [ser(o) for o in obj] if isinstance(obj, list) else ser(obj)
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ---------------- Plants ----------------
def asset(i, name, typ, station_x, health=Health.OK, risk=0.04, state=MachineState.RUN):
    return Asset(id=f"EQ-{i}", name=name, type=typ, state=state, health=health,
                 risk_72h=risk, position=(station_x, 12.0))

line3 = Line(id="L3", plant_id="PLANT-A", name="Line 3 — Drill assembly", product="Cordless drill CD-18",
             state=MachineState.RUN, health=Health.WARNING, speed_pct=100, oee=0.87,
             current_order_id="ORD-5521", stations=[
    Station(id="L3-ST1", name="Housing press", order=1, assets=[asset(3010, "Hydraulic press", "press", 4)]),
    Station(id="L3-ST2", name="Spindle machining", order=2, assets=[
        asset(3011, "CNC spindle 1", "spindle", 10),
        asset(3012, "CNC spindle 2", "spindle", 13, Health.CRITICAL, 0.78)]),
    Station(id="L3-ST3", name="Gearbox assembly", order=3, assets=[asset(3013, "Assembly robot", "robot", 20)]),
    Station(id="L3-ST4", name="Conveyor", order=4, assets=[asset(3014, "Belt conveyor", "conveyor", 27)]),
    Station(id="L3-ST5", name="End-of-line test", order=5, assets=[asset(3015, "Torque tester", "inspection", 34)]),
])

def simple_line(pid, lid, name, product, base, oee):
    return Line(id=lid, plant_id=pid, name=name, product=product, state=MachineState.RUN, health=Health.OK,
                speed_pct=100, oee=oee, stations=[
        Station(id=f"{lid}-ST{k+1}", name=n, order=k+1,
                assets=[asset(base + k, n, t, 4 + k * 8)])
        for k, (n, t) in enumerate([("Press", "press"), ("Spindle", "spindle"), ("Robot", "robot"), ("Tester", "inspection")])])

plants = [
    Plant(id="PLANT-A", name="Plant A", location="Pune", lines=[
        simple_line("PLANT-A", "L1", "Line 1 — Grinder", "Angle grinder AG-9", 3001, 0.91),
        simple_line("PLANT-A", "L2", "Line 2 — Impact driver", "Impact driver ID-12", 3005, 0.89),
        line3]),
    Plant(id="PLANT-B", name="Plant B", location="Chennai", lines=[
        simple_line("PLANT-B", "L1", "Line 1 — Drill assembly", "Cordless drill CD-18", 4001, 0.85),
        Line(id="L2", plant_id="PLANT-B", name="Line 2 — Hammer drill", product="Hammer drill HD-24",
             state=MachineState.RUN, health=Health.OK, speed_pct=100, oee=0.88, stations=[
            Station(id="L2-ST1", name="Spindle machining", order=1, assets=[asset(4005, "CNC spindle", "spindle", 4)]),
            Station(id="L2-ST2", name="Conveyor", order=2, assets=[asset(4006, "Belt conveyor", "conveyor", 12)])])]),
]
dump("plants", plants)

users = [
    {"username": "op", "password": "demo", "id": "USR-op", "name": "Ravi (Operator)", "role": "operator", "plant_ids": ["PLANT-A"]},
    {"username": "lead", "password": "demo", "id": "USR-lead", "name": "Anita (Maintenance Lead)", "role": "maintenance_lead", "plant_ids": ["PLANT-A"]},
    {"username": "manager", "password": "demo", "id": "USR-manager", "name": "Karan (Plant Manager)", "role": "plant_manager", "plant_ids": ["PLANT-A", "PLANT-B"]},
    {"username": "admin", "password": "demo", "id": "USR-admin", "name": "Admin", "role": "admin", "plant_ids": ["PLANT-A", "PLANT-B"]},
]
dump("users", users)

# ---------------- OT signals for EQ-3012 (last 48 h, 1 point / 5 min) ----------------
# The curve lives in p4-ml-data/data_gen/story.py so the chart and the ML training data are the same story:
# normal 2.1 mm/s until 3 days before, 3.2 mm/s six hours before, 4.3 mm/s at the alert (+34 % in 6 h).
TAGS = {Signal.VIBRATION_RMS: ("mm/s", "VIB_RMS"), Signal.VIBRATION_PEAK: ("mm/s", "VIB_PK"),
        Signal.VIBRATION_KURTOSIS: ("", "VIB_KURT"), Signal.BEARING_TEMP: ("°C", "BRG_T"),
        Signal.MOTOR_CURRENT: ("A", "MTR_I"), Signal.POWER_KW: ("kW", "PWR"), Signal.SPINDLE_RPM: ("rpm", "RPM")}
times = [NOW - timedelta(minutes=5 * k) for k in range(48 * 12)][::-1]
levels = [story.means(t) for t in times]
signals = [SignalSeries(asset_id="EQ-3012", signal=sig, unit=unit, source_tag=f"L3.ST2.SPDL2.{tag}", points=[
    SignalPoint(ts=t, value=round(lv[sig.value] + random.gauss(0, story.NOISE[sig.value]), 3))
    for t, lv in zip(times, levels)]) for sig, (unit, tag) in TAGS.items()]
dump("signals_EQ-3012", signals)
# Threshold rule (ISO 10816 zone C) = 4.5 mm/s RMS sustained 30 min -> not yet breached at NOW; ML warns earlier.

alarms = [Alarm(id="ALM-9001", asset_id="EQ-3012", code="VIB-HI-W", severity=AlarmSeverity.WARNING,
                message="Vibration RMS above 4.0 mm/s (warning band)", raised_at=NOW - timedelta(minutes=12))]
dump("alarms", alarms)

# ---------------- ERP ----------------
part = Part(sku="SKF-6205", description="Deep groove ball bearing 6205-2RS, spindle rear", unit_cost=EUR(85),
            stock=[PlantStock(plant_id="PLANT-A", quantity=0, storage_location="A-MRO-03"),
                   PlantStock(plant_id="PLANT-B", quantity=4, storage_location="B-MRO-11")],
            supplier_id="SUP-17", supplier_lead_time_days=21)
dump("part_SKF-6205", part)
supplier = Supplier(id="SUP-17", name="Bearing Supply Co.", lead_time_days=21, express_available=False)
dump("supplier_SUP-17", supplier)

# ---------------- MES ----------------
order = ProductionOrder(id="ORD-5521", product="Cordless drill CD-18", quantity=5000, completed=2900,
                        due_at=DUE, line_id="L3", status=OrderStatus.AT_RISK)
dump("orders", [order, ProductionOrder(id="ORD-5530", product="Angle grinder AG-9", quantity=3000, completed=1200,
                                       due_at=DUE + timedelta(days=7), line_id="L1", status=OrderStatus.RUNNING)])
schedule = []
t = NOW.replace(minute=0)
for h in range(0, 72, 8):
    s = t + timedelta(hours=h)
    schedule.append(ScheduleSlot(line_id="L3", start=s, end=s + timedelta(hours=8), order_id="ORD-5521"))
dump("schedule_L3", schedule)
dump("oee_L3", LineOEE(line_id="L3", window=TimeWindow(start=NOW - timedelta(hours=24), end=NOW),
                       availability=0.95, performance=0.93, quality=0.985, oee=0.87, downtime_minutes=72))

# ---------------- Maintenance ----------------
history = [
    WorkOrder(id="WO-2024-0871", asset_id="EQ-3012", type=WorkOrderType.CORRECTIVE,
              title="Spindle 2 rear bearing failure", root_cause="Rear bearing outer race wear (lubrication breakdown)",
              findings="Vibration rose ~120% over 3 days before seizure; line stopped 14 h.",
              parts=[PartUse(sku="SKF-6205", quantity=1)], technician_id="TECH-104",
              planned_start=datetime(2024, 11, 5, 6, tzinfo=UTC), duration_hours=2.5,
              status=WorkOrderStatus.DONE, created_at=datetime(2024, 11, 5, 2, tzinfo=UTC)),
    WorkOrder(id="WO-2026-0091", asset_id="EQ-3012", type=WorkOrderType.PREVENTIVE,
              title="Spindle 2 lubrication and inspection", findings="OK", parts=[], technician_id="TECH-112",
              planned_start=datetime(2026, 6, 2, 6, tzinfo=UTC), duration_hours=1, status=WorkOrderStatus.DONE,
              created_at=datetime(2026, 6, 1, 9, tzinfo=UTC)),
]
dump("history_EQ-3012", history)

# ---------------- Quality ----------------
defects = [Defect(id=f"DEF-{700+k}", line_id="L3", asset_id="EQ-3012", defect_type="bore_diameter",
                  measured=round(10.032 + 0.002 * k, 3), spec_min=9.98, spec_max=10.03,
                  ts=NOW - timedelta(minutes=40 * (9 - k))) for k in range(9)]
dump("defects_L3", DefectSummary(line_id="L3", window=TimeWindow(start=NOW - timedelta(hours=24), end=NOW),
                                 defect_rate_pct=1.6, trend_pct=+220.0, items=defects))

# ---------------- Engineering ----------------
dump("bom_EQ-3012", AssetBom(asset_id="EQ-3012", items=[
    BomItem(sku="SKF-6205", description="Rear spindle bearing 6205-2RS", quantity=1),
    BomItem(sku="SKF-7008", description="Front angular contact bearing 7008", quantity=2),
    BomItem(sku="BLT-HTD-8M", description="Drive belt HTD 8M", quantity=1)]))
dump("docsearch_bearing", DocSearchResponse(query="spindle vibration rising bearing", chunks=[
    DocChunk(doc_id="MAN-SPINDLE-200", title="CNC Spindle 200 Maintenance Manual", section="7.4", page=58,
             text="If vibration RMS rises more than 30% within 24 h together with kurtosis above 4, inspect the "
                  "rear bearing (6205-2RS). Continued operation above 4.5 mm/s may cause seizure within 48–96 h. "
                  "Reducing spindle speed to 70% lowers bearing load and extends safe run time.", score=0.91),
    DocChunk(doc_id="WO-2024-0871", title="Work order: Spindle 2 rear bearing failure", section="findings", page=1,
             text="Vibration rose ~120% over 3 days before seizure; rear bearing outer race worn.", score=0.86),
    DocChunk(doc_id="MAN-SPINDLE-200", title="CNC Spindle 200 Maintenance Manual", section="7.6", page=61,
             text="Misalignment shows as high vibration at 2× running speed with normal kurtosis.", score=0.62)]))

# ---------------- Workforce ----------------
techs = [Technician(id="TECH-104", name="Suresh", skills=["bearing_replacement", "spindle_alignment"],
                    certifications=["spindle-200"], on_shift=False),
         Technician(id="TECH-112", name="Meera", skills=["bearing_replacement", "electrical"],
                    certifications=["spindle-200"], on_shift=True),
         Technician(id="TECH-120", name="Arjun", skills=["hydraulics"], certifications=[], on_shift=True)]
dump("technicians", techs)
dump("shifts_PLANT-A", [
    Shift(id="SH-0413-N", name="night", start=NIGHT, end=NIGHT + timedelta(hours=8), plant_id="PLANT-A",
          technicians=[techs[0]]),
    Shift(id="SH-0413-E", name="evening", start=NOW.replace(hour=14, minute=0), end=NOW.replace(hour=22, minute=0),
          plant_id="PLANT-A", technicians=[techs[1], techs[2]])])

# ---------------- Energy ----------------
CO2 = 0.42
dump("energy_L3", LineConsumption(line_id="L3", window=TimeWindow(start=NOW - timedelta(hours=24), end=NOW),
    kwh=1840, cost=EUR(1840 * 0.21), co2_kg=round(1840 * CO2, 1), by_asset=[
        AssetConsumption(asset_id="EQ-3012", kwh=268, avg_power_kw=11.9, baseline_power_kw=11.0),
        AssetConsumption(asset_id="EQ-3011", kwh=262, avg_power_kw=10.9, baseline_power_kw=10.9)]))
dump("tariffs", [Tariff(name="peak", start_hour=6, end_hour=22, price_per_kwh=EUR(0.28)),
                 Tariff(name="off_peak", start_hour=22, end_hour=6, price_per_kwh=EUR(0.12))])
dump("co2_factor", Co2Factor(kg_per_kwh=CO2, source="Grid average (mock)"))

# ---------------- ML ----------------
shap = [ShapFeature(name="vib_rms_trend_6h", value=0.34, contribution=0.21, description="Vibration RMS +34% in 6 h"),
        ShapFeature(name="vib_kurtosis_mean", value=5.1, contribution=0.14, description="Kurtosis 5.1 (impulsive, bearing wear)"),
        ShapFeature(name="bearing_temp_trend_6h", value=8.6, contribution=0.09, description="Bearing temp +8.6 °C in 6 h"),
        ShapFeature(name="power_kw_vs_baseline", value=0.08, contribution=0.05, description="Power draw +8% vs baseline"),
        ShapFeature(name="hours_since_service", value=3150, contribution=0.03, description="3,150 h since last service")]
health = AssetHealth(asset_id="EQ-3012", health=Health.CRITICAL, risk_72h=0.78, rul_hours=70, anomaly_score=0.91,
                     predicted_failure_mode="bearing_wear", rule_threshold_breached=False, top_features=shap,
                     model_version="fp-xgb-1.0.0", evaluated_at=NOW)
dump("health_EQ-3012", health)
# Model page numbers come from the trained model (p4-ml-data/models/latest) when it exists, so the mock and the
# real backend show the same figures. Retrain -> `make export` -> fixtures follow.
_mdir = Path(__file__).resolve().parent.parent / "p4-ml-data" / "models"
_ver = (_mdir / "latest").read_text().strip() if (_mdir / "latest").is_file() else "fp-xgb-1.0.0"
_clf_m = {"precision": 0.88, "recall": 0.83, "f1": 0.85, "roc_auc": 0.94, "lead_time_gain_hours": 31}
_an_m, _trained = {"precision": 0.79, "recall": 0.86, "f1": 0.82}, datetime(2026, 10, 10, tzinfo=UTC)
if (_mdir / _ver / "metrics.json").exists():
    _clf_m = json.loads((_mdir / _ver / "metrics.json").read_text())
    _meta = json.loads((_mdir / _ver / "model_meta.json").read_text())
    _an_m = _meta.get("anomaly_metrics", _an_m)
    _trained = datetime.fromisoformat(_meta["trained_at"])
dump("models", [
    ModelInfo(version="fp-xgb-1.0.0", kind="failure_classifier", algorithm="xgboost", metrics=_clf_m,
              feature_names=list(FEATURES), trained_at=_trained),
    ModelInfo(version="an-if-1.0.0", kind="anomaly", algorithm="isolation_forest", metrics=_an_m,
              feature_names=list(FEATURES), trained_at=_trained)])

# ---------------- Incident + options ----------------
evid = {
    "ml": Evidence(kind=EvidenceKind.ML, ref_id="EQ-3012:fp-xgb-1.0.0", title="Failure risk 78% in 72 h",
                   snippet="Top drivers: vibration +34% in 6 h, kurtosis 5.1", link="/assets/EQ-3012"),
    "manual": Evidence(kind=EvidenceKind.MANUAL, ref_id="MAN-SPINDLE-200#7.4", title="Manual §7.4 Bearing wear",
                       snippet="RMS +30% in 24 h with kurtosis > 4 → inspect rear bearing 6205-2RS",
                       link="/docs/MAN-SPINDLE-200?section=7.4"),
    "wo": Evidence(kind=EvidenceKind.WORK_ORDER, ref_id="WO-2024-0871", title="Same failure in Nov 2024",
                   snippet="Rear bearing outer race worn; line stopped 14 h", link="/work-orders/WO-2024-0871"),
    "quality": Evidence(kind=EvidenceKind.QUALITY, ref_id="L3:bore_diameter", title="Bore defects +220%",
                        snippet="9 drills out of tolerance since 15:40", link="/quality/L3"),
    "sensor": Evidence(kind=EvidenceKind.SENSOR, ref_id="EQ-3012:vibration_rms", title="Vibration 4.3 mm/s",
                       snippet="Rising for 6 h; rule threshold 4.5 not yet hit", link="/assets/EQ-3012?signal=vibration_rms"),
}
diagnosis = Diagnosis(root_cause="Rear spindle bearing (SKF-6205) wear", failure_mode="bearing_wear", confidence=0.60,
                      evidence=[evid["sensor"], evid["ml"], evid["manual"], evid["wo"], evid["quality"]],
                      alternatives=["Spindle misalignment (manual §7.6) — kurtosis argues against it",
                                    "Sensor drift — ruled out, bearing temperature also rising"],
                      required_parts=["SKF-6205"])

INC = "INC-0042"
wo_draft = lambda start: WorkOrderDraft(asset_id="EQ-3012", type=WorkOrderType.PREDICTIVE,
    title="Replace rear spindle bearing EQ-3012", root_cause=diagnosis.root_cause,
    parts=[PartUse(sku="SKF-6205", quantity=1)], technician_id="TECH-104", planned_start=start,
    duration_hours=2.5, incident_id=INC)
transfer = lambda mode: StockTransferDraft(sku="SKF-6205", from_plant="PLANT-B", to_plant="PLANT-A",
                                           quantity=1, mode=mode, incident_id=INC)

def cost(downtime, parts, logistics, labour, penalty):
    tot = downtime + parts + logistics + labour + penalty
    return CostBreakdown(downtime=EUR(downtime), parts=EUR(parts), logistics=EUR(logistics), labour=EUR(labour),
                         late_penalty=EUR(penalty), total=EUR(tot))

options = OptionsResponse(incident_id=INC, needs_human_review=True,
    reason="Diagnosis confidence 0.60 is below 0.70 and the plan creates work orders.",
    options=[
        Option(id="OPT-A", label="Stop now, repair today", summary="Stop Line 3, express the bearing from Plant B, repair as soon as it arrives.",
               actions=[SpeedChangeAction(description="Stop Line 3", line_id="L3", speed_pct=0, start=NOW,
                                          end=NOW + timedelta(hours=8)),
                        StockTransferAction(description="Express 1× SKF-6205 Plant B → A", draft=transfer(TransferMode.EXPRESS)),
                        WorkOrderAction(description="Bearing swap on arrival", draft=wo_draft(NOW + timedelta(hours=6)))],
               cost=cost(16000, 85, 900, 400, 5000), downtime_hours=8, deadline_met=False, energy_kwh=0, co2_kg=0,
               failure_risk_until_repair=0.0, confidence=0.9,
               risks=["Order ORD-5521 misses Friday by ~1 shift", "€5,000 late penalty"], recommended=False),
        Option(id="OPT-B", label="Run at 70%, repair Wednesday night",
               summary="Slow Line 3 to 70% to cut bearing load, standard transfer from Plant B, swap on the night shift.",
               actions=[SpeedChangeAction(description="Line 3 to 70% until repair", line_id="L3", speed_pct=70,
                                          start=NOW, end=NIGHT),
                        StockTransferAction(description="Standard transfer 1× SKF-6205 Plant B → A", draft=transfer(TransferMode.STANDARD)),
                        WorkOrderAction(description="Bearing swap Wed 22:00 (night shift, TECH-104)", draft=wo_draft(NIGHT))],
               cost=cost(1400, 85, 350, 650, 0), downtime_hours=2.5, deadline_met=True, energy_kwh=-140, co2_kg=-58.8,
               failure_risk_until_repair=0.12, confidence=0.6,
               risks=["12% chance bearing fails before Wednesday night", "Diagnosis only 60% certain"], recommended=True),
        Option(id="OPT-C", label="Keep running, order from supplier", summary="Run at full speed and wait 21 days for the supplier.",
               actions=[], cost=cost(28000, 85, 0, 400, 5000), downtime_hours=14, deadline_met=False, energy_kwh=0, co2_kg=0,
               failure_risk_until_repair=0.78, confidence=0.85,
               risks=["78% chance of seizure within 72 h", "Repeat of WO-2024-0871 (14 h stop)"], recommended=False)])
dump("options_INC-0042", options)

incident = Incident(id=INC, title="EQ-3012 spindle bearing wear risk", plant_id="PLANT-A", line_id="L3", asset_id="EQ-3012",
                    trigger=IncidentTrigger.ML_ALERT, severity=Severity.HIGH, status=IncidentStatus.AWAITING_APPROVAL,
                    created_at=NOW, updated_at=NOW + timedelta(seconds=48), diagnosis=diagnosis)
dump("incident_INC-0042", incident)

# ---------------- Scripted agent run (also the LLM fallback) ----------------
events, seq, ts = [], 0, NOW
def ev(agent, typ, msg, **kw):
    global seq, ts
    seq += 1
    ts += timedelta(milliseconds=kw.pop("dt", 900))
    events.append(AgentEvent(incident_id=INC, seq=seq, ts=ts, agent=agent, type=typ, message=msg, **kw))

A = Agent
ev(A.ORCHESTRATOR, EventType.STATUS, "started")
ev(A.ORCHESTRATOR, EventType.THOUGHT, "ML alert on EQ-3012 (risk 78%). Plan: confirm signal, diagnose, find part, schedule, energy, decide.")
ev(A.MONITOR, EventType.STATUS, "started")
ev(A.MONITOR, EventType.TOOL_CALL, "Checking asset health", tool="get_asset_health", args={"asset_id": "EQ-3012"})
ev(A.MONITOR, EventType.TOOL_RESULT, "Risk 78% in 72 h, RUL ~70 h, anomaly 0.91", tool="get_asset_health")
ev(A.MONITOR, EventType.THOUGHT, "Threshold rule not yet breached (4.3 < 4.5 mm/s) — ML is ~31 h ahead of the rule.")
ev(A.MONITOR, EventType.RESULT, "High severity", result=MonitorResult(asset_id="EQ-3012", severity=Severity.HIGH,
   summary="Vibration +34% in 6 h, failure likely within 3 days", risk_72h=0.78, rul_hours=70))
ev(A.DIAGNOSIS, EventType.STATUS, "started")
ev(A.DIAGNOSIS, EventType.TOOL_CALL, "Searching manuals", tool="search_manuals",
   args={"query": "spindle vibration rising bearing", "asset_id": "EQ-3012", "top_k": 5}, dt=1400)
ev(A.DIAGNOSIS, EventType.EVIDENCE, "Manual §7.4 matches the pattern", evidence=[evid["manual"]])
ev(A.DIAGNOSIS, EventType.TOOL_CALL, "Checking past failures", tool="get_maintenance_history", args={"asset_id": "EQ-3012"})
ev(A.DIAGNOSIS, EventType.EVIDENCE, "Same failure on this spindle in Nov 2024", evidence=[evid["wo"]])
ev(A.DIAGNOSIS, EventType.TOOL_CALL, "Checking quality", tool="get_defects", args={"line_id": "L3", "hours": 24})
ev(A.DIAGNOSIS, EventType.EVIDENCE, "Bore defects up 220% — consistent with spindle wobble", evidence=[evid["quality"]])
ev(A.DIAGNOSIS, EventType.THOUGHT, "Kurtosis 5.1 points to bearing damage, but misalignment can't be ruled out without inspection.")
ev(A.DIAGNOSIS, EventType.RESULT, "Rear bearing wear, 60% confident", confidence=0.6, result=diagnosis, dt=1500)
ev(A.SPARE_PARTS, EventType.STATUS, "started")
ev(A.SPARE_PARTS, EventType.TOOL_CALL, "Finding the part in the BOM", tool="get_bom", args={"asset_id": "EQ-3012"})
ev(A.SPARE_PARTS, EventType.TOOL_CALL, "Checking stock", tool="check_stock", args={"sku": "SKF-6205"})
ev(A.SPARE_PARTS, EventType.TOOL_RESULT, "Plant A: 0. Plant B: 4. Supplier: 21 days.", tool="check_stock",
   evidence=[Evidence(kind=EvidenceKind.INVENTORY, ref_id="SKF-6205", title="0 in Plant A, 4 in Plant B",
                      snippet="Supplier SUP-17 lead time 21 days", link="/parts/SKF-6205")])
ev(A.SPARE_PARTS, EventType.RESULT, "Use Plant B stock", result=SparePartsResult(sku="SKF-6205", local_stock=0,
   recommended_source=1, sources=[
       PartSource(plant_id="PLANT-B", available_qty=4, eta_hours=6, cost=EUR(985)),
       PartSource(plant_id="PLANT-B", available_qty=4, eta_hours=20, cost=EUR(435)),
       PartSource(supplier_id="SUP-17", available_qty=50, eta_hours=504, cost=EUR(85))]))
ev(A.SCHEDULER, EventType.STATUS, "started")
ev(A.SCHEDULER, EventType.TOOL_CALL, "Reading order book", tool="get_orders", args={"line_id": "L3"})
ev(A.SCHEDULER, EventType.TOOL_CALL, "Who can swap a bearing on night shift?", tool="find_technicians",
   args={"skill": "bearing_replacement"})
ev(A.SCHEDULER, EventType.THOUGHT, "2,100 drills left. At 70% speed until Wed 22:00 we still finish Friday 14:00.")
ev(A.SCHEDULER, EventType.RESULT, "Night shift window found", result=SchedulerResult(order_id="ORD-5521", windows=[
   RepairWindow(start=NIGHT, end=NIGHT + timedelta(hours=2.5), shift="night", technician_ids=["TECH-104"],
                line_speed_before_pct=70, deadline_met=True, lost_output_units=310)]))
ev(A.ENERGY, EventType.STATUS, "started")
ev(A.ENERGY, EventType.TOOL_CALL, "Reading line energy", tool="get_energy", args={"line_id": "L3", "hours": 24})
ev(A.ENERGY, EventType.THOUGHT, "EQ-3012 draws 8% above baseline — friction from the worn bearing.")
ev(A.ENERGY, EventType.RESULT, "Option B saves ~140 kWh", result=EnergyResult(excess_power_pct=8.0, estimates=[
   EnergyEstimate(option_id="OPT-A", energy_kwh=0, energy_cost=EUR(0), co2_kg=0, note="Restart spike offsets savings"),
   EnergyEstimate(option_id="OPT-B", energy_kwh=-140, energy_cost=EUR(-31), co2_kg=-58.8, note="70% speed + off-peak repair"),
   EnergyEstimate(option_id="OPT-C", energy_kwh=60, energy_cost=EUR(17), co2_kg=25.2, note="Worn bearing keeps wasting power")]))
ev(A.DECISION, EventType.STATUS, "started")
ev(A.DECISION, EventType.THOUGHT, "Option B meets Friday at ~€2.5k vs €22k for A. Confidence 60% → human must approve.")
ev(A.DECISION, EventType.RESULT, "3 options, B recommended", confidence=0.6, result=options, dt=1500)
ev(A.ORCHESTRATOR, EventType.STATUS, "finished")
(OUT / "agent_events_INC-0042.jsonl").write_text("\n".join(e.model_dump_json() for e in events))

# ---------------- After approval ----------------
APPROVED_AT = NOW + timedelta(minutes=4, seconds=10)
dump("approval_INC-0042", Approval(id="APR-0007", incident_id=INC, option_id="OPT-B", decision=DecisionKind.APPROVE,
    reason="Evidence matches 2024 failure; inspect misalignment during swap.", approver_id="USR-lead",
    approver_role=Role.MAINTENANCE_LEAD, approval_token="<signed-jwt>", approved_at=APPROVED_AT, executed_actions=[
        ActionResult(kind="speed_change", ref_id="L3", ok=True, message="Line 3 set to 70% until Wed 22:00"),
        ActionResult(kind="stock_transfer", ref_id="STO-4500012", ok=True, message="1× SKF-6205 Plant B → A, ETA Wed 18:00"),
        ActionResult(kind="work_order", ref_id="WO-2026-0153", ok=True, message="Work order for Wed 22:00, TECH-104")]))
dump("feedback_INC-0042", Feedback(id="FB-0031", incident_id=INC, confirmed=True,
    actual_root_cause="Rear bearing outer race worn; alignment OK", stored_for_retraining=True,
    new_similar_case_confidence=0.82, created_at=NIGHT + timedelta(hours=3)))

# ---------------- Audit (hash chain built by mock_server/audit.py at startup) ----------------
dump("audit_seed", [
    {"actor_type": "agent", "actor": "diagnosis", "action": "llm_call_redacted", "resource": INC,
     "details": {"redacted": ["serial_number", "person_name"], "model": "claude-sonnet-4-5"}},
    {"actor_type": "agent", "actor": "spare_parts", "action": "tool_call", "resource": "check_stock",
     "details": {"sku": "SKF-6205"}},
    {"actor_type": "user", "actor": "USR-op", "action": "approve_denied", "resource": INC,
     "details": {"reason": "role operator lacks approve"}},
    {"actor_type": "user", "actor": "USR-lead", "action": "approve", "resource": INC, "details": {"option_id": "OPT-B"}},
    {"actor_type": "system", "actor": "executor", "action": "create_work_order", "resource": "WO-2026-0153", "details": {}},
])
print("fixtures written:", len(list(OUT.iterdir())))
