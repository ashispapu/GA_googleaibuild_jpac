from fastapi import APIRouter, Depends

from plantguard_contracts.schemas.plants import Line, Plant
from plantguard_contracts.schemas.security import AccessClaims

from app.container import Container
from app.routers.deps import VIEW, container

router = APIRouter(prefix="/plants", tags=["plants"])


@router.get("", response_model=list[Plant])
def list_plants(claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /plants: only the plants the user may see."""
    return [p for p in c.plants.list_plants() if p.id in claims.plant_ids]


@router.get("/{plant_id}/lines/{line_id}", response_model=Line)
def get_line(plant_id: str, line_id: str, claims: AccessClaims = VIEW, c: Container = Depends(container)):
    """GET /plants/{plant}/lines/{line}; 404 if either is unknown."""
    c.policy.check_plant(claims, plant_id)
    plant = next((p for p in c.plants.list_plants() if p.id == plant_id), None) or _missing(plant_id)
    return next((l for l in plant.lines if l.id == line_id), None) or _missing(line_id)


def _missing(what: str):
    """Raise KeyError so the error handler returns 404."""
    raise KeyError(what)
