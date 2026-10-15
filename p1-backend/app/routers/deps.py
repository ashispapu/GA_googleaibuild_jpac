"""FastAPI dependencies. Routers get services from the container here and never construct anything themselves."""
from fastapi import Depends, WebSocket
from starlette.requests import HTTPConnection, Request

from plantguard_contracts.schemas.security import AccessClaims, Permission

from app.container import Container
from app.core.errors import ApiError


def container(conn: HTTPConnection) -> Container:
    """Dependency: the app's Container (set on app.state in create_app)."""
    return conn.app.state.container


def current_user(request: Request, c: Container = Depends(container)) -> AccessClaims:
    """Dependency: AccessClaims from the `Authorization: Bearer` header; 401 if missing/invalid."""
    return c.authenticator.authenticate(request.headers.get("authorization", "").removeprefix("Bearer ").strip())


def require(permission: Permission):
    """Build a dependency that needs the given Permission (403 FORBIDDEN otherwise)."""
    def dependency(claims: AccessClaims = Depends(current_user), c: Container = Depends(container)) -> AccessClaims:
        """Authenticate, then check the permission through AccessPolicy."""
        return c.policy.require(claims, permission)
    return dependency


VIEW = Depends(require(Permission.VIEW_PLANT))


async def pump(ws: WebSocket, frames, close_when_done: bool = False) -> None:
    """Send every text frame from the async iterator `frames` until it ends or the client goes away.
    A watcher reads the socket at the same time, so a disconnect (or a server shutdown) stops the sender even when
    `frames` is idle; otherwise a quiet stream would keep its handler (and the process's shutdown) waiting forever."""
    import anyio
    finished = False
    async with anyio.create_task_group() as tg:
        async def watcher():
            while True:
                if (await ws.receive())["type"] == "websocket.disconnect":
                    tg.cancel_scope.cancel()
                    return
        tg.start_soon(watcher)
        async for text in frames:
            await ws.send_text(text)
        finished = True
        tg.cancel_scope.cancel()
    if finished and close_when_done:
        try:
            await ws.close()
        except RuntimeError:
            pass


async def ws_user(ws: WebSocket, token: str, permission: Permission = Permission.VIEW_PLANT) -> AccessClaims | None:
    """WebSocket auth via ?token=. Closes with 4401/4403 and returns None when the caller must stop."""
    c = container(ws)
    try:
        claims = c.authenticator.authenticate(token)
        return c.policy.require(claims, permission)
    except ApiError as e:
        await ws.close(code=4401 if e.status == 401 else 4403)
        return None
