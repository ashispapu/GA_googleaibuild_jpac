"""PlantGuard Garden: live position of every incident's agent in the system architecture."""
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from plantguard_contracts.schemas.security import AccessClaims
from plantguard_contracts.schemas.workflow import GardenAgent, GardenSnapshot

from app.container import Container
from app.routers.deps import VIEW, container, ws_user

router = APIRouter(prefix="/garden", tags=["garden"])


@router.get("", response_model=GardenSnapshot)
def snapshot(claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """Where each incident's agent is right now (plants you may see)."""
    if c.garden is None:
        return GardenSnapshot(now=c.clock(), agents=[])
    return c.garden.snapshot(claims)


@router.websocket("/stream")
async def stream(ws: WebSocket, token: str):
    """GardenAgent frames: the snapshot first, then every move."""
    claims = await ws_user(ws, token)
    if claims is None:
        return
    c: Container = ws.app.state.container
    await ws.accept()
    sub = c.bus.subscribe("garden.activity")
    try:
        if c.garden is not None:
            for g in c.garden.snapshot(claims).agents:
                await ws.send_text(g.model_dump_json())
        async for payload in sub:
            g = GardenAgent.model_validate(payload)
            if g.plant_id in claims.plant_ids:
                await ws.send_text(g.model_dump_json())
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        sub.close()
