"""Tool client for agents. Day 1: calls the mock server over HTTP. Later: P1's ToolGateway, same calls.
Enforces the allow-list and validates input/output against the contract, so mistakes show up early.

    tc = HttpToolClient("http://localhost:8000")
    part = await tc.call("spare_parts", "check_stock", {"sku": "SKF-6205"}, "INC-0042")
"""
import re

import httpx

from plantguard_contracts.tools import TOOLS


class HttpToolClient:
    def __init__(self, base_url: str = "http://localhost:8000", user: str = "lead", password: str = "demo",
                 transport: httpx.AsyncBaseTransport | None = None):
        # transport lets tests run in-process: httpx.ASGITransport(app=mock_server.app.app)
        self.base, self.user, self.password, self.token, self.transport = base_url, user, password, None, transport

    async def _auth(self, client):
        r = await client.post(f"{self.base}/auth/token", json={"username": self.user, "password": self.password})
        r.raise_for_status()
        self.token = r.json()["access_token"]

    async def call(self, agent: str, tool: str, args: dict, incident_id: str) -> dict:
        spec = TOOLS.get(tool)
        if spec is None or agent not in spec.agents:
            raise PermissionError(f"{agent} may not call {tool}")
        if spec.writes:
            raise PermissionError("write tools are executed by the backend after approval")
        inp = spec.input.model_validate(args).model_dump(mode="json", exclude_none=True)
        method, path = spec.endpoint.split(" ", 1)
        for key in re.findall(r"{(\w+)}", path):
            path = path.replace("{" + key + "}", str(inp.pop(key)))
        async with httpx.AsyncClient(timeout=spec.timeout_s, transport=self.transport) as client:
            if not self.token:
                await self._auth(client)
            h = {"Authorization": f"Bearer {self.token}", "X-Incident-Id": incident_id, "X-Agent": agent}
            r = await (client.get(self.base + path, params=inp, headers=h) if method == "GET"
                       else client.post(self.base + path, json=inp, headers=h))
            r.raise_for_status()
            data = r.json()
        if isinstance(data, list):  # list endpoints are wrapped as {"items": [...]} in tool outputs
            data = {"items": data}
        return spec.output.model_validate(data).model_dump(mode="json")
