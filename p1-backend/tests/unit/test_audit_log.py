from app.services.audit_log import HashChainAuditLog


def test_audit_chain_detects_tampering():
    """Changing a stored entry makes verify() fail at that entry."""
    log = HashChainAuditLog()
    for i in range(5):
        log.log("user", "USR-lead", "a", f"R{i}", {"i": i})
    assert log.verify() == (True, 5, None)
    log._entries[2]["details"]["i"] = 99  # someone edits history
    assert log.verify() == (False, 3, 3)


def test_audit_chain_detects_deleted_entry():
    """Removing an entry breaks the chain."""
    log = HashChainAuditLog()
    for i in range(4):
        log.log("system", "x", "a", "r")
    del log._entries[1]
    assert log.verify()[0] is False


def test_audit_paging_is_newest_first():
    """list() returns newest first and the cursor pages backwards."""
    log = HashChainAuditLog()
    for i in range(5):
        log.log("system", "x", "a", "r")
    page, total, nxt = log.list(limit=2)
    assert [e.seq for e in page] == [5, 4] and total == 5 and nxt == 4
    page, _, nxt = log.list(limit=2, before_seq=nxt)
    assert [e.seq for e in page] == [3, 2]
