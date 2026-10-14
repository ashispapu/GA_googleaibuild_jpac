"""Regenerate shared artifacts. Run after ANY schema change: PYTHONPATH=. python scripts_export.py"""
import json
from pathlib import Path
from pydantic import TypeAdapter
from mock_server.app import app
from plantguard_contracts.schemas.incidents import AgentEvent
from plantguard_contracts.schemas.ot import TelemetryMessage
from plantguard_contracts.tools import TOOLS, openai_tool_schema
from plantguard_contracts.schemas import workflow as wf

out = Path("generated")
spec = app.openapi()
# WebSocket frames are not in OpenAPI by default: add them as named components so TS types are generated.
# v1.2 models served by the real backend (repair workflow, sessions, documents, garden, simulator status)
EXTRA = (AgentEvent, TelemetryMessage, wf.IncidentOverview, wf.JobView, wf.MaintenanceLogEntry, wf.SessionInfo,
         wf.DocumentInfo, wf.DocumentDetail, wf.GardenAgent, wf.GardenSnapshot, wf.SimStatus, wf.FaultRequest,
         wf.FaultResponse, wf.SimSpeedRequest, wf.IncidentContext, wf.WorkUpdate, wf.WorkCompleteRequest,
         wf.ResolveRequest, wf.ReanalyseRequest, wf.WorkNoteRequest, wf.WorkStartRequest, wf.OptionChatMessage,
         wf.OptionChatRequest, wf.OptionChatResponse, wf.SignoffTask)
for m in EXTRA:
    s = m.model_json_schema(ref_template="#/components/schemas/{model}")
    for k, v in s.pop("$defs", {}).items():
        spec["components"]["schemas"].setdefault(k, v)
    spec["components"]["schemas"][m.__name__] = s
(out / "openapi.json").write_text(json.dumps(spec, indent=2))
(out / "tools.json").write_text(json.dumps({n: {"agents": sorted(t.agents), "writes": t.writes, "endpoint": t.endpoint,
                                               "schema": openai_tool_schema(t)} for n, t in TOOLS.items()}, indent=2))
print("generated/openapi.json, generated/tools.json")
