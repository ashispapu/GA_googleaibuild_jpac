from datetime import datetime
from enum import Enum
from .common import Contract, DataQuality, MachineState


class Signal(str, Enum):
    VIBRATION_RMS = "vibration_rms"
    VIBRATION_PEAK = "vibration_peak"
    VIBRATION_KURTOSIS = "vibration_kurtosis"
    BEARING_TEMP = "bearing_temp"
    MOTOR_TEMP = "motor_temp"
    SPINDLE_RPM = "spindle_rpm"
    MOTOR_CURRENT = "motor_current"
    POWER_KW = "power_kw"


class SignalPoint(Contract):
    ts: datetime
    value: float
    quality: DataQuality = DataQuality.GOOD


class SignalSeries(Contract):
    asset_id: str
    signal: Signal
    unit: str  # mm/s, °C, rpm, A, kW
    source_tag: str  # raw PLC tag, e.g. L3.ST2.SPDL.VIB_RMS
    points: list[SignalPoint]


class TelemetryMessage(Contract):
    """Pushed on WS /ot/stream, one per asset per second."""
    asset_id: str
    line_id: str
    ts: datetime
    state: MachineState
    values: dict[Signal, float]
    quality: DataQuality = DataQuality.GOOD


class AlarmSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Alarm(Contract):
    id: str
    asset_id: str
    code: str  # e.g. VIB-HI-01
    severity: AlarmSeverity
    message: str
    raised_at: datetime
    acknowledged: bool = False
