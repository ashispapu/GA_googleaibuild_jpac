"""PlantTool: an LLM-callable wrapper around one contract ToolSpec (C4). Calls go through the ToolGateway via the run
context, so the allow-list, schema validation, timeout and audit always apply."""
from __future__ import annotations

import logging

from pydantic import ValidationError

from plantguard_contracts.tools import ToolSpec

from tools.base import Tool, ToolInvocation, ToolOutcome, clean_schema, to_content
from tools.plant.presenter import get_presenter

log = logging.getLogger("plantguard.tools")


def friendly_error(e: Exception) -> str:
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status == 404 or isinstance(e, KeyError):
        return "not found"
    if isinstance(e, PermissionError):
        return f"not allowed ({e})"
    if isinstance(e, TimeoutError):
        return "timed out"
    return type(e).__name__


class PlantTool(Tool):
    kind = "plant"

    def __init__(self, spec: ToolSpec, max_result_chars: int = 4000):
        self.spec, self.name, self.description = spec, spec.name, spec.description
        self.presenter = get_presenter(spec.name)
        self.max_result_chars = max_result_chars

    def parameters(self) -> dict:
        return clean_schema(self.spec.input.model_json_schema())

    def call_label(self, args: dict) -> str:
        return self.presenter.label(args) or super().call_label(args)

    def normalise(self, args: dict) -> dict:
        """Validate the model's arguments against the contract input (raises ValidationError)."""
        return self.spec.input.model_validate(args).model_dump(mode="json", exclude_none=True)

    async def run(self, args: dict, inv: ToolInvocation) -> ToolOutcome:
        try:
            clean = self.normalise(args)
        except ValidationError as e:
            msg = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:4])
            return ToolOutcome(content=f"ERROR invalid arguments for {self.name}: {msg}", summary="", is_error=True)
        try:
            data = await inv.ctx.call_tool(inv.principal, self.name, clean)
        except Exception as e:  # gateway errors are reported to the model, never crash the run
            log.warning("tool %s failed for %s: %r", self.name, inv.principal, e)
            why = friendly_error(e)
            return ToolOutcome(content=f"ERROR {self.name} failed: {why}", summary=f"Couldn't read data: {why}",
                               is_error=True)
        return self.present(data, clean, inv)

    def present(self, data: dict, args: dict, inv: ToolInvocation) -> ToolOutcome:
        pol = inv.ctx.config.policy("evidence")
        p = self.presenter
        try:
            evidence, ev_msg = p.evidence(data, args, pol)
        except Exception:  # a presenter bug must not cost us the data
            log.exception("evidence presenter failed for %s", self.name)
            evidence, ev_msg = [], ""
        try:
            summary = p.summary(data, args)
        except Exception:
            log.exception("summary presenter failed for %s", self.name)
            summary = ""
        try:
            compact = p.compact(data)
        except Exception:
            compact = data
        return ToolOutcome(content=to_content(compact, self.max_result_chars), data=data, summary=summary,
                           evidence=evidence, evidence_message=ev_msg)
