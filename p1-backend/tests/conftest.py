"""Fresh container per test. Run: make test-p1-unit"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "p1-backend", ROOT / "p1-backend" / "tests"):
    sys.path.insert(0, str(p))

from fastapi.testclient import TestClient  # noqa: E402

from app.container import build_container  # noqa: E402
from app.main import create_app  # noqa: E402
from helpers import login  # noqa: E402


@pytest.fixture()
def container():
    """A brand-new Container (fresh audit log, incidents, tokens) for each test."""
    return build_container()


@pytest.fixture()
def client(container):
    """A TestClient on an app built from the fresh container."""
    return TestClient(create_app(container))


@pytest.fixture()
def users(client):
    """Logged-in auth headers for the demo users op, lead, manager and admin."""
    return {u: login(client, u) for u in ("op", "lead", "manager", "admin")}
