"""The .ini drives everything; invalid configs fail at load time. Tools: presenters, schemas, allow-list."""
import asyncio
import shutil
from pathlib import Path

import pytest

from harness.config import ConfigError, load_config
from harness.events import EventEmitter, clip
from harness.registry import AgentRegistry
from harness.config import StreamingConfig
from plantguard_contracts.tools import TOOLS
from tools.registry import Toolbox

P2 = Path(__file__).resolve().parents[1]
CENTRAL = P2 / "config" / "agents.ini"


def test_shipped_config_is_complete(cfg):
    assert cfg.llm.model.startswith("gemini/") and cfg.llm.api_key_env == "GEMINI_API_KEY"
    assert [s.agent for s in cfg.flow("ml_alert")] == ["orchestrator", "monitor", "diagnosis", "spare_parts",
                                                         "scheduler", "energy", "decision", "orchestrator"]
    assert cfg.agent("manual_researcher").is_helper and cfg.agent("manual_researcher").principal == "diagnosis"
    reg = AgentRegistry(cfg)
    for name in cfg.agents:  # every agent loads its class and its own markdown prompt
        a = reg.create(name)
        assert (a.config.folder / a.config.prompt).read_text().strip()
        assert a.config.folder.name == name


def test_toolbox_passes_contract_tools_and_controls(cfg):
    tb, reg = Toolbox(cfg), AgentRegistry(cfg)
    names = lambda agent, mode: {t.name for t in tb.for_agent(reg.create(agent), mode)}  # noqa: E731
    assert names("diagnosis", "pipeline") == {"get_asset_health", "get_signals", "search_manuals", "get_maintenance_history",
                                              "get_defects", "get_bom", "ask_manual_researcher", "submit_result"}
    assert names("spare_parts", "pipeline") == {"get_bom", "check_stock", "get_supplier"}
    assert "handoff_task" in names("orchestrator", "answer") and names("orchestrator", "plan") == set()
    for t in tb.for_agent(reg.create("diagnosis"), "pipeline"):  # function schemas are provider-friendly
        s = t.schema()["function"]["parameters"]
        assert s["type"] == "object" and "$defs" not in str(s) and "$ref" not in str(s)


def _ini_copy(tmp_path, replace: dict[str, str]) -> Path:
    text = CENTRAL.read_text()
    for a, b in replace.items():
        assert a in text
        text = text.replace(a, b)
    p = tmp_path / "agents.ini"
    p.write_text(text)
    return p


@pytest.mark.parametrize("change,err", [
    ({"[agents]\n": "[agent.monitor]\ntools = check_stock\n\n[agents]\n"}, "allow-list"),
    ({"[agents]\n": "[agent.monitor]\ntools = create_work_order\n\n[agents]\n"}, "write tool"),
    ({"[agents]\n": "[agent.monitor]\nsub_agents = nobody\n\n[agents]\n"}, "sub-agent"),
    ({"[agents]\n": "[agent.orchestrator]\nhandoff_targets = manual_researcher\n\n[agents]\n"}, "helper"),
    ({"steps = orchestrator:answer": "steps = manual_researcher"}, "helper"),
    ({"mode = live": "mode = sometimes"}, "mode"),
    ({"[option.OPT-C]": "[option.C]"}, "OPT-A"),
])
def test_invalid_configs_fail_fast(tmp_path, change, err):
    with pytest.raises(ConfigError, match=err):
        load_config(_ini_copy(tmp_path, change))


def test_central_overrides_folder_defaults(tmp_path):
    ini = _ini_copy(tmp_path, {"[agents]\n": "[agent.diagnosis]\nmax_steps = 9\nmodel = gemini/gemini-2.5-pro\n\n"
                                             "[policy.diagnosis]\nw_quality = 0.1\n\n[agents]\n"})
    c = load_config(ini)
    assert c.agent("diagnosis").max_steps == 9 and c.llm_for("diagnosis").model == "gemini/gemini-2.5-pro"
    assert c.llm_for("monitor").model == "gemini/gemini-2.5-flash"
    assert c.policy("diagnosis").float("w_quality") == 0.1 and c.policy("diagnosis").float("w_manual") == 0.15


def test_new_agent_is_just_a_folder(tmp_path):
    agents = tmp_path / "agents"
    shutil.copytree(P2 / "agents", agents, ignore=shutil.ignore_patterns("__pycache__"))
    (agents / "safety").mkdir()
    (agents / "safety" / "agent.py").write_text("from harness.base_agent import Agent\n")
    (agents / "safety" / "agent.ini").write_text("[agent]\ndescription = Checks safety rules\nprincipal = decision\n"
                                                "tools = get_orders\n")
    (agents / "safety" / "system_prompt.md").write_text("You check safety for {{asset_id}}.")
    c = load_config(CENTRAL, agents_dir=agents)
    assert c.agent("safety").is_helper and c.agent("safety").tools == ("get_orders",)


def test_every_read_tool_has_a_presenter_and_works_on_the_mock(cfg, mock_tools):
    from tools.plant.presenter import PRESENTERS
    reads = [n for n, t in TOOLS.items() if not t.writes]
    assert set(reads) <= set(PRESENTERS)


def test_clip_and_emitter_limits():
    assert clip("**Bold** heading\n\nsecond line", 200) == "Bold heading second line"
    long = "word " * 80
    out = clip(long, 200)
    assert len(out) <= 200 and out.endswith("…")
    sent = []

    async def sink(e):
        sent.append(e)
    em = EventEmitter("INC-0042", StreamingConfig(flush_chars=5), sink)

    async def go():
        s = em.stream("monitor", "message")
        em.vault.placeholder("person_name", "Suresh")
        for piece in ["Ask <PER", "SON_NAME", "_1> now", " please"]:
            await s.push(piece)
        await s.close(None)
    asyncio.run(go())
    text = "".join(e.message for e in sent)
    assert text == "Ask Suresh now please" and all("<PER" not in e.message for e in sent)
