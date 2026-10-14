"""Loads config/simulation.ini and config/plant.yaml into typed settings."""
from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SimSettings:
    root: Path
    scale: float = 60.0
    data_dir: Path = Path("var/plantguard")
    reset_on_start: bool = False
    history_retention_h: float = 72
    history_interval_min: float = 5
    history_backfill_h: float = 24
    telemetry_interval_s: float = 1.0
    noise: float = 0.02
    mean_fault_interval_h: float = 10
    max_concurrent_faults: int = 3
    breakdown_h_min: float = 30
    breakdown_h_max: float = 90
    speed_exponent: float = 1.5
    misalignment_looks_like_bearing: float = 0.35
    bad_install_probability: float = 0.05
    random_faults: bool = True
    initial_faults: dict[str, tuple[str, float]] = field(default_factory=dict)
    score_interval_s: float = 5
    window_min: float = 10
    alert_risk: float = 0.70
    alert_debounce_min: float = 30
    verify_min: float = 30
    verify_max_risk: float = 0.30
    require_parts_in_stock: bool = True
    approvals_enabled: bool = True
    source_plant_release: bool = True
    manager_cosign_over_eur: float = 10000.0
    manager_cosign_stop_h: float = 4.0
    line_rate_units_per_h: float = 37.5
    inspection_share: float = 0.05
    base_defect_rate: float = 0.004
    agents_on_llm_failure: str = "degrade"
    plant: dict[str, Any] = field(default_factory=dict)

    @property
    def models(self) -> dict[str, Any]:
        return self.plant["models"]


def _b(v: str | None, default: bool) -> bool:
    return default if v in (None, "") else v.strip().lower() in ("1", "true", "yes", "on")


def load_sim_settings(root: Path, ini: Path | None = None, plant_yaml: Path | None = None) -> SimSettings:
    ini = ini or root / "config" / "simulation.ini"
    plant_yaml = plant_yaml or root / "config" / "plant.yaml"
    p = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    p.optionxform = str  # keep asset ids as written
    p.read(ini, encoding="utf-8")
    g = lambda sec, key, conv=float, default=None: (conv(p[sec][key]) if p.has_section(sec) and p[sec].get(key, "").strip()  # noqa: E731
                                                    else default)
    initial = {}
    if p.has_section("faults.initial"):
        for asset, spec in p["faults.initial"].items():
            mode, _, prog = spec.partition("@")
            initial[asset.strip()] = (mode.strip(), float(prog or 0.3))
    data_dir = Path(g("storage", "data_dir", str, "var/plantguard"))
    return SimSettings(
        root=root,
        scale=g("clock", "scale", float, 60.0),
        data_dir=data_dir if data_dir.is_absolute() else root / data_dir,
        reset_on_start=_b(p.get("storage", "reset_on_start", fallback=None), False),
        history_retention_h=g("storage", "history_retention_h", float, 72),
        history_interval_min=g("storage", "history_interval_min", float, 5),
        history_backfill_h=g("storage", "history_backfill_h", float, 24),
        telemetry_interval_s=g("telemetry", "interval_s", float, 1.0),
        noise=g("telemetry", "noise", float, 0.02),
        mean_fault_interval_h=g("faults", "mean_interval_h", float, 10),
        max_concurrent_faults=g("faults", "max_concurrent", int, 3),
        breakdown_h_min=g("faults", "breakdown_h_min", float, 30),
        breakdown_h_max=g("faults", "breakdown_h_max", float, 90),
        speed_exponent=g("faults", "speed_exponent", float, 1.5),
        misalignment_looks_like_bearing=g("faults", "misalignment_looks_like_bearing", float, 0.35),
        bad_install_probability=g("faults", "bad_install_probability", float, 0.05),
        random_faults=_b(p.get("faults", "random", fallback=None), True),
        initial_faults=initial,
        score_interval_s=g("health", "score_interval_s", float, 5),
        window_min=g("health", "window_min", float, 10),
        alert_risk=g("health", "alert_risk", float, 0.7),
        alert_debounce_min=g("health", "alert_debounce_min", float, 30),
        verify_min=g("repair", "verify_min", float, 30),
        verify_max_risk=g("repair", "verify_max_risk", float, 0.3),
        require_parts_in_stock=_b(p.get("repair", "require_parts_in_stock", fallback=None), True),
        approvals_enabled=_b(p.get("approvals", "enabled", fallback=None), True),
        source_plant_release=_b(p.get("approvals", "source_plant_release", fallback=None), True),
        manager_cosign_over_eur=g("approvals", "manager_cosign_over_eur", float, 10000.0),
        manager_cosign_stop_h=g("approvals", "manager_cosign_stop_h", float, 4.0),
        line_rate_units_per_h=g("production", "line_rate_units_per_h", float, 37.5),
        inspection_share=g("production", "inspection_share", float, 0.05),
        base_defect_rate=g("production", "base_defect_rate", float, 0.004),
        agents_on_llm_failure=(p.get("agents", "on_llm_failure", fallback="degrade") or "degrade").strip(),
        plant=yaml.safe_load(plant_yaml.read_text(encoding="utf-8")),
    )
