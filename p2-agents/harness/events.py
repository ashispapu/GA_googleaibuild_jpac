"""EventEmitter: the only place AgentEvents are built. Owns `seq`, timestamps, clipping (THOUGHT <= 200 chars, C3),
redaction restore, and live token streaming.

Streaming model (additive contract v1.1):
  thought_delta / message_delta chunks share a `stream_id`; the stream is closed by ONE regular event with the same
  stream_id (THOUGHT for notes/reasoning, MESSAGE for answers). A UI that ignores deltas still gets the classic shape.
Deltas are coalesced (flush_chars / flush_interval_ms) so the bus is not flooded with one event per token.
"""
from __future__ import annotations

import itertools
import re
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from plantguard_contracts.schemas.incidents import Agent, AgentEvent, EventType, Evidence

from guard.redactor import RedactionVault
from harness.config import StreamingConfig

Sink = Callable[[AgentEvent], Awaitable[None]]
_MD = re.compile(r"(\*\*|__|`+|^#+\s*|^\s*[-*]\s+)", re.MULTILINE)
_OPEN_PLACEHOLDER = re.compile(r"<[A-Z_0-9]{0,40}$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def plain(text: str) -> str:
    """Markdown and line breaks out, single spaces in: THOUGHTs are plain language (C3)."""
    return re.sub(r"\s+", " ", _MD.sub("", text or "")).strip()


def clip(text: str, limit: int) -> str:
    """Plain text <= limit chars, cut at a sentence end if possible, else at a word, with an ellipsis."""
    t = plain(text)
    if len(t) <= limit:
        return t
    cut = t[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if end >= limit * 0.5:
        return cut[:end + 1]
    sp = cut[:limit - 1].rfind(" ")
    return (cut[:sp] if sp > limit * 0.5 else cut[:limit - 1]).rstrip(" ,;:-") + "…"


class DeltaStream:
    """One live text stream (reasoning or answer) of one agent turn."""

    _ids = itertools.count(1)

    def __init__(self, emitter: "EventEmitter", agent: str, kind: str, sub_agent: str | None, enabled: bool):
        self.e, self.agent, self.kind, self.sub_agent, self.enabled = emitter, agent, kind, sub_agent, enabled
        self.id = f"{emitter.incident_id}:{agent}{'.' + sub_agent if sub_agent else ''}:{kind}:{next(self._ids)}"
        self._buf, self._all, self._sent = "", [], False
        self._last_flush = time.monotonic()
        self.closed = False

    @property
    def text(self) -> str:
        return self.e.vault.restore("".join(self._all))

    @property
    def started(self) -> bool:
        return bool(self._all)

    async def push(self, chunk: str) -> None:
        if not chunk or self.closed:
            return
        self._all.append(chunk)
        if not self.enabled:
            return
        self._buf += chunk
        cfg = self.e.streaming
        due = (time.monotonic() - self._last_flush) * 1000 >= cfg.flush_interval_ms
        if len(self._buf) >= cfg.flush_chars or due:
            await self.flush()

    async def flush(self, final: bool = False) -> None:
        if not self.enabled or not self._buf:
            return
        out = self._buf
        hold = ""
        if not final:  # never cut a redaction placeholder in half: keep an open "<PERSON_NA" for the next flush
            m = _OPEN_PLACEHOLDER.search(out)
            if m:
                out, hold = out[:m.start()], out[m.start():]
        if out:
            et = EventType.THOUGHT_DELTA if self.kind == "thought" else EventType.MESSAGE_DELTA
            await self.e.emit(self.agent, et, self.e.vault.restore(out), stream_id=self.id, sub_agent=self.sub_agent,
                              raw=True)
            self._sent = True
        self._buf = hold
        self._last_flush = time.monotonic()

    async def close(self, as_type: EventType | None, message: str | None = None, **kw) -> AgentEvent | None:
        """Flush what is left and emit the closing event (THOUGHT / MESSAGE) with the same stream_id."""
        if self.closed:
            return None
        await self.flush(final=True)
        self.closed = True
        if as_type is None:
            return None
        body = message if message is not None else self.text
        if not plain(body):
            return None
        return await self.e.emit(self.agent, as_type, body, stream_id=self.id if self._sent else None,
                                 sub_agent=self.sub_agent, **kw)


class EventEmitter:
    def __init__(self, incident_id: str, streaming: StreamingConfig, sink: Sink, clock=utcnow,
                 vault: RedactionVault | None = None, start_seq: int = 0):
        self.incident_id, self.streaming, self._sink, self._clock = incident_id, streaming, sink, clock
        self.vault = vault if vault is not None else RedactionVault()
        self._seq = start_seq
        self.events: list[AgentEvent] = []

    @property
    def last_seq(self) -> int:
        return self._seq

    async def emit(self, agent: str, type: EventType, message: str = "", *, tool: str | None = None,
                   args: dict[str, Any] | None = None, evidence: list[Evidence] | None = None,
                   confidence: float | None = None, result: Any = None, duration_ms: int | None = None,
                   stream_id: str | None = None, sub_agent: str | None = None, raw: bool = False) -> AgentEvent:
        msg = message if raw else self.vault.restore(message or "")
        if not raw:
            if type in (EventType.THOUGHT, EventType.TOOL_CALL, EventType.TOOL_RESULT, EventType.EVIDENCE,
                        EventType.HANDOFF, EventType.RESULT):
                msg = clip(msg, self.streaming.thought_max_chars)
            elif type in (EventType.MESSAGE, EventType.ERROR):
                msg = msg.strip()[: self.streaming.message_max_chars]
        self._seq += 1
        ev = AgentEvent(incident_id=self.incident_id, seq=self._seq, ts=self._clock(), agent=Agent(agent), type=type,
                        message=msg, tool=tool, args=args, evidence=evidence or [], confidence=confidence,
                        result=result, duration_ms=duration_ms, stream_id=stream_id, sub_agent=sub_agent)
        self.events.append(ev)
        await self._sink(ev)
        return ev

    def stream(self, agent: str, kind: str, sub_agent: str | None = None) -> DeltaStream:
        on = self.streaming.enabled and (self.streaming.stream_thinking if kind == "thought"
                                         else self.streaming.stream_messages)
        return DeltaStream(self, agent, kind, sub_agent, on)
