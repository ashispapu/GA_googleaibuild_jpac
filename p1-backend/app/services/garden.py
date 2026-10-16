"""PlantGuard Garden: where each incident's agent is in the system right now.

Every agent event is mapped to the system it touches (a tool call to ERP puts the agent on the ERP node, reading the
historian puts it on the historian, thinking puts it on the LLM ...). Between analyses the incident sits with the
people/system that owns the next step (lead for approval, technician for the job, ML for the repair check).
Published on the bus channel `garden.activity`; GET /garden is the snapshot, WS /garden/stream the live feed."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta

from plantguard_contracts.schemas.incidents import AgentEvent, EventType, IncidentStatus
from plantguard_contracts.schemas.security import AccessClaims
from plantguard_contracts.schemas.workflow import GardenAgent, GardenSnapshot, Stage

log = logging.getLogger("plantguard.garden")

TOOL_NODE = {
    "get_asset_health": "ml", "get_signals": "historian", "get_alarms": "scada", "search_manuals": "vdb",
    "get_maintenance_history": "cmms", "get_defects": "qms", "get_bom": "plm", "check_stock": "erp",
    "get_supplier": "suppliers", "get_orders": "mes", "get_schedule": "mes", "find_technicians": "workforce",
    "get_energy": "ems", "get_tariffs": "ems", "get_co2_factor": "ems", "get_incident": "incidents",
    "create_work_order": "cmms", "create_stock_transfer": "erp",
}
TOOL_TEXT = {
    "get_asset_health": "reading the ML health score", "get_signals": "reading sensor history",
    "get_alarms": "checking alarms", "search_manuals": "searching the manuals",
    "get_maintenance_history": "reading past repairs", "get_defects": "checking quality rejects",
    "get_bom": "reading the parts list", "check_stock": "checking stock", "get_supplier": "asking the supplier",
    "get_orders": "reading the order book", "get_schedule": "reading the line schedule",
    "find_technicians": "finding a technician", "get_energy": "reading energy use", "get_tariffs": "reading tariffs",
    "get_co2_factor": "reading the CO2 factor", "get_incident": "reading the incident record",
    "create_work_order": "creating the work order", "create_stock_transfer": "booking the part transfer",
}
AGENT_NAME = {"orchestrator": "Coordinator", "monitor": "Monitor", "diagnosis": "Diagnosis", "spare_parts": "Spare parts",
              "scheduler": "Scheduler", "energy": "Energy", "decision": "Decision"}
STAGE_NODE = {Stage.AWAITING_APPROVAL: ("lead", "lead", "waiting for approval"),
              Stage.AWAITING_SIGNOFF: ("lead", "lead", "waiting for the other sign-offs"),
              Stage.WAITING_TECHNICIAN: ("technician", "technician", "job waiting for the technician"),
              Stage.TECHNICIAN_WORKING: ("floor", "technician", "technician repairing the machine"),
              Stage.VERIFYING: ("ml", "monitor", "checking the machine after the repair"),
              Stage.READY_TO_CLOSE: ("technician", "technician", "check passed, technician to close"),
              Stage.REPAIR_FAILED: ("technician", "technician", "repair did not work"),
              Stage.RESOLVED: ("cmms", "technician", "closed, fix stored in the maintenance log"),
              Stage.REJECTED: ("incidents", "lead", "rejected"),
              Stage.ANSWERED: ("incidents", "orchestrator", "question answered")}
KEEP_DONE_FOR = timedelta(hours=2)  # plant time a closed incident stays in the garden


def color_of(incident_id: str) -> int:
    m = re.search(r"(\d+)$", incident_id)
    return int(m.group(1)) % 10 if m else 0


class GardenService:
    def __init__(self, bus, clock, stage_fn, repo):
        self.bus, self.clock, self.stage_fn, self.repo = bus, clock, stage_fn, repo
        self.agents: dict[str, GardenAgent] = {}
        self.kv = None  # SharedKV: positions shared by every process (set by the container)

    def _put(self, rec, agent: str, node: str, activity: str) -> None:
        inc = rec.incident
        if self.kv is not None:  # another process may have moved it: compare with the shared position
            raw = self.kv.get_json("garden", inc.id)
            prev = GardenAgent.model_validate(raw) if raw else None
        else:
            prev = self.agents.get(inc.id)
        g = GardenAgent(incident_id=inc.id, asset_id=inc.asset_id, plant_id=inc.plant_id, agent=agent, node=node,
                        activity=activity, stage=self.stage_fn(rec), ts=self.clock(), color=color_of(inc.id))
        if prev and prev.node == g.node and prev.activity == g.activity and prev.stage == g.stage:
            return
        self.agents[inc.id] = g
        if self.kv is not None:
            self.kv.put_json("garden", inc.id, g.model_dump(mode="json"))
        try:
            asyncio.get_running_loop().create_task(self.bus.publish("garden.activity", g))
        except RuntimeError:
            pass

    def on_event(self, rec, ev: AgentEvent) -> None:
        agent = ev.agent.value
        who = AGENT_NAME.get(agent, agent)
        if ev.sub_agent:
            who += f" › {ev.sub_agent.replace('_', ' ')}"
        if ev.type == EventType.TOOL_CALL and ev.tool:
            what = TOOL_TEXT.get(ev.tool, ev.tool.replace("_", " "))
            arg = next((str(ev.args[k]) for k in ("sku", "supplier_id", "asset_id", "line_id", "query") if ev.args and ev.args.get(k)), "")
            self._put(rec, agent, TOOL_NODE.get(ev.tool, "agents"), f"{who} · {what}" + (f" ({arg[:40]})" if arg else ""))
        elif ev.type in (EventType.THOUGHT_DELTA, EventType.MESSAGE_DELTA):
            self._put(rec, agent, "llm", f"{who} · thinking" if ev.type == EventType.THOUGHT_DELTA else f"{who} · writing")
        elif ev.type == EventType.STATUS and ev.message == "started":
            self._put(rec, agent, "agents", f"{who} · started")
        elif ev.type == EventType.HANDOFF:
            self._put(rec, agent, "agents", f"{who} · handing over to {AGENT_NAME.get(str((ev.args or {}).get('to')), 'another agent')}")
        elif ev.type == EventType.ERROR:
            self._put(rec, agent, "agents", f"{who} · {ev.message[:60]}")
        elif ev.type == EventType.STATUS and ev.message == "finished" and agent == "orchestrator":
            self.on_status(rec)

    def on_status(self, rec) -> None:
        stage = self.stage_fn(rec)
        if stage == Stage.AI_ANALYSING:
            self._put(rec, "orchestrator", "agents", f"Coordinator · analysis round {rec.incident.round}")
            return
        node, agent, text = STAGE_NODE.get(stage, ("incidents", "orchestrator", stage.value))
        self._put(rec, agent, node, text[0].upper() + text[1:])

    def snapshot(self, claims: AccessClaims) -> GardenSnapshot:
        now = self.clock()
        out = []
        source = ([GardenAgent.model_validate(v) for v in self.kv.all_json("garden").values()] if self.kv is not None
                  else list(self.agents.values()))
        for g in source:
            if self._before_reset(self.repo.get(g.incident_id)):
                continue
            if g.plant_id not in claims.plant_ids:
                continue
            if g.stage in (Stage.RESOLVED, Stage.REJECTED, Stage.ANSWERED) and now - g.ts > KEEP_DONE_FOR:
                if self.kv is not None:
                    self.kv.delete("garden", g.incident_id)  # keep the shared hash small
                continue
            out.append(g)
        return GardenSnapshot(now=now, agents=sorted(out, key=lambda a: a.incident_id))

    def _before_reset(self, rec) -> bool:
        from app.sim.clock import parse_ts
        db = getattr(self.repo, "_db", None)
        at = db.meta(f"sim_reset_at:{rec.incident.plant_id}") if db is not None else None
        return bool(at and rec.incident.created_at <= parse_ts(at))

    def rebuild(self) -> None:
        """After a restart: put every recent incident back where it was."""
        for rec in self.repo.all():
            if self._before_reset(rec):
                continue
            if rec.incident.status in (IncidentStatus.RESOLVED, IncidentStatus.REJECTED) and \
                    self.clock() - rec.incident.updated_at > KEEP_DONE_FOR:
                continue
            last = next((e for e in reversed(rec.events) if e.type == EventType.TOOL_CALL), None)
            if rec.incident.status == IncidentStatus.ANALYSING and last is not None:
                self.on_event(rec, last)
            else:
                self.on_status(rec)
