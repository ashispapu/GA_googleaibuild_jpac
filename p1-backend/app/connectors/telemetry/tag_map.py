"""config/tag_map.yaml: raw PLC tag -> (asset, signal, unit, valid range). Shared with the simulator."""
from dataclasses import dataclass
from pathlib import Path

import yaml

from plantguard_contracts.schemas.ot import Signal


@dataclass(frozen=True)
class Tag:
    """One PLC tag: which asset and signal it carries, unit, protocol and valid range."""

    name: str
    asset_id: str
    signal: Signal
    unit: str
    protocol: str
    low: float
    high: float


class TagMap:
    """Lookup of tags by raw name or by (asset, signal)."""

    def __init__(self, tags: list[Tag]):
        """Index the tags both ways."""
        self._by_tag = {t.name: t for t in tags}
        self._by_asset_signal = {(t.asset_id, t.signal): t for t in tags}

    @classmethod
    def load(cls, path: Path) -> "TagMap":
        """Read config/tag_map.yaml into a TagMap."""
        raw = yaml.safe_load(path.read_text()) or {}
        return cls([Tag(name, v["asset_id"], Signal(v["signal"]), str(v.get("unit", "")), v.get("protocol", ""),
                        float(v["range"][0]), float(v["range"][1])) for name, v in raw.items()])

    def resolve(self, raw_tag: str) -> Tag | None:
        """Tag for a raw PLC tag name, or None."""
        return self._by_tag.get(raw_tag)

    def range_of(self, asset_id: str, signal: Signal) -> tuple[float, float] | None:
        """(low, high) valid range of an asset's signal, or None if not mapped."""
        t = self._by_asset_signal.get((asset_id, signal))
        return (t.low, t.high) if t else None
