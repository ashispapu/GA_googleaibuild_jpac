from .common import Contract, Money, TimeWindow


class AssetConsumption(Contract):
    asset_id: str
    kwh: float
    avg_power_kw: float
    baseline_power_kw: float  # deviation hints at wear


class LineConsumption(Contract):
    line_id: str
    window: TimeWindow
    kwh: float
    cost: Money
    co2_kg: float
    by_asset: list[AssetConsumption]


class Tariff(Contract):
    name: str  # peak | off_peak
    start_hour: int
    end_hour: int
    price_per_kwh: Money


class Co2Factor(Contract):
    kg_per_kwh: float
    source: str
