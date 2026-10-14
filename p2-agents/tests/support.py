"""Helpers for the agent-layer tests (path setup, runtime builders)."""
from __future__ import annotations

import dataclasses
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
P2 = ROOT / "p2-agents"
for p in (str(ROOT), str(P2)):
    if p not in sys.path:
        sys.path.insert(0, p)

from agents.tool_client import HttpToolClient  # noqa: E402
from harness.config import load_config  # noqa: E402
from harness.runtime import PlantAgentRuntime  # noqa: E402
from llm.client import LLMClient  # noqa: E402
from llm.providers.scripted import ScriptedProvider  # noqa: E402
from plantguard_contracts.schemas.incidents import Incident  # noqa: E402



def make_incident(trigger="ml_alert", question=None, id_="INC-0042"):
    now = datetime.now(timezone.utc)
    return Incident(id=id_, title="EQ-3012 spindle bearing wear risk", plant_id="PLANT-A", line_id="L3",
                    asset_id="EQ-3012", trigger=trigger, severity="high", status="analysing", created_at=now,
                    updated_at=now, question=question)


def with_overrides(cfg, **sections):
    """cfg with replaced sub-configs, e.g. runtime={"on_llm_failure": "degrade"}."""
    changes = {k: dataclasses.replace(getattr(cfg, k), **v) for k, v in sections.items()}
    return dataclasses.replace(cfg, **changes)


def make_runtime(cfg, tools, script=None, piece_chars=7, sink=None):
    provider = ScriptedProvider(script, piece_chars=piece_chars)
    rt = PlantAgentRuntime(cfg, tools, LLMClient(provider=provider, redaction_sink=sink))
    return rt, provider


async def collect(rt, incident):
    return [e async for e in rt.run(incident)]
