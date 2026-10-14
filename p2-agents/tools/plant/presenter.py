"""Presenters turn a raw tool result into: a TOOL_CALL label, a TOOL_RESULT summary, Evidence cards for the UI and a
compact view for the model. One presenter per contract tool, registered by the per-domain modules in this folder."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from plantguard_contracts.schemas.incidents import Evidence

from harness.config import Policy

Label = Callable[[dict], str]
Summary = Callable[[dict, dict], str]
EvidenceFn = Callable[[dict, dict, Policy], tuple[list[Evidence], str]]
Compact = Callable[[dict], Any]


@dataclass
class Presenter:
    label: Label = lambda args: ""
    summary: Summary = lambda data, args: ""
    evidence: EvidenceFn = lambda data, args, pol: ([], "")
    compact: Compact = lambda data: data


PRESENTERS: dict[str, Presenter] = {}


def presenter(tool: str, **kw) -> Presenter:
    PRESENTERS[tool] = Presenter(**kw)
    return PRESENTERS[tool]


def get_presenter(tool: str) -> Presenter:
    return PRESENTERS.get(tool) or Presenter(label=lambda a, t=tool: t.replace("_", " ").capitalize())


def pct(x: float) -> str:
    return f"{round(x * 100)}%"
