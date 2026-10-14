"""Typed view over the agent-layer .ini files.

Two levels, read in this order (later wins):
  1. agents/<name>/agent.ini   - defaults that travel with each agent folder
  2. config/agents.ini         - the central file; [agent.<name>] and [policy.<name>] override the folder defaults

The path of the central file can be changed with the env var PLANTGUARD_AGENTS_INI.
Nothing in the harness hard-codes a flow, a model, a tool list or a business number: it all comes from here.
"""
from __future__ import annotations

import configparser
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

P2_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INI = P2_ROOT / "config" / "agents.ini"
AGENTS_DIR = P2_ROOT / "agents"
INI_ENV = "PLANTGUARD_AGENTS_INI"


class ConfigError(ValueError):
    """The .ini files are inconsistent (unknown agent, tool outside the allow-list, bad value...)."""


# ------------------------------------------------------------------ helpers
def _list(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").replace("\n", ",").split(",") if x.strip()]


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _opt_float(v: str | None) -> float | None:
    return None if v is None or str(v).strip() == "" else float(v)


def _opt_int(v: str | None) -> int | None:
    return None if v is None or str(v).strip() == "" else int(v)


def _opt_str(v: str | None) -> str | None:
    return None if v is None or str(v).strip() == "" else str(v).strip()


def _new_parser() -> configparser.ConfigParser:
    p = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";", "#"))
    p.optionxform = str  # keep key case
    return p


class Policy(dict):
    """Business parameters for one agent (or a shared group). Typed getters with defaults, so code reads
    `policy.float("downtime_eur_per_h")` and the number itself lives in the .ini."""

    def str(self, key: str, default: str | None = None) -> str:
        v = self.get(key)
        if v is None or v == "":
            if default is None:
                raise ConfigError(f"policy value '{key}' is missing")
            return default
        return v

    def float(self, key: str, default: float | None = None) -> float:
        v = self.get(key)
        if v is None or v == "":
            if default is None:
                raise ConfigError(f"policy value '{key}' is missing")
            return float(default)
        return float(v)

    def int(self, key: str, default: int | None = None) -> int:
        return int(self.float(key, default))

    def bool(self, key: str, default: bool = False) -> bool:
        return _bool(self.get(key), default)

    def list(self, key: str, default: list[str] | None = None) -> list[str]:
        v = self.get(key)
        return _list(v) if v not in (None, "") else list(default or [])


# ------------------------------------------------------------------ config objects
@dataclass(frozen=True)
class LLMConfig:
    provider: str = "litellm"
    model: str = "gemini/gemini-2.5-flash"
    api_key_env: str = "GEMINI_API_KEY"
    api_base: str | None = None
    temperature: float | None = 0.2
    max_tokens: int | None = 2048
    timeout_s: float = 20.0
    num_retries: int = 1
    reasoning_effort: str | None = "low"
    thinking_budget_tokens: int | None = None
    include_thoughts: bool = True
    redact: bool = True
    proxy: str | None = None  # None = env/LLM_PROXY/OS; URL; or none = direct (see llm/network.py)
    retry_max_attempts: int = 5       # total tries per model call (1 = no retry); see llm/retry.py
    retry_base_s: float = 2.0         # first backoff; doubles each try
    retry_max_s: float = 60.0         # cap per wait (a server "retry in N s" hint can still ask for longer)
    requests_per_minute: int = 0      # client-side pacing per model; 0 = off (Gemini free tier: 5)
    extra_params: dict = field(default_factory=dict)

    def api_key(self) -> str | None:
        if self.uses_vertex():
            return None  # Vertex AI signs in with Google credentials (ADC / service account), not an API key
        return os.getenv(self.api_key_env) if self.api_key_env else None

    # ---- Vertex AI (Google Cloud), switched on from the environment like Google's own SDK ----
    @staticmethod
    def vertex_enabled() -> bool:
        return any(_bool(os.getenv(k), False) for k in ("GOOGLE_GENAI_USE_ENTERPRISE", "GOOGLE_GENAI_USE_VERTEXAI"))

    def uses_vertex(self) -> bool:
        return self.model.startswith(("vertex_ai/", "vertex_ai_beta/")) or (
            self.vertex_enabled() and self.model.startswith("gemini/"))

    def needs_key(self) -> bool:
        return bool(self.api_key_env) and not self.uses_vertex()

    def provider_model(self) -> tuple[str, dict]:
        """(LiteLLM model string, extra kwargs). With GOOGLE_GENAI_USE_ENTERPRISE / GOOGLE_GENAI_USE_VERTEXAI = true,
        gemini/<m> runs on Vertex AI in GOOGLE_CLOUD_PROJECT at GOOGLE_CLOUD_LOCATION (default global)."""
        if not self.uses_vertex():
            return self.model, {}
        model = self.model if self.model.startswith(("vertex_ai/", "vertex_ai_beta/")) else \
            "vertex_ai/" + self.model.split("/", 1)[1]
        kw = {}
        project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("VERTEXAI_PROJECT")
        location = os.getenv("GOOGLE_CLOUD_LOCATION") or os.getenv("VERTEXAI_LOCATION") or "global"
        if project and "vertex_project" not in self.extra_params:
            kw["vertex_project"] = project
        if "vertex_location" not in self.extra_params:
            kw["vertex_location"] = location
        return model, kw

    def merged(self, overrides: dict[str, Any]) -> "LLMConfig":
        clean = {k: v for k, v in overrides.items() if v is not None}
        return replace(self, **clean) if clean else self


@dataclass(frozen=True)
class StreamingConfig:
    enabled: bool = True
    stream_thinking: bool = True
    stream_messages: bool = True
    flush_chars: int = 48
    flush_interval_ms: int = 120
    thought_max_chars: int = 200
    message_max_chars: int = 1500


@dataclass(frozen=True)
class RuntimeConfig:
    mode: str = "live"  # live | replay
    on_llm_failure: str = "raise"  # raise | degrade
    max_run_seconds: float = 60.0
    max_handoffs: int = 3
    max_subagent_depth: int = 2
    replay_file: Path | None = None
    require_api_key: bool = True  # live mode without the key env var set -> backend keeps the replay


@dataclass(frozen=True)
class ToolsConfig:
    max_result_chars: int = 4000
    disabled: tuple[str, ...] = ()


@dataclass(frozen=True)
class FlowStep:
    agent: str
    mode: str = "pipeline"  # pipeline | plan | summary | answer | <any task template key>


@dataclass(frozen=True)
class AgentConfig:
    name: str
    folder: Path
    class_ref: str = "agent:Agent"  # module:Class inside the folder
    principal: str = ""             # identity used for the contract allow-list and AgentEvent.agent
    description: str = ""
    prompt: str = "system_prompt.md"
    tasks: dict[str, str] = field(default_factory=dict)  # mode -> template file
    tools: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    sub_agents: tuple[str, ...] = ()
    handoff_targets: tuple[str, ...] = ()
    max_steps: int = 4
    finish_tool: bool = True        # expose submit_result so the LLM can hand structured fields back
    llm: dict[str, Any] = field(default_factory=dict)
    policy: Policy = field(default_factory=Policy)
    enabled: bool = True

    @property
    def is_helper(self) -> bool:
        """A helper (non-contract) agent acts on behalf of its principal and shows up as AgentEvent.sub_agent."""
        return self.principal != self.name


@dataclass(frozen=True)
class AgentsConfig:
    llm: LLMConfig
    streaming: StreamingConfig
    runtime: RuntimeConfig
    tools: ToolsConfig
    flows: dict[str, tuple[FlowStep, ...]]
    agents: dict[str, AgentConfig]
    policies: dict[str, Policy]
    source: Path
    options: dict[str, Policy] = field(default_factory=dict)  # [option.OPT-X] strategies, in file order

    def agent(self, name: str) -> AgentConfig:
        try:
            return self.agents[name]
        except KeyError:
            raise ConfigError(f"unknown agent '{name}' (known: {', '.join(sorted(self.agents))})") from None

    def flow(self, trigger: str) -> tuple[FlowStep, ...]:
        if trigger in self.flows:
            return self.flows[trigger]
        if "default" in self.flows:
            return self.flows["default"]
        raise ConfigError(f"no [flow.{trigger}] and no [flow.default] in {self.source}")

    def policy(self, name: str) -> Policy:
        return self.policies.get(name, Policy())

    def llm_for(self, agent: str) -> LLMConfig:
        return self.llm.merged(self.agent(agent).llm)


# ------------------------------------------------------------------ loading
def _llm_section(sec: configparser.SectionProxy | dict) -> dict[str, Any]:
    g = sec.get
    out: dict[str, Any] = {}
    if g("provider"):
        out["provider"] = g("provider").strip()
    if g("model"):
        out["model"] = g("model").strip()
    if "api_key_env" in sec:
        out["api_key_env"] = (g("api_key_env") or "").strip()
    if "api_base" in sec:
        out["api_base"] = _opt_str(g("api_base"))
    if "proxy" in sec:
        out["proxy"] = _opt_str(g("proxy"))
    for key, conv in (("retry_max_attempts", int), ("retry_base_s", float), ("retry_max_s", float),
                      ("requests_per_minute", int)):
        if g(key) not in (None, ""):
            out[key] = conv(g(key))
    if g("temperature") not in (None, ""):
        out["temperature"] = float(g("temperature"))
    if g("max_tokens") not in (None, ""):
        out["max_tokens"] = int(g("max_tokens"))
    if g("timeout_s") not in (None, ""):
        out["timeout_s"] = float(g("timeout_s"))
    if g("num_retries") not in (None, ""):
        out["num_retries"] = int(g("num_retries"))
    if "reasoning_effort" in sec:
        out["reasoning_effort"] = _opt_str(g("reasoning_effort"))
    if "thinking_budget_tokens" in sec:
        out["thinking_budget_tokens"] = _opt_int(g("thinking_budget_tokens"))
    if g("include_thoughts") not in (None, ""):
        out["include_thoughts"] = _bool(g("include_thoughts"), True)
    if g("redact") not in (None, ""):
        out["redact"] = _bool(g("redact"), True)
    if g("extra_params") not in (None, ""):
        try:
            out["extra_params"] = json.loads(g("extra_params"))
        except json.JSONDecodeError as e:
            raise ConfigError(f"[llm] extra_params must be JSON: {e}") from e
    return out


def _parse_steps(text: str) -> tuple[FlowStep, ...]:
    steps = []
    for item in _list(text):
        agent, _, mode = item.partition(":")
        steps.append(FlowStep(agent.strip(), (mode or "pipeline").strip()))
    return tuple(steps)


def _load_agent(folder: Path, central: configparser.ConfigParser) -> AgentConfig:
    name = folder.name
    local = _new_parser()
    if (folder / "agent.ini").exists():
        local.read(folder / "agent.ini", encoding="utf-8")
    a: dict[str, str] = dict(local["agent"]) if local.has_section("agent") else {}
    if central.has_section(f"agent.{name}"):
        a.update(dict(central[f"agent.{name}"]))
    tasks = dict(local["tasks"]) if local.has_section("tasks") else {}
    if central.has_section(f"tasks.{name}"):
        tasks.update(dict(central[f"tasks.{name}"]))
    pol = Policy(dict(local["policy"]) if local.has_section("policy") else {})
    if central.has_section(f"policy.{name}"):
        pol.update(dict(central[f"policy.{name}"]))
    llm_keys = ("model", "temperature", "max_tokens", "timeout_s", "reasoning_effort", "thinking_budget_tokens",
                "include_thoughts", "provider", "api_base", "num_retries")
    llm_over = _llm_section({k: a[k] for k in llm_keys if k in a})
    return AgentConfig(
        name=name, folder=folder,
        class_ref=a.get("class", "agent:Agent").strip(),
        principal=(a.get("principal") or name).strip(),
        description=a.get("description", "").strip(),
        prompt=a.get("prompt", "system_prompt.md").strip(),
        tasks=tasks,
        tools=tuple(_list(a.get("tools"))),
        required_tools=tuple(_list(a.get("required_tools"))),
        sub_agents=tuple(_list(a.get("sub_agents"))),
        handoff_targets=tuple(_list(a.get("handoff_targets"))),
        max_steps=int(a.get("max_steps", 4) or 4),
        finish_tool=_bool(a.get("finish_tool"), True),
        llm=llm_over,
        policy=pol,
        enabled=_bool(a.get("enabled"), True),
    )


def load_config(path: str | os.PathLike | None = None, agents_dir: Path = AGENTS_DIR) -> AgentsConfig:
    """Read the central .ini plus every agents/<name>/agent.ini and validate the whole picture."""
    src = Path(path or os.getenv(INI_ENV) or DEFAULT_INI)
    if not src.exists():
        raise ConfigError(f"agent config not found: {src}")
    p = _new_parser()
    p.read(src, encoding="utf-8")

    llm = LLMConfig(**_llm_section(p["llm"])) if p.has_section("llm") else LLMConfig()
    s = p["streaming"] if p.has_section("streaming") else {}
    streaming = StreamingConfig(
        enabled=_bool(s.get("enabled"), True), stream_thinking=_bool(s.get("stream_thinking"), True),
        stream_messages=_bool(s.get("stream_messages"), True), flush_chars=int(s.get("flush_chars", 48)),
        flush_interval_ms=int(s.get("flush_interval_ms", 120)), thought_max_chars=int(s.get("thought_max_chars", 200)),
        message_max_chars=int(s.get("message_max_chars", 1500)))
    r = p["runtime"] if p.has_section("runtime") else {}
    replay = _opt_str(r.get("replay_file"))
    runtime = RuntimeConfig(
        mode=(r.get("mode") or "live").strip(), on_llm_failure=(r.get("on_llm_failure") or "raise").strip(),
        max_run_seconds=float(r.get("max_run_seconds", 60)), max_handoffs=int(r.get("max_handoffs", 3)),
        max_subagent_depth=int(r.get("max_subagent_depth", 2)),
        require_api_key=_bool(r.get("require_api_key"), True),
        replay_file=(src.parent / replay).resolve() if replay else None)
    t = p["tools"] if p.has_section("tools") else {}
    tools = ToolsConfig(max_result_chars=int(t.get("max_result_chars", 4000)), disabled=tuple(_list(t.get("disabled"))))

    flows = {sec.split(".", 1)[1]: _parse_steps(p[sec].get("steps", "")) for sec in p.sections()
             if sec.startswith("flow.")}
    enabled = set(_list(p["agents"].get("enabled"))) if p.has_section("agents") and p["agents"].get("enabled") else None
    agents: dict[str, AgentConfig] = {}
    for folder in sorted(x for x in agents_dir.iterdir() if x.is_dir() and (x / "agent.py").exists()):
        cfg = _load_agent(folder, p)
        if enabled is not None and cfg.name not in enabled:
            cfg = replace(cfg, enabled=False)
        if cfg.enabled:
            agents[cfg.name] = cfg
    policies = {sec.split(".", 1)[1]: Policy(dict(p[sec])) for sec in p.sections() if sec.startswith("policy.")}
    for name, a in agents.items():  # folder policy merged with central override (already done) wins over shared
        policies[name] = a.policy

    options = {sec.split(".", 1)[1]: Policy(dict(p[sec])) for sec in p.sections() if sec.startswith("option.")}
    cfg = AgentsConfig(llm, streaming, runtime, tools, flows, agents, policies, src, options)
    validate(cfg)
    return cfg


def validate(cfg: AgentsConfig) -> None:
    """Fail fast on anything the contract or the harness would reject at run time."""
    from plantguard_contracts.schemas.incidents import Agent
    from plantguard_contracts.tools import TOOLS

    contract_agents = {a.value for a in Agent}
    if cfg.runtime.mode not in ("live", "replay"):
        raise ConfigError("[runtime] mode must be live or replay")
    if cfg.runtime.on_llm_failure not in ("raise", "degrade"):
        raise ConfigError("[runtime] on_llm_failure must be raise or degrade")
    for name, a in cfg.agents.items():
        if a.principal not in contract_agents:
            raise ConfigError(f"agent '{name}': principal '{a.principal}' is not a contract Agent")
        if not (a.folder / a.prompt).exists():
            raise ConfigError(f"agent '{name}': prompt file {a.prompt} missing in {a.folder}")
        for mode, file in a.tasks.items():
            if not (a.folder / file).exists():
                raise ConfigError(f"agent '{name}': task template '{mode}' -> {file} missing")
        for tool in (*a.tools, *a.required_tools):
            spec = TOOLS.get(tool)
            if spec is None:
                raise ConfigError(f"agent '{name}': unknown tool '{tool}'")
            if spec.writes:
                raise ConfigError(f"agent '{name}': write tool '{tool}' is executed by the backend only (C3)")
            if a.principal not in spec.agents:
                raise ConfigError(f"agent '{name}': tool '{tool}' is not on the allow-list of '{a.principal}' (C4)")
        for sub in a.sub_agents:
            if sub not in cfg.agents:
                raise ConfigError(f"agent '{name}': sub-agent '{sub}' is not an enabled agent")
            if sub == name:
                raise ConfigError(f"agent '{name}' cannot be its own sub-agent")
        for tgt in a.handoff_targets:
            if tgt not in cfg.agents:
                raise ConfigError(f"agent '{name}': handoff target '{tgt}' is not an enabled agent")
            if cfg.agents[tgt].is_helper:
                raise ConfigError(f"agent '{name}': handoff target '{tgt}' must be a contract agent, not a helper")
    import re as _re
    for oid, opt in cfg.options.items():
        if not _re.fullmatch(r"OPT-[A-Z]", oid):
            raise ConfigError(f"[option.{oid}]: option ids must look like OPT-A (contract OptionId)")
        if opt.get("strategy") not in ("stop_now", "slow_then_swap", "run_to_supplier"):
            raise ConfigError(f"[option.{oid}]: strategy must be stop_now | slow_then_swap | run_to_supplier")
    for trigger, steps in cfg.flows.items():
        if not steps:
            raise ConfigError(f"[flow.{trigger}] has no steps")
        for st in steps:
            if st.agent not in cfg.agents:
                raise ConfigError(f"[flow.{trigger}] uses unknown/disabled agent '{st.agent}'")
            if cfg.agents[st.agent].is_helper:
                raise ConfigError(f"[flow.{trigger}] step '{st.agent}' is a helper; helpers run only as sub-agents")
