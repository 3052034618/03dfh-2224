from collections import defaultdict
from typing import List, Tuple, Dict
from .models import Waybill, TemperatureReading


class MatchPair:
    def __init__(self, waybill: Waybill, readings: List[TemperatureReading], basis: str):
        self.waybill = waybill
        self.readings = readings
        self.basis = basis


class MatchResult:
    def __init__(self):
        self.matched: List[MatchPair] = []
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
    waybill_no_readings: Dict[str, List[TemperatureReading]] = defaultdict(list)
    plate_readings: Dict[str, List[TemperatureReading]] = defaultdict(list)

    for r in readings:
        device_readings[r.device_id].append(r)
        if r.waybill_no:
            waybill_no_readings[r.waybill_no].append(r)
        if r.license_plate:
            plate_readings[r.license_plate].append(r)

    waybill_no_set = {w.waybill_no for w in waybills}

    for waybill in waybills:
        if not waybill.departure_time:
            result.no_departure.append(waybill)
        if not waybill.arrival_time:
            result.no_arrival.append(waybill)

        matched_readings = []
        basis = ""

        if waybill.waybill_no in waybill_no_readings:
            matched_readings = waybill_no_readings[waybill.waybill_no]
            basis = "运单号匹配"

        if not matched_readings and waybill.license_plate and waybill.license_plate in plate_readings:
            plate_r = plate_readings[waybill.license_plate]
            if waybill.departure_time and waybill.arrival_time:
                plate_r = [r for r in plate_r
                           if waybill.departure_time <= r.timestamp <= waybill.arrival_time]
            elif waybill.departure_time:
                plate_r = [r for r in plate_r if r.timestamp >= waybill.departure_time]
            if plate_r:
                matched_readings = plate_r
                basis = "车牌+时间段匹配"

        if not matched_readings and waybill.device_id:
            dev_r = device_readings.get(waybill.device_id, [])
            if waybill.departure_time and waybill.arrival_time:
                dev_r = [r for r in dev_r
                         if waybill.departure_time <= r.timestamp <= waybill.arrival_time]
            elif waybill.departure_time:
                dev_r = [r for r in dev_r if r.timestamp >= waybill.departure_time]
            if dev_r:
                matched_readings = dev_r
                basis = "设备编号+时间段匹配"

        if not matched_readings and not waybill.device_id:
            result.no_device_id.append(waybill)
            result.no_temp_data.append(waybill)
            continue

        if not matched_readings:
            result.no_temp_data.append(waybill)
        else:
            result.matched.append(MatchPair(waybill, matched_readings, basis))

    all_matched_devices = {pair.waybill.device_id for pair in result.matched}
    for device_id in device_readings:
        if device_id not in all_matched_devices:
            result.unmatched_devices.append(device_id)

    return result
