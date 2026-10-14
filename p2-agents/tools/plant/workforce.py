"""Workforce tools: find_technicians. Names are personal data: the LLM client redacts them before any prompt."""
from tools.plant.presenter import presenter

presenter(
    "find_technicians",
    label=lambda a: f"Finding technicians{' for ' + a['skill'].replace('_', ' ') if a.get('skill') else ''}",
    summary=lambda d, a: f"{len(d.get('items', []))} technician(s) with the skill",
)
