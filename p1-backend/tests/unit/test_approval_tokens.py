import pytest

from app.services.approval_tokens import ApprovalError, ApprovalTokenService
from plantguard_contracts.schemas.common import ErrorCode


def test_approval_token_scope_expiry_and_single_use():
    """Token must match incident and action, expires, and works once per action."""
    now = [1000.0]
    svc = ApprovalTokenService("s" * 32, ttl_s=600, clock=lambda: now[0])
    tok = svc.issue("USR-lead", "INC-0042", "OPT-B", {"work_order"})
    claims = svc.verify(tok, "INC-0042", "work_order")
    with pytest.raises(ApprovalError):  # other incident
        svc.verify(tok, "INC-0043", "work_order")
    with pytest.raises(ApprovalError):  # other action kind
        svc.verify(tok, "INC-0042", "stock_transfer")
    svc.consume(claims, "work_order")
    with pytest.raises(ApprovalError):  # replay
        svc.consume(claims, "work_order")
    now[0] += 601
    with pytest.raises(ApprovalError) as e:
        svc.verify(tok, "INC-0042", "work_order")
    assert e.value.code == ErrorCode.APPROVAL_INVALID


def test_empty_token_is_approval_required():
    """No token gives APPROVAL_REQUIRED."""
    with pytest.raises(ApprovalError) as e:
        ApprovalTokenService("s" * 32).verify("", "INC-0042", "work_order")
    assert e.value.code == ErrorCode.APPROVAL_REQUIRED
