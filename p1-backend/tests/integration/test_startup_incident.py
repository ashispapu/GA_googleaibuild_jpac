"""Starting in the crisis scene must open INC-0042 at startup (else the overview shows the risk but the incident is 404)."""
from dataclasses import replace

from app.container import build_container
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from helpers import login

from plantguard_contracts.schemas.simulator import Scenario


def _app(**kw):
    return create_app(build_container(replace(Settings.from_env(), **kw)))


def test_crisis_start_opens_hero_incident():
    with TestClient(_app(initial_scenario=Scenario.CRISIS)) as client:
        r = client.get("/incidents/INC-0042", headers=login(client, "op"))
        assert r.status_code == 200, r.text
        inc = r.json()
        assert inc["asset_id"] == "EQ-3012" and inc["trigger"] == "ml_alert" and inc["status"] == "analysing"
        # idempotent: a later open of the same asset returns the same incident
        again = client.post("/incidents", json={"asset_id": "EQ-3012", "trigger": "ml_alert"}, headers=login(client, "op"))
        assert again.json()["id"] == "INC-0042"


def test_calm_start_or_flag_off_opens_nothing():
    for kw in ({"initial_scenario": Scenario.CALM}, {"open_incident_on_start": False}):
        with TestClient(_app(**kw)) as client:
            assert client.get("/incidents/INC-0042", headers=login(client, "op")).status_code == 404, kw
