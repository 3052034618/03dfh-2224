from datetime import timedelta
from typing import List, Optional
from .models import Waybill, TemperatureReading, TempZone, ExceptionItem


_GAP_THRESHOLD_MINUTES = 30
_LONG_GAP_THRESHOLD_MINUTES = 120
_SPIKE_THRESHOLD = 5.0
_OVER_TEMP_CONTINUE_MINUTES = 10


def detect_exceptions(
    waybill: Waybill,
    readings: List[TemperatureReading],
    temp_zone: Optional[TempZone] = None,
) -> List[ExceptionItem]:
    items = []
    zone = temp_zone or waybill.temp_zone

    if not readings:
        items.append(ExceptionItem(
            waybill_no=waybill.waybill_no,
            license_plate=waybill.license_plate,
            device_id=waybill.device_id,
            severity="high",
            category="no_data",
            detail="整票运单无任何温度记录",
        ))
        return items

    items.extend(_detect_gaps(waybill, readings))
    items.extend(_detect_spikes(waybill, readings))
    if zone:
        items.extend(_detect_over_temp(waybill, readings, zone))

    items.sort(key=lambda x: x.sort_key())
    return items


def _detect_gaps(waybill, readings) -> List[ExceptionItem]:
    items = []
    for i in range(1, len(readings)):
        prev = readings[i - 1]
        curr = readings[i]
        gap_min = (curr.timestamp - prev.timestamp).total_seconds() / 60.0

        if gap_min >= _LONG_GAP_THRESHOLD_MINUTES:
            items.append(ExceptionItem(
                waybill_no=waybill.waybill_no,
                license_plate=waybill.license_plate,
                device_id=waybill.device_id,
                severity="high",
                category="gap",
                start_time=prev.timestamp,
                end_time=curr.timestamp,
                minutes=round(gap_min, 1),
                detail=f"长时间无数据：间隔{round(gap_min, 0)}分钟"
                       f"（{prev.timestamp.strftime('%H:%M')}→{curr.timestamp.strftime('%H:%M')}）",
            ))
        elif gap_min >= _GAP_THRESHOLD_MINUTES:
            items.append(ExceptionItem(
                waybill_no=waybill.waybill_no,
                license_plate=waybill.license_plate,
                device_id=waybill.device_id,
                severity="medium",
                category="gap",
                start_time=prev.timestamp,
                end_time=curr.timestamp,
                minutes=round(gap_min, 1),
                detail=f"疑似断点：间隔{round(gap_min, 0)}分钟"
                       f"（{prev.timestamp.strftime('%H:%M')}→{curr.timestamp.strftime('%H:%M')}）",
            ))

    if waybill.departure_time and readings:
        first = readings[0]
        if first.timestamp > waybill.departure_time + timedelta(minutes=_GAP_THRESHOLD_MINUTES):
            lead_min = (first.timestamp - waybill.departure_time).total_seconds() / 60.0
            items.append(ExceptionItem(
                waybill_no=waybill.waybill_no,
                license_plate=waybill.license_plate,
                device_id=waybill.device_id,
                severity="medium",
                category="gap",
                start_time=waybill.departure_time,
                end_time=first.timestamp,
                minutes=round(lead_min, 1),
                detail=f"起运后{round(lead_min, 0)}分钟才出现首条温度记录",
            ))

    if waybill.arrival_time and readings:
        last = readings[-1]
        if last.timestamp < waybill.arrival_time - timedelta(minutes=_GAP_THRESHOLD_MINUTES):
            lag_min = (waybill.arrival_time - last.timestamp).total_seconds() / 60.0
            items.append(ExceptionItem(
                waybill_no=waybill.waybill_no,
                license_plate=waybill.license_plate,
                device_id=waybill.device_id,
                severity="medium",
                category="gap",
                start_time=last.timestamp,
                end_time=waybill.arrival_time,
                minutes=round(lag_min, 1),
                detail=f"末条记录距到达时间{round(lag_min, 0)}分钟",
            ))

    return items


def _detect_spikes(waybill, readings) -> List[ExceptionItem]:
    items = []
    for i in range(1, len(readings)):
        delta = abs(readings[i].temperature - readings[i - 1].temperature)
        if delta >= _SPIKE_THRESHOLD:
            items.append(ExceptionItem(
                waybill_no=waybill.waybill_no,
                license_plate=waybill.license_plate,
                device_id=waybill.device_id,
                severity="high",
                category="spike",
                start_time=readings[i - 1].timestamp,
                end_time=readings[i].timestamp,
                temperature=readings[i].temperature,
                detail=f"温度突升/降{round(delta, 1)}℃"
                       f"（{readings[i-1].temperature}℃→{readings[i].temperature}℃）",
            ))
    return items


def _detect_over_temp(waybill, readings, zone) -> List[ExceptionItem]:
    items = []
    in_over = False
    start_idx = 0

    for i, r in enumerate(readings):
        is_over = zone.is_over(r.temperature)
        if is_over and not in_over:
            start_idx = i
            in_over = True
        elif not is_over and in_over:
            duration = (readings[i - 1].timestamp - readings[start_idx].timestamp).total_seconds() / 60.0
            if duration >= _OVER_TEMP_CONTINUE_MINUTES:
                severity = "high" if duration >= 30 else "medium"
                seg_temps = [readings[j].temperature for j in range(start_idx, i)]
                items.append(ExceptionItem(
                    waybill_no=waybill.waybill_no,
                    license_plate=waybill.license_plate,
                    device_id=waybill.device_id,
                    severity=severity,
                    category="over_temp",
                    start_time=readings[start_idx].timestamp,
                    end_time=readings[i - 1].timestamp,
                    minutes=round(duration, 1),
                    temperature=max(seg_temps) if zone.is_over(max(seg_temps)) else min(seg_temps),
                    detail=f"超温持续{round(duration, 0)}分钟"
                           f"（{readings[start_idx].timestamp.strftime('%H:%M')}"
                           f"→{readings[i-1].timestamp.strftime('%H:%M')}，"
                           f"偏离{zone}）",
                ))
            in_over = False

    if in_over:
        duration = (readings[-1].timestamp - readings[start_idx].timestamp).total_seconds() / 60.0
        if duration >= _OVER_TEMP_CONTINUE_MINUTES:
            severity = "high" if duration >= 30 else "medium"
            seg_temps = [readings[j].temperature for j in range(start_idx, len(readings))]
            items.append(ExceptionItem(
                waybill_no=waybill.waybill_no,
                license_plate=waybill.license_plate,
                device_id=waybill.device_id,
                severity=severity,
                category="over_temp",
                start_time=readings[start_idx].timestamp,
                end_time=readings[-1].timestamp,
                minutes=round(duration, 1),
                temperature=max(seg_temps) if zone.is_over(max(seg_temps)) else min(seg_temps),
                detail=f"超温持续{round(duration, 0)}分钟"
                       f"（至记录末尾仍超温，偏离{zone}）",
            ))

    return items
