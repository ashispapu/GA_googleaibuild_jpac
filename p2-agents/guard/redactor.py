"""Redactor (AG-2). Runs on EVERY prompt before it reaches the LLM (C7).

Serial numbers, names, emails, PLC tags and IPs become placeholders like <PERSON_NAME_1>. The mapping back to the
real value lives only in memory (a `RedactionVault`, one per agent run) and never in the RedactionReport, which keeps
hashes only. The vault keeps placeholders stable across all LLM calls of a run, so the model can refer to
"<PERSON_NAME_1>" in a later turn and the UI still shows the real name after `restore()`.
"""
from __future__ import annotations

import hashlib
import re
import threading
import uuid
from datetime import datetime, timezone

from plantguard_contracts.schemas.security import RedactionFinding, RedactionReport

PATTERNS = {
    "serial_number": r"\bSN-\d{4,}-[A-Z]\b",
    "email": r"\b[\w.]+@[\w.]+\.\w+\b",
    "ip_address": r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
    "plc_tag": r"\bL\d+\.ST\d+\.[A-Z0-9]+\.[A-Z_]+\b",
}
NAMES = ["Suresh", "Meera", "Arjun", "Ravi", "Anita", "Karan"]  # from fixtures/story/technicians + users
PLACEHOLDER_RE = re.compile(r"<([A-Z_]+)_(\d+)>")


def _rules(names: list[str]) -> dict[str, str]:
    rules = dict(PATTERNS)
    if names:
        rules["person_name"] = r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b"
    return rules


class RedactionVault:
    """In-memory, per-run map value <-> placeholder. Never serialised, never logged."""

    def __init__(self):
        self._by_value: dict[str, str] = {}
        self._by_placeholder: dict[str, str] = {}
        self._n = 0
        self._lock = threading.Lock()

    def placeholder(self, kind: str, value: str) -> tuple[str, bool]:
        with self._lock:
            if value in self._by_value:
                return self._by_value[value], False
            self._n += 1
            ph = f"<{kind.upper()}_{self._n}>"
            self._by_value[value], self._by_placeholder[ph] = ph, value
            return ph, True

    def restore(self, text: str) -> str:
        if not text or "<" not in text:
            return text
        return PLACEHOLDER_RE.sub(lambda m: self._by_placeholder.get(m.group(0), m.group(0)), text)

    @property
    def size(self) -> int:  # not __len__: an empty vault must stay truthy (`vault or new` would silently fork it)
        return len(self._by_placeholder)


class RegexRedactor:  # implements interfaces.Redactor
    def __init__(self, names: list[str] | None = None):
        self._rules = _rules(NAMES if names is None else names)
        self._vaults: dict[str, RedactionVault] = {}  # call_id -> vault, for the Protocol's restore(text, report)

    def redact(self, text: str, agent: str, vault: RedactionVault | None = None,
               model: str = "configured-by-llm-client") -> tuple[str, RedactionReport]:
        vault = vault if vault is not None else RedactionVault()
        clean, findings = self.redact_text(text, vault)
        rep = RedactionReport(call_id=uuid.uuid4().hex[:8], agent=agent, ts=datetime.now(timezone.utc), model=model,
                              findings=findings, chars_in=len(text), chars_out=len(clean))
        self._vaults[rep.call_id] = vault
        return clean, rep

    def redact_text(self, text: str, vault: RedactionVault) -> tuple[str, list[RedactionFinding]]:
        """Replace every sensitive value; return the clean text and one finding per newly seen value."""
        findings: list[RedactionFinding] = []
        if not text:
            return text, findings
        for kind, pat in self._rules.items():
            for m in sorted(set(re.findall(pat, text))):
                ph, new = vault.placeholder(kind, m)
                findings.append(RedactionFinding(kind=kind, original_hash=hashlib.sha256(m.encode()).hexdigest(),
                                                 placeholder=ph))
                text = text.replace(m, ph)
        return text, findings

    def restore(self, text: str, report: RedactionReport) -> str:
        vault = self._vaults.get(report.call_id)
        return vault.restore(text) if vault else text
