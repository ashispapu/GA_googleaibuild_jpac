from fastapi import APIRouter, Depends, Request

from plantguard_contracts.schemas.auth import TokenRequest, TokenResponse, User
from plantguard_contracts.schemas.common import ErrorCode, Role
from plantguard_contracts.schemas.security import AccessClaims, AuthConfig, Permission, ROLE_PERMISSIONS
from plantguard_contracts.schemas.workflow import SessionInfo

from app.container import Container
from app.core.errors import ApiError
from app.routers.deps import container, current_user
from app.services.auth import user_from_claims

router = APIRouter(prefix="/auth", tags=["auth"])


def demo_hints() -> bool:
    """The login page's demo-user list. Off in production unless DEMO_LOGIN_HINTS=true (a demo environment)."""
    import os
    from app.core.observability import is_production
    flag = os.getenv("DEMO_LOGIN_HINTS")
    return flag.lower() in ("1", "true", "yes") if flag else not is_production()


@router.get("/config", response_model=AuthConfig)
def auth_config(c: Container = Depends(container)):
    """No auth needed. Tells the UI whether to show the local login form or redirect to Keycloak."""
    return c.verifier.config()


@router.get("/demo-users")
def demo_users(c: Container = Depends(container)) -> list[dict]:
    """No auth needed. Simulated plant + local sign-in only (empty otherwise): the demo users for the login page,
    with each technician's open jobs, so you can see whom to sign in as. Never lists anything in keycloak mode."""
    engine = c.engine
    if engine is None or c.verifier.config().mode != "local" or not demo_hints():
        return []
    it = engine.it
    out = []
    for u in it.q("SELECT id, username, name, role, technician_id FROM users WHERE active=1 ORDER BY role, username"):
        plants = [r["plant_id"] for r in it.q("SELECT plant_id FROM user_plants WHERE user_id=? ORDER BY plant_id", (u["id"],))]
        jobs = []
        if u["technician_id"]:
            jobs = [{"incident_id": r["id"], "asset_id": r["asset_id"], "status": r["status"]} for r in it.q(
                "SELECT id, asset_id, status FROM incidents WHERE assigned_technician_id=? "
                "AND status NOT IN ('resolved', 'rejected') ORDER BY id", (u["technician_id"],))]
        signoffs = []
        pol = c.incidents.approval_policy
        if pol is not None and u["role"] in ("maintenance_lead", "plant_manager", "admin"):
            claims = AccessClaims(sub=u["id"], name=u["name"], role=Role(u["role"]), plant_ids=plants, iat=0, exp=1)
            signoffs = [{"incident_id": r.incident.id, "step": s.label} for r in c.incidents.repo.all()
                        for s in pol.waits_for(r.incident, claims)]
        out.append({"username": u["username"], "name": u["name"], "role": u["role"], "plant_ids": plants,
                    "technician_id": u["technician_id"], "open_jobs": jobs, "signoffs": signoffs})
    return out


@router.post("/token", response_model=TokenResponse)
def login(body: TokenRequest, request: Request, c: Container = Depends(container)):
    """Local mode only. In keycloak mode the UI logs in at Keycloak (see /auth/config). Opens a server-side session
    on the simulated plant."""
    return c.login.login(body, request.headers.get("user-agent"), request.client.host if request.client else None)


@router.get("/me", response_model=User)
def me(claims: AccessClaims = Depends(current_user)):
    """The logged-in user's id, name, role, plants and permissions."""
    return user_from_claims(claims)


@router.post("/refresh", response_model=TokenResponse)
def refresh(claims: AccessClaims = Depends(current_user), c: Container = Depends(container)):
    """A fresh 15-minute token for the same (still active) session."""
    u = {"id": claims.sub, "name": claims.name, "role": claims.role.value, "plant_ids": claims.plant_ids}
    return c.login.token_for(u, claims.sid)


@router.post("/logout", status_code=204)
def logout(claims: AccessClaims = Depends(current_user), c: Container = Depends(container)):
    """Ends the session server-side: the token stops working immediately."""
    if c.sessions is not None and claims.sid:
        c.sessions.revoke(claims.sid, claims.sub)
    c.audit.log("user", claims.sub, "logout", "auth", {})


@router.get("/sessions", response_model=list[SessionInfo])
def sessions(all_users: bool = False, include_ended: bool = False, claims: AccessClaims = Depends(current_user),
             c: Container = Depends(container)):
    """Your active sessions; admins and users with view_audit may list everyone's (?all_users=true)."""
    if c.sessions is None:
        return []
    if all_users and Permission.VIEW_AUDIT not in ROLE_PERMISSIONS[claims.role]:
        raise ApiError(403, ErrorCode.FORBIDDEN, "Only admins and auditors can see everyone's sessions")
    return c.sessions.list(None if all_users else claims.sub, claims.sid, include_ended)


@router.delete("/sessions/{sid}", status_code=204)
def end_session(sid: str, claims: AccessClaims = Depends(current_user), c: Container = Depends(container)):
    """End one of your sessions (admins: anyone's)."""
    if c.sessions is None:
        raise KeyError(sid)
    owner = c.sessions.owner(sid)
    if owner is None:
        raise KeyError(sid)
    if owner != claims.sub and claims.role != Role.ADMIN:
        raise ApiError(403, ErrorCode.FORBIDDEN, "Not your session")
    c.sessions.revoke(sid, claims.sub)
    c.audit.log("user", claims.sub, "end_session", "auth", {"user": owner})
