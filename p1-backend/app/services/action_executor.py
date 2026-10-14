"""ActionExecutor: runs the actions of an approved option. One handler per action kind (OCP: new kind = new class).
Agents never call write tools; this is the only caller (through ToolGateway, so it is validated and audited)."""
from dataclasses import dataclass
from typing import Protocol

from plantguard_contracts.interfaces import AuditLogger, MESConnector, ToolGateway
from plantguard_contracts.schemas.approvals import ActionResult
from plantguard_contracts.schemas.incidents import Option, SpeedChangeAction, StockTransferAction, WorkOrderAction

EXECUTOR = "executor"


@dataclass(frozen=True)
class ExecutionContext:
    """What a handler needs to know about the approval it is acting on."""

    incident_id: str
    approval_token: str
    approver_id: str


class ActionHandler(Protocol):
    """Runs one kind of action (`kind`). Add a new class to support a new action kind."""

    kind: str
    async def execute(self, action, ctx: ExecutionContext) -> ActionResult:
        """Perform the action and return an ActionResult."""


class SpeedChangeHandler:
    """Applies an approved line speed change through the MES connector."""

    kind = "speed_change"

    def __init__(self, mes: MESConnector, audit: AuditLogger):
        """mes: connector that stores the new speed; audit: where the change is logged."""
        self._mes, self._audit = mes, audit

    async def execute(self, action: SpeedChangeAction, ctx: ExecutionContext) -> ActionResult:
        """Set the line speed, write an audit entry and report success."""
        self._mes.set_line_speed(action.line_id, action.speed_pct, action.start, action.end, incident_id=ctx.incident_id)
        self._audit.log("system", EXECUTOR, "set_line_speed", action.line_id,
                        {"approved_by": ctx.approver_id, "incident_id": ctx.incident_id, "speed_pct": action.speed_pct})
        return ActionResult(kind=self.kind, ref_id=action.line_id, ok=True, message=action.description)


class PurchaseOrderHandler:
    """v1.2: orders the part from the supplier. Same guarantee as the write tools: the approval token must cover a
    purchase_order and the draft must equal the approved option's draft (WriteAuthorizer), and the token is spent."""

    kind = "purchase_order"

    def __init__(self, erp, authorizer, audit: AuditLogger):
        self._erp, self._auth, self._audit = erp, authorizer, audit

    async def execute(self, action, ctx: ExecutionContext) -> ActionResult:
        draft = action.draft.model_dump(mode="json")
        self._auth.authorize(ctx.approval_token, ctx.incident_id, self.kind, draft, consume=True)
        po = self._erp.create_purchase_order(action.draft)
        self._audit.log("system", EXECUTOR, "create_purchase_order", po.id,
                        {"approved_by": ctx.approver_id, "incident_id": ctx.incident_id, "sku": po.sku,
                         "supplier_id": po.supplier_id, "eta": po.eta.isoformat()})
        return ActionResult(kind=self.kind, ref_id=po.id, ok=True, message=action.description)


class ToolWriteHandler:
    """work_order and stock_transfer both go through the ToolGateway write tools."""

    def __init__(self, kind: str, tool: str, gateway: ToolGateway, audit: AuditLogger):
        """kind: action kind; tool: gateway write tool to call; gateway/audit: injected services."""
        self.kind, self._tool, self._gateway, self._audit = kind, tool, gateway, audit

    async def execute(self, action: WorkOrderAction | StockTransferAction, ctx: ExecutionContext) -> ActionResult:
        """Call the write tool through the ToolGateway with the draft and approval token, audit the result and return the
        created record's id."""
        args = action.draft.model_dump(mode="json") | {"approval_token": ctx.approval_token}
        created = await self._gateway.call(EXECUTOR, self._tool, args, ctx.incident_id)
        self._audit.log("system", EXECUTOR, f"create_{self.kind}", created["id"],
                        {"approved_by": ctx.approver_id, "incident_id": ctx.incident_id})
        return ActionResult(kind=self.kind, ref_id=created["id"], ok=True, message=action.description)


class ActionExecutor:
    """Runs every action of an approved option using the handler registered for its kind."""

    def __init__(self, handlers: list[ActionHandler]):
        """handlers: one per action kind; they are indexed by `kind`."""
        self._handlers = {h.kind: h for h in handlers}

    async def execute(self, option: Option, ctx: ExecutionContext) -> list[ActionResult]:
        """Run the option's actions in order. Each result is reported on its own: an unknown kind or an exception becomes
        ok=False and the remaining actions still run."""
        results = []
        for action in option.actions:
            handler = self._handlers.get(action.kind)
            if handler is None:
                results.append(ActionResult(kind=action.kind, ok=False, message="No executor for this action kind"))
                continue
            try:
                results.append(await handler.execute(action, ctx))
            except Exception as e:  # report per action; the others still run
                results.append(ActionResult(kind=action.kind, ok=False, message=f"{type(e).__name__}: {e}"[:200]))
        return results
