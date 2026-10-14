"""The plant hierarchy (plant -> line -> station -> machine) without live values, cached once. PlantDirectory uses it
to locate machines cheaply; GET /plants uses SqlPlants for live health and states."""
from plantguard_contracts.schemas.common import Health, MachineState
from plantguard_contracts.schemas.plants import Asset, Line, Plant, Station


class StaticPlants:  # implements PlantConnector
    def __init__(self, it):
        out = []
        for p in it.q("SELECT * FROM plants ORDER BY id"):
            lines = []
            for l in it.q("SELECT * FROM lines WHERE plant_id=? ORDER BY id", (p["id"],)):
                stations: dict[str, Station] = {}
                for e in it.q("SELECT * FROM equipment WHERE plant_id=? AND line_id=? ORDER BY station_seq, asset_id",
                              (p["id"], l["id"])):
                    st = stations.setdefault(e["station_id"], Station(id=e["station_id"], name=e["station_name"],
                                                                      order=e["station_seq"], assets=[]))
                    st.assets.append(Asset(id=e["asset_id"], name=e["name"], type=e["type"], state=MachineState.RUN,
                                           health=Health.OK, risk_72h=0.0, position=(e["pos_x"], e["pos_y"])))
                lines.append(Line(id=l["id"], plant_id=p["id"], name=l["name"], product=l["product"], state=MachineState.RUN,
                                  health=Health.OK, speed_pct=100, oee=0.0, stations=list(stations.values())))
            out.append(Plant(id=p["id"], name=p["name"], location=p["location"], lines=lines))
        self._plants = out

    def list_plants(self) -> list[Plant]:
        return self._plants
