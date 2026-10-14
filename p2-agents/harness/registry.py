"""AgentRegistry: finds each agent folder's class (agents/<name>/agent.py, `class = agent:MonitorAgent` in agent.ini)
and builds instances. Adding an agent = adding a folder; nothing else in the harness changes."""
from __future__ import annotations

import importlib

from harness.base_agent import BaseAgent
from harness.config import AgentsConfig, ConfigError


class AgentRegistry:
    def __init__(self, config: AgentsConfig, package: str = "agents"):
        self.config, self.package = config, package
        self._classes: dict[str, type[BaseAgent]] = {}
        for name in config.agents:
            self._classes[name] = self._load(name)

    def _load(self, name: str) -> type[BaseAgent]:
        cfg = self.config.agent(name)
        mod_name, _, cls_name = cfg.class_ref.partition(":")
        module = importlib.import_module(f"{self.package}.{name}.{mod_name}")
        cls = getattr(module, cls_name or "Agent", None)
        if not (isinstance(cls, type) and issubclass(cls, BaseAgent)):
            raise ConfigError(f"agent '{name}': {cfg.class_ref} is not a BaseAgent subclass")
        return cls

    def create(self, name: str) -> BaseAgent:
        try:
            cls = self._classes[name]
        except KeyError:
            raise ConfigError(f"agent '{name}' is not enabled") from None
        return cls(self.config.agent(name), self.config)

    def names(self) -> list[str]:
        return list(self._classes)
