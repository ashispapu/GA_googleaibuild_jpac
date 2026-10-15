"""Approval tokens: short-lived, tied to incident + option + action kinds, single use per action (CONTRACTS C7)."""
import threading
import time
import uuid
from typing import Callable

import jwt

from plantguard_contracts.schemas.common import ErrorCode
from plantguard_contracts.schemas.security import ApprovalClaims

APPROVAL_TTL = 10 * 60
ALGORITHM = "HS256"
ACTION_FOR_TOOL = {"create_work_order": "work_order", "create_stock_transfer": "stock_transfer"}


class ApprovalError(Exception):
    """Raised when an approval token is missing, invalid, expired, wrong-scoped or already used."""

    def __init__(self, code: ErrorCode, message: str):
        """code: APPROVAL_REQUIRED or APPROVAL_INVALID; message: readable text."""
        super().__init__(message)
        self.code, self.message = code, message


class ApprovalTokenService:
    """Issues, verifies and spends approval tokens (always HS256, signed by the backend)."""

    def __init__(self, secret: str, ttl_s: int = APPROVAL_TTL, clock: Callable[[], float] = time.time):
        """secret: signing key; ttl_s: lifetime (10 min); clock: injectable for tests."""
        self._secret, self._ttl, self._clock = secret, ttl_s, clock
        self._used: set[tuple[str, str]] = set()
        self._lock = threading.Lock()
        self.shared = None  # SharedKV: single use across every API instance (set by the container)

    def issue(self, approver_id: str, incident_id: str, option_id: str, allowed_actions: set[str]) -> str:
        """Create a token for one approver, incident, option and set of action kinds."""
        now = int(self._clock())
        claims = ApprovalClaims(sub=approver_id, incident_id=incident_id, option_id=option_id,
                                allowed_actions=sorted(allowed_actions), jti=uuid.uuid4().hex, iat=now,
                                exp=now + self._ttl)
        return jwt.encode(claims.model_dump(mode="json"), self._secret, algorithm=ALGORITHM)

    def verify(self, token: str, incident_id: str, action: str) -> ApprovalClaims:
        """Check signature, type, expiry, incident and action coverage; return the claims. Does not spend the token.
        Check signature, type, expiry, incident and action coverage; return the claims. Does not spend the token."""
        if not token:
            raise ApprovalError(ErrorCode.APPROVAL_REQUIRED, "This action needs an approval token")
        try:
            raw = jwt.decode(token, self._secret, algorithms=[ALGORITHM], options={"verify_exp": False})
            claims = ApprovalClaims(**raw)
        except Exception:
            raise ApprovalError(ErrorCode.APPROVAL_INVALID, "Approval token invalid") from None
        if raw.get("typ") != "approval" or claims.exp <= self._clock():
            raise ApprovalError(ErrorCode.APPROVAL_INVALID, "Approval token invalid or expired")
        if claims.incident_id != incident_id or action not in claims.allowed_actions:
            raise ApprovalError(ErrorCode.APPROVAL_INVALID, "Approval token does not cover this action")
        return claims

    def consume(self, claims: ApprovalClaims, action: str) -> None:
        """Mark (jti, action) as spent. A token may be used once per action kind it allows."""
        if self.shared is not None:
            if not self.shared.claim_once(f"approval:{claims.jti}:{action}", self._ttl + 60):
                raise ApprovalError(ErrorCode.APPROVAL_INVALID, "Approval token already used for this action")
            return
        with self._lock:
            if (claims.jti, action) in self._used:
                raise ApprovalError(ErrorCode.APPROVAL_INVALID, "Approval token already used for this action")
            self._used.add((claims.jti, action))
