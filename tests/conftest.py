"""Contract tests run against the scripted story (fixtures), not the simulated plant."""
import os

os.environ.setdefault("DATA_MODE", "fixtures")
