"""Users, sessions, audit chain and id sequences stored in the IT database (simulated plant)."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timedelta, timezone

from plantguard_contracts.schemas.audit import AuditEntry
from plantguard_contracts.schemas.common import Role
from plantguard_contracts.schemas.workflow import SessionInfo

from app.services.audit_log import GENESIS, entry_hash
from app.sim.clock import iso, parse_ts
from app.sim.db import Database
from app.sim.seed import check_password

SESSION_TTL = timedelta(hours=12)       # absolute
SESSION_IDLE = timedelta(minutes=60)    # no request for this long -> expired
TOUCH_EVERY = timedelta(seconds=30)


def real_now() -> datetime:
    return datetime.now(timezone.utc)


class SqlUserDirectory:
    """UserDirectory over it.db (hashed passwords)."""

    def __init__(self, it: Database):
        self.it = it

    def _user(self, r) -> dict:
        return {"id": r["id"], "username": r["username"], "name": r["name"], "role": r["role"],
                "technician_id": r["technician_id"],
                "plant_ids": [p["plant_id"] for p in self.it.q("SELECT plant_id FROM user_plants WHERE user_id=? ORDER BY plant_id",
                                                               (r["id"],))]}

    def find(self, username: str) -> dict | None:
        r = self.it.one("SELECT * FROM users WHERE username=? AND active=1", (username,))
        return self._user(r) if r else None

    def verify(self, username: str, password: str) -> dict | None:
        r = self.it.one("SELECT * FROM users WHERE username=? AND active=1", (username,))
        if r is None or not check_password(password, r["password_hash"]):
            return None
        return self._user(r)


class SessionStore:
    def __init__(self, it: Database):
        self.it = it
        self._touched: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def create(self, user_id: str, user_agent: str | None, ip: str | None) -> str:
        sid = uuid.uuid4().hex
        now = real_now()
        self.it.x("INSERT INTO sessions(id, user_id, created_at, last_seen_at, expires_at, user_agent, ip) VALUES(?,?,?,?,?,?,?)",
                  (sid, user_id, iso(now), iso(now), iso(now + SESSION_TTL), (user_agent or "")[:200], ip))
        return sid

    def check(self, sid: str, user_id: str) -> bool:
        """Active, same user, not revoked, not expired, not idle. Touches last_seen (throttled)."""
        r = self.it.one("SELECT * FROM sessions WHERE id=?", (sid,))
        now = real_now()
        if r is None or r["user_id"] != user_id or r["revoked_at"] or parse_ts(r["expires_at"]) <= now:
            return False
        if now - parse_ts(r["last_seen_at"]) > SESSION_IDLE:
            self.it.x("UPDATE sessions SET revoked_at=?, revoked_by='idle' WHERE id=?", (iso(now), sid))
            return False
        with self._lock:
            last = self._touched.get(sid)
            if last is None or now - last > TOUCH_EVERY:
                self._touched[sid] = now
                self.it.x("UPDATE sessions SET last_seen_at=? WHERE id=?", (iso(now), sid))
        return True

    def revoke(self, sid: str, by: str) -> bool:
        n = self.it.one("SELECT revoked_at FROM sessions WHERE id=?", (sid,))
        if n is None:
            return False
        if not n["revoked_at"]:
            self.it.x("UPDATE sessions SET revoked_at=?, revoked_by=? WHERE id=?", (iso(real_now()), by, sid))
        return True

    def owner(self, sid: str) -> str | None:
        return self.it.val("SELECT user_id FROM sessions WHERE id=?", (sid,))

    def list(self, user_id: str | None, current: str | None, include_ended: bool = False) -> list[SessionInfo]:
        sql = "SELECT s.*, u.name, u.role FROM sessions s JOIN users u ON u.id=s.user_id"
        args: list = []
        if user_id:
            sql += " WHERE s.user_id=?"
            args.append(user_id)
        rows = self.it.q(sql + " ORDER BY s.last_seen_at DESC LIMIT 200", args)
        now = real_now()
        out = []
        for r in rows:
            active = not r["revoked_at"] and parse_ts(r["expires_at"]) > now and now - parse_ts(r["last_seen_at"]) <= SESSION_IDLE
            if not active and not include_ended:
                continue
            out.append(SessionInfo(id=r["id"], user_id=r["user_id"], user_name=r["name"], role=Role(r["role"]),
                                   created_at=parse_ts(r["created_at"]), last_seen_at=parse_ts(r["last_seen_at"]),
                                   expires_at=parse_ts(r["expires_at"]),
                                   revoked_at=parse_ts(r["revoked_at"]) if r["revoked_at"] else None,
                                   user_agent=r["user_agent"], ip=r["ip"], current=r["id"] == current, active=active))
        return out


class SqlAuditLog:
    """Hash-chained audit log persisted in it.db (same API as HashChainAuditLog)."""

    def __init__(self, it: Database, clock):
        self.it, self._clock, self._lock = it, clock, threading.Lock()

    def log(self, actor_type: str, actor: str, action: str, resource: str, details: dict | None = None) -> AuditEntry:
        # One chain for every process: read-last + append happen in one transaction under a database-wide lock
        # (Postgres advisory lock; SQLite is single-process and its write transaction already serialises).
        with self._lock, self.it.tx() as c:
            self.it.advisory_lock(c, "plantguard-audit-chain")
            last = c.execute("SELECT seq, hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
            seq, prev = (last["seq"] + 1, last["hash"]) if last else (1, GENESIS)
            entry = {"seq": seq, "ts": self._clock().isoformat(), "actor_type": actor_type, "actor": actor,
                     "action": action, "resource": resource, "details": json.loads(json.dumps(details or {}, default=str))}
            entry["prev_hash"] = prev
            entry["hash"] = entry_hash(prev, entry)
            c.execute("INSERT INTO audit_log(seq, ts, actor_type, actor, action, resource, details, prev_hash, hash) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", (seq, entry["ts"], actor_type, actor, action, resource,
                                                    json.dumps(entry["details"], default=str), prev, entry["hash"]))
            return AuditEntry(**entry)

    def _entry(self, r) -> dict:
        return {"seq": r["seq"], "ts": r["ts"], "actor_type": r["actor_type"], "actor": r["actor"], "action": r["action"],
                "resource": r["resource"], "details": json.loads(r["details"]), "prev_hash": r["prev_hash"], "hash": r["hash"]}

    def verify(self) -> tuple[bool, int, int | None]:
        with self._lock:
            prev, n = GENESIS, 0
            for r in self.it.q("SELECT * FROM audit_log ORDER BY seq"):
                n += 1
                e = self._entry(r)
                if e["seq"] != n or e["prev_hash"] != prev or e["hash"] != entry_hash(prev, e):
                    return False, n, e["seq"]
                prev = e["hash"]
            return True, n, None

    def list(self, incident_id: str | None = None, limit: int = 100,
             before_seq: int | None = None) -> tuple[list[AuditEntry], int, int | None]:
        where, args = [], []
        if incident_id:
            where.append("(resource=? OR " + ("(details::jsonb->>'incident_id')" if self.it.dialect == "postgres"
                                              else "json_extract(details, '$.incident_id')") + "=?)")
            args += [incident_id, incident_id]
        total = self.it.val("SELECT COUNT(*) FROM audit_log" + (" WHERE " + " AND ".join(where) if where else ""), args, 0)
        if before_seq is not None:
            where.append("seq < ?")
            args.append(before_seq)
        rows = self.it.q("SELECT * FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "") +
                         " ORDER BY seq DESC LIMIT ?", [*args, limit + 1])
        page = rows[:limit]
        more = len(rows) > limit
        return [AuditEntry(**self._entry(r)) for r in page], total, (page[-1]["seq"] if more and page else None)


class DbSequence:
    """IdSequence-compatible counter persisted in it.db (APR-0007, FB-0031 ...)."""

    def __init__(self, it: Database, name: str, prefix: str, start: int, width: int = 4):
        self.it, self.name, self.prefix, self.start, self.width = it, name, prefix, start, width

    def next(self) -> str:
        return f"{self.prefix}{str(self.it.next_id(self.name, self.start)).zfill(self.width)}"

    def reset(self) -> None:
        return None
