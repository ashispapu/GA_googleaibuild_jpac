"""Where things are: asset -> line -> plant. Built on the PlantConnector so it follows whatever source is plugged in."""
from dataclasses import dataclass

from plantguard_contracts.interfaces import PlantConnector


@dataclass(frozen=True)
class AssetLocation:
    """Where an asset lives: plant, line, id and display name."""

    plant_id: str
    line_id: str
    asset_id: str
    asset_name: str


class PlantDirectory:
    """Answers 'where is this asset / which assets are on this line' from the plant hierarchy."""

    def __init__(self, plants: PlantConnector):
        """plants: the PlantConnector to read the hierarchy from."""
        self._plants = plants

    def locate(self, asset_id: str) -> AssetLocation:
        """Find an asset in plant -> line -> station -> asset; KeyError (-> 404) if unknown."""
        for plant in self._plants.list_plants():
            for line in plant.lines:
                for station in line.stations:
                    for asset in station.assets:
                        if asset.id == asset_id:
                            return AssetLocation(plant.id, line.id, asset.id, asset.name)
        raise KeyError(asset_id)

    def line_assets(self, line_id: str, plant_ids: list[str] | None = None) -> tuple[str, list[str]]:
        """(plant_id, asset ids) of the line. Line ids repeat across plants, so callers pass the plants they may see."""
        for plant in self._plants.list_plants():
            if plant_ids is not None and plant.id not in plant_ids:
                continue
            for line in plant.lines:
                if line.id == line_id:
                    return plant.id, [a.id for s in line.stations for a in s.assets]
        raise KeyError(line_id)

    def plant_of_asset(self, asset_id: str) -> str:
        """Plant id of an asset."""
        return self.locate(asset_id).plant_id
