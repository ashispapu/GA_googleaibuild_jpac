"""Provider-neutral LLM types. Messages use the OpenAI chat format, which LiteLLM speaks for every provider."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol

from harness.config import LLMConfig


class LLMUnavailable(Exception):
    """Any provider error (network, auth, quota, bad response). The runtime decides: replay fallback or degrade."""


@dataclass
class ToolCallRequest:
    """One function call the model asked for (arguments already parsed)."""
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""
    provider_fields: dict[str, Any] = field(default_factory=dict)  # e.g. Gemini thought signatures

    def as_message_part(self) -> dict:
        part = {"id": self.id, "type": "function",
                "function": {"name": self.name, "arguments": self.raw_arguments or json.dumps(self.arguments)}}
        if self.provider_fields:
            part["provider_specific_fields"] = self.provider_fields
        return part


ChunkKind = Literal["reasoning", "text", "tool_call", "done"]


@dataclass
class LLMChunk:
    kind: ChunkKind
    text: str = ""
    tool_call: ToolCallRequest | None = None
    finish_reason: str | None = None
    usage: dict[str, int] | None = None


@dataclass
class LLMRequest:
    agent: str
    system: str
    messages: list[dict]
    tools: list[dict]
    config: LLMConfig


class LLMProvider(Protocol):
    """A model backend. Must stream: reasoning deltas, text deltas, then whole tool calls, then one `done`."""

    def stream(self, req: LLMRequest) -> AsyncIterator[LLMChunk]: ...
