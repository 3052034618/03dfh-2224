from datetime import timedelta
from typing import List, Optional
from .models import (
    Waybill, TemperatureReading, TempZone,
    WaybillReport, OverTempSegment, KeyTimePoint,
)


def generate_report(
    waybill: Waybill,
    readings: List[TemperatureReading],
    temp_zone: Optional[TempZone] = None,
) -> WaybillReport:
    zone = temp_zone or waybill.temp_zone
    report = WaybillReport(
        waybill_no=waybill.waybill_no,
        license_plate=waybill.license_plate,
        device_id=waybill.device_id,
        customer=waybill.customer,
        temp_zone=zone,
        departure_time=waybill.departure_time,
        arrival_time=waybill.arrival_time,
    )

    if not readings:
        return report

    report.has_data = True
    report.reading_count = len(readings)

    temps = [r.temperature for r in readings]
    report.min_temp = min(temps)
    report.max_temp = max(temps)

    if zone:
        over_segments = _find_over_temp_segments(readings, zone)
        report.over_temp_segments = over_segments
        report.over_temp_minutes = sum(s.minutes for s in over_segments)

    report.key_time_points = _extract_key_time_points(readings, zone, waybill)

    return report


def _find_over_temp_segments(
    readings: List[TemperatureReading], zone: TempZone
) -> List[OverTempSegment]:
    segments = []
    in_over = False
    start_idx = 0

    for i, r in enumerate(readings):
        is_over = zone.is_over(r.temperature)
        if is_over and not in_over:
            start_idx = i
            in_over = True
        elif not is_over and in_over:
            seg = _build_segment(readings, start_idx, i - 1)
            if seg:
                segments.append(seg)
            in_over = False

    if in_over:
        seg = _build_segment(readings, start_idx, len(readings) - 1)
        if seg:
            segments.append(seg)

    return segments


def _build_segment(readings, start_idx, end_idx):
    if start_idx > end_idx:
        return None
    start_r = readings[start_idx]
    end_r = readings[end_idx]
    seg_temps = [readings[j].temperature for j in range(start_idx, end_idx + 1)]
    minutes = (end_r.timestamp - start_r.timestamp).total_seconds() / 60.0
    return OverTempSegment(
        start=start_r.timestamp,
        end=end_r.timestamp,
        minutes=round(minutes, 1),
        min_temp=min(seg_temps),
        max_temp=max(seg_temps),
    )


def _extract_key_time_points(
    readings: List[TemperatureReading],
    zone: Optional[TempZone],
    waybill: Waybill,
) -> List[KeyTimePoint]:
    points = []

    if readings:
        points.append(KeyTimePoint(
            timestamp=readings[0].timestamp,
            temperature=readings[0].temperature,
            label="首条记录",
        ))
        points.append(KeyTimePoint(
            timestamp=readings[-1].timestamp,
            temperature=readings[-1].temperature,
            label="末条记录",
        ))

    min_temp = min(readings, key=lambda r: r.temperature)
    points.append(KeyTimePoint(
        timestamp=min_temp.timestamp,
        temperature=min_temp.temperature,
        label="最低温点",
    ))

    max_temp = max(readings, key=lambda r: r.temperature)
    points.append(KeyTimePoint(
        timestamp=max_temp.timestamp,
        temperature=max_temp.temperature,
        label="最高温点",
    ))

    if zone:
        for i, r in enumerate(readings):
            if zone.is_over(r.temperature):
                prev_ok = i == 0 or not zone.is_over(readings[i - 1].temperature)
                if prev_ok:
                    points.append(KeyTimePoint(
                        timestamp=r.timestamp,
                        temperature=r.temperature,
                        label="超温起始",
                    ))
                next_ok = i == len(readings) - 1 or not zone.is_over(readings[i + 1].temperature)
                if next_ok and not prev_ok:
                    points.append(KeyTimePoint(
                        timestamp=r.timestamp,
                        temperature=r.temperature,
                        label="超温结束",
                    ))

    if waybill.departure_time:
        dep_reading = min(readings, key=lambda r: abs((r.timestamp - waybill.departure_time).total_seconds()))
        points.append(KeyTimePoint(
            timestamp=dep_reading.timestamp,
            temperature=dep_reading.temperature,
            label="起运时刻",
        ))

    if waybill.arrival_time:
        arr_reading = min(readings, key=lambda r: abs((r.timestamp - waybill.arrival_time).total_seconds()))
        points.append(KeyTimePoint(
            timestamp=arr_reading.timestamp,
            temperature=arr_reading.temperature,
            label="到达时刻",
        ))

    seen = set()
    unique_points = []
    for p in points:
        key = (p.timestamp, p.label)
        if key not in seen:
            seen.add(key)
            unique_points.append(p)

    unique_points.sort(key=lambda p: p.timestamp)
    return unique_points
