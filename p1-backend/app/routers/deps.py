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


async def ws_user(ws: WebSocket, token: str, permission: Permission = Permission.VIEW_PLANT) -> AccessClaims | None:
    """WebSocket auth via ?token=. Closes with 4401/4403 and returns None when the caller must stop."""
    c = container(ws)
    try:
        claims = c.authenticator.authenticate(token)
        return c.policy.require(claims, permission)
    except ApiError as e:
        await ws.close(code=4401 if e.status == 401 else 4403)
        return None
