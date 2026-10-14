"""Toolbox: builds the tool list each agent gets, from the .ini. The runtime passes the list into `agent.run()`."""
from __future__ import annotations

from typing import TYPE_CHECKING

from harness.config import AgentsConfig
from tools.agent import HandoffTool, SubAgentTool, SubmitResultTool
from tools.base import Tool
from tools.plant import PlantTool, build_plant_tools

if TYPE_CHECKING:  # pragma: no cover
    from harness.base_agent import BaseAgent


class Toolbox:
    def __init__(self, config: AgentsConfig, extra: dict[str, Tool] | None = None):
        self.config = config
        self.plant: dict[str, PlantTool] = build_plant_tools(config.tools.max_result_chars, config.tools.disabled)
        self.extra = dict(extra or {})  # custom tools registered by name (usable from [agent.*] tools = ...)

    def get(self, name: str) -> Tool:
        if name in self.plant:
            return self.plant[name]
        if name in self.extra:
            return self.extra[name]
        raise KeyError(f"tool '{name}' is not available (disabled in [tools] or unknown)")

    def plant_tool(self, name: str) -> PlantTool:
        return self.plant[name]

    def for_agent(self, agent: "BaseAgent", mode: str) -> list[Tool]:
        """Plant tools from `tools =`, one ask_<x> per sub-agent, handoff_task if targets exist, and submit_result
        when the agent defines structured output for this mode."""
        cfg = agent.config
        modes = getattr(agent, "plant_tool_modes", None)  # None = data tools in every mode
        out: list[Tool] = [self.get(t) for t in cfg.tools if (t in self.plant or t in self.extra)
                           and (modes is None or mode in modes)]
        for sub in (cfg.sub_agents if mode in agent.subagent_modes else ()):
            out.append(SubAgentTool(sub, self.config.agent(sub).description))
        if cfg.handoff_targets and mode in agent.handoff_modes:
            out.append(HandoffTool({t: self.config.agent(t).description for t in cfg.handoff_targets}))
        model = agent.output_model_for(mode)
        if model is not None and cfg.finish_tool:
            out.append(SubmitResultTool(model))
        return out
