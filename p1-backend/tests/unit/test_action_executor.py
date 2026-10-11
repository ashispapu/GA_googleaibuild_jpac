import asyncio

from app.core.config import Settings
from app.services.action_executor import ActionExecutor, ExecutionContext
from plantguard_contracts.schemas.approvals import ActionResult


def test_executor_reports_failed_action_and_runs_the_rest(container):
    """One failing action is reported ok=False and the others still run."""
    from plantguard_contracts.schemas.incidents import OptionsResponse
    opts = OptionsResponse.model_validate_json(
        (Settings().fixtures_dir / "options_INC-0042.json").read_text()).options[1]

    class Boom:
        kind = "stock_transfer"
        async def execute(self, action, ctx): raise RuntimeError("SAP down")

    class Ok:
        def __init__(self, kind): self.kind = kind
        async def execute(self, action, ctx): return ActionResult(kind=self.kind, ok=True, message="done")

    res = asyncio.run(ActionExecutor([Ok("speed_change"), Boom(), Ok("work_order")]).execute(
        opts, ExecutionContext("INC-0042", "t", "USR-lead")))
    assert [r.ok for r in res] == [True, False, True] and "SAP down" in res[1].message
