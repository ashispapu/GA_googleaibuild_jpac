"""Plant tools: one PlantTool per read tool in the contract registry. Importing the domain modules registers their
presenters (labels, summaries, evidence)."""
from plantguard_contracts.tools import TOOLS

from tools.plant import energy, engineering, erp, incidents, maintenance, mes, ml, ot, quality, workforce  # noqa: F401
from tools.plant.tool import PlantTool


def build_plant_tools(max_result_chars: int = 4000, disabled: tuple[str, ...] = ()) -> dict[str, PlantTool]:
    return {n: PlantTool(s, max_result_chars) for n, s in TOOLS.items() if not s.writes and n not in disabled}
