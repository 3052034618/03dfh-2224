from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import re


@dataclass
class TempZone:
    low: float
    high: float
    raw: str = ""

    @staticmethod
    def parse(text: str) -> Optional["TempZone"]:
        if not text:
            return None
        text = text.strip()
        m = re.match(r"(-?[\d.]+)\s*[~—]+\s*(-?[\d.]+)", text)
        if not m:
            m = re.match(r"(-?[\d.]+)\s*--\s*(-?[\d.]+)", text)
        if not m:
            m = re.match(r"(-?[\d.]+)\s+-\s+(-?[\d.]+)", text)
        if m:
            low, high = float(m.group(1)), float(m.group(2))
            return TempZone(low=min(low, high), high=max(low, high), raw=text)
        m = re.match(r"([<>≤≥])\s*(-?[\d.]+)", text)
        if m:
            op, val = m.group(1), float(m.group(2))
            if op in ("<", "≤"):
                return TempZone(low=-999, high=val, raw=text)
            return TempZone(low=val, high=999, raw=text)
        try:
            val = float(text)
            return TempZone(low=val, high=val, raw=text)
        except ValueError:
            return None

    def is_over(self, temp: float) -> bool:
        return temp < self.low or temp > self.high

    def __str__(self):
        if self.raw:
            return self.raw
        return f"{self.low}~{self.high}"


@dataclass
class Waybill:
    waybill_no: str
    license_plate: str = ""
    device_id: str = ""
    customer: str = ""
    temp_zone: Optional[TempZone] = None
    departure_time: Optional[datetime] = None
    arrival_time: Optional[datetime] = None
    raw_temp_zone: str = ""


@dataclass
class TemperatureReading:
    device_id: str
    timestamp: datetime
    temperature: float


@dataclass
class OverTempSegment:
    start: datetime
    end: datetime
    minutes: float
    min_temp: float
    max_temp: float


@dataclass
class KeyTimePoint:
    timestamp: datetime
    temperature: float
    label: str


@dataclass
class WaybillReport:
    waybill_no: str
    license_plate: str
    device_id: str
    customer: str
    temp_zone: Optional[TempZone]
    departure_time: Optional[datetime]
    arrival_time: Optional[datetime]
    min_temp: Optional[float] = None
    max_temp: Optional[float] = None
    reading_count: int = 0
    over_temp_minutes: float = 0.0
    over_temp_segments: list = field(default_factory=list)
    key_time_points: list = field(default_factory=list)
    has_data: bool = False


@dataclass
class ExceptionItem:
    waybill_no: str
    license_plate: str
    device_id: str
    severity: str  # "high", "medium", "low"
    category: str  # "gap", "no_data", "spike", "over_temp"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    detail: str = ""
    minutes: float = 0.0
    temperature: Optional[float] = None

    SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

    def sort_key(self):
        return (self.SEVERITY_ORDER.get(self.severity, 9), self.start_time or datetime.min)
