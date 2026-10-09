"""ToolGateway (BE-6). The only door agents use to reach plant data."""
from plantguard_contracts.tools import TOOLS


class ToolGateway:  # implements interfaces.ToolGateway
    async def call(self, agent: str, tool: str, args: dict, incident_id: str) -> dict:
        spec = TOOLS.get(tool)
        if spec is None or agent not in spec.agents:
            raise PermissionError(f"{agent} may not call {tool}")
        inp = spec.input.model_validate(args)  # reject bad args
        # TODO: call the matching connector, with spec.timeout_s
        # TODO: audit.log("agent", agent, "tool_call", tool, {"incident_id": incident_id, ...})
        # TODO: return spec.output.model_validate(result).model_dump(mode="json")
        raise NotImplementedError
