"""Append-only, hash-chained audit log (BE-8). hash = sha256(prev_hash + canonical_json(entry without hashes))."""
import hashlib
import json
import threading
from typing import Iterable

from plantguard_contracts.schemas.audit import AuditEntry

from app.core.common import Clock, utcnow

GENESIS = "0" * 64


def canonical_json(obj) -> str:
    """Stable JSON (sorted keys, no spaces) so the same entry always hashes the same."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def entry_hash(prev_hash: str, entry: dict) -> str:
    """sha256 of the previous hash plus the entry's canonical JSON (hash fields excluded)."""
    body = {k: v for k, v in entry.items() if k not in ("prev_hash", "hash")}
    return hashlib.sha256((prev_hash + canonical_json(body)).encode()).hexdigest()


class HashChainAuditLog:  # implements interfaces.AuditLogger
    """Append-only audit log; each entry stores the previous entry's hash, so edits are detectable."""

    def __init__(self, clock: Clock = utcnow, seed: Iterable[dict] = ()):
        """clock: timestamp source; seed: historic entries to load first (fixtures/story/audit_seed)."""
        self._clock, self._entries, self._lock = clock, [], threading.Lock()
        for s in seed:
            self.log(s["actor_type"], s["actor"], s["action"], s["resource"], s.get("details"))

    def log(self, actor_type: str, actor: str, action: str, resource: str, details: dict | None = None) -> AuditEntry:
        """Append one entry (actor, action, resource, details) linked to the previous hash and return it."""
        with self._lock:
            prev = self._entries[-1]["hash"] if self._entries else GENESIS
            entry = {"seq": len(self._entries) + 1, "ts": self._clock().isoformat(), "actor_type": actor_type,
                     "actor": actor, "action": action, "resource": resource, "details": details or {}}
            entry["prev_hash"] = prev
            entry["hash"] = entry_hash(prev, entry)
            self._entries.append(entry)
            return AuditEntry(**entry)

    def verify(self) -> tuple[bool, int, int | None]:
        """(valid, entries checked, seq of the first broken entry)."""
        with self._lock:
            prev = GENESIS
            for i, e in enumerate(self._entries, start=1):
                if e["seq"] != i or e["prev_hash"] != prev or e["hash"] != entry_hash(prev, e):
                    return False, i, e["seq"]
                prev = e["hash"]
            return True, len(self._entries), None

    def list(self, incident_id: str | None = None, limit: int = 100,
             before_seq: int | None = None) -> tuple[list[AuditEntry], int, int | None]:
        """Newest first. Returns (page, total matching, next cursor = seq to pass as before_seq)."""
        with self._lock:
            match = [e for e in self._entries if not incident_id or e["resource"] == incident_id
                     or e["details"].get("incident_id") == incident_id]
            older = [e for e in match if before_seq is None or e["seq"] < before_seq]
            page = older[::-1][:limit]
            more = len(older) > len(page)
            return [AuditEntry(**e) for e in page], len(match), (page[-1]["seq"] if more else None)
