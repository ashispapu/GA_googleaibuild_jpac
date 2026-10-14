"""Shared check for the two REST write endpoints (work order, stock transfer)."""
from plantguard_contracts.schemas.security import AccessClaims

from app.container import Container
from app.core.errors import ApiError
from app.services.approval_tokens import ApprovalError


def authorize_write(c: Container, claims: AccessClaims, body, kind: str) -> None:
    """Write endpoints need a valid, matching, unused approval token on top of the normal permission."""
    payload = body.model_dump(mode="json", exclude={"approval_token"})
    try:
        c.write_authorizer.authorize(body.approval_token, body.incident_id, kind, payload, consume=True)
    except ApprovalError as e:
        c.audit.log("user", claims.sub, f"{kind}_rejected", body.incident_id, {"code": e.code.value})
        raise ApiError(403, e.code, e.message) from None
