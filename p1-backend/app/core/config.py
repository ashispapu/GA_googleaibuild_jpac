"""Settings read once from the environment (see .env.example). Auth settings live in plantguard_contracts.auth."""
import os
from dataclasses import dataclass, field
from pathlib import Path

from plantguard_contracts.schemas.simulator import Scenario

ROOT = Path(__file__).resolve().parents[3]


def _flag(name: str, default: bool) -> bool:
    """Read a boolean env var: 1/true/yes/on (any case) is True, anything else False."""
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    """Immutable settings object. Every field has a default, so tests can build one directly."""

    fixtures_dir: Path = ROOT / "fixtures" / "story"
    tag_map_path: Path = ROOT / "config" / "tag_map.yaml"
    model_path: Path = ROOT / "p4-ml-data" / "models" / "latest"
    cors_origins: tuple[str, ...] = ("*",)
    rate_limit_per_min: int = 1200       # sustained requests per client
    rate_limit_burst: int = 200          # short bursts
    hero_asset_id: str = "EQ-3012"       # asset the simulator's warning/crisis scenes act on
    initial_scenario: Scenario = Scenario.CRISIS
    incident_id_start: int = 42
    telemetry_interval_s: float = 1.0
    scoring_enabled: bool = False        # run MQTT/synthetic ingest + ML scoring loop (BE-4/BE-5)
    scoring_interval_s: float = 60.0
    agent_events_file: Path = ROOT / "fixtures" / "story" / "agent_events_INC-0042.jsonl"
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        """Build Settings from environment variables (CORS_ORIGINS, RATE_LIMIT_*, INITIAL_SCENARIO, SCORING_*,
        MODEL_PATH)."""
        origins = tuple(o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip())
        return cls(
            cors_origins=origins or ("*",),
            rate_limit_per_min=int(os.getenv("RATE_LIMIT_PER_MIN", cls.rate_limit_per_min)),
            rate_limit_burst=int(os.getenv("RATE_LIMIT_BURST", cls.rate_limit_burst)),
            initial_scenario=Scenario(os.getenv("INITIAL_SCENARIO", Scenario.CRISIS.value)),
            scoring_enabled=_flag("SCORING_ENABLED", False),
            scoring_interval_s=float(os.getenv("SCORING_INTERVAL_S", cls.scoring_interval_s)),
            model_path=Path(os.getenv("MODEL_PATH", str(cls.model_path))),
        )
