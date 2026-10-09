from datetime import datetime
from .common import Contract, PlantId, TechnicianId


class Technician(Contract):
    id: TechnicianId
    name: str
    skills: list[str]  # bearing_replacement | spindle_alignment | electrical | hydraulics
    certifications: list[str]
    on_shift: bool


class Shift(Contract):
    id: str
    name: str  # morning | evening | night
    start: datetime
    end: datetime
    plant_id: PlantId
    technicians: list[Technician]
