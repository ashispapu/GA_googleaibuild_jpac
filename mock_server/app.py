"""Mock backend: the full API contract served from /fixtures.
Frontend and agents build against this from hour 1; Backend core replaces it module by module.
Run: PYTHONPATH=. uvicorn mock_server.app:app --reload --port 8000
"""
import asyncio, hashlib, json, math, random, time, uuid
from datetime import datetime, timezone
from pathlib import Path

import jwt
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import TypeAdapter

from plantguard_contracts.schemas.common import ErrorCode, ErrorResponse, Page, Role
from plantguard_contracts.schemas.auth import TokenRequest, TokenResponse, User
from plantguard_contracts.schemas.plants import Line, Plant
from plantguard_contracts.schemas.ot import Alarm, Signal, SignalSeries, TelemetryMessage
from plantguard_contracts.schemas.erp import Part, StockTransfer, StockTransferCreate, Supplier, TransferStatus
from plantguard_contracts.schemas.mes import LineOEE, ProductionOrder, ScheduleSlot
from plantguard_contracts.schemas.maintenance import WorkOrder, WorkOrderCreate, WorkOrderStatus
from plantguard_contracts.schemas.quality import DefectSummary, Inspection
from plantguard_contracts.schemas.engineering import AssetBom, DocSearchRequest, DocSearchResponse
from plantguard_contracts.schemas.workforce import Shift, Technician
from plantguard_contracts.schemas.energy import Co2Factor, LineConsumption, Tariff
from plantguard_contracts.schemas.ml import AssetHealth, ModelInfo, PredictRequest
from plantguard_contracts.schemas.incidents import AgentEvent, Incident, IncidentCreate, IncidentStatus, OptionsResponse
from plantguard_contracts.schemas.approvals import (ActionResult, Approval, ApprovalRequest, DecisionKind,
                                                    Feedback, FeedbackRequest)
from plantguard_contracts.schemas.audit import AuditEntry, AuditVerify
from plantguard_contracts.auth import InvalidToken, TokenVerifier
from plantguard_contracts.schemas.security import (ROLE_PERMISSIONS, AccessClaims, AuthConfig, AuthMode, Permission,
                                                   RedactionReport)
from plantguard_contracts.schemas.simulator import Scenario, ScenarioResponse
from plantguard_contracts.schemas.workflow import IncidentContext
from plantguard_contracts.context import basic_context

FX = Path(__file__).resolve().parent.parent / "fixtures" / "story"
ACCESS_TTL, APPROVAL_TTL = 15 * 60, 10 * 60


def fx(name):
    p = FX / f"{name}.json"
    if not p.exists():
        raise KeyError(name)
    return json.loads(p.read_text())


def now():
    return datetime.now(timezone.utc)


def err(status, code: ErrorCode, message, **details):
    raise HTTPException(status, detail=ErrorResponse(code=code, message=message, trace_id=uuid.uuid4().hex[:12],
                                                     details=details or None).model_dump(mode="json"))


# ---------------- state ----------------
STATE = {"scene": Scenario.CRISIS, "incident": None, "approval": None, "used_jti": set(), "audit": []}


def audit(actor_type, actor, action, resource, details=None):
    prev = STATE["audit"][-1]["hash"] if STATE["audit"] else "0" * 64
    e = {"seq": len(STATE["audit"]) + 1, "ts": now().isoformat(), "actor_type": actor_type, "actor": actor,
         "action": action, "resource": resource, "details": details or {}, "prev_hash": prev}
    e["hash"] = hashlib.sha256((prev + json.dumps({k: v for k, v in e.items() if k != "prev_hash"},
                                                  sort_keys=True)).encode()).hexdigest()
    STATE["audit"].append(e)
    return e


for s in fx("audit_seed"):
    audit(**s)


# ---------------- auth ----------------
# One verifier for both modes (AUTH_MODE=local|keycloak). P1's backend uses the same class.
VERIFIER = TokenVerifier.from_env()


def decode(token) -> AccessClaims:
    try:
        return VERIFIER.verify(token)
    except InvalidToken:
        err(401, ErrorCode.UNAUTHENTICATED, "Missing or invalid token")


def current(authorization: str = Header(default="")) -> AccessClaims:
    return decode(authorization.removeprefix("Bearer ").strip())


def need(perm: Permission):
    def dep(c: AccessClaims = Depends(current)):
        if perm not in ROLE_PERMISSIONS[c.role]:
            audit("user", c.sub, f"{perm.value}_denied", "-", {"role": c.role.value})
            err(403, ErrorCode.FORBIDDEN, f"Role {c.role.value} lacks {perm.value}")
        return c
    return dep


def scope(c: AccessClaims, plant_id: str):
    if plant_id not in c.plant_ids:
        err(403, ErrorCode.PLANT_SCOPE, f"No access to {plant_id}")


def check_approval(token: str, incident_id: str, action: str):
    try:
        cl = jwt.decode(token, VERIFIER.secret, algorithms=["HS256"])
    except Exception:
        err(403, ErrorCode.APPROVAL_INVALID, "Approval token invalid or expired")
    if cl.get("typ") != "approval" or cl["incident_id"] != incident_id or action not in cl["allowed_actions"]:
        err(403, ErrorCode.APPROVAL_INVALID, "Approval token does not cover this action")


# ---------------- app ----------------
ERR = {s: {"model": ErrorResponse} for s in (401, 403, 404, 409, 422)}
app = FastAPI(title="PlantGuard AI API", version="1.0.0", responses=ERR,
              description="Contract v1. Mock implementation backed by fixtures/story.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(HTTPException)
async def http_exc(_: Request, e: HTTPException):
    body = e.detail if isinstance(e.detail, dict) else ErrorResponse(
        code=ErrorCode.NOT_FOUND if e.status_code == 404 else ErrorCode.VALIDATION, message=str(e.detail),
        trace_id=uuid.uuid4().hex[:12]).model_dump(mode="json")
    return JSONResponse(body, status_code=e.status_code)


@app.exception_handler(KeyError)
async def key_exc(_: Request, e: KeyError):
    return JSONResponse(ErrorResponse(code=ErrorCode.NOT_FOUND, message=f"Not found: {e}",
                                      trace_id=uuid.uuid4().hex[:12]).model_dump(mode="json"), status_code=404)


V = Depends(need(Permission.VIEW_PLANT))
R = {n: APIRouter(prefix=f"/{n}", tags=[n]) for n in
     ["auth", "plants", "ot", "erp", "mes", "maintenance", "quality", "engineering", "workforce", "energy",
      "ml", "incidents", "audit", "security", "simulator"]}

# ---- auth
@R["auth"].get("/config", response_model=AuthConfig)
def auth_config():
    """No auth needed. Tells the UI whether to show the local login form or redirect to Keycloak."""
    return VERIFIER.config()


@R["auth"].post("/token", response_model=TokenResponse)
def login(body: TokenRequest):
    """Local mode only. In keycloak mode the UI logs in at Keycloak (see /auth/config)."""
    if VERIFIER.mode != AuthMode.LOCAL:
        err(501, ErrorCode.NOT_IMPLEMENTED, "Local login is off: log in with Keycloak (GET /auth/config)")
    u = next((u for u in fx("users") if u["username"] == body.username and u["password"] == body.password), None)
    if not u:
        err(401, ErrorCode.UNAUTHENTICATED, "Wrong username or password")
    audit("user", u["id"], "login", "auth")
    user = User(id=u["id"], name=u["name"], role=u["role"], plant_ids=u["plant_ids"],
                permissions=sorted(p.value for p in ROLE_PERMISSIONS[Role(u["role"])]))
    return TokenResponse(access_token=VERIFIER.issue_local(u["id"], u["name"], u["role"], u["plant_ids"]),
                         expires_in=ACCESS_TTL, user=user)


@R["auth"].get("/me", response_model=User)
def me(c: AccessClaims = Depends(current)):
    return User(id=c.sub, name=c.name, role=c.role, plant_ids=c.plant_ids,
                permissions=sorted(p.value for p in ROLE_PERMISSIONS[c.role]))


# ---- plants
def scene_plants():
    plants = fx("plants")
    if STATE["scene"] in (Scenario.CALM, Scenario.RESET, Scenario.RESOLVED):
        for p in plants:
            for l in p["lines"]:
                l["health"] = "ok"
                for s in l["stations"]:
                    for a in s["assets"]:
                        a.update(health="ok", risk_72h=0.04)
    return plants


@R["plants"].get("", response_model=list[Plant])
def list_plants(c=V):
    return [p for p in scene_plants() if p["id"] in c.plant_ids]


@R["plants"].get("/{plant_id}/lines/{line_id}", response_model=Line)
def get_line(plant_id: str, line_id: str, c=V):
    scope(c, plant_id)
    p = next(p for p in scene_plants() if p["id"] == plant_id)
    return next((l for l in p["lines"] if l["id"] == line_id), None) or err(404, ErrorCode.NOT_FOUND, "line")


# ---- OT
@R["ot"].get("/assets/{asset_id}/signals", response_model=list[SignalSeries])
def signals(asset_id: str, signals: list[Signal] = Query(default=[]), minutes: int = 360, c=V):
    data = fx(f"signals_{asset_id}")
    n = max(1, minutes // 5)
    out = [dict(s, points=s["points"][-n:]) for s in data if not signals or s["signal"] in [x.value for x in signals]]
    return out


@R["ot"].get("/alarms", response_model=Page[Alarm])
def alarms(plant_id: str | None = None, active: bool = True, c=V):
    items = [] if STATE["scene"] in (Scenario.CALM, Scenario.RESET) else fx("alarms")
    return {"items": items, "total": len(items)}


@R["ot"].websocket("/stream")
async def ot_stream(ws: WebSocket, token: str, line_id: str = "L3"):
    c = decode(token)
    await ws.accept()
    plant = next(p for p in fx("plants") if p["id"] == "PLANT-A")
    line = next(l for l in plant["lines"] if l["id"] == line_id)
    assets = [a["id"] for s in line["stations"] for a in s["assets"]]
    t0 = time.time()
    try:
        while True:
            el = time.time() - t0
            for a in assets:
                bad = a == "EQ-3012" and STATE["scene"] in (Scenario.WARNING, Scenario.CRISIS, Scenario.FULL_STORY)
                ramp = min(1.0, el / 60) if STATE["scene"] == Scenario.WARNING else (1.0 if bad else 0.0)
                vib = 2.1 + (2.2 * ramp if bad else 0) + random.gauss(0, .06)
                msg = TelemetryMessage(asset_id=a, line_id=line_id, ts=now(), state="run", values={
                    "vibration_rms": round(vib, 3), "bearing_temp": round(58 + (8 * ramp if bad else 0) + random.gauss(0, .2), 2),
                    "spindle_rpm": round(12000 * (0.7 if STATE["approval"] else 1.0) + random.gauss(0, 10)),
                    "motor_current": round(21 + random.gauss(0, .1), 2),
                    "power_kw": round(11 * (1.08 if bad else 1.0) + random.gauss(0, .05), 2)})
                await ws.send_text(msg.model_dump_json())
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


# ---- ERP
@R["erp"].get("/parts/{sku}", response_model=Part)
def part(sku: str, c=V):
    return fx(f"part_{sku}")


@R["erp"].get("/suppliers/{supplier_id}", response_model=Supplier)
def supplier(supplier_id: str, c=V):
    return fx(f"supplier_{supplier_id}")


@R["erp"].post("/stock-transfers", response_model=StockTransfer, status_code=201)
def create_transfer(body: StockTransferCreate, c=V):
    check_approval(body.approval_token, body.incident_id, "stock_transfer")
    eta_h = 6 if body.mode == "express" else 20
    return StockTransfer(id="STO-4500012", sku=body.sku, from_plant=body.from_plant, to_plant=body.to_plant,
                         quantity=body.quantity, mode=body.mode, status=TransferStatus.CREATED,
                         eta=datetime.fromtimestamp(time.time() + eta_h * 3600, timezone.utc),
                         cost={"amount": 900 if body.mode == "express" else 350})


# ---- MES
@R["mes"].get("/orders", response_model=list[ProductionOrder])
def orders(line_id: str | None = None, c=V):
    return [o for o in fx("orders") if not line_id or o["line_id"] == line_id]


@R["mes"].get("/lines/{line_id}/schedule", response_model=list[ScheduleSlot])
def schedule(line_id: str, hours: int = 72, c=V):
    return fx(f"schedule_{line_id}")


@R["mes"].get("/lines/{line_id}/oee", response_model=LineOEE)
def oee(line_id: str, c=V):
    return fx(f"oee_{line_id}")


# ---- Maintenance
@R["maintenance"].get("/assets/{asset_id}/history", response_model=list[WorkOrder])
def history(asset_id: str, c=V):
    return fx(f"history_{asset_id}")


@R["maintenance"].post("/work-orders", response_model=WorkOrder, status_code=201)
def create_wo(body: WorkOrderCreate, c=V):
    check_approval(body.approval_token, body.incident_id, "work_order")
    d = body.model_dump(exclude={"approval_token"})
    return WorkOrder(id="WO-2026-0153", status=WorkOrderStatus.SCHEDULED, created_at=now(), **d)


# ---- Quality / Engineering / Workforce / Energy
@R["quality"].get("/lines/{line_id}/defects", response_model=DefectSummary)
def defects(line_id: str, hours: int = 24, c=V):
    return fx(f"defects_{line_id}")


@R["quality"].get("/inspections", response_model=list[Inspection])
def inspections(line_id: str, c=V):
    return []


@R["engineering"].get("/assets/{asset_id}/bom", response_model=AssetBom)
def bom(asset_id: str, c=V):
    return fx(f"bom_{asset_id}")


@R["engineering"].post("/docs/search", response_model=DocSearchResponse)
def doc_search(body: DocSearchRequest, c=V):
    r = fx("docsearch_bearing")
    return dict(r, query=body.query, chunks=r["chunks"][: body.top_k])


@R["workforce"].get("/shifts", response_model=list[Shift])
def shifts(plant_id: str, c=V):
    scope(c, plant_id)
    return fx(f"shifts_{plant_id}")


@R["workforce"].get("/technicians", response_model=list[Technician])
def technicians(skill: str | None = None, on_shift: bool | None = None, c=V):
    return [t for t in fx("technicians") if (not skill or skill in t["skills"])
            and (on_shift is None or t["on_shift"] == on_shift)]


@R["energy"].get("/lines/{line_id}/consumption", response_model=LineConsumption)
def consumption(line_id: str, hours: int = 24, c=V):
    return fx(f"energy_{line_id}")


@R["energy"].get("/tariffs", response_model=list[Tariff])
def tariffs(c=V):
    return fx("tariffs")


@R["energy"].get("/co2-factor", response_model=Co2Factor)
def co2(c=V):
    return fx("co2_factor")


# ---- ML
@R["ml"].get("/assets/{asset_id}/health", response_model=AssetHealth)
def health(asset_id: str, c=V):
    h = fx(f"health_{asset_id}")
    if STATE["scene"] in (Scenario.CALM, Scenario.RESET, Scenario.RESOLVED):
        h.update(health="ok", risk_72h=0.04, anomaly_score=0.1, rul_hours=2000, predicted_failure_mode=None)
    return h


@R["ml"].post("/predict", response_model=AssetHealth)
def predict(body: PredictRequest, c=V):
    return health(body.window.asset_id, c)


@R["ml"].get("/models", response_model=list[ModelInfo])
def models(c=V):
    return fx("models")


# ---- Incidents
def incident():
    if not STATE["incident"]:
        raise KeyError("INC-0042")
    return STATE["incident"]


@R["incidents"].post("", response_model=Incident, status_code=201)
def create_incident(body: IncidentCreate, c=Depends(need(Permission.RUN_AGENTS))):
    inc = fx("incident_INC-0042")
    inc.update(status="analysing", diagnosis=None, created_at=now().isoformat(), updated_at=now().isoformat())
    STATE["incident"], STATE["approval"] = inc, None
    audit("user", c.sub, "open_incident", inc["id"], {"asset_id": body.asset_id})
    return inc


@R["incidents"].get("/{incident_id}", response_model=Incident)
def get_incident(incident_id: str, c=V):
    return incident()


def scripted_events():
    return [json.loads(l) for l in (FX / "agent_events_INC-0042.jsonl").read_text().splitlines()]


@R["incidents"].get("/{incident_id}/events", response_model=list[AgentEvent])
def events(incident_id: str, after_seq: int = 0, c=V):
    incident()
    return [e for e in scripted_events() if e["seq"] > after_seq]


@R["incidents"].websocket("/{incident_id}/stream")
async def incident_stream(ws: WebSocket, incident_id: str, token: str, speed: float = 1.0):
    """Replays the scripted agent run with real timing. speed=2 plays twice as fast."""
    decode(token)
    await ws.accept()
    prev = None
    try:
        for e in scripted_events():
            ts = datetime.fromisoformat(e["ts"])
            if prev:
                await asyncio.sleep(max(0.05, (ts - prev).total_seconds() / speed))
            prev = ts
            e["ts"] = now().isoformat()
            await ws.send_text(json.dumps(e))
        if STATE["incident"]:
            STATE["incident"].update(status="awaiting_approval", diagnosis=fx("incident_INC-0042")["diagnosis"])
        await ws.close()
    except WebSocketDisconnect:
        pass


@R["incidents"].get("/{incident_id}/context", response_model=IncidentContext)
def incident_context(incident_id: str, c=V):
    """v1.2 get_incident tool: what is known about the incident."""
    inc = STATE["incident"] or fx("incident_INC-0042")
    return basic_context(Incident(**inc))


@R["incidents"].get("/{incident_id}/options", response_model=OptionsResponse)
def options(incident_id: str, c=V):
    incident()
    return fx("options_INC-0042")


@R["incidents"].post("/{incident_id}/approve", response_model=Approval)
def approve(incident_id: str, body: ApprovalRequest, c=Depends(need(Permission.APPROVE))):
    inc = incident()
    if STATE["approval"]:
        err(409, ErrorCode.CONFLICT, "Incident already decided")
    opt = next((o for o in fx("options_INC-0042")["options"] if o["id"] == body.option_id), None) \
        or err(404, ErrorCode.NOT_FOUND, "option")
    if body.decision == DecisionKind.REJECT:
        inc["status"] = "rejected"
        appr = Approval(id="APR-0007", incident_id=incident_id, option_id=body.option_id, decision=body.decision,
                        reason=body.reason, approver_id=c.sub, approver_role=c.role, approval_token=None,
                        approved_at=now(), executed_actions=[])
    else:
        t = int(time.time())
        token = jwt.encode({"typ": "approval", "sub": c.sub, "incident_id": incident_id, "option_id": body.option_id,
                            "allowed_actions": sorted({a["kind"] for a in opt["actions"]}), "jti": uuid.uuid4().hex,
                            "iat": t, "exp": t + APPROVAL_TTL}, VERIFIER.secret, algorithm="HS256")
        executed = [ActionResult(**r) for r in fx("approval_INC-0042")["executed_actions"]] if body.option_id == "OPT-B" \
            else [ActionResult(kind=a["kind"], ok=True, message=a["description"]) for a in opt["actions"]]
        for r in executed:
            audit("system", "executor", f"create_{r.kind}", r.ref_id or "-", {"approved_by": c.sub})
        inc.update(status="executed", selected_option_id=body.option_id)
        appr = Approval(id="APR-0007", incident_id=incident_id, option_id=body.option_id, decision=body.decision,
                        reason=body.reason, approver_id=c.sub, approver_role=c.role, approval_token=token,
                        approved_at=now(), executed_actions=executed)
    audit("user", c.sub, body.decision.value, incident_id, {"option_id": body.option_id})
    STATE["approval"] = appr
    return appr


@R["incidents"].post("/{incident_id}/feedback", response_model=Feedback)
def feedback(incident_id: str, body: FeedbackRequest, c=Depends(need(Permission.SUBMIT_FEEDBACK))):
    incident()["status"] = "resolved"
    audit("user", c.sub, "feedback", incident_id, {"confirmed": body.confirmed})
    f = fx("feedback_INC-0042")
    return dict(f, confirmed=body.confirmed, actual_root_cause=body.actual_root_cause, created_at=now().isoformat())


# ---- Audit + security
@R["audit"].get("", response_model=Page[AuditEntry])
def audit_list(incident_id: str | None = None, c=Depends(need(Permission.VIEW_AUDIT))):
    items = [e for e in STATE["audit"] if not incident_id or e["resource"] == incident_id]
    return {"items": items[::-1], "total": len(items)}


@R["audit"].get("/verify", response_model=AuditVerify)
def audit_verify(c=Depends(need(Permission.VIEW_AUDIT))):
    prev = "0" * 64
    for e in STATE["audit"]:
        h = hashlib.sha256((prev + json.dumps({k: v for k, v in e.items() if k not in ("prev_hash", "hash")} | {"seq": e["seq"]},
                                              sort_keys=True)).encode()).hexdigest()
        if e["prev_hash"] != prev or h != e["hash"]:
            return AuditVerify(valid=False, entries_checked=e["seq"], broken_at_seq=e["seq"])
        prev = e["hash"]
    return AuditVerify(valid=True, entries_checked=len(STATE["audit"]))


@R["security"].get("/redactions", response_model=list[RedactionReport])
def redactions(c=V):
    return [RedactionReport(call_id="LLM-0001", agent="diagnosis", ts=now(), model="claude-sonnet-4-5", chars_in=4120,
                            chars_out=4064, findings=[
            {"kind": "serial_number", "original_hash": hashlib.sha256(b"SN-88231-X").hexdigest(), "placeholder": "<SERIAL_1>"},
            {"kind": "person_name", "original_hash": hashlib.sha256(b"Suresh").hexdigest(), "placeholder": "<PERSON_1>"},
            {"kind": "plc_tag", "original_hash": hashlib.sha256(b"L3.ST2.SPDL2.VIB_RMS").hexdigest(), "placeholder": "<TAG_1>"}])]


# ---- Simulator
@R["simulator"].post("/scenario/{name}", response_model=ScenarioResponse)
def scenario(name: Scenario, c=Depends(need(Permission.RUN_SIMULATOR))):
    STATE["scene"] = name
    if name in (Scenario.RESET, Scenario.CALM):
        STATE.update(incident=None, approval=None)
    elif name in (Scenario.WARNING, Scenario.CRISIS, Scenario.FULL_STORY) and not STATE["incident"]:
        # the monitor "opens" INC-0042 on the ML alert, so the UI can go straight to the war room
        inc = fx("incident_INC-0042")
        inc.update(status="analysing", diagnosis=None, created_at=now().isoformat(), updated_at=now().isoformat())
        STATE.update(incident=inc, approval=None)
        audit("system", "health_watcher", "open_incident", inc["id"], {"trigger": "ml_alert"})
    elif name == Scenario.RESOLVED and STATE["incident"]:
        STATE["incident"]["status"] = "resolved"
    return ScenarioResponse(scenario=name, started=True, message=f"Scene set to {name.value}")


for r in R.values():
    app.include_router(r)
