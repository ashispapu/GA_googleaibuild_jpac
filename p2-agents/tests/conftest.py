"""Fixtures for the agent-layer tests. Run from the repo root:  make test-p2"""
import os

os.environ.setdefault("DATA_MODE", "fixtures")  # the scripted story; sim tests opt in explicitly
import httpx
import pytest

import support  # noqa: F401  (sets sys.path)
from agents.tool_client import HttpToolClient
from harness.config import load_config


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture
def mock_tools():
    from mock_server.app import app
    return HttpToolClient("http://mock", transport=httpx.ASGITransport(app=app))


