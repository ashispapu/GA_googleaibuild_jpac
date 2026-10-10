import asyncio
import time

import pytest

from app.services.tool_gateway import ToolGateway, ToolTimeout
from data import WORK_ORDER_DRAFT
from plantguard_contracts.tools import TOOLS


def test_gateway_enforces_allow_list_and_audits(container):
    """An agent may only call its own tools; denials are audited."""
    gw = container.tool_gateway
    with pytest.raises(PermissionError):
        asyncio.run(gw.call("energy", "check_stock", {"sku": "SKF-6205"}, "INC-0042"))
    with pytest.raises(PermissionError):  # LLM agents can never use write tools
        asyncio.run(gw.call("diagnosis", "create_work_order", {}, "INC-0042"))
    out = asyncio.run(gw.call("spare_parts", "check_stock", {"sku": "SKF-6205"}, "INC-0042"))
    assert out["sku"] == "SKF-6205"
    actions = [e.action for e in container.audit.list(limit=5)[0]]
    assert actions[0] == "tool_call" and actions.count("tool_denied") == 2


def test_gateway_serves_every_read_tool(container):
    """Every read tool in the contract returns valid output."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "p2-agents"))
    import test_tools_on_mock as t  # the shared list of one valid call per read tool
    for tool, (agent, args) in t.CALLS.items():
        asyncio.run(container.tool_gateway.call(agent, tool, args, "INC-0042"))
    assert set(t.CALLS) == {n for n, s in TOOLS.items() if not s.writes}


def test_gateway_rejects_bad_args(container):
    """Invalid arguments are rejected before the tool runs."""
    with pytest.raises(Exception) as e:
        asyncio.run(container.tool_gateway.call("spare_parts", "check_stock", {"sku": "x", "evil": 1}, "INC-0042"))
    assert "evil" in str(e.value)


def test_gateway_times_out_slow_tools(container):
    """A tool slower than its timeout raises ToolTimeout."""
    slow = lambda a: time.sleep(0.5)  # noqa: E731
    spec = TOOLS["get_tariffs"]
    fast_spec = type(spec)(spec.name, spec.description, spec.input, spec.output, spec.endpoint, spec.agents,
                           spec.writes, 0.05)
    gw = ToolGateway({"get_tariffs": slow}, container.audit, container.write_authorizer, {"get_tariffs": fast_spec})
    with pytest.raises(ToolTimeout):
        asyncio.run(gw.call("energy", "get_tariffs", {}, "INC-0042"))


def test_gateway_write_needs_approval_and_token_never_reaches_audit(container):
    """Writes need a valid approval and the token is never written to the audit log."""
    draft = WORK_ORDER_DRAFT
    with pytest.raises(Exception) as e:
        asyncio.run(container.tool_gateway.call("executor", "create_work_order", draft | {"approval_token": "bad"},
                                                "INC-0042"))
    assert "pprov" in type(e.value).__name__ + str(e.value)
    assert "approval_token" not in str([x.details for x in container.audit.list(limit=50)[0]])
