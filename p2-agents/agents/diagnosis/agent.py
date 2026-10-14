"""Diagnosis: finds the root cause. The LLM reads the evidence and proposes wording (root cause, alternatives);
the failure mode is validated, and the CONFIDENCE is computed in code from which independent sources agree
([policy] w_* weights). With the shipped weights the story gives 0.60 (C9: "the twist")."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from plantguard_contracts.schemas.incidents import Diagnosis, Evidence

from domain.planning import required_skill
from harness.base_agent import AgentRunResult, BaseAgent, FinalResult
from harness.context import RunContext

FailureMode = Literal["bearing_wear", "overheating", "misalignment", "unknown"]


class DiagnosisDraft(BaseModel):
    root_cause: str = Field(description="Most likely root cause in plain words, max 80 chars, e.g. the part and how it fails")
    failure_mode: FailureMode = Field(description="Failure mode category")
    alternatives: list[str] = Field(default_factory=list, description="Other causes you could not rule out, each "
                                    "with a short reason (max 3)")
    required_parts: list[str] = Field(default_factory=list, description="SKUs from the bill of materials needed for "
                                      "the repair")


def _words(text: str) -> str:
    """'Rear spindle bearing 6205-2RS' -> 'Rear spindle bearing' (drop part-number tokens)."""
    return " ".join(w for w in text.split() if not re.search(r"\d", w)).strip(" ,")


class DiagnosisAgent(BaseAgent):
    output_models = {"pipeline": DiagnosisDraft}

    def keywords(self, mode: str) -> list[str]:
        return self.policy.list(f"keywords.{mode}", [mode.split("_")[0]])

    def extra_vars(self, ctx: RunContext) -> dict:
        mode = ctx.facts.get("ml_failure_mode") or "bearing_wear"
        inc = ctx.incident
        excluded = list(getattr(inc, "excluded_failure_modes", []) or [])
        note = ""
        if excluded:
            note = (f"IMPORTANT: this is analysis round {getattr(inc, 'round', 2)}. A repair for "
                    f"{', '.join(m.replace('_', ' ') for m in excluded)} was already done and did NOT fix the machine, so "
                    "that cause is ruled out. Find the next most likely cause.")
            if getattr(inc, "previous_findings", None):
                note += f" The technician reported: {inc.previous_findings}"
        q_mode = next((m for m in [mode, *self.policy.list("failure_modes", [])] if m not in excluded), "unknown")
        return {"mode_keyword": (self.keywords(mode) or [""])[0], "retry_note": note,
                "mode_query": self.policy.get(f"query.{q_mode}", self.policy.get("query.unknown", "vibration rising"))}

    async def finalize(self, ctx: RunContext, res: AgentRunResult) -> FinalResult | None:
        p, d = self.policy, (res.structured or {})
        health = self.data(ctx, res, "get_asset_health") or {}
        docs = (self.data(ctx, res, "search_manuals") or {}).get("chunks", [])
        history = (self.data(ctx, res, "get_maintenance_history") or {}).get("items", [])
        defects = self.data(ctx, res, "get_defects") or {}
        bom = (self.data(ctx, res, "get_bom") or {}).get("items", [])
        modes = p.list("failure_modes", ["bearing_wear", "overheating", "misalignment"])

        excluded = set(getattr(ctx.incident, "excluded_failure_modes", []) or [])
        mode = d.get("failure_mode") if d.get("failure_mode") in modes else (health.get("predicted_failure_mode") or "unknown")
        if mode in excluded or mode == "unknown":
            mode = self._next_mode(modes, excluded, docs, health) or mode
        kw = [k.lower() for k in self.keywords(mode)] if mode != "unknown" else []
        hit = lambda text: any(k in (text or "").lower() for k in kw)  # noqa: E731
        min_score = self.app.policy("evidence").float("manual_min_score", 0.8)
        corrective = [w for w in history if w.get("type") == "corrective"]

        # ---- confidence: independent sources that support this failure mode (code, not LLM) ----
        support = {
            "ml_mode": health.get("predicted_failure_mode") == mode and mode != "unknown",
            "manual": any(c.get("score", 0) >= min_score and hit(c.get("text")) for c in docs
                          if not c["doc_id"].startswith("WO-")),
            "history": any(hit(f"{w.get('root_cause')} {w.get('title')}") for w in corrective),
            "quality": defects.get("trend_pct", 0) >= self.app.policy("evidence").float("defect_trend_min_pct", 100),
            "sensor": bool(self.data(ctx, res, "get_signals")),
        }
        conf = sum(p.float(f"w_{k}", 0) for k, ok in support.items() if ok)
        conf = round(min(conf, p.float("max_confidence", 0.85)), 2)
        if p.bool("use_case_memory", False):
            remembered = ctx.memory.confidence_for(ctx.incident.asset_id, mode)
            if remembered is not None:
                conf = max(conf, remembered)

        # ---- parts: BOM items referenced by the evidence ----
        bom_skus = {b["sku"]: b for b in bom}
        text_blob = " ".join([c.get("text", "") for c in docs] + [f"{w.get('root_cause')} {w.get('findings')}" for w in history])
        used = {pu["sku"] for w in corrective for pu in w.get("parts", [])}
        parts = [s for s in d.get("required_parts", []) if s in bom_skus]
        if not parts:  # the part named in the manual's troubleshooting/procedure section for this failure mode
            for c in sorted(docs, key=lambda c: -c.get("score", 0)):
                if c["doc_id"].startswith("WO-") or not hit(f"{c.get('section', '')} {c.get('text', '')}"):
                    continue
                if str(c.get("section", ""))[:1] not in "78":  # not the general description or the spare-parts list
                    continue
                named = sorted((c["text"].find(s), s) for s in bom_skus if s in c.get("text", ""))
                if named:
                    parts = [named[0][1]]
                    break
        if not parts and mode != "unknown":  # the BOM part whose repair skill matches this failure mode
            plan = self.app.policy("planning")
            want = required_skill(None, mode, plan)
            parts = [s for s in bom_skus if required_skill(s, None, plan) == want]
        if not parts:
            parts = [s for s in bom_skus if s in used or any(t in text_blob for t in re.findall(r"\d{4,}", s))]
        sku = parts[0] if parts else None
        part_name = _words(bom_skus[sku]["description"]) if sku else ""

        root = (d.get("root_cause") or "").strip()[:120]
        if not root:
            suffix = p.get(f"root_cause_suffix.{mode}", mode.replace("_", " "))
            root = f"{part_name} ({sku}) {suffix}" if sku else f"{mode.replace('_', ' ').capitalize()} on {ctx.incident.asset_id}"
        alts = [a for a in d.get("alternatives", []) if a][:3]
        if not alts:
            for other in (m for m in modes if m != mode):
                okw = [k.lower() for k in self.keywords(other)]
                c = next((c for c in docs if any(k in c.get("text", "").lower() for k in okw)), None)
                if c:
                    sec = str(c.get("section", ""))
                    where = f"manual §{sec}" if sec[:1].isdigit() else f"past repair {c.get('doc_id', '')}".strip()
                    alts.append(f"{other.replace('_', ' ').capitalize()} ({where})")

        evidence: list[Evidence] = []
        for tool in ("get_signals", "get_asset_health", "search_manuals", "get_maintenance_history", "get_defects"):
            evidence += self.evidence_for(ctx, res, tool)
        seen, uniq = set(), []
        for e in evidence + res.evidence:
            if e.ref_id not in seen:
                seen.add(e.ref_id)
                uniq.append(e)

        result = Diagnosis(root_cause=root, failure_mode=mode, confidence=conf, evidence=uniq[: p.int("max_evidence", 6)],
                           alternatives=alts, required_parts=parts)
        suffix_word = p.get(f"root_cause_suffix.{mode}", mode.replace("_", " "))
        label = (f"{part_name} {suffix_word}" if part_name and p.bool("label_from_part", True)
                 else p.get(f"label.{mode}", mode.replace("_", " ")))
        label = label[:1].upper() + label[1:]
        facts = {"failure_mode": mode, "diagnosis_confidence": conf, "sku": sku, "part_name": part_name.lower(),
                 "required_skill": required_skill(sku, mode, self.app.policy("planning")),
                 "root_cause": root, "similar_work_order": corrective[0]["id"] if corrective else None,
                 "confidence_support": support}
        return FinalResult(result, f"{label}, {round(conf * 100)}% confident", confidence=conf, facts=facts)

    def _next_mode(self, modes: list[str], excluded: set[str], docs: list[dict], health: dict) -> str | None:
        """Most likely cause that is not ruled out: the one the manual chunks talk about most (then the ML's)."""
        left = [m for m in modes if m not in excluded]
        if not left:
            return None
        def score(m: str) -> float:
            kw = [k.lower() for k in self.keywords(m)]
            s = sum(c.get("score", 0) for c in docs if any(k in c.get("text", "").lower() for k in kw))
            return s + (1.0 if health.get("predicted_failure_mode") == m else 0.0)
        return max(left, key=score)

