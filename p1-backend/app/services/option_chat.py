"""Options chat (v1.2): approvers discuss the options with the Decision agent; each workable suggestion becomes a new
option next to the AI's (which are never changed). The thread is stored per incident and analysis round."""
from __future__ import annotations

import logging

from plantguard_contracts.schemas.common import ErrorCode
from plantguard_contracts.schemas.incidents import IncidentStatus
from plantguard_contracts.schemas.security import AccessClaims
from plantguard_contracts.schemas.workflow import OptionChatMessage, OptionChatResponse

from app.core.errors import ApiError
from app.sim.clock import iso, parse_ts

log = logging.getLogger("plantguard.option_chat")
MAX_OPTIONS = 12


class OptionChat:
    def __init__(self, incidents, it, audit, clock, users):
        self.incidents, self.it, self.audit, self.clock, self.users = incidents, it, audit, clock, users

    def _nothing(self, inc):
        from plantguard_contracts.schemas.incidents import OptionsResponse
        return OptionChatResponse(reply=OptionChatMessage(seq=0, role="assistant", text="", ts=self.clock()),
                                  added_option_id=None,
                                  options=OptionsResponse(incident_id=inc.id, options=[], needs_human_review=True, reason=""))

    def history(self, incident_id: str, claims: AccessClaims) -> list[OptionChatMessage]:
        rec = self.incidents.scoped_record(incident_id, claims)
        return self._rows(rec.incident.id, rec.incident.round)

    def _rows(self, incident_id: str, rnd: int) -> list[OptionChatMessage]:
        out = []
        for r in self.it.q("SELECT * FROM option_chat WHERE incident_id=? AND round=? ORDER BY seq", (incident_id, rnd)):
            u = self.users(r["author_id"]) if r["author_id"] else None
            out.append(OptionChatMessage(seq=r["seq"], role=r["role"], author_id=r["author_id"],
                                         author_name=u.name if u else None, text=r["text"], option_id=r["option_id"],
                                         ts=parse_ts(r["ts"])))
        return out

    def _add(self, incident_id: str, rnd: int, role: str, author: str | None, text: str, option_id: str | None):
        seq = self.it.val("SELECT MAX(seq) FROM option_chat WHERE incident_id=? AND round=?", (incident_id, rnd), 0) + 1
        self.it.x("INSERT INTO option_chat(incident_id, round, seq, role, author_id, text, option_id, ts) "
                  "VALUES(?,?,?,?,?,?,?,?)", (incident_id, rnd, seq, role, author, text, option_id, iso(self.clock())))
        return seq

    async def post(self, incident_id: str, message: str, claims: AccessClaims, only_if_plan: bool = False) -> OptionChatResponse:
        svc = self.incidents
        rec = svc.scoped_record(incident_id, claims)
        inc = rec.incident
        if only_if_plan:
            looks = getattr(svc.runtime, "looks_like_plan", None)
            if (rec.options is None or inc.status != IncidentStatus.AWAITING_APPROVAL or inc.plant_id not in claims.plant_ids
                    or looks is None or not looks(message)):
                return OptionChatResponse(reply=OptionChatMessage(seq=0, role="assistant", text="", ts=self.clock()),
                                          added_option_id=None, options=svc.options(inc.id, claims)) \
                    if rec.options is not None else self._nothing(inc)
        if rec.options is None or inc.status != IncidentStatus.AWAITING_APPROVAL:
            what = ("is being signed off; decline it first to change the plan" if inc.status == IncidentStatus.AWAITING_SIGNOFF
                    else f"is {inc.status.value.replace('_', ' ')}")
            raise ApiError(409, ErrorCode.CONFLICT, f"The options can only change while they wait for approval; this "
                                                    f"incident {what}")
        if inc.plant_id not in claims.plant_ids:
            raise ApiError(403, ErrorCode.PLANT_SCOPE, "Only approvers of this plant can change its options")
        refine = getattr(svc.runtime, "refine_options", None)
        if refine is None:
            raise ApiError(409, ErrorCode.CONFLICT, "The options chat needs the live agents (simulated plant)")
        history = [{"role": m.role, "text": m.text} for m in self._rows(inc.id, inc.round)]
        self._add(inc.id, inc.round, "user", claims.sub, message.strip(), None)
        if len(rec.options.options) >= MAX_OPTIONS:
            out = {"reply": f"There are already {MAX_OPTIONS} options. Approve one, or decline the ones you don't want "
                            "and start a new incident analysis.", "option": None}
        else:
            try:
                out = await refine(inc, svc.options(inc.id, claims), history, message.strip(),
                                   claims.name.split(" (")[0])
            except Exception as e:  # never lose the approver's message over a planning error
                log.exception("options chat failed")
                out = {"reply": f"I couldn't build that plan ({type(e).__name__}). Try saying it another way.", "option": None}
        option = out.get("option")
        if option is not None:
            rec.options = rec.options.model_copy(update={"options": [*rec.options.options, option]})
            inc.updated_at = self.clock()
            svc.repo.save(rec)
            self.audit.log("user", claims.sub, "suggest_option", inc.id,
                           {"incident_id": inc.id, "option_id": option.id, "total_eur": option.cost.total.amount})
            await svc.publish_status(rec)
        seq = self._add(inc.id, inc.round, "assistant", None, out["reply"], option.id if option else None)
        reply = OptionChatMessage(seq=seq, role="assistant", text=out["reply"], option_id=option.id if option else None,
                                  ts=self.clock())
        return OptionChatResponse(reply=reply, added_option_id=option.id if option else None,
                                  options=svc.options(inc.id, claims))
