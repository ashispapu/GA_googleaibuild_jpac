"""Redactor (AG-2). Runs on EVERY prompt before it reaches the LLM."""
import hashlib, re, uuid
from datetime import datetime, timezone

from plantguard_contracts.schemas.security import RedactionFinding, RedactionReport

PATTERNS = {
    "serial_number": r"\bSN-\d{4,}-[A-Z]\b",
    "email": r"\b[\w.]+@[\w.]+\.\w+\b",
    "ip_address": r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
    "plc_tag": r"\bL\d+\.ST\d+\.[A-Z0-9]+\.[A-Z_]+\b",
}
NAMES = ["Suresh", "Meera", "Arjun", "Ravi", "Anita", "Karan"]  # from fixtures/story/technicians + users


class RegexRedactor:  # implements interfaces.Redactor
    def redact(self, text: str, agent: str) -> tuple[str, RedactionReport]:
        chars_in, findings, n = len(text), [], 0
        rules = dict(PATTERNS, person_name=r"\b(?:" + "|".join(NAMES) + r")\b")
        for kind, pat in rules.items():
            for m in sorted(set(re.findall(pat, text))):
                n += 1
                ph = f"<{kind.upper()}_{n}>"
                findings.append(RedactionFinding(kind=kind, original_hash=hashlib.sha256(m.encode()).hexdigest(), placeholder=ph))
                text = text.replace(m, ph)
        return text, RedactionReport(call_id=uuid.uuid4().hex[:8], agent=agent, ts=datetime.now(timezone.utc),
                                     model="configured-by-llm-client", findings=findings, chars_in=chars_in, chars_out=len(text))

    def restore(self, text: str, report: RedactionReport) -> str:
        return text  # TODO: keep a per-call map in memory (never in the report) to put values back
