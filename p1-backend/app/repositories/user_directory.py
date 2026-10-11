"""Local-mode user directory (fixtures/story/users.json). Keycloak mode never touches this."""
from typing import Protocol

from app.connectors.store import FixtureStore


class UserDirectory(Protocol):
    """Lookup of local demo users by username."""

    def find(self, username: str) -> dict | None:
        """The user dict (id, name, role, password, plant_ids) or None."""


class FixtureUserDirectory:
    """Reads users from fixtures/story/users.json."""

    def __init__(self, store: FixtureStore):
        """store: the FixtureStore to read from."""
        self._store = store

    def find(self, username: str) -> dict | None:
        """Return the user with this username, or None."""
        return next((u for u in self._store.load("users") if u["username"] == username), None)
