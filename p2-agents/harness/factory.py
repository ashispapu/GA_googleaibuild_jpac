"""Composition helpers for the backend (and scripts).

    runtime = build_runtime(gateway=c.tool_gateway, redaction_sink=c.redactions.add)

`live_runtime_from_config()` returns None when [runtime] mode = replay, so the backend keeps its replay runtime.
"""
from __future__ import annotations

import logging
from pathlib import Path

from harness.config import AgentsConfig, load_config
from harness.context import ToolExecutor
from harness.runtime import PlantAgentRuntime
from llm.client import LLMClient, RedactionSink
from llm.types import LLMProvider

log = logging.getLogger("plantguard.factory")


def build_runtime(gateway: ToolExecutor, *, config: AgentsConfig | None = None, config_path: str | Path | None = None,
                  redaction_sink: RedactionSink | None = None, provider: LLMProvider | None = None,
                  clock=None) -> PlantAgentRuntime:
    cfg = config or load_config(config_path)
    llm = LLMClient(provider=provider, redaction_sink=redaction_sink)
    kw = {"clock": clock} if clock else {}
    return PlantAgentRuntime(cfg, gateway, llm, **kw)


def live_runtime_from_config(gateway: ToolExecutor, *, config_path: str | Path | None = None,
                             redaction_sink: RedactionSink | None = None, clock=None) -> PlantAgentRuntime | None:
    cfg = load_config(config_path)
    if cfg.runtime.mode != "live":
        log.info("agent runtime mode is '%s': using the recorded replay", cfg.runtime.mode)
        return None
    if cfg.runtime.require_api_key and cfg.llm.provider == "litellm" and cfg.llm.needs_key() and not cfg.llm.api_key():
        log.warning("[runtime] mode = live but %s is not set: using the recorded replay "
                    "(set the key, or require_api_key = false for keyless providers)", cfg.llm.api_key_env)
        return None
    if cfg.llm.provider == "litellm":
        from llm.network import apply_proxy
        log.warning("LLM network: %s", apply_proxy(cfg.llm.proxy))
    log.info("live agent runtime: model=%s provider=%s flows=%s", cfg.llm.model, cfg.llm.provider, sorted(cfg.flows))
    return build_runtime(gateway, config=cfg, redaction_sink=redaction_sink, clock=clock)


def plant_runtime_from_config(gateway: ToolExecutor, *, config_path: str | Path | None = None,
                              redaction_sink: RedactionSink | None = None, clock=None,
                              on_llm_failure: str = "degrade") -> PlantAgentRuntime:
    """Runtime for the simulated plant: always live (a recording of another machine would be wrong data), and when the
    model is unavailable (no key, quota, network) the agents finish rule-based on the real plant data."""
    from dataclasses import replace
    cfg = load_config(config_path)
    cfg = replace(cfg, runtime=replace(cfg.runtime, mode="live", on_llm_failure=on_llm_failure))
    # a real plant must order what it does not have: options that wait for the supplier carry a purchase order
    cfg.policies.setdefault("planning", type(cfg.policy("planning"))())["purchase_orders"] = "true"
    if cfg.llm.provider == "litellm":
        from llm.network import apply_proxy
        log.warning("LLM network: %s", apply_proxy(cfg.llm.proxy))
        if cfg.llm.uses_vertex():
            model, kw = cfg.llm.provider_model()
            log.warning("LLM: Vertex AI %s in project %s, location %s (Google credentials, no API key)", model,
                        kw.get("vertex_project") or cfg.llm.extra_params.get("vertex_project") or "?",
                        kw.get("vertex_location") or cfg.llm.extra_params.get("vertex_location"))
        elif cfg.llm.api_key_env and not cfg.llm.api_key():
            log.warning("%s is not set: agents will finish rule-based (set the key for live reasoning)", cfg.llm.api_key_env)
    log.info("plant agent runtime: model=%s on_llm_failure=%s", cfg.llm.model, on_llm_failure)
    return build_runtime(gateway, config=cfg, redaction_sink=redaction_sink, clock=clock)
