"""Provider registry. `[llm] provider` is a short name below or any `package.module:Class` path."""
from __future__ import annotations

import importlib

from llm.types import LLMProvider

BUILTIN = {
    "litellm": "llm.providers.litellm_provider:LiteLLMProvider",
    "scripted": "llm.providers.scripted:ScriptedProvider",
}


def load_provider(name: str) -> LLMProvider:
    ref = BUILTIN.get(name, name)
    if ":" not in ref:
        raise ValueError(f"unknown LLM provider '{name}' (use one of {sorted(BUILTIN)} or module:Class)")
    mod, cls = ref.split(":", 1)
    return getattr(importlib.import_module(mod), cls)()
