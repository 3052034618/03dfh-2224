import csv
import os
import sys
from datetime import datetime
from typing import Optional

import click

from .loader import load_waybills, load_temperature_dir
from .matcher import match_waybills_readings
from .models import TempZone
from .reporter import generate_report
from .analyzer import detect_exceptions


_SEVERITY_LABEL = {"high": "严重", "medium": "中等", "low": "轻微"}
_CATEGORY_LABEL = {
    "gap": "数据断点",
    "no_data": "无数据",
    "spike": "温度突升/降",
    "over_temp": "超温",
}


@click.group()
@click.version_option("1.0.0", prog_name="coldchain")
def cli():
    """冷链运单温度留痕工具 —— 批量整理温度记录，生成留痕报告与异常清单"""
    pass


@cli.command()
@click.option("--waybill-file", "-w", required=True, help="运单清单文件路径（CSV/XLSX）")
@click.option("--temp-dir", "-t", required=True, help="温度记录文件夹路径")
@click.option("--output", "-o", default=None, help="匹配结果输出文件路径（CSV）")
def match(waybill_file, temp_dir, output):
    """校验匹配：按运单号/车牌/设备编号匹配，提示缺失项"""
    if not os.path.exists(waybill_file):
        click.echo(f"错误：运单文件不存在 → {waybill_file}", err=True)
        sys.exit(1)
    if not os.path.isdir(temp_dir):
        click.echo(f"错误：温度记录文件夹不存在 → {temp_dir}", err=True)
        sys.exit(1)

    click.echo("正在加载运单清单...")
    waybills = load_waybills(waybill_file)
    click.echo(f"  已加载 {len(waybills)} 条运单")

    click.echo("正在加载温度记录...")
    readings = load_temperature_dir(temp_dir)
    click.echo(f"  已加载 {len(readings)} 条温度记录")

    click.echo("正在匹配...")
    result = match_waybills_readings(waybills, readings)

    click.echo()
    click.echo("=" * 60)
    click.echo("匹配结果摘要")
    click.echo("=" * 60)
    click.echo(f"  匹配成功：{len(result.matched)} 票")
    click.echo(f"  无温度数据：{len(result.no_temp_data)} 票")
    click.echo(f"  缺少起运时间：{len(result.no_departure)} 票")
    click.echo(f"  缺少到达时间：{len(result.no_arrival)} 票")
    click.echo(f"  无设备编号：{len(result.no_device_id)} 票")
    click.echo(f"  未匹配设备：{len(result.unmatched_devices)} 个")

    if result.no_departure:
        click.echo()
        click.echo(click.style("⚠ 缺少起运时间的运单：", fg="yellow"))
        for w in result.no_departure:
            click.echo(f"  - {w.waybill_no}  车牌:{w.license_plate or '—'}  设备:{w.device_id or '—'}")

    if result.no_arrival:
        click.echo()
        click.echo(click.style("⚠ 缺少到达时间的运单：", fg="yellow"))
        for w in result.no_arrival:
            click.echo(f"  - {w.waybill_no}  车牌:{w.license_plate or '—'}  设备:{w.device_id or '—'}")

    if result.no_temp_data:
        click.echo()
        click.echo(click.style("✗ 无对应温度数据的运单：", fg="red"))
        for w in result.no_temp_data:
            click.echo(f"  - {w.waybill_no}  车牌:{w.license_plate or '—'}  设备:{w.device_id or '—'}")

    if result.no_device_id:
        click.echo()
        click.echo(click.style("⚠ 无设备编号的运单（无法匹配温度数据）：", fg="yellow"))
        for w in result.no_device_id:
            click.echo(f"  - {w.waybill_no}  车牌:{w.license_plate or '—'}")

    if result.unmatched_devices:
        click.echo()
        click.echo(click.style("ℹ 以下设备有温度数据但未匹配到运单：", fg="cyan"))
        for dev in result.unmatched_devices:
            click.echo(f"  - {dev}")

    if output:
        _write_match_csv(output, result)
        click.echo()
        click.echo(f"匹配结果已导出 → {output}")


def _write_match_csv(filepath, result):
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["运单号", "车牌", "设备编号", "客户", "温区", "起运时间", "到达时间",
                         "匹配状态", "温度记录条数"])
        for w, readings in result.matched:
            writer.writerow([
                w.waybill_no, w.license_plate, w.device_id, w.customer,
                w.raw_temp_zone, _fmt_dt(w.departure_time), _fmt_dt(w.arrival_time),
                "已匹配", len(readings),
            ])
        for w in result.no_temp_data:
            writer.writerow([
                w.waybill_no, w.license_plate, w.device_id, w.customer,
                w.raw_temp_zone, _fmt_dt(w.departure_time), _fmt_dt(w.arrival_time),
                "无温度数据", 0,
            ])


@cli.command()
@click.option("--waybill-file", "-w", required=True, help="运单清单文件路径（CSV/XLSX）")
@click.option("--temp-dir", "-t", required=True, help="温度记录文件夹路径")
@click.option("--customer", "-c", default=None, help="筛选客户名称（模糊匹配）")
@click.option("--temp-zone", "-z", default=None, help="覆盖温区标准（如 -18~-15）")
@click.option("--output", "-o", default=None, help="报告输出文件路径（CSV）")
def report(waybill_file, temp_dir, customer, temp_zone, output):
    """留痕报告：输出每票运单的最低温、最高温、超温分钟数和关键时间点"""
    if not os.path.exists(waybill_file):
        click.echo(f"错误：运单文件不存在 → {waybill_file}", err=True)
        sys.exit(1)
    if not os.path.isdir(temp_dir):
        click.echo(f"错误：温度记录文件夹不存在 → {temp_dir}", err=True)
        sys.exit(1)

    override_zone = TempZone.parse(temp_zone) if temp_zone else None

    waybills = load_waybills(waybill_file)
    readings = load_temperature_dir(temp_dir)

    if customer:
        waybills = [w for w in waybills if customer in (w.customer or "")]

    match_result = match_waybills_readings(waybills, readings)

    click.echo()
    click.echo("=" * 70)
    click.echo("冷链运单温度留痕报告")
    click.echo("=" * 70)
    if customer:
        click.echo(f"客户筛选：{customer}")
    if override_zone:
        click.echo(f"温区标准：{override_zone}")

    reports = []
    for waybill, wb_readings in match_result.matched:
        zone = override_zone or waybill.temp_zone
        r = generate_report(waybill, wb_readings, zone)
        reports.append(r)
        _print_waybill_report(r)

    for w in match_result.no_temp_data:
        r = generate_report(w, [], override_zone)
        reports.append(r)
        _print_waybill_report(r)

    click.echo()
    click.echo(f"共 {len(reports)} 票运单"
               f"（有数据 {sum(1 for r in reports if r.has_data)} 票，"
               f"无数据 {sum(1 for r in reports if not r.has_data)} 票）")

    if output:
        _write_report_csv(output, reports)
        click.echo(f"报告已导出 → {output}")


def _print_waybill_report(r):
    click.echo()
    click.echo(click.style(f"▶ 运单 {r.waybill_no}", bold=True))
    click.echo(f"  车牌：{r.license_plate or '—'}    设备：{r.device_id or '—'}    客户：{r.customer or '—'}")
    click.echo(f"  温区标准：{r.temp_zone or '—'}")
    click.echo(f"  起运：{_fmt_dt(r.departure_time)}    到达：{_fmt_dt(r.arrival_time)}")

    if not r.has_data:
        click.echo(click.style("  ✗ 无温度数据", fg="red"))
        return

    click.echo(f"  最低温：{r.min_temp}℃    最高温：{r.max_temp}℃    记录条数：{r.reading_count}")
    if r.temp_zone:
        over_label = click.style(f"{r.over_temp_minutes} 分钟", fg="red") if r.over_temp_minutes > 0 else "0 分钟"
        click.echo(f"  超温时长：{over_label}")

        if r.over_temp_segments:
            click.echo("  超温时段：")
            for seg in r.over_temp_segments:
                click.echo(f"    {_fmt_dt(seg.start)} ~ {_fmt_dt(seg.end)}  "
                           f"({seg.minutes}分钟)  {seg.min_temp}℃~{seg.max_temp}℃")

    click.echo("  关键时间点：")
    for kp in r.key_time_points:
        click.echo(f"    {_fmt_dt(kp.timestamp)}  {kp.temperature}℃  [{kp.label}]")


def _write_report_csv(filepath, reports):
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "运单号", "车牌", "设备编号", "客户", "温区标准",
            "起运时间", "到达时间", "最低温(℃)", "最高温(℃)",
            "记录条数", "超温分钟数", "有数据",
        ])
        for r in reports:
            writer.writerow([
                r.waybill_no, r.license_plate, r.device_id, r.customer,
                str(r.temp_zone) if r.temp_zone else "",
                _fmt_dt(r.departure_time), _fmt_dt(r.arrival_time),
                r.min_temp if r.min_temp is not None else "",
                r.max_temp if r.max_temp is not None else "",
                r.reading_count, r.over_temp_minutes, "是" if r.has_data else "否",
            ])

        writer.writerow([])
        writer.writerow(["=== 关键时间点明细 ==="])
        writer.writerow(["运单号", "时间", "温度(℃)", "标签"])
        for r in reports:
            for kp in r.key_time_points:
                writer.writerow([
                    r.waybill_no,
                    _fmt_dt(kp.timestamp),
                    kp.temperature,
                    kp.label,
                ])


@cli.command()
@click.option("--waybill-file", "-w", required=True, help="运单清单文件路径（CSV/XLSX）")
@click.option("--temp-dir", "-t", required=True, help="温度记录文件夹路径")
@click.option("--temp-zone", "-z", default=None, help="覆盖温区标准（如 -18~-15）")
@click.option("--severity", "-s", type=click.Choice(["high", "medium", "low", "all"]),
              default="all", help="筛选严重程度")
@click.option("--output", "-o", default=None, help="异常清单输出文件路径（CSV）")
def exceptions(waybill_file, temp_dir, temp_zone, severity, output):
    """异常清单：按严重程度列出断点、无数据、温度突升等情况"""
    if not os.path.exists(waybill_file):
        click.echo(f"错误：运单文件不存在 → {waybill_file}", err=True)
        sys.exit(1)
    if not os.path.isdir(temp_dir):
        click.echo(f"错误：温度记录文件夹不存在 → {temp_dir}", err=True)
        sys.exit(1)

    override_zone = TempZone.parse(temp_zone) if temp_zone else None

    waybills = load_waybills(waybill_file)
    readings = load_temperature_dir(temp_dir)

    match_result = match_waybills_readings(waybills, readings)

    all_items = []
    for waybill, wb_readings in match_result.matched:
        zone = override_zone or waybill.temp_zone
        items = detect_exceptions(waybill, wb_readings, zone)
        all_items.extend(items)

    for w in match_result.no_temp_data:
        zone = override_zone or w.temp_zone
        items = detect_exceptions(w, [], zone)
        all_items.extend(items)

    if severity != "all":
        all_items = [it for it in all_items if it.severity == severity]

    all_items.sort(key=lambda x: x.sort_key())

    click.echo()
    click.echo("=" * 70)
    click.echo("冷链温度异常清单")
    click.echo("=" * 70)

    if not all_items:
        click.echo(click.style("  未检测到异常 ✓", fg="green"))
        return

    high_count = sum(1 for it in all_items if it.severity == "high")
    medium_count = sum(1 for it in all_items if it.severity == "medium")
    low_count = sum(1 for it in all_items if it.severity == "low")
    click.echo(f"  严重 {high_count}  |  中等 {medium_count}  |  轻微 {low_count}")
    click.echo()

    for idx, it in enumerate(all_items, 1):
        sev_label = _SEVERITY_LABEL.get(it.severity, it.severity)
        cat_label = _CATEGORY_LABEL.get(it.category, it.category)
        color = {"high": "red", "medium": "yellow", "low": "white"}.get(it.severity, "white")

        header = f"#{idx}  [{sev_label}]  {cat_label}"
        click.echo(click.style(f"  {header}", fg=color))
        click.echo(f"  运单：{it.waybill_no}  车牌：{it.license_plate or '—'}  设备：{it.device_id or '—'}")
        if it.start_time and it.end_time:
            click.echo(f"  时间：{_fmt_dt(it.start_time)} ~ {_fmt_dt(it.end_time)}"
                       f"（{it.minutes}分钟）")
        if it.temperature is not None:
            click.echo(f"  温度：{it.temperature}℃")
        click.echo(f"  详情：{it.detail}")
        click.echo()

    if output:
        _write_exceptions_csv(output, all_items)
        click.echo(f"异常清单已导出 → {output}")


def _write_exceptions_csv(filepath, items):
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["序号", "严重程度", "类别", "运单号", "车牌", "设备编号",
                         "开始时间", "结束时间", "时长(分钟)", "温度(℃)", "详情"])
        for idx, it in enumerate(items, 1):
            writer.writerow([
                idx,
                _SEVERITY_LABEL.get(it.severity, it.severity),
                _CATEGORY_LABEL.get(it.category, it.category),
                it.waybill_no,
                it.license_plate,
                it.device_id,
                _fmt_dt(it.start_time),
                _fmt_dt(it.end_time),
                it.minutes,
                it.temperature if it.temperature is not None else "",
                it.detail,
            ])


def _fmt_dt(dt):
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M")


def main():
    cli()


if __name__ == "__main__":
    main()
