"""Reads fixtures/story/*.json. Names come from request data, so they are validated (no path traversal)."""
import json
import re
from pathlib import Path
from typing import Any

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class FixtureStore:
    """Reads JSON files from fixtures/story/ by name."""

    def __init__(self, root: Path):
        """root: folder containing the JSON fixtures."""
        self.root = root

    def load(self, name: str) -> Any:
        """Raises KeyError for unknown or unsafe names; routers turn that into 404 NOT_FOUND."""
        if not _SAFE.match(name) or ".." in name:
            raise KeyError(name)
        path = self.root / f"{name}.json"
        if not path.is_file():
            raise KeyError(name)
        return json.loads(path.read_text())
