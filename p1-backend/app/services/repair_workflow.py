"""RepairWorkflow: the incident life cycle after approval, on the simulated plant.

approved option -> work order for the assigned technician (parts reserved / in transit)
technician starts   -> machine stopped for maintenance, line stops            (needs every part in stock at the plant)
technician finishes -> parts consumed from stock, simulator applies the repair physics, machine restarts,
                       the line goes back to normal speed, the machine is watched for [repair] verify_min
check passed        -> technician confirms and closes: work order done, feedback stored, fix added to the manuals index
check failed        -> repair_failed: the technician can ask the agents, or request a new analysis (round 2+, the
                       cause that was repaired without success is excluded and the technician's findings are passed on)
Only the assigned technician can start, finish and close. Everything is linked: incident <-> approval <-> work order
<-> stock transfer <-> stock movements <-> line speed plan <-> verification <-> feedback <-> audit."""
from __future__ import annotations

import json

import logging
from datetime import datetime, timedelta

from plantguard_contracts.schemas.approvals import Approval, Feedback
from plantguard_contracts.schemas.common import ErrorCode, Role
from plantguard_contracts.schemas.incidents import IncidentStatus, IncidentTrigger, WorkOrderAction
from plantguard_contracts.schemas.maintenance import PartUse, WorkOrderCreate, WorkOrderType
from plantguard_contracts.schemas.security import AccessClaims, Permission, ROLE_PERMISSIONS
from plantguard_contracts.schemas.workflow import (IncidentContext, IncidentOverview, JobView, MaintenanceLogEntry,
                                                   ManualRef, PartNeed, PersonRef, ReanalyseRequest, ResolveRequest,
                                                   Stage, VerificationView, WorkCompleteRequest, WorkUpdate)
from plantguard_contracts.schemas.engineering import DocSearchRequest

from app.core.errors import ApiError
from app.repositories.incident_repository import IncidentRecord
from app.sim.clock import iso, parse_ts

log = logging.getLogger("plantguard.workflow")
DONE = (IncidentStatus.RESOLVED, IncidentStatus.REJECTED)


def conflict(msg: str) -> ApiError:
    return ApiError(409, ErrorCode.CONFLICT, msg)


class RepairWorkflow:
    def __init__(self, incidents, it, engine, erp, mes, maintenance, workforce, retriever, audit, clock, cfg):
        self.incidents, self.it, self.engine = incidents, it, engine
        self.erp, self.mes, self.maintenance, self.workforce = erp, mes, maintenance, workforce
        self.retriever, self.audit, self.clock, self.cfg = retriever, audit, clock, cfg
        self.start_run = None  # set by the container: incident_id -> start the agents

    # ------------------------------------------------------------------ people
    def user(self, user_id: str | None) -> PersonRef | None:
        if not user_id:
            return None
        r = self.it.one("SELECT id, name FROM users WHERE id=?", (user_id,))
        return PersonRef(id=r["id"], name=r["name"]) if r else PersonRef(id=user_id, name=user_id)

    def tech(self, tech_id: str | None) -> PersonRef | None:
        if not tech_id:
            return None
        r = self.it.one("SELECT id, name FROM technicians WHERE id=?", (tech_id,))
        return PersonRef(id=r["id"], name=r["name"]) if r else None

    def tech_of(self, claims: AccessClaims) -> str | None:
        return self.it.val("SELECT technician_id FROM users WHERE id=?", (claims.sub,))

    def _assignee(self, rec: IncidentRecord, claims: AccessClaims) -> None:
        if Permission.PERFORM_WORK not in ROLE_PERMISSIONS[claims.role]:
            raise ApiError(403, ErrorCode.FORBIDDEN, f"Role {claims.role.value} lacks perform_work")
        mine = self.tech_of(claims)
        if not mine or mine != rec.incident.assigned_technician_id:
            raise ApiError(403, ErrorCode.FORBIDDEN, "Only the assigned technician can do this")

    # ------------------------------------------------------------------ approval -> job
    def after_approval(self, rec: IncidentRecord, approval: Approval) -> None:
        inc = rec.incident
        wo_id = next((a.ref_id for a in approval.executed_actions if a.kind == "work_order" and a.ok and a.ref_id), None)
        if wo_id is None:
            wo_id = self._fallback_work_order(rec, approval)
        if wo_id:
            wo = self.maintenance.get(wo_id)
            inc.work_order_id, inc.assigned_technician_id = wo_id, wo.technician_id
            who = self.signers_text(approval) or \
                f"approved by {(self.user(approval.approver_id) or PersonRef(id='?', name='?')).name}"
            self._update(wo_id, None, "assigned", f"Option {approval.option_id[-1]} {who}. "
                         f"Assigned to {(self.tech(wo.technician_id) or PersonRef(id='?', name='?')).name}.")

    @staticmethod
    def signers_text(approval: Approval) -> str:
        """'approved by Anita, released by Dev (Plant B), co-signed by Karan' from the approval chain."""
        verbs = {"requesting_plant": "approved by", "manager": "co-signed by"}
        bits = []
        for s in approval.signoffs:
            if s.decision != "approved" or not s.signed_name:
                continue
            name = s.signed_name.split(" (")[0]
            verb = verbs.get(s.key, f"released by")
            bits.append(f"{verb} {name}" + (f" ({s.label.replace('Release by ', '')})" if s.key.startswith("source_plant:") else ""))
        # one person signing several steps is listed once per step; that is what the record says
        return ", ".join(bits)

    def _fallback_work_order(self, rec: IncidentRecord, approval: Approval) -> str | None:
        """An approved option without a repair job (e.g. 'keep running, order from the supplier'): the CMMS still
        needs a job for when the part arrives."""
        inc = rec.incident
        diag = inc.diagnosis
        sku = (diag.required_parts or [None])[0] if diag else None
        if not sku:
            return None
        part = self.it.one("SELECT p.repair_skill, s.lead_time_days FROM parts p JOIN suppliers s ON s.id=p.supplier_id "
                           "WHERE sku=?", (sku,))
        techs = self.workforce.find_technicians(part["repair_skill"] if part else None, None, inc.plant_id) or \
            self.workforce.find_technicians(None, None, inc.plant_id)
        if not techs:
            return None
        start = self.clock() + timedelta(days=part["lead_time_days"] if part else 1)
        wo = self.maintenance.create_work_order(WorkOrderCreate(
            asset_id=inc.asset_id, type=WorkOrderType.PREDICTIVE, title=f"Repair {inc.asset_id} when the part arrives",
            root_cause=diag.root_cause, parts=[PartUse(sku=sku, quantity=1)], technician_id=techs[0].id,
            planned_start=start, duration_hours=2.5, incident_id=inc.id, approval_token="system"))
        self.audit.log("system", "executor", "create_work_order", wo.id, {"incident_id": inc.id, "approved_by": approval.approver_id})
        return wo.id

    # ------------------------------------------------------------------ the repair
    async def start_work(self, incident_id: str, claims: AccessClaims, note: str | None) -> JobView:
        rec = self.incidents.scoped_record(incident_id, claims)
        self._assignee(rec, claims)
        inc = rec.incident
        if inc.status != IncidentStatus.EXECUTED:
            raise conflict(f"Incident is {inc.status.value}; the repair cannot start now")
        parts = self.parts(rec)
        missing = [p for p in parts if not p.ready]
        if missing and self.cfg.require_parts_in_stock:
            p = missing[0]
            eta = f", arriving {iso(p.transfer_eta)}" if p.transfer_eta else ""
            raise conflict(f"Part {p.sku} is not in stock at {inc.plant_id} yet{eta}")
        now = self.clock()
        self.it.x("UPDATE work_orders SET status='in_progress', started_at=? WHERE id=?", (iso(now), inc.work_order_id))
        self.engine.set_state(inc.asset_id, "stopped", "maintenance")
        self._update(inc.work_order_id, claims.sub, "started", note or "Repair started; machine locked out")
        inc.status, inc.updated_at = IncidentStatus.IN_REPAIR, now
        self.audit.log("user", claims.sub, "start_repair", inc.work_order_id, {"incident_id": incident_id})
        await self.incidents.publish_status(rec)
        return self.job(incident_id, claims)

    async def add_note(self, incident_id: str, claims: AccessClaims, note: str) -> JobView:
        rec = self.incidents.scoped_record(incident_id, claims)
        self._assignee(rec, claims)
        if not rec.incident.work_order_id:
            raise conflict("No work order yet")
        self._update(rec.incident.work_order_id, claims.sub, "note", note.strip()[:1000])
        self.audit.log("user", claims.sub, "work_note", rec.incident.work_order_id, {"incident_id": incident_id})
        return self.job(incident_id, claims)

    async def complete_work(self, incident_id: str, claims: AccessClaims, req: WorkCompleteRequest) -> JobView:
        rec = self.incidents.scoped_record(incident_id, claims)
        self._assignee(rec, claims)
        inc = rec.incident
        if inc.status != IncidentStatus.IN_REPAIR:
            raise conflict(f"Incident is {inc.status.value}; start the repair first")
        wo = self.maintenance.get(inc.work_order_id)
        used = req.parts_used if req.parts_used is not None else wo.parts
        now = self.clock()
        with self.it.tx() as c:
            for p in used:
                row = c.execute("SELECT quantity FROM stock WHERE sku=? AND plant_id=?", (p.sku, inc.plant_id)).fetchone()
                if row is None or row["quantity"] < p.quantity:
                    raise conflict(f"Not enough {p.sku} in stock at {inc.plant_id}")
                c.execute("UPDATE stock SET quantity=quantity-? WHERE sku=? AND plant_id=?", (p.quantity, p.sku, inc.plant_id))
                c.execute("INSERT INTO stock_movements(sku, plant_id, qty_delta, reason, work_order_id, ts) VALUES(?,?,?,?,?,?)",
                          (p.sku, inc.plant_id, -p.quantity, "consumption", wo.id, iso(now)))
            c.execute("DELETE FROM work_order_parts WHERE work_order_id=?", (wo.id,))
            for p in used:
                c.execute("INSERT INTO work_order_parts(work_order_id, sku, quantity) VALUES(?,?,?)", (wo.id, p.sku, p.quantity))
            c.execute("UPDATE work_orders SET findings=? WHERE id=?", (req.findings.strip()[:2000], wo.id))
        fixed, _truth = self.engine.apply_repair(inc.asset_id, [p.sku for p in used])  # truth stays in the OT world
        still_broken = self.engine.condition(inc.asset_id)
        self.engine.clear_alarms(inc.asset_id)
        self.engine.set_state(inc.asset_id, "fault" if still_broken and still_broken["progress"] >= 1.0 else "run",
                              "breakdown" if still_broken and still_broken["progress"] >= 1.0 else "production")
        self.mes.clear_speed_plans(incident_id)
        ends = self.engine.start_verification(incident_id, wo.id)
        parts_txt = ", ".join(f"{p.quantity}× {p.sku}" for p in used) or "no parts"
        self._update(wo.id, claims.sub, "completed", f"Repair done ({parts_txt}). {req.findings.strip()[:300]}")
        self._update(wo.id, None, "verification",
                     f"Machine restarted; watching the readings until {parse_ts(ends):%a %d %b %H:%M} (plant time)")
        inc.status, inc.updated_at = IncidentStatus.VERIFYING, now
        self.audit.log("user", claims.sub, "complete_repair", wo.id, {"incident_id": incident_id, "parts": parts_txt})
        await self.incidents.publish_status(rec)
        return self.job(incident_id, claims)

    async def on_verification(self, payload: dict) -> None:
        try:
            rec = self.incidents.repo.get(payload["incident_id"])
        except KeyError:
            return
        inc = rec.incident
        if inc.status != IncidentStatus.VERIFYING:
            return
        wo_id = payload["work_order_id"]
        if payload["passed"]:
            self._update(wo_id, None, "verification", f"Check passed: {payload['details']}. The technician can close it.")
        else:
            self._update(wo_id, None, "verification", f"Check failed: {payload['details']}.")
            self.it.x("UPDATE work_orders SET status='done', completed_at=?, outcome='not_fixed', "
                      "root_cause='Suspected: ' || COALESCE(root_cause, '?') || ' (repair did not fix it)' WHERE id=?",
                      (iso(self.clock()), wo_id))
            inc.status = IncidentStatus.REPAIR_FAILED
        inc.updated_at = self.clock()
        self.audit.log("system", "verifier", "repair_check_passed" if payload["passed"] else "repair_check_failed",
                       wo_id, {"incident_id": inc.id, "details": payload["details"]})
        await self.incidents.publish_status(rec)

    async def resolve(self, incident_id: str, req: ResolveRequest, claims: AccessClaims) -> Feedback:
        rec = self.incidents.scoped_record(incident_id, claims)
        self._assignee(rec, claims)
        inc = rec.incident
        v = self.verification(incident_id)
        if inc.status != IncidentStatus.VERIFYING or v is None or v.result != "passed":
            raise conflict("The incident can be closed only after the machine check has passed")
        now = self.clock()
        notes = (req.notes or "").strip()
        self.it.x("UPDATE work_orders SET status='done', completed_at=?, outcome='fixed', root_cause=?, "
                  "findings=TRIM(COALESCE(findings, '') || ' ' || ?) WHERE id=?",
                  (iso(now), req.actual_root_cause.strip()[:300], notes, inc.work_order_id))
        self._update(inc.work_order_id, claims.sub, "closed",
                     f"Closed. Root cause: {req.actual_root_cause.strip()[:200]}"
                     + ("" if req.confirmed else " (differs from the AI's diagnosis)"))
        fb = self.incidents.make_feedback(rec, req.confirmed, req.actual_root_cause)
        rec.feedback = fb
        inc.status, inc.updated_at, inc.resolved_at = IncidentStatus.RESOLVED, now, now
        self.incidents.repo.save_feedback(rec, fb, inc.assigned_technician_id, claims.sub)
        try:
            self.retriever.index_work_order(inc.work_order_id, iso(now))
        except Exception:
            log.exception("could not index %s", inc.work_order_id)
        self.audit.log("user", claims.sub, "feedback", incident_id,
                       {"confirmed": req.confirmed, "technician_id": inc.assigned_technician_id, "incident_id": incident_id})
        self.audit.log("user", claims.sub, "close_incident", incident_id, {"incident_id": incident_id,
                                                                          "work_order_id": inc.work_order_id})
        await self.incidents.publish_status(rec)
        await self.incidents.runtime.on_feedback(inc, fb)
        return fb

    async def reanalyse(self, incident_id: str, req: ReanalyseRequest, claims: AccessClaims) -> IncidentOverview:
        rec = self.incidents.scoped_record(incident_id, claims)
        inc = rec.incident
        if inc.status != IncidentStatus.REPAIR_FAILED:
            raise conflict(f"Incident is {inc.status.value}; a new analysis is only for a repair that did not work")
        is_lead = Permission.APPROVE in ROLE_PERMISSIONS[claims.role]
        if not is_lead:
            self._assignee(rec, claims)
        wo = self.maintenance.get(inc.work_order_id) if inc.work_order_id else None
        mode = inc.diagnosis.failure_mode if inc.diagnosis else None
        recommended = set(inc.diagnosis.required_parts if inc.diagnosis else [])
        fitted = {p.sku for p in (wo.parts if wo else [])}
        tried = bool(recommended & fitted)  # the recommended fix was really done -> that cause is ruled out
        excluded = list(dict.fromkeys([*inc.excluded_failure_modes, *([mode] if tried and mode and mode != "unknown" else [])]))
        note = "" if tried or not recommended else (
            f"The recommended part ({', '.join(sorted(recommended))}) was not fitted; "
            f"{', '.join(sorted(fitted)) or 'nothing'} was fitted instead, so the first diagnosis is still open.")
        findings = " ".join(x for x in [(wo.findings if wo else None), req.reason.strip(), note] if x)
        self.audit.log("user", claims.sub, "reanalyse", incident_id, {"incident_id": incident_id, "round": inc.round + 1,
                                                                     "excluded": excluded})
        if wo:
            self._update(wo.id, claims.sub, "reanalysis", f"New analysis requested: {req.reason.strip()[:300]}")
        inc.round += 1
        inc.excluded_failure_modes = excluded
        inc.previous_findings = findings[:1000] or None
        inc.status = IncidentStatus.ANALYSING
        inc.diagnosis, inc.selected_option_id = None, None
        inc.work_order_id, inc.assigned_technician_id, inc.approved_by = None, None, None
        inc.updated_at = self.clock()
        rec.options, rec.approval = None, None
        await self.incidents.publish_status(rec)
        if self.start_run:
            self.start_run(incident_id)
        return self.overview(rec)

    # ------------------------------------------------------------------ views
    def verification(self, incident_id: str) -> VerificationView | None:
        r = self.it.one("SELECT * FROM repair_verifications WHERE incident_id=? ORDER BY id DESC LIMIT 1", (incident_id,))
        if r is None:
            return None
        return VerificationView(result=r["result"], started_at=parse_ts(r["started_at"]), ends_at=parse_ts(r["ends_at"]),
                                details=r["details"], max_risk=r["max_risk"])

    def stage(self, rec: IncidentRecord, v: VerificationView | None = None) -> Stage:
        s = rec.incident.status
        if rec.incident.question:
            return Stage.ANSWERED if s == IncidentStatus.RESOLVED else Stage.AI_ANALYSING
        if s == IncidentStatus.VERIFYING:
            v = v or self.verification(rec.incident.id)
            return Stage.READY_TO_CLOSE if v and v.result == "passed" else Stage.VERIFYING
        return {IncidentStatus.OPEN: Stage.AI_ANALYSING, IncidentStatus.ANALYSING: Stage.AI_ANALYSING,
                IncidentStatus.AWAITING_APPROVAL: Stage.AWAITING_APPROVAL, IncidentStatus.APPROVED: Stage.WAITING_TECHNICIAN,
                IncidentStatus.AWAITING_SIGNOFF: Stage.AWAITING_SIGNOFF,
                IncidentStatus.EXECUTED: Stage.WAITING_TECHNICIAN, IncidentStatus.IN_REPAIR: Stage.TECHNICIAN_WORKING,
                IncidentStatus.REPAIR_FAILED: Stage.REPAIR_FAILED, IncidentStatus.REJECTED: Stage.REJECTED,
                IncidentStatus.RESOLVED: Stage.RESOLVED}[s]

    def overview(self, rec: IncidentRecord) -> IncidentOverview:
        inc = rec.incident
        eq = self.it.one("SELECT e.name, l.name AS line_name FROM equipment e JOIN lines l ON l.plant_id=e.plant_id "
                         "AND l.id=e.line_id WHERE asset_id=?", (inc.asset_id,))
        v = self.verification(inc.id) if not inc.question else None
        wo = self.it.one("SELECT status, planned_start FROM work_orders WHERE id=?", (inc.work_order_id,)) \
            if inc.work_order_id else None
        option = next((o for o in (rec.options.options if rec.options else []) if o.id == inc.selected_option_id), None)
        stage = self.stage(rec, v)
        resolution = None
        if inc.status == IncidentStatus.RESOLVED and inc.work_order_id:
            w = self.it.one("SELECT root_cause FROM work_orders WHERE id=?", (inc.work_order_id,))
            resolution = w["root_cause"] if w else None
        n_q = sum(1 for r in self.incidents.repo.all() if r.incident.parent_id == inc.id)
        return IncidentOverview(
            incident=inc, asset_name=eq["name"] if eq else inc.asset_id, line_name=eq["line_name"] if eq else inc.line_id,
            stage=stage, status_tag="done" if inc.status in DONE else "in_progress",
            ai_working=inc.status in (IncidentStatus.OPEN, IncidentStatus.ANALYSING),
            approval=self.user(inc.approved_by), approved_at=rec.approval.approved_at if rec.approval else None,
            option_id=inc.selected_option_id, option_label=option.label if option else None,
            technician=self.tech(inc.assigned_technician_id), work_order_id=inc.work_order_id,
            work_order_status=wo["status"] if wo else None,
            planned_start=parse_ts(wo["planned_start"]) if wo and wo["planned_start"] else None,
            verification=v, machine_state=self.engine.machine_state(inc.asset_id)[0], questions=n_q, resolution=resolution)

    def list(self, claims: AccessClaims, scope: str = "all", mine: bool = False, kind: str = "incident",
             plant_id: str | None = None, parent_id: str | None = None) -> list[IncidentOverview]:
        tech = self.tech_of(claims) if mine else None
        out = []
        for rec in self.incidents.visible(claims):
            inc = rec.incident
            if kind == "incident" and inc.question:
                continue
            if kind == "question" and not inc.question:
                continue
            if plant_id and inc.plant_id != plant_id:
                continue
            if parent_id and inc.parent_id != parent_id:
                continue
            done = inc.status in DONE
            if (scope == "open" and done) or (scope == "done" and not done):
                continue
            if mine:
                if kind == "question" and inc.opened_by != claims.sub:
                    continue
                if kind == "incident" and (not tech or inc.assigned_technician_id != tech):
                    continue
            out.append(self.overview(rec))
        out.sort(key=lambda o: o.incident.updated_at, reverse=True)
        return out

    def parts(self, rec: IncidentRecord) -> list[PartNeed]:
        inc = rec.incident
        if not inc.work_order_id:
            return []
        out = []
        for p in self.it.q("SELECT w.sku, w.quantity, p.description FROM work_order_parts w JOIN parts p ON p.sku=w.sku "
                           "WHERE work_order_id=?", (inc.work_order_id,)):
            have = self.it.val("SELECT quantity FROM stock WHERE sku=? AND plant_id=?", (p["sku"], inc.plant_id), 0)
            tr = self.it.one("SELECT * FROM stock_transfers WHERE incident_id=? AND sku=? ORDER BY created_at DESC LIMIT 1",
                             (inc.id, p["sku"]))
            po = self.it.one("SELECT * FROM purchase_orders WHERE incident_id=? AND sku=? ORDER BY created_at DESC LIMIT 1",
                             (inc.id, p["sku"]))
            out.append(PartNeed(sku=p["sku"], description=p["description"], quantity=p["quantity"], in_stock=have,
                                transfer_id=tr["id"] if tr else None, transfer_status=tr["status"] if tr else None,
                                transfer_eta=parse_ts(tr["eta"]) if tr else None, ready=have >= p["quantity"],
                                purchase_order_id=po["id"] if po else None, purchase_order_status=po["status"] if po else None,
                                purchase_order_eta=parse_ts(po["eta"]) if po else None))
        return out

    def manuals(self, rec: IncidentRecord) -> list[ManualRef]:
        inc = rec.incident
        mode = inc.diagnosis.failure_mode if inc.diagnosis else None
        query = " ".join(x for x in [mode.replace("_", " ") if mode else "", inc.diagnosis.root_cause if inc.diagnosis else "",
                                     "replacing procedure"] if x)
        refs: list[ManualRef] = []
        seen = set()
        for ev in (inc.diagnosis.evidence if inc.diagnosis else []):
            if ev.kind.value == "manual" and "#" in ev.ref_id:
                doc, sec = ev.ref_id.split("#", 1)
                row = self.it.one("SELECT c.heading, c.page, d.title FROM doc_chunks c JOIN documents d ON d.id=c.doc_id "
                                  "WHERE c.doc_id=? AND c.section=?", (doc, sec))
                if row and (doc, sec) not in seen:
                    seen.add((doc, sec))
                    refs.append(ManualRef(doc_id=doc, title=row["title"], section=sec, heading=row["heading"],
                                          page=row["page"], why="Cited by the diagnosis"))
        sku = (inc.diagnosis.required_parts or [None])[0] if inc.diagnosis else None
        queries = [q for q in (query, f"replacing {sku} procedure steps" if sku else "") if q.strip()]
        for q in queries:
            for c in self.retriever.search(DocSearchRequest(query=q, asset_id=inc.asset_id, top_k=6)).chunks:
                if c.doc_id.startswith("WO-") or (c.doc_id, c.section) in seen or not c.section[:1] in "78":
                    continue  # troubleshooting (7.x) and procedures (8.x) are what a technician needs
                row = self.it.one("SELECT heading FROM doc_chunks WHERE doc_id=? AND section=?", (c.doc_id, c.section))
                seen.add((c.doc_id, c.section))
                refs.append(ManualRef(doc_id=c.doc_id, title=c.title, section=c.section,
                                      heading=row["heading"] if row else c.section, page=c.page,
                                      why="Repair procedure" if c.section.startswith("8") else "Troubleshooting"))
                break
        return refs[:4]

    def updates(self, wo_id: str | None) -> list[WorkUpdate]:
        if not wo_id:
            return []
        return [WorkUpdate(ts=parse_ts(r["ts"]), author=self.user(r["author_id"]), kind=r["kind"], note=r["note"])
                for r in self.it.q("SELECT * FROM work_order_updates WHERE work_order_id=? ORDER BY id", (wo_id,))]

    def job(self, incident_id: str, claims: AccessClaims) -> JobView:
        rec = self.incidents.scoped_record(incident_id, claims)
        inc = rec.incident
        ov = self.overview(rec)
        parts = self.parts(rec)
        mine = bool(self.tech_of(claims)) and self.tech_of(claims) == inc.assigned_technician_id
        missing = [p for p in parts if not p.ready]
        blocked = None
        if inc.status == IncidentStatus.EXECUTED and missing and self.cfg.require_parts_in_stock:
            p = missing[0]
            if p.transfer_id:
                blocked = f"Waiting for {p.sku} (transfer {p.transfer_id}, {p.transfer_status.value})"
            elif p.purchase_order_id:
                blocked = f"Waiting for {p.sku} (purchase order {p.purchase_order_id} from the supplier, due " \
                          f"{p.purchase_order_eta:%a %d %b %H:%M})"
            else:
                blocked = f"Waiting for {p.sku} (not in stock)"
        option = next((o for o in (rec.options.options if rec.options else []) if o.id == inc.selected_option_id), None)
        wo = self.maintenance.get(inc.work_order_id) if inc.work_order_id else None
        return JobView(
            incident=inc, overview=ov, diagnosis=inc.diagnosis, option=option,
            approval_reason=rec.approval.reason if rec.approval else None, work_order=wo,
            updates=self.updates(inc.work_order_id), parts=parts, manuals=self.manuals(rec),
            can_start=mine and inc.status == IncidentStatus.EXECUTED and blocked is None, blocked_reason=blocked,
            can_complete=mine and inc.status == IncidentStatus.IN_REPAIR,
            can_close=mine and ov.stage == Stage.READY_TO_CLOSE,
            can_reanalyse=inc.status == IncidentStatus.REPAIR_FAILED and (
                mine or Permission.APPROVE in ROLE_PERMISSIONS[claims.role]),
            is_assignee=mine)

    def timeline(self, incident_id: str, claims: AccessClaims) -> list[WorkUpdate]:
        """Everything that happened to an incident, from every table that links to it."""
        rec = self.incidents.scoped_record(incident_id, claims)
        inc = rec.incident
        out = [WorkUpdate(ts=inc.created_at, author=self.user(inc.opened_by) if inc.opened_by != "system" else None,
                          kind="opened", note=f"Incident opened ({inc.trigger.value.replace('_', ' ')})")]
        for ev in rec.events:
            if ev.type.value == "result" and ev.agent.value == "decision":
                out.append(WorkUpdate(ts=ev.ts, kind="options", note=f"AI team suggested options (round {ev.round or 1})"))
        for a in self.it.q("SELECT payload FROM approvals WHERE incident_id=? ORDER BY approved_at", (incident_id,)):
            ap = Approval.model_validate_json(a["payload"])
            if ap.executed_actions or ap.decision.value == "reject":
                out.append(WorkUpdate(ts=ap.approved_at, author=self.user(ap.approver_id), kind=ap.decision.value,
                                      note=f"{'Approved' if ap.decision.value == 'approve' else 'Rejected'} option "
                                           f"{ap.option_id[-1]}: {ap.reason}"))
        # v1.2: approval chain and options chat, from the (hash-chained) audit log
        for a in self.it.q("SELECT ts, actor, action, details FROM audit_log WHERE resource=? AND action IN "
                           "('sign_option', 'decline_option', 'suggest_option') ORDER BY seq", (incident_id,)):
            d = json.loads(a["details"])
            letter = str(d.get("option_id", "?"))[-1]
            labels = ", ".join(d.get("labels") or [])
            note = {"sign_option": f"Signed option {letter}" + (f" as {labels}" if labels else ""),
                    "decline_option": f"Declined option {letter}" + (f" ({labels})" if labels else "")
                                      + (f": {d['reason']}" if d.get("reason") else ""),
                    "suggest_option": f"Suggested a new plan in the options chat: option {letter} "
                                      f"(€{float(d.get('total_eur', 0)):,.0f})"}[a["action"]]
            kind = {"sign_option": "approve", "decline_option": "reject", "suggest_option": "options"}[a["action"]]
            out.append(WorkUpdate(ts=parse_ts(a["ts"]), author=self.user(a["actor"]), kind=kind, note=note))
        for r in self.it.q("SELECT ts, sku, qty_delta, purchase_order_id FROM stock_movements WHERE purchase_order_id IN "
                           "(SELECT id FROM purchase_orders WHERE incident_id=?)", (incident_id,)):
            out.append(WorkUpdate(ts=parse_ts(r["ts"]), kind="parts",
                                  note=f"{r['qty_delta']}× {r['sku']} received from the supplier ({r['purchase_order_id']})"))
        for wo in self.it.q("SELECT id FROM work_orders WHERE incident_id=? ORDER BY created_at", (incident_id,)):
            out += self.updates(wo["id"])
        for r in self.it.q("SELECT ts, sku, qty_delta, reason FROM stock_movements WHERE transfer_id IN "
                           "(SELECT id FROM stock_transfers WHERE incident_id=?) AND reason='transfer_in'", (incident_id,)):
            out.append(WorkUpdate(ts=parse_ts(r["ts"]), kind="parts", note=f"{r['qty_delta']}× {r['sku']} delivered"))
        if inc.resolved_at and not inc.question:
            out.append(WorkUpdate(ts=inc.resolved_at, kind="resolved", note="Incident closed by the technician"))
        # some tables keep whole seconds: within one second, show things in the order they must have happened
        rank = {"opened": 0, "options": 1, "reject": 2, "approve": 3, "created": 4, "assigned": 5}
        out.sort(key=lambda u: (u.ts.replace(microsecond=0), rank.get(u.kind, 6), u.ts))
        return out

    def context(self, incident_id: str) -> IncidentContext:
        rec = self.incidents.repo.get(incident_id)
        inc = rec.incident
        wo = self.maintenance.get(inc.work_order_id) if inc.work_order_id else None
        option = next((o for o in (rec.options.options if rec.options else []) if o.id == inc.selected_option_id), None)
        v = self.verification(inc.id)
        notes = [u.note for u in self.updates(inc.work_order_id) if u.kind in ("note", "completed", "started")]
        if inc.previous_findings:
            notes.insert(0, f"Previous round: {inc.previous_findings}")
        return IncidentContext(
            incident_id=inc.id, asset_id=inc.asset_id, status=inc.status, stage=self.stage(rec, v), round=inc.round,
            diagnosis=inc.diagnosis.root_cause if inc.diagnosis else None,
            failure_mode=inc.diagnosis.failure_mode if inc.diagnosis else None,
            confidence=inc.diagnosis.confidence if inc.diagnosis else None,
            chosen_option=f"{option.id}: {option.label}" if option else None,
            approved_by=(self.user(inc.approved_by).name if inc.approved_by else None),
            technician_id=inc.assigned_technician_id, work_order_id=inc.work_order_id,
            work_order_status=wo.status.value if wo else None, parts=[f"{p.quantity}x {p.sku}" for p in (wo.parts if wo else [])],
            technician_notes=notes[-8:],
            verification=f"{v.result}: {v.details or ''}".strip() if v else None,
            excluded_failure_modes=inc.excluded_failure_modes)

    def maintenance_log(self, claims: AccessClaims, limit: int = 100) -> list[MaintenanceLogEntry]:
        out = []
        for rec in self.incidents.visible(claims):
            inc = rec.incident
            if inc.question or inc.status != IncidentStatus.RESOLVED or not inc.resolved_at:
                continue
            wos = [r["id"] for r in self.it.q("SELECT id FROM work_orders WHERE incident_id=? ORDER BY created_at", (inc.id,))]
            final = self.maintenance.get(inc.work_order_id) if inc.work_order_id else None
            fb = rec.feedback
            eq = self.it.one("SELECT name FROM equipment WHERE asset_id=?", (inc.asset_id,))
            down = None
            if final:
                w = self.it.one("SELECT started_at, completed_at FROM work_orders WHERE id=?", (final.id,))
                if w and w["started_at"] and w["completed_at"]:
                    down = round((parse_ts(w["completed_at"]) - parse_ts(w["started_at"])).total_seconds() / 3600, 1)
            option = next((o for o in (rec.options.options if rec.options else []) if o.id == inc.selected_option_id), None)
            out.append(MaintenanceLogEntry(
                incident_id=inc.id, asset_id=inc.asset_id, asset_name=eq["name"] if eq else inc.asset_id,
                plant_id=inc.plant_id, line_id=inc.line_id, opened_at=inc.created_at, resolved_at=inc.resolved_at,
                rounds=inc.round, ai_root_cause=inc.diagnosis.root_cause if inc.diagnosis else None,
                actual_root_cause=fb.actual_root_cause if fb else (final.root_cause if final else None),
                ai_confirmed=fb.confirmed if fb else None,
                solution=(f"{final.title}" + (f": {final.findings}" if final and final.findings else "")) if final else
                (option.label if option else "Closed"),
                parts=final.parts if final else [], work_orders=wos, technician=self.tech(inc.assigned_technician_id),
                approved_by=self.user(inc.approved_by), option_id=inc.selected_option_id, downtime_hours=down,
                time_to_fix_hours=round((inc.resolved_at - inc.created_at).total_seconds() / 3600, 1)))
        out.sort(key=lambda e: e.resolved_at, reverse=True)
        return out[:limit]

    # ------------------------------------------------------------------ helpers
    def _update(self, wo_id: str | None, author: str | None, kind: str, note: str) -> None:
        if not wo_id:
            return
        self.it.x("INSERT INTO work_order_updates(work_order_id, ts, author_id, kind, note) VALUES(?,?,?,?,?)",
                  (wo_id, iso(self.clock()), author, kind, note))

    async def on_alarm(self, payload: dict) -> None:
        """A critical OT alarm (or a breakdown) opens an incident if none is open for the machine yet."""
        if payload.get("severity") != "critical" or self.engine.event_before_reset(payload["asset_id"], payload.get("ts")):
            return
        from plantguard_contracts.schemas.incidents import IncidentCreate
        try:
            await self.incidents.open(IncidentCreate(asset_id=payload["asset_id"], trigger=IncidentTrigger.ALARM,
                                                     alarm_id=payload.get("alarm_id")), ("system", "alarm_watcher"))
        except Exception:
            log.exception("could not open incident from alarm")


__all__ = ["RepairWorkflow", "Role", "datetime"]
