from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect

from plantguard_contracts.schemas.common import Page
from plantguard_contracts.schemas.ot import Alarm, Signal, SignalSeries
from plantguard_contracts.schemas.security import AccessClaims

from app.container import Container
from app.routers.deps import VIEW, container, ws_user

router = APIRouter(prefix="/ot", tags=["ot"])


@router.get("/assets/{asset_id}/signals", response_model=list[SignalSeries])
def signals(asset_id: str, signals: list[Signal] = Query(default=[]), minutes: int = Query(360, ge=1, le=10080),
            claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /ot/assets/{asset_id}/signals"""
    return c.ot.get_signals(asset_id, signals, minutes)


@router.get("/alarms", response_model=Page[Alarm])
def alarms(plant_id: str | None = None, active: bool = True, claims: AccessClaims = VIEW,
           c: Container = Depends(container)):
    """GET /ot/alarms: active alarms, optionally for one plant (plant scope enforced)."""
    if plant_id:
        c.policy.check_plant(claims, plant_id)
    items = c.ot.get_alarms(plant_id, active)
    return Page[Alarm](items=items, total=len(items))


@router.websocket("/stream")
async def stream(ws: WebSocket, token: str, line_id: str = "L3"):
    """One TelemetryMessage per asset per second."""
    claims = await ws_user(ws, token)
    if claims is None:
        return
    c: Container = ws.app.state.container
    try:
        plant_id, _ = c.directory.line_assets(line_id, claims.plant_ids)
    except KeyError:
        await ws.close(code=4404)
        return
    await ws.accept()
    try:
        async for msg in c.ot.subscribe(plant_id, line_id):
            await ws.send_text(msg.model_dump_json())
    except (WebSocketDisconnect, RuntimeError):
        pass
