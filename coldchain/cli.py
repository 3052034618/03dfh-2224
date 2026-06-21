import csv
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Optional, List

import click

from .loader import load_waybills, load_temperature_dir
from .matcher import match_waybills_readings
from .models import TempZone, WaybillReport, ExceptionItem
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
@click.version_option("1.1.0", prog_name="coldchain")
def cli():
    """冷链运单温度留痕工具 —— 批量整理温度记录，生成留痕报告与异常清单"""
    pass


@cli.command()
@click.option("--waybill-file", "-w", required=True, help="运单清单文件路径（CSV/XLSX）")
@click.option("--temp-dir", "-t", required=True, help="温度记录文件夹路径")
@click.option("--output", "-o", default=None, help="匹配结果输出文件路径（CSV/XLSX）")
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

    if result.matched:
        click.echo()
        click.echo(click.style("✓ 匹配成功的运单：", fg="green"))
        for pair in result.matched:
            w = pair.waybill
            click.echo(f"  - {w.waybill_no}  车牌:{w.license_plate or '—'}  "
                       f"设备:{w.device_id or '—'}  "
                       f"匹配依据:{pair.basis}  "
                       f"记录数:{len(pair.readings)}")

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
        _ensure_dir(output)
        if output.lower().endswith((".xlsx", ".xls")):
            _write_excel(output, _build_match_rows(result), sheet_name="匹配结果")
        else:
            _write_match_csv(output, result)
        click.echo()
        click.echo(f"匹配结果已导出 → {output}")


def _build_match_rows(result):
    rows = []
    for pair in result.matched:
        w = pair.waybill
        rows.append({
            "运单号": w.waybill_no, "车牌": w.license_plate, "设备编号": w.device_id,
            "客户": w.customer, "温区": w.raw_temp_zone,
            "起运时间": _fmt_dt(w.departure_time), "到达时间": _fmt_dt(w.arrival_time),
            "匹配状态": "已匹配", "匹配依据": pair.basis, "温度记录条数": len(pair.readings),
        })
    for w in result.no_temp_data:
        rows.append({
            "运单号": w.waybill_no, "车牌": w.license_plate, "设备编号": w.device_id,
            "客户": w.customer, "温区": w.raw_temp_zone,
            "起运时间": _fmt_dt(w.departure_time), "到达时间": _fmt_dt(w.arrival_time),
            "匹配状态": "无温度数据", "匹配依据": "", "温度记录条数": 0,
        })
    return rows


def _write_match_csv(filepath, result):
    _ensure_dir(filepath)
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["运单号", "车牌", "设备编号", "客户", "温区", "起运时间", "到达时间",
                         "匹配状态", "匹配依据", "温度记录条数"])
        for row in _build_match_rows(result):
            writer.writerow([row["运单号"], row["车牌"], row["设备编号"], row["客户"],
                             row["温区"], row["起运时间"], row["到达时间"],
                             row["匹配状态"], row["匹配依据"], row["温度记录条数"]])


@cli.command()
@click.option("--waybill-file", "-w", required=True, help="运单清单文件路径（CSV/XLSX）")
@click.option("--temp-dir", "-t", required=True, help="温度记录文件夹路径")
@click.option("--customer", "-c", default=None, help="筛选客户名称（模糊匹配）")
@click.option("--temp-zone", "-z", default=None, help="覆盖温区标准（如 -18~-15 或 2-8）")
@click.option("--output", "-o", default=None, help="报告输出文件路径（CSV/XLSX）")
def report(waybill_file, temp_dir, customer, temp_zone, output):
    """留痕报告：输出每票运单的最低温、最高温、超温分钟数和关键时间点"""
    if not os.path.exists(waybill_file):
        click.echo(f"错误：运单文件不存在 → {waybill_file}", err=True)
        sys.exit(1)
    if not os.path.isdir(temp_dir):
        click.echo(f"错误：温度记录文件夹不存在 → {temp_dir}", err=True)
        sys.exit(1)

    override_zone = None
    if temp_zone:
        override_zone = TempZone.parse(temp_zone)
        if not override_zone:
            click.echo(f'错误：无法解析温区标准「{temp_zone}」，请检查格式', err=True)
            sys.exit(1)

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
        click.echo(click.style(f"温区标准（命令行指定）：{override_zone.display()}", bold=True))
    elif waybills and waybills[0].temp_zone:
        click.echo("温区标准：采用运单清单中的温区")

    reports = []
    for pair in match_result.matched:
        zone = override_zone or pair.waybill.temp_zone
        r = generate_report(pair.waybill, pair.readings, zone, pair.basis)
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
        _ensure_dir(output)
        if output.lower().endswith((".xlsx", ".xls")):
            _export_full_excel(output, match_result, reports, override_zone)
        else:
            _write_report_csv(output, reports)
        click.echo(f"报告已导出 → {output}")


def _print_waybill_report(r):
    click.echo()
    click.echo(click.style(f"▶ 运单 {r.waybill_no}", bold=True))
    click.echo(f"  车牌：{r.license_plate or '—'}    设备：{r.device_id or '—'}    客户：{r.customer or '—'}")
    if r.temp_zone:
        click.echo(f"  温区标准：{r.temp_zone.display()}")
    if r.match_basis:
        click.echo(f"  匹配依据：{r.match_basis}")
    click.echo(f"  起运：{_fmt_dt(r.departure_time)}    到达：{_fmt_dt(r.arrival_time)}")

    if not r.has_data:
        click.echo(click.style("  ✗ 无温度数据", fg="red"))
        return

    click.echo(f"  最低温：{r.min_temp}℃    最高温：{r.max_temp}℃    记录条数：{r.reading_count}")
    if r.temp_zone:
        over_label = click.style(f"{r.over_temp_minutes} 分钟", fg="red") if r.over_temp_minutes > 0 else "0 分钟"
        click.echo(f"  超温时长（按{r.temp_zone.display()}标准）：{over_label}")

        if r.over_temp_segments:
            click.echo("  超温时段：")
            for seg in r.over_temp_segments:
                click.echo(f"    {_fmt_dt(seg.start)} ~ {_fmt_dt(seg.end)}  "
                           f"({seg.minutes}分钟)  {seg.min_temp}℃~{seg.max_temp}℃")

    click.echo("  关键时间点：")
    for kp in r.key_time_points:
        click.echo(f"    {_fmt_dt(kp.timestamp)}  {kp.temperature}℃  [{kp.label}]")


def _build_report_rows(reports):
    rows = []
    for r in reports:
        rows.append({
            "运单号": r.waybill_no, "车牌": r.license_plate, "设备编号": r.device_id,
            "客户": r.customer,
            "温区标准": r.temp_zone.display() if r.temp_zone else "",
            "匹配依据": r.match_basis,
            "起运时间": _fmt_dt(r.departure_time), "到达时间": _fmt_dt(r.arrival_time),
            "最低温(℃)": r.min_temp if r.min_temp is not None else "",
            "最高温(℃)": r.max_temp if r.max_temp is not None else "",
            "记录条数": r.reading_count,
            "超温分钟数": r.over_temp_minutes,
            "有数据": "是" if r.has_data else "否",
        })
    return rows


def _build_keypoint_rows(reports):
    rows = []
    for r in reports:
        for kp in r.key_time_points:
            rows.append({
                "运单号": r.waybill_no,
                "时间": _fmt_dt(kp.timestamp),
                "温度(℃)": kp.temperature,
                "标签": kp.label,
            })
    return rows


def _write_report_csv(filepath, reports):
    _ensure_dir(filepath)
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        headers = ["运单号", "车牌", "设备编号", "客户", "温区标准", "匹配依据",
                   "起运时间", "到达时间", "最低温(℃)", "最高温(℃)",
                   "记录条数", "超温分钟数", "有数据"]
        writer.writerow(headers)
        for row in _build_report_rows(reports):
            writer.writerow([row[h] for h in headers])

        writer.writerow([])
        writer.writerow(["=== 关键时间点明细 ==="])
        writer.writerow(["运单号", "时间", "温度(℃)", "标签"])
        for row in _build_keypoint_rows(reports):
            writer.writerow([row["运单号"], row["时间"], row["温度(℃)"], row["标签"]])


@cli.command()
@click.option("--waybill-file", "-w", required=True, help="运单清单文件路径（CSV/XLSX）")
@click.option("--temp-dir", "-t", required=True, help="温度记录文件夹路径")
@click.option("--temp-zone", "-z", default=None, help="覆盖温区标准（如 -18~-15 或 2-8）")
@click.option("--severity", "-s", type=click.Choice(["high", "medium", "low", "all"]),
              default="all", help="筛选严重程度")
@click.option("--output", "-o", default=None, help="异常清单输出文件路径（CSV/XLSX）")
@click.option("--note-template", "-n", is_flag=True, default=False,
              help="导出时增加备注列和处理人列，方便回看补说明")
def exceptions(waybill_file, temp_dir, temp_zone, severity, output, note_template):
    """异常清单：按严重程度列出断点、无数据、温度突升等情况"""
    if not os.path.exists(waybill_file):
        click.echo(f"错误：运单文件不存在 → {waybill_file}", err=True)
        sys.exit(1)
    if not os.path.isdir(temp_dir):
        click.echo(f"错误：温度记录文件夹不存在 → {temp_dir}", err=True)
        sys.exit(1)

    override_zone = None
    if temp_zone:
        override_zone = TempZone.parse(temp_zone)
        if not override_zone:
            click.echo(f'错误：无法解析温区标准「{temp_zone}」，请检查格式', err=True)
            sys.exit(1)
        click.echo(click.style(f"温区标准（命令行指定）：{override_zone.display()}", bold=True))

    waybills = load_waybills(waybill_file)
    readings = load_temperature_dir(temp_dir)

    match_result = match_waybills_readings(waybills, readings)

    all_items = []
    for pair in match_result.matched:
        zone = override_zone or pair.waybill.temp_zone
        items = detect_exceptions(pair.waybill, pair.readings, zone)
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
        _ensure_dir(output)
        if output.lower().endswith((".xlsx", ".xls")):
            _write_excel(output, _build_exception_rows(all_items, note_template),
                         sheet_name="异常清单")
        else:
            _write_exceptions_csv(output, all_items, note_template)
        click.echo(f"异常清单已导出 → {output}")


def _build_exception_rows(items, note_template=False):
    rows = []
    for idx, it in enumerate(items, 1):
        row = {
            "序号": idx,
            "严重程度": _SEVERITY_LABEL.get(it.severity, it.severity),
            "类别": _CATEGORY_LABEL.get(it.category, it.category),
            "运单号": it.waybill_no,
            "车牌": it.license_plate,
            "设备编号": it.device_id,
            "开始时间": _fmt_dt(it.start_time),
            "结束时间": _fmt_dt(it.end_time),
            "时长(分钟)": it.minutes,
            "温度(℃)": it.temperature if it.temperature is not None else "",
            "详情": it.detail,
        }
        if note_template:
            row["备注"] = ""
            row["处理人"] = ""
        rows.append(row)
    return rows


def _write_exceptions_csv(filepath, items, note_template=False):
    _ensure_dir(filepath)
    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        headers = ["序号", "严重程度", "类别", "运单号", "车牌", "设备编号",
                   "开始时间", "结束时间", "时长(分钟)", "温度(℃)", "详情"]
        if note_template:
            headers += ["备注", "处理人"]
        writer.writerow(headers)
        for row in _build_exception_rows(items, note_template):
            writer.writerow([row[h] for h in headers])


@cli.command()
@click.option("--month-dir", "-m", required=True,
              help="月份文件夹路径（含运单清单和温度子目录）")
@click.option("--temp-zone", "-z", default=None, help="覆盖温区标准（如 -18~-15 或 2-8）")
@click.option("--output", "-o", default=None, help="汇总输出文件路径（XLSX，推荐）")
def batch(month_dir, temp_zone, output):
    """批量处理：扫描月份文件夹，按客户汇总，一次生成完整留痕报告"""
    if not os.path.isdir(month_dir):
        click.echo(f"错误：月份文件夹不存在 → {month_dir}", err=True)
        sys.exit(1)

    override_zone = None
    if temp_zone:
        override_zone = TempZone.parse(temp_zone)
        if not override_zone:
            click.echo(f'错误：无法解析温区标准「{temp_zone}」，请检查格式', err=True)
            sys.exit(1)
        click.echo(click.style(f"温区标准（命令行指定）：{override_zone.display()}", bold=True))

    waybill_files = []
    temp_dirs = []

    for fname in sorted(os.listdir(month_dir)):
        fpath = os.path.join(month_dir, fname)
        if os.path.isfile(fpath) and fname.lower().endswith((".csv", ".xlsx", ".xls")):
            waybill_files.append(fpath)
        elif os.path.isdir(fpath):
            temp_dirs.append(fpath)

    if not waybill_files:
        click.echo(f"错误：月份文件夹中未找到运单清单文件 → {month_dir}", err=True)
        sys.exit(1)

    all_waybills = []
    for wf in waybill_files:
        click.echo(f"加载运单 → {os.path.basename(wf)}")
        all_waybills.extend(load_waybills(wf))

    all_readings = []
    for td in temp_dirs:
        click.echo(f"加载温度 → {os.path.basename(td)}/")
        all_readings.extend(load_temperature_dir(td))

    click.echo(f"共加载 {len(all_waybills)} 条运单、{len(all_readings)} 条温度记录")

    match_result = match_waybills_readings(all_waybills, all_readings)

    customer_waybills = defaultdict(list)
    for w in all_waybills:
        cust = w.customer or "未指定客户"
        customer_waybills[cust].append(w)

    customer_match = defaultdict(lambda: {"matched": 0, "no_data": 0, "exceptions": 0})
    all_reports = []
    all_exceptions = []

    for pair in match_result.matched:
        cust = pair.waybill.customer or "未指定客户"
        zone = override_zone or pair.waybill.temp_zone
        r = generate_report(pair.waybill, pair.readings, zone, pair.basis)
        all_reports.append(r)
        customer_match[cust]["matched"] += 1
        items = detect_exceptions(pair.waybill, pair.readings, zone)
        all_exceptions.extend(items)
        if items:
            customer_match[cust]["exceptions"] += 1

    for w in match_result.no_temp_data:
        cust = w.customer or "未指定客户"
        zone = override_zone or w.temp_zone
        r = generate_report(w, [], override_zone)
        all_reports.append(r)
        customer_match[cust]["no_data"] += 1
        items = detect_exceptions(w, [], zone)
        all_exceptions.extend(items)
        if items:
            customer_match[cust]["exceptions"] += 1

    click.echo()
    click.echo("=" * 70)
    click.echo("批量处理结果 — 按客户汇总")
    click.echo("=" * 70)

    total_wb = 0
    total_matched = 0
    total_no_data = 0
    total_exceptions = 0

    for cust in sorted(customer_waybills.keys()):
        cm = customer_match[cust]
        wb_count = len(customer_waybills[cust])
        total_wb += wb_count
        total_matched += cm["matched"]
        total_no_data += cm["no_data"]
        total_exceptions += cm["exceptions"]

        status_parts = [f"{wb_count}票"]
        if cm["matched"]:
            status_parts.append(click.style(f"有数据{cm['matched']}票", fg="green"))
        if cm["no_data"]:
            status_parts.append(click.style(f"缺数据{cm['no_data']}票", fg="red"))
        if cm["exceptions"]:
            status_parts.append(click.style(f"有异常{cm['exceptions']}票", fg="yellow"))

        click.echo(f"  {cust}：{' | '.join(status_parts)}")

    click.echo()
    click.echo(f"  合计 {total_wb} 票运单 | "
               f"有数据 {total_matched} | 缺数据 {total_no_data} | 有异常 {total_exceptions}")

    if output:
        _ensure_dir(output)
        if not output.lower().endswith((".xlsx", ".xls")):
            output = output.rsplit(".", 1)[0] + ".xlsx"
            click.echo(f"提示：批量导出推荐 XLSX 格式，已自动调整为 → {output}")

        _export_full_excel(output, match_result, all_reports, override_zone,
                           all_exceptions=all_exceptions)
        click.echo(f"完整留痕报告已导出 → {output}")
    else:
        click.echo()
        click.echo("提示：使用 -o output.xlsx 可导出完整留痕报告（含匹配、摘要、异常三张表）")


def _export_full_excel(filepath, match_result, reports, override_zone,
                       all_exceptions=None):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = openpyxl.Workbook()

    header_font = Font(bold=True)
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_font_white = Font(bold=True, color="FFFFFF")
    high_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    medium_fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )

    ws1 = wb.active
    ws1.title = "匹配结果"
    match_rows = _build_match_rows(match_result)
    _write_sheet(ws1, match_rows, header_font, header_fill, header_font_white, thin_border)

    ws2 = wb.create_sheet("温度摘要")
    report_rows = _build_report_rows(reports)
    _write_sheet(ws2, report_rows, header_font, header_fill, header_font_white, thin_border)

    ws3 = wb.create_sheet("关键时间点")
    kp_rows = _build_keypoint_rows(reports)
    _write_sheet(ws3, kp_rows, header_font, header_fill, header_font_white, thin_border)

    if all_exceptions is not None:
        ws4 = wb.create_sheet("异常清单")
        exc_rows = _build_exception_rows(all_exceptions, note_template=True)
        _write_sheet(ws4, exc_rows, header_font, header_fill, header_font_white, thin_border)
        for row_idx in range(2, ws4.max_row + 1):
            sev_cell = ws4.cell(row=row_idx, column=2)
            if sev_cell.value == "严重":
                for col_idx in range(1, ws4.max_column + 1):
                    ws4.cell(row=row_idx, column=col_idx).fill = high_fill
            elif sev_cell.value == "中等":
                for col_idx in range(1, ws4.max_column + 1):
                    ws4.cell(row=row_idx, column=col_idx).fill = medium_fill

    for ws in wb.worksheets:
        for col in ws.columns:
            max_len = 0
            col_letter = col[0].column_letter
            for cell in col:
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            ws.column_dimensions[col_letter].width = min(max_len + 4, 40)

    wb.save(filepath)


def _write_sheet(ws, rows, header_font, header_fill, header_font_white, thin_border):
    from openpyxl.styles import Alignment
    if not rows:
        return
    headers = list(rows[0].keys())
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = header_font_white
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border

    for row_idx, row_data in enumerate(rows, 2):
        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=row_data.get(h, ""))
            cell.border = thin_border
            cell.alignment = Alignment(vertical="center")


def _write_excel(filepath, rows, sheet_name="Sheet1"):
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"),
    )

    _write_sheet(ws, rows, header_font, header_fill, header_font, thin_border)

    for col in ws.columns:
        max_len = 0
        col_letter = col[0].column_letter
        for cell in col:
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = min(max_len + 4, 40)

    wb.save(filepath)


def _ensure_dir(filepath):
    d = os.path.dirname(filepath)
    if d:
        os.makedirs(d, exist_ok=True)


def _fmt_dt(dt):
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M")


def main():
    cli()


if __name__ == "__main__":
    main()
