"""Decides whether a write (work order, stock transfer) is covered by an approval. Used by REST and ToolGateway."""
from typing import Protocol

from plantguard_contracts.schemas.common import ErrorCode
from plantguard_contracts.schemas.incidents import Option
from plantguard_contracts.schemas.security import ApprovalClaims

from app.services.approval_tokens import ApprovalError, ApprovalTokenService


class ApprovedOptions(Protocol):
    """Anything that can say which option was approved for an incident (the incident repository)."""

    def approved_option(self, incident_id: str, option_id: str) -> Option | None:
        """The option, only if the incident is approved/executed with exactly that option."""


class WriteAuthorizer:
    """Allows a write only if a valid token covers it AND the draft equals the approved option's draft."""

    def __init__(self, tokens: ApprovalTokenService, options: ApprovedOptions):
        """tokens: verifies/spends tokens; options: finds the approved option."""
        self._tokens, self._options = tokens, options

    def authorize(self, token: str, incident_id: str, kind: str, payload: dict, *, consume: bool) -> ApprovalClaims:
        """payload = the draft being written (without the token). It must equal a draft of the approved option,
        so a stolen or reused token cannot be used to write anything else."""
        claims = self._tokens.verify(token, incident_id, kind)
        option = self._options.approved_option(incident_id, claims.option_id)
        if option is None or not any(a.kind == kind and a.draft.model_dump(mode="json") == payload
                                     for a in option.actions if hasattr(a, "draft")):
            raise ApprovalError(ErrorCode.APPROVAL_INVALID, "Request does not match the approved action")
        if consume:
            self._tokens.consume(claims, kind)
        return claims
