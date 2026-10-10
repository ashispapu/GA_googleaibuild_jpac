"""ToolGateway (BE-6). The only door agents use to reach plant data.
Checks the allow-list, validates input and output against the contract, enforces the approval check on
write tools, applies the per-tool timeout and writes an audit entry for every call."""
import asyncio
import inspect
import time

from plantguard_contracts.interfaces import AuditLogger
from plantguard_contracts.tools import TOOLS, ToolSpec

from app.services.approval_tokens import ACTION_FOR_TOOL
from app.services.write_authorizer import WriteAuthorizer
from app.services.tool_handlers import ToolHandler, to_plain

WRITE_AGENT = "executor"


class ToolTimeout(TimeoutError):
    """A tool took longer than its allowed time."""

    pass


class ToolGateway:  # implements interfaces.ToolGateway
    """Single entry for tool calls: allow-list, validation, write approval, timeout and audit."""

    def __init__(self, handlers: dict[str, ToolHandler], audit: AuditLogger, authorizer: WriteAuthorizer,
                 registry: dict[str, ToolSpec] | None = None):
        """handlers: tool name -> function; audit: call log; authorizer: write check; registry: tool specs (defaults to
        the contract's TOOLS)."""
        self._handlers, self._audit, self._authorizer = handlers, audit, authorizer
        self._registry = TOOLS if registry is None else registry

    async def call(self, agent: str, tool: str, args: dict, incident_id: str) -> dict:
        """Run a tool for an agent: deny unknown tools/agents (audited), validate the arguments, authorise writes, run
        with timeout, validate the result and always audit the call (approval_token is never logged)."""
        spec = self._registry.get(tool)
        if spec is None or agent not in spec.agents:
            self._audit.log("agent", agent, "tool_denied", tool, {"incident_id": incident_id})
            raise PermissionError(f"{agent} may not call {tool}")
        handler = self._handlers.get(tool)
        if handler is None:
            raise NotImplementedError(f"tool {tool} has no handler")
        inp = spec.input.model_validate(args)  # reject bad args
        if spec.writes:
            self._authorize_write(spec, inp, tool, incident_id)
        started, outcome = time.monotonic(), "ok"
        try:
            result = await self._invoke(handler, inp, spec.timeout_s)
            return spec.output.model_validate(to_plain(result)).model_dump(mode="json")
        except Exception as e:
            outcome = type(e).__name__
            raise
        finally:
            self._audit.log("system" if spec.writes else "agent", agent, "tool_call", tool, {
                "incident_id": incident_id, "outcome": outcome,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "args": inp.model_dump(mode="json", exclude={"approval_token"}, exclude_none=True)})

    def _authorize_write(self, spec: ToolSpec, inp, tool: str, incident_id: str) -> None:
        """Require the draft's incident_id to match the caller and a valid approval covering exactly this draft (not
        consumed here)."""
        draft = inp.model_dump(mode="json", exclude={"approval_token"})
        if draft.get("incident_id") != incident_id:
            raise PermissionError("write tool incident_id does not match the calling incident")
        # The executor is the backend's own single-run path; REST write routes are the ones that consume tokens.
        self._authorizer.authorize(inp.approval_token, incident_id, ACTION_FOR_TOOL[tool], draft, consume=False)

    @staticmethod
    async def _invoke(handler: ToolHandler, inp, timeout_s: float):
        """Run the handler (sync handlers in a thread) under the tool's timeout."""
        async def run():
            """Await an async handler or run a sync one in a worker thread."""
            if inspect.iscoroutinefunction(handler):
                return await handler(inp)
            return await asyncio.to_thread(handler, inp)
        try:
            return await asyncio.wait_for(run(), timeout_s)
        except asyncio.TimeoutError:
            raise ToolTimeout(f"tool exceeded {timeout_s}s") from None
