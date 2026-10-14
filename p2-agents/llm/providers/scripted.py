"""Scripted provider: deterministic, offline stand-in for a model. Used by tests and by `[llm] provider = scripted`
for rehearsals without network. A script is a function (LLMRequest) -> Turn; the provider streams the Turn the
same way a real model would (reasoning in small pieces, then text, then tool calls)."""
from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from llm.types import LLMChunk, LLMRequest, LLMUnavailable, ToolCallRequest


@dataclass
class Turn:
    reasoning: str = ""
    text: str = ""
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail: bool = False


Script = Callable[[LLMRequest], Turn]
_ids = itertools.count(1)


def _pieces(text: str, size: int):
    for i in range(0, len(text), size):
        yield text[i:i + size]


class ScriptedProvider:
    def __init__(self, script: Script | None = None, piece_chars: int = 7, delay_s: float = 0.0):
        self.script = script or default_script
        self.piece_chars, self.delay_s = piece_chars, delay_s
        self.requests: list[LLMRequest] = []  # what the agents sent (for assertions)

    async def stream(self, req: LLMRequest) -> AsyncIterator[LLMChunk]:
        self.requests.append(req)
        turn = self.script(req)
        if turn.fail:
            raise LLMUnavailable("scripted failure")
        for kind, text in (("reasoning", turn.reasoning), ("text", turn.text)):
            for piece in _pieces(text, self.piece_chars):
                if self.delay_s:
                    await asyncio.sleep(self.delay_s)
                yield LLMChunk(kind, text=piece)
        for name, args in turn.tool_calls:
            yield LLMChunk("tool_call", tool_call=ToolCallRequest(id=f"call_{next(_ids)}", name=name, arguments=args))
        yield LLMChunk("done", finish_reason="tool_calls" if turn.tool_calls else "stop")


def default_script(req: LLMRequest) -> Turn:
    """Minimal generic behaviour: answer in one line without tools (the harness fetches required data itself)."""
    return Turn(reasoning="Reviewing the data I have.", text="Done; details are in the result.")
