"""Authentication (token -> claims), login (local mode) and RBAC / plant-scope policy (BE-2)."""
import hmac

from plantguard_contracts.auth import InvalidToken, TokenVerifier
from plantguard_contracts.interfaces import AuditLogger
from plantguard_contracts.schemas.auth import TokenRequest, TokenResponse, User
from plantguard_contracts.schemas.common import ErrorCode, Role
from plantguard_contracts.schemas.security import ROLE_PERMISSIONS, AccessClaims, AuthMode, Permission

from app.core.errors import ApiError
from app.repositories.user_directory import UserDirectory

ACCESS_TTL = 15 * 60


def user_from_claims(c: AccessClaims) -> User:
    """Turn token claims into the User object returned by /auth/me and /auth/token (permissions come from
    ROLE_PERMISSIONS)."""
    return User(id=c.sub, name=c.name, role=c.role, plant_ids=c.plant_ids,
                permissions=sorted(p.value for p in ROLE_PERMISSIONS[c.role]))


class Authenticator:
    """Single place that turns a bearer token into AccessClaims, in both AUTH_MODEs. With a session store (simulated
    plant) a token is only valid while its server-side session is: logout, revocation and idle time end it."""

    def __init__(self, verifier: TokenVerifier, sessions=None):
        """verifier: validates tokens in local or keycloak mode; sessions: optional SessionStore."""
        self.verifier, self.sessions = verifier, sessions

    def authenticate(self, token: str) -> AccessClaims:
        """Bearer token -> AccessClaims, or ApiError 401 if missing/invalid/ended session."""
        try:
            claims = self.verifier.verify(token)
        except InvalidToken:
            raise ApiError(401, ErrorCode.UNAUTHENTICATED, "Missing or invalid token") from None
        if self.sessions is not None and self.verifier.mode == AuthMode.LOCAL:
            if not claims.sid or not self.sessions.check(claims.sid, claims.sub):
                raise ApiError(401, ErrorCode.UNAUTHENTICATED, "Session ended; please sign in again")
        return claims


class LoginService:
    """POST /auth/token. Local mode only; in keycloak mode the UI logs in at Keycloak."""

    def __init__(self, verifier: TokenVerifier, users: UserDirectory, audit: AuditLogger, sessions=None):
        """verifier: issues local tokens; users: user lookup; audit: logs logins; sessions: optional SessionStore."""
        self._verifier, self._users, self._audit, self.sessions = verifier, users, audit, sessions

    def login(self, req: TokenRequest, user_agent: str | None = None, ip: str | None = None) -> TokenResponse:
        """Check username/password (constant-time), audit success or failure and return a 15-minute token plus the user.
        501 when AUTH_MODE is keycloak. With a session store, a server-side session is opened as well."""
        if self._verifier.mode != AuthMode.LOCAL:
            raise ApiError(501, ErrorCode.NOT_IMPLEMENTED, "Local login is off: log in with Keycloak (GET /auth/config)")
        if hasattr(self._users, "verify"):
            u = self._users.verify(req.username, req.password)
            ok = u is not None
        else:
            u = self._users.find(req.username)
            ok = u is not None and hmac.compare_digest(u["password"].encode(), req.password.encode())
        if not ok:
            self._audit.log("user", req.username[:64], "login_failed", "auth", {})
            raise ApiError(401, ErrorCode.UNAUTHENTICATED, "Wrong username or password")
        sid = self.sessions.create(u["id"], user_agent, ip) if self.sessions is not None else None
        self._audit.log("user", u["id"], "login", "auth", {"session": sid[:8] if sid else None})
        return self.token_for(u, sid)

    def token_for(self, u: dict, sid: str | None) -> TokenResponse:
        claims_token = self._verifier.issue_local(u["id"], u["name"], u["role"], u["plant_ids"], sid=sid)
        user = User(id=u["id"], name=u["name"], role=Role(u["role"]), plant_ids=u["plant_ids"],
                    permissions=sorted(p.value for p in ROLE_PERMISSIONS[Role(u["role"])]))
        return TokenResponse(access_token=claims_token, expires_in=ACCESS_TTL, user=user)


class AccessPolicy:
    """RBAC + plant scope. The backend always decides permissions, whoever issued the token."""

    def __init__(self, audit: AuditLogger):
        """audit: denied permission checks are logged here."""
        self._audit = audit

    def require(self, claims: AccessClaims, permission: Permission) -> AccessClaims:
        """Return the claims if the role has the permission; otherwise audit it and raise 403 FORBIDDEN."""
        if permission not in ROLE_PERMISSIONS[claims.role]:
            self._audit.log("user", claims.sub, f"{permission.value}_denied", "-", {"role": claims.role.value})
            raise ApiError(403, ErrorCode.FORBIDDEN, f"Role {claims.role.value} lacks {permission.value}")
        return claims

    def check_plant(self, claims: AccessClaims, plant_id: str) -> None:
        """Raise 403 PLANT_SCOPE unless the user may see this plant."""
        if plant_id not in claims.plant_ids:
            raise ApiError(403, ErrorCode.PLANT_SCOPE, f"No access to {plant_id}")
