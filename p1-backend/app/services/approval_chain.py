"""Approval chain (v1.2): who must sign an option before anything is created.

One approval is not enough when an option
  - takes a part out of ANOTHER plant's stock     -> that plant's maintenance lead must release it,
  - costs more than [approvals] manager_cosign_over_eur, or stops the line for more than manager_cosign_stop_h
                                                  -> the plant manager must co-sign.
The requesting plant's lead (or manager) always signs first. Nothing is executed until every step is signed; any
signer can decline, which sends the incident back to "awaiting approval" with the reason.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from plantguard_contracts.schemas.common import Role
from plantguard_contracts.schemas.incidents import ApprovalStep, Incident, Option, OptionsResponse
from plantguard_contracts.schemas.security import AccessClaims, Permission, ROLE_PERMISSIONS

APPROVER_ROLES = [Role.MAINTENANCE_LEAD.value, Role.PLANT_MANAGER.value, Role.ADMIN.value]
MANAGER_ROLES = [Role.PLANT_MANAGER.value, Role.ADMIN.value]


@dataclass(frozen=True)
class ApprovalSettings:
    enabled: bool = True
    manager_cosign_over_eur: float = 10000.0
    manager_cosign_stop_h: float = 4.0
    source_plant_release: bool = True


def plant_name(plant_id: str) -> str:
    return f"Plant {plant_id.split('-')[-1]}"


class ApprovalPolicy:
    def __init__(self, settings: ApprovalSettings):
        self.s = settings

    # ---- which steps an option needs ----
    def steps_for(self, inc: Incident, option: Option) -> list[ApprovalStep]:
        steps = [ApprovalStep(key="requesting_plant", label=f"Maintenance lead, {plant_name(inc.plant_id)}",
                              plant_id=inc.plant_id, roles=APPROVER_ROLES,
                              reason="Chooses the repair for the machine")]
        if not self.s.enabled:
            return steps
        if self.s.source_plant_release:
            for a in option.actions:
                if a.kind == "stock_transfer" and a.draft.from_plant != inc.plant_id:
                    src = a.draft.from_plant
                    key = f"source_plant:{src}"
                    if all(st.key != key for st in steps):
                        steps.append(ApprovalStep(
                            key=key, label=f"Release by {plant_name(src)}", plant_id=src, roles=APPROVER_ROLES,
                            reason=f"{a.draft.quantity}× {a.draft.sku} leaves {plant_name(src)}'s own spare stock"))
        why = []
        total = option.cost.total.amount
        if total > self.s.manager_cosign_over_eur:
            why.append(f"costs €{total:,.0f} (over €{self.s.manager_cosign_over_eur:,.0f})")
        stop_h = self.stop_hours(option)
        if stop_h > self.s.manager_cosign_stop_h:
            why.append(f"stops the line for {stop_h:.0f} h (over {self.s.manager_cosign_stop_h:g} h)")
        if why:
            steps.append(ApprovalStep(key="manager", label="Plant manager co-sign", plant_id=inc.plant_id,
                                      roles=MANAGER_ROLES, reason="Option " + " and ".join(why)))
        return steps

    @staticmethod
    def stop_hours(option: Option) -> float:
        h = 0.0
        for a in option.actions:
            if a.kind == "speed_change" and a.speed_pct == 0:
                h = max(h, ((a.end - a.start).total_seconds() / 3600) if a.end else option.downtime_hours)
        return h

    def annotate(self, inc: Incident, resp: OptionsResponse) -> OptionsResponse:
        return resp.model_copy(update={"options": [o.model_copy(update={"approvals_needed": self.steps_for(inc, o)})
                                                   for o in resp.options]})

    # ---- who may sign ----
    @staticmethod
    def eligible(step: ApprovalStep, claims: AccessClaims) -> bool:
        return (step.decision == "pending" and Permission.APPROVE in ROLE_PERMISSIONS[claims.role]
                and claims.role.value in step.roles and step.plant_id in claims.plant_ids)

    def sign(self, steps: list[ApprovalStep], claims: AccessClaims, decision: str, note: str, at: datetime) -> list[str]:
        """Mark every pending step this person may sign. Returns the signed keys (empty = not allowed)."""
        signed = []
        for i, st in enumerate(steps):
            if self.eligible(st, claims):
                steps[i] = st.model_copy(update={"decision": decision, "signed_by": claims.sub, "signed_name": claims.name,
                                                 "signed_at": at, "note": note or None})
                signed.append(st.key)
                if decision == "declined":
                    break  # one decline ends the chain
        return signed

    @staticmethod
    def pending(steps: list[ApprovalStep]) -> list[ApprovalStep]:
        return [s for s in steps if s.decision == "pending"]

    @staticmethod
    def waits_for(inc: Incident, claims: AccessClaims) -> list[ApprovalStep]:
        """Pending steps of this incident's chain that this user may sign."""
        if not inc.signoff or inc.signoff.status != "in_progress":
            return []
        return [s for s in inc.signoff.steps if ApprovalPolicy.eligible(s, claims)]
