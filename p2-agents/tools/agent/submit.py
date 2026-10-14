"""submit_result: the LLM hands back the structured, wording-only fields of its result (root cause text, summary,
alternatives...). Numbers are never taken from here (C3): the agent's finalize() computes them in code."""
from __future__ import annotations

from pydantic import BaseModel, ValidationError

from tools.base import Finish, Tool, ToolInvocation, ToolOutcome, clean_schema


class SubmitResultTool(Tool):
    kind = "agent"
    name = "submit_result"

    def __init__(self, model: type[BaseModel], description: str = ""):
        self.model = model
        self.description = description or ("Submit your final findings in this structure when you are done. "
                                           "Call it once, after you have checked the data.")

    def parameters(self) -> dict:
        return clean_schema(self.model.model_json_schema())

    async def run(self, args: dict, inv: ToolInvocation) -> ToolOutcome:
        try:
            payload = self.model.model_validate(args).model_dump(mode="json")
        except ValidationError as e:
            msg = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:4])
            return ToolOutcome(content=f"ERROR invalid result: {msg}. Fix and call submit_result again.", is_error=True)
        return ToolOutcome(content="Result received.", data=payload, control=Finish(payload))
