"""Security contract: token claims, permission matrix, redaction report."""
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, ConfigDict

from .common import Contract, IncidentId, OptionId, PlantId, Role, UserId


class AuthMode(str, Enum):
    LOCAL = "local"        # backend issues HS256 tokens from fixtures/story/users.json (dev, tests, demo fallback)
    KEYCLOAK = "keycloak"  # Keycloak (on-prem) issues RS256 tokens; backend only verifies them


class AuthConfig(Contract):
    """GET /auth/config (no auth). The UI reads this to know how to log in."""
    mode: AuthMode
    issuer: str | None = None          # http://localhost:8080/realms/plantguard
    client_id: str | None = None       # plantguard-web (public client, PKCE)
    authorize_url: str | None = None
    token_url: str | None = None
    logout_url: str | None = None


class KeycloakClaims(BaseModel):
    """Raw claims in a Keycloak access token (realm config/keycloak/plantguard-realm.json).
    Extra standard claims (jti, typ, sid, scope...) are ignored."""
    model_config = ConfigDict(extra="ignore")
    iss: str
    aud: str | list[str]
    exp: int
    iat: int
    sub: str                 # Keycloak's own UUID; NOT used as our user id
    azp: str | None = None
    preferred_username: str
    name: str | None = None
    user_id: UserId          # user attribute "user_id" -> claim, e.g. USR-lead (keeps audit ids stable)
    plant_ids: list[PlantId]  # user attribute "plant_ids" (multivalued) -> claim
    roles: list[str]          # realm roles -> claim "roles"; may include Keycloak defaults like offline_access


class AccessClaims(Contract):
    """Normalised identity the backend uses everywhere, whatever issued the token.
    Local mode: this IS the JWT body (HS256). Keycloak mode: built from KeycloakClaims via to_access_claims().
    Header: Authorization: Bearer <jwt>. WebSockets pass it as ?token=<jwt>."""
    sub: UserId
    name: str
    role: Role
    plant_ids: list[PlantId]
    iat: int
    exp: int  # 15 min
    sid: str | None = None  # additive (v1.2): server-side session id (local mode); revoked sessions are rejected


class ApprovalClaims(Contract):
    """Approval JWT, returned by POST /incidents/{id}/approve, required by every write tool."""
    typ: str = "approval"
    sub: UserId
    incident_id: IncidentId
    option_id: OptionId
    allowed_actions: list[str]  # work_order | stock_transfer | speed_change
    jti: str  # single use
    iat: int
    exp: int  # 10 min


class Permission(str, Enum):
    VIEW_PLANT = "view_plant"
    VIEW_COSTS = "view_costs"
    RUN_AGENTS = "run_agents"
    APPROVE = "approve"
    SUBMIT_FEEDBACK = "submit_feedback"
    VIEW_AUDIT = "view_audit"
    RUN_SIMULATOR = "run_simulator"
    PERFORM_WORK = "perform_work"  # additive (v1.2): start/finish repairs, close incidents (assigned technician only)


ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.OPERATOR: {Permission.VIEW_PLANT, Permission.RUN_AGENTS, Permission.SUBMIT_FEEDBACK},
    Role.MAINTENANCE_LEAD: {Permission.VIEW_PLANT, Permission.VIEW_COSTS, Permission.RUN_AGENTS,
                            Permission.APPROVE, Permission.SUBMIT_FEEDBACK},
    Role.PLANT_MANAGER: {Permission.VIEW_PLANT, Permission.VIEW_COSTS, Permission.RUN_AGENTS,
                         Permission.APPROVE, Permission.VIEW_AUDIT},
    Role.ADMIN: set(Permission),
    Role.TECHNICIAN: {Permission.VIEW_PLANT, Permission.RUN_AGENTS, Permission.SUBMIT_FEEDBACK, Permission.PERFORM_WORK},
}


# A user with several realm roles gets the strongest one.
ROLE_PRIORITY: list[Role] = [Role.ADMIN, Role.PLANT_MANAGER, Role.MAINTENANCE_LEAD, Role.TECHNICIAN, Role.OPERATOR]


def to_access_claims(kc: KeycloakClaims) -> AccessClaims:
    """Keycloak token -> our claims. Raises ValueError if the user has none of our roles."""
    for role in ROLE_PRIORITY:
        if role.value in kc.roles:
            return AccessClaims(sub=kc.user_id, name=kc.name or kc.preferred_username, role=role,
                                plant_ids=kc.plant_ids, iat=kc.iat, exp=kc.exp)
    raise ValueError("user has no PlantGuard role")


class RedactionFinding(Contract):
    kind: str  # serial_number | person_name | email | plc_tag | ip_address
    original_hash: str  # sha256, never the original value
    placeholder: str  # e.g. <SERIAL_1>


class RedactionReport(Contract):
    """Logged for every LLM call; shown in the Security panel."""
    call_id: str
    agent: str
    ts: datetime
    model: str  # LLM_MODEL from .env, e.g. claude-sonnet-4-5
    findings: list[RedactionFinding]
    chars_in: int
    chars_out: int
