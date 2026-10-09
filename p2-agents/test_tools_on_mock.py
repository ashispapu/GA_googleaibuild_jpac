"""Check every read tool works against the mock: PYTHONPATH=.:p2-agents python p2-agents/test_tools_on_mock.py"""
import asyncio

import httpx

from agents.tool_client import HttpToolClient
from mock_server.app import app
from plantguard_contracts.tools import TOOLS

CALLS = {
    "get_asset_health": ("monitor", {"asset_id": "EQ-3012"}),
    "get_signals": ("monitor", {"asset_id": "EQ-3012", "signals": ["vibration_rms"], "minutes": 60}),
    "get_alarms": ("monitor", {"plant_id": "PLANT-A"}),
    "search_manuals": ("diagnosis", {"query": "spindle vibration rising bearing", "asset_id": "EQ-3012", "top_k": 3}),
    "get_maintenance_history": ("diagnosis", {"asset_id": "EQ-3012"}),
    "get_defects": ("diagnosis", {"line_id": "L3"}),
    "get_bom": ("spare_parts", {"asset_id": "EQ-3012"}),
    "check_stock": ("spare_parts", {"sku": "SKF-6205"}),
    "get_supplier": ("spare_parts", {"supplier_id": "SUP-17"}),
    "get_orders": ("scheduler", {"line_id": "L3"}),
    "get_schedule": ("scheduler", {"line_id": "L3"}),
    "find_technicians": ("scheduler", {"skill": "bearing_replacement"}),
    "get_energy": ("energy", {"line_id": "L3"}),
    "get_tariffs": ("energy", {}),
    "get_co2_factor": ("energy", {}),
}


async def main():
    tc = HttpToolClient("http://mock", transport=httpx.ASGITransport(app=app))
    ok = 0
    for tool, (agent, args) in CALLS.items():
        try:
            await tc.call(agent, tool, args, "INC-0042")
            ok += 1
        except Exception as e:
            print("FAIL", tool, repr(e)[:300])
    print(f"{ok}/{len(CALLS)} read tools OK (registry has {sum(not t.writes for t in TOOLS.values())})")
    try:
        await tc.call("energy", "check_stock", {"sku": "SKF-6205"}, "INC-0042")
    except PermissionError as e:
        print("allow-list blocks:", e)


if __name__ == "__main__":
    asyncio.run(main())
