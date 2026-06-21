from collections import defaultdict
from typing import List, Tuple, Dict
from .models import Waybill, TemperatureReading


class MatchResult:
    def __init__(self):
        self.matched: List[Tuple[Waybill, List[TemperatureReading]]] = []
        self.no_departure: List[Waybill] = []
        self.no_arrival: List[Waybill] = []
        self.no_temp_data: List[Waybill] = []
        self.unmatched_devices: List[str] = []
        self.no_device_id: List[Waybill] = []


def match_waybills_readings(
    waybills: List[Waybill],
    readings: List[TemperatureReading],
) -> MatchResult:
    result = MatchResult()

    device_readings: Dict[str, List[TemperatureReading]] = defaultdict(list)
    for r in readings:
        device_readings[r.device_id].append(r)

    for waybill in waybills:
        if not waybill.departure_time:
            result.no_departure.append(waybill)
        if not waybill.arrival_time:
            result.no_arrival.append(waybill)

        if not waybill.device_id:
            result.no_device_id.append(waybill)
            result.no_temp_data.append(waybill)
            continue

        matched_readings = device_readings.get(waybill.device_id, [])

        if waybill.license_plate:
            plate_readings = [
                r for r in matched_readings
                if not r.device_id or r.device_id == waybill.device_id
            ]
            matched_readings = plate_readings

        if waybill.departure_time and waybill.arrival_time:
            filtered = [
                r for r in matched_readings
                if waybill.departure_time <= r.timestamp <= waybill.arrival_time
            ]
            matched_readings = filtered
        elif waybill.departure_time:
            filtered = [
                r for r in matched_readings
                if r.timestamp >= waybill.departure_time
            ]
            matched_readings = filtered

        if not matched_readings:
            result.no_temp_data.append(waybill)
        else:
            result.matched.append((waybill, matched_readings))

    all_matched_devices = {w.device_id for w, _ in result.matched}
    for device_id in device_readings:
        if device_id not in all_matched_devices:
            result.unmatched_devices.append(device_id)

    return result
