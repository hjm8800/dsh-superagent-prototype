"""Excel 数据对比表导出。

build(comparison) -> bytes：生成包含总览、结算汇总/逐项、材料、竣工工程量、风险建议的 xlsx。
"""
from __future__ import annotations

import io
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .diff import (
    RISK_HIGH,
    RISK_MID,
    STATUS_NEW,
    STATUS_REMOVED,
    STATUS_SAME,
    STATUS_ORDER,
)
from .normalize import format_delta, format_money, format_number, format_ratio

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)
TITLE_FONT = Font(bold=True, size=15, color="1F4E78")
SECTION_FONT = Font(bold=True, size=12, color="1F4E78")
SUBTITLE_FONT = Font(bold=True, size=10, color="404040")
NOTE_FONT = Font(size=9, color="7F7F7F")
POS_FONT = Font(color="C00000")
NEG_FONT = Font(color="2E7D32")
RISK_FILLS = {
    RISK_HIGH: PatternFill("solid", fgColor="F8CBD1"),
    RISK_MID: PatternFill("solid", fgColor="FFE8B3"),
    "低": PatternFill("solid", fgColor="E2EFDA"),
}
SAME_FILL = PatternFill("solid", fgColor="F2F2F2")
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

MONEY_FMT = "#,##0.00"
QTY_FMT = "#,##0.###"
RATIO_FMT = "0.0%"

DIFF_HEADERS = [
    ("状态", "status", 10, "text"),
    ("风险", "risk", 6, "text"),
    ("名称", "name", 34, "text"),
    ("规格/编号", "spec", 26, "text"),
    ("单位", "unit", 8, "text"),
    ("原数量", "old_qty", 12, "qty"),
    ("新数量", "new_qty", 12, "qty"),
    ("数量差", "qty_delta", 12, "delta_qty"),
    ("原单价", "old_price", 14, "money"),
    ("新单价", "new_price", 14, "money"),
    ("原金额", "old_amount", 16, "money"),
    ("新金额", "new_amount", 16, "money"),
    ("金额差", "amount_delta", 16, "delta_money"),
    ("金额变化率", "amount_ratio", 12, "ratio"),
    ("说明", "detail", 62, "text"),
]


def _cell(ws, row: int, column: int, value, font=None, fill=None, number_format=None,
          alignment=None, border=True):
    cell = ws.cell(row=row, column=column, value=value)
    if font:
        cell.font = font
    if fill:
        cell.fill = fill
    if number_format:
        cell.number_format = number_format
    if alignment:
        cell.alignment = alignment
    if border:
        cell.border = BORDER
    return cell


def _write_title(ws, row: int, text: str, span: int = 8) -> int:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max(span, 2))
    _cell(ws, row, 1, text, font=TITLE_FONT, border=False)
    ws.row_dimensions[row].height = 24
    return row + 1


def _write_section(ws, row: int, text: str, span: int = 8) -> int:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max(span, 2))
    _cell(ws, row, 1, text, font=SECTION_FONT, border=False)
    return row + 1


def _write_note(ws, row: int, text: str, span: int = 8) -> int:
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=max(span, 2))
    cell = _cell(ws, row, 1, text, font=NOTE_FONT, border=False)
    cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[row].height = 30
    return row + 1


def _write_headers(ws, row: int, headers: list[str]) -> int:
    for column, header in enumerate(headers, start=1):
        _cell(ws, row, column, header, font=HEADER_FONT, fill=HEADER_FILL,
              alignment=Alignment(horizontal="center", vertical="center", wrap_text=True))
    ws.row_dimensions[row].height = 28
    return row + 1


def _apply_spec(ws, row: int, column: int, value, spec: str):
    cell = _cell(ws, row, column, value, border=True,
                 alignment=Alignment(vertical="center", wrap_text=True))
    if spec == "money":
        cell.number_format = MONEY_FMT
    elif spec == "qty":
        cell.number_format = QTY_FMT
    elif spec == "ratio":
        cell.number_format = RATIO_FMT
    elif spec == "delta_money":
        cell.number_format = MONEY_FMT
        if isinstance(value, (int, float)):
            cell.font = POS_FONT if value > 0 else (NEG_FONT if value < 0 else None)
    elif spec == "delta_qty":
        cell.number_format = QTY_FMT
        if isinstance(value, (int, float)):
            cell.font = POS_FONT if value > 0 else (NEG_FONT if value < 0 else None)
    return cell


def _diff_detail(diff: dict) -> str:
    parts = list(diff.get("reasons") or [])
    if diff.get("note"):
        parts.append(diff["note"])
    return "；".join(part for part in parts if part)


def _diff_row(diff: dict) -> list:
    old = diff.get("old") or {}
    new = diff.get("new") or {}
    return [
        diff.get("status", ""),
        diff.get("risk", ""),
        diff.get("name", ""),
        diff.get("spec", "") or diff.get("item_type", ""),
        diff.get("unit", ""),
        old.get("qty"),
        new.get("qty"),
        diff.get("qty_delta"),
        old.get("unit_price"),
        new.get("unit_price"),
        old.get("amount"),
        new.get("amount"),
        diff.get("amount_delta"),
        diff.get("amount_ratio"),
        _diff_detail(diff),
    ]


def _write_diff_table(ws, row: int, diffs: list[dict], include_unchanged: bool = False,
                      span: int = 15) -> int:
    row = _write_headers(ws, row, [header for header, _, _, _ in DIFF_HEADERS])
    for column, (_, _, width, _) in enumerate(DIFF_HEADERS, start=1):
        ws.column_dimensions[get_column_letter(column)].width = width
    for diff in diffs:
        if not include_unchanged and diff.get("status") == STATUS_SAME:
            continue
        values = _diff_row(diff)
        for column, (_, _, _, spec) in enumerate(DIFF_HEADERS, start=1):
            _apply_spec(ws, row, column, values[column - 1], spec)
        risk = diff.get("risk")
        fill = RISK_FILLS.get(risk)
        if diff.get("status") == STATUS_SAME:
            fill = SAME_FILL
        if fill:
            for column in range(1, len(DIFF_HEADERS) + 1):
                ws.cell(row=row, column=column).fill = fill
        row += 1
    if row > 2:
        ws.auto_filter.ref = f"A{row and 1}:{get_column_letter(span)}{row - 1}"
    return row


def _summary_metrics(comparison: dict) -> list[list]:
    analysis = comparison.get("analysis") or {}
    totals = analysis.get("totals") or {}
    settlement_totals = totals.get("settlement") or {}
    material_totals = totals.get("material") or {}
    settlement = comparison["documents"]["settlement"]
    material = comparison["documents"]["material"]
    boq = comparison["documents"]["boq"]

    rows: list[list] = []
    if settlement_totals.get("old_equipment") is not None or settlement_totals.get("new_equipment") is not None:
        old_value, new_value = settlement_totals.get("old_equipment"), settlement_totals.get("new_equipment")
        delta = (new_value - old_value) if (old_value is not None and new_value is not None) else None
        rows.append(["审核结算（表一口径·设备检修费）", old_value, new_value, delta,
                     (delta / old_value) if (delta is not None and old_value) else None,
                     settlement["summary"].get("changed_count"),
                     settlement["summary"].get("risk_counts", {}).get(RISK_HIGH, 0)])
    if settlement_totals.get("new_discount_final") is not None and settlement_totals.get("old_equipment") is not None:
        old_value = settlement_totals.get("old_equipment")
        new_value = settlement_totals.get("new_discount_final")
        delta = new_value - old_value
        rows.append(["审核结算（下浮后送审口径）", old_value, new_value, delta,
                     delta / old_value if old_value else None, None, None])
    if settlement_totals.get("old_complete") is not None and settlement_totals.get("new_complete") is not None:
        old_value, new_value = settlement_totals["old_complete"], settlement_totals["new_complete"]
        delta = new_value - old_value
        rows.append(["审核结算（含配件完整口径）", old_value, new_value, delta,
                     delta / old_value if old_value else None, None, None])
    if material_totals.get("old_yigong") is not None or material_totals.get("new_yigong") is not None:
        old_value, new_value = material_totals.get("old_yigong"), material_totals.get("new_yigong")
        delta = (new_value - old_value) if (old_value is not None and new_value is not None) else None
        rows.append(["材料清册（乙供未计价）", old_value, new_value, delta,
                     (delta / old_value) if (delta is not None and old_value) else None,
                     material["summary"].get("changed_count"),
                     material["summary"].get("risk_counts", {}).get(RISK_HIGH, 0)])
    if material_totals.get("new_ag") is not None:
        old_value, new_value = material_totals.get("old_ag"), material_totals.get("new_ag")
        delta = (new_value - old_value) if (old_value is not None and new_value is not None) else None
        rows.append(["材料清册（甲供未计价）", old_value, new_value, delta,
                     (delta / old_value) if (delta is not None and old_value) else None,
                     None, None])
    old_parts, new_parts = material_totals.get("old_parts"), material_totals.get("new_parts")
    if old_parts is not None or new_parts is not None:
        delta = (new_parts - old_parts) if (old_parts is not None and new_parts is not None) else None
        rows.append(["配件购置费（汇总口径）", old_parts, new_parts, delta,
                     (delta / old_parts) if (delta is not None and old_parts) else None, None, None])
    boq_counts = {
        "texts_old": len(comparison["documents"]["boq"]["old"].get("texts") or []),
        "texts_new": len(comparison["documents"]["boq"]["new"].get("texts") or []),
    }
    rows.append(["竣工资料工程量（文本段数）", boq_counts["texts_old"], boq_counts["texts_new"],
                 boq_counts["texts_new"] - boq_counts["texts_old"], None,
                 boq["summary"].get("changed_count"), boq["summary"].get("risk_counts", {}).get(RISK_HIGH, 0)])
    return rows


def _sheet_overview(ws, comparison: dict):
    project = comparison.get("project") or {}
    analysis = comparison.get("analysis") or {}
    row = 1
    row = _write_title(ws, row, f"{project.get('name', '')} · 原/新资料逐项对比分析", span=8)
    row = _write_note(ws, row,
                      f"生成时间：{project.get('generated_at', '')}；原版资料目录：{project.get('old_dir', '')}；"
                      f"新版资料目录：{project.get('new_dir', '')}")
    ws.column_dimensions["A"].width = 30
    for column in range(2, 9):
        ws.column_dimensions[get_column_letter(column)].width = 16

    row = _write_section(ws, row + 1, "一、关键指标", span=8)
    row = _write_headers(ws, row, ["类别", "原版", "新版", "变化", "变化率", "变化项数", "高风险项"])
    for metric in _summary_metrics(comparison):
        for column, value in enumerate(metric, start=1):
            spec = "text"
            if column in (2, 3, 4) and isinstance(value, (int, float)):
                spec = "money"
            elif column == 5 and isinstance(value, (int, float)):
                spec = "ratio"
            cell = _apply_spec(ws, row, column, value, spec)
            if column == 4 and isinstance(value, (int, float)):
                cell.font = POS_FONT if value > 0 else (NEG_FONT if value < 0 else None)
        row += 1

    row = _write_section(ws, row + 1, "二、主要结论", span=8)
    for index, conclusion in enumerate(analysis.get("conclusions") or [], start=1):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
        cell = _cell(ws, row, 1, f"{index}. {conclusion}", border=False,
                     alignment=Alignment(wrap_text=True, vertical="top"))
        ws.row_dimensions[row].height = max(18, 16 * (len(conclusion) // 70 + 1))
        row += 1

    findings = analysis.get("findings") or []
    if findings:
        row = _write_section(ws, row + 1, "三、资料完整性与口径发现", span=8)
        for index, finding in enumerate(findings, start=1):
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
            cell = _cell(ws, row, 1, f"{index}. {finding}", border=False,
                         alignment=Alignment(wrap_text=True, vertical="top"))
            ws.row_dimensions[row].height = max(18, 16 * (len(finding) // 70 + 1))
            row += 1

    risks = analysis.get("risks") or []
    row = _write_section(ws, row + 1, "四、风险汇总", span=8)
    risk_counts = comparison.get("summary", {}).get("risk_counts") or {}
    row = _write_note(ws, row,
                      f"高风险 {risk_counts.get(RISK_HIGH, 0)} 项、中风险 {risk_counts.get(RISK_MID, 0)} 项、"
                      f"低风险 {risk_counts.get('低', 0)} 项。详见“风险与建议”工作表。")
    return row


def _sheet_settlement_summary(ws, comparison: dict):
    row = 1
    row = _write_title(ws, row, "审核结算 · 汇总对比", span=8)
    analysis = comparison.get("analysis") or {}
    totals = (analysis.get("totals") or {}).get("settlement") or {}
    ws.column_dimensions["A"].width = 26
    for column in range(2, 9):
        ws.column_dimensions[get_column_letter(column)].width = 18

    row = _write_section(ws, row + 1, "费用构成", span=8)
    row = _write_headers(ws, row, ["费用项", "原版", "新版", "变化", "变化率"])
    analysis_totals = (comparison.get("analysis") or {}).get("totals") or {}
    material_totals = analysis_totals.get("material") or {}
    items = [
        ("设备检修费（表一口径）", "old_equipment", "new_equipment"),
        ("配件购置费（含配件完整口径）", "old_parts_total", "new_parts_total"),
        ("设备检修费+配件购置费", "old_complete", "new_complete"),
        ("可抵扣增值税", "old_vat", "new_vat"),
        ("编制基准期价差", "old_price_diff", "new_price_diff"),
        ("下浮后送审金额", None, "new_discount_final"),
    ]
    totals = dict(totals)
    totals["old_parts_total"] = material_totals.get("old_parts")
    totals["new_parts_total"] = material_totals.get("new_parts")
    for label, old_key, new_key in items:
        old_value = totals.get(old_key) if old_key else None
        new_value = totals.get(new_key) if new_key else None
        if old_value is None and new_value is None:
            continue
        delta = (new_value - old_value) if (old_value is not None and new_value is not None) else None
        _apply_spec(ws, row, 1, label, "text")
        _apply_spec(ws, row, 2, old_value, "money")
        _apply_spec(ws, row, 3, new_value, "money")
        cell = _apply_spec(ws, row, 4, delta, "money")
        if isinstance(delta, (int, float)):
            cell.font = POS_FONT if delta > 0 else (NEG_FONT if delta < 0 else None)
        _apply_spec(ws, row, 5, (delta / old_value) if (delta is not None and old_value) else None, "ratio")
        row += 1

    categories = comparison["documents"]["settlement"].get("old", {}).get("categories") or []
    new_categories = comparison["documents"]["settlement"].get("new", {}).get("categories") or []
    if categories or new_categories:
        row = _write_section(ws, row + 1, "分部/单位工程对比（原版 vs 新版）", span=8)
        row = _write_headers(ws, row, ["分部/单位工程", "原版合计", "新版合计", "变化", "变化率"])
        old_map = {((item.get("section") or "") + "|" + (item.get("name") or "")): item for item in categories}
        new_map = {((item.get("section") or "") + "|" + (item.get("name") or "")): item for item in new_categories}
        keys = list(old_map.keys()) + [key for key in new_map if key not in old_map]
        for key in keys:
            old_item = old_map.get(key) or {}
            new_item = new_map.get(key) or {}
            old_amount = old_item.get("合计")
            new_amount = new_item.get("合计")
            if old_amount is None and new_amount is None:
                old_amount, new_amount = old_item.get("检修费"), new_item.get("检修费")
            if old_amount is None and new_amount is None:
                continue
            delta = (new_amount - old_amount) if (old_amount is not None and new_amount is not None) else None
            name = (old_item.get("name") or new_item.get("name") or "").strip()
            _apply_spec(ws, row, 1, f"{old_item.get('section') or new_item.get('section') or ''} {name}".strip(), "text")
            _apply_spec(ws, row, 2, old_amount, "money")
            _apply_spec(ws, row, 3, new_amount, "money")
            _apply_spec(ws, row, 4, delta, "money")
            _apply_spec(ws, row, 5, (delta / old_amount) if (delta is not None and old_amount) else None, "ratio")
            row += 1
    row = _write_note(ws, row + 1,
                      "说明：原版表二甲为分部汇总；新版若无表二甲，则由表三甲“项目划分”行聚合。"
                      "最终口径优先采用“下浮后送审金额”，无则采用设备检修费。")
    return row


def _sheet_diff(ws, title: str, diffs: list[dict], include_unchanged: bool = False):
    row = 1
    row = _write_title(ws, row, title, span=15)
    row = _write_diff_table(ws, row + 1, diffs, include_unchanged=include_unchanged)
    ws.freeze_panes = "A4"
    return row


def _sheet_boq_text(ws, comparison: dict):
    row = 1
    row = _write_title(ws, row, "竣工资料工程量 · 文字描述对比", span=8)
    for column, width in enumerate([12, 12, 12, 14, 60], start=1):
        ws.column_dimensions[get_column_letter(column)].width = width
    row = _write_headers(ws, row + 1, ["状态", "相似度", "原版位置", "新版位置", "摘要"])
    changes = comparison["documents"]["boq"].get("text_changes") or []
    for change in changes:
        values = [
            change.get("status", ""),
            change.get("similarity"),
            change.get("old_label", "") or change.get("old_id", ""),
            change.get("new_label", "") or change.get("new_id", ""),
            change.get("summary", ""),
        ]
        for column, value in enumerate(values, start=1):
            spec = "ratio" if column == 2 else "text"
            _apply_spec(ws, row, column, value, spec)
        if change.get("status") != STATUS_SAME:
            for column in range(1, 6):
                ws.cell(row=row, column=column).fill = RISK_FILLS[RISK_MID]
        row += 1
    return row


def _sheet_boq_text_full(ws, comparison: dict):
    row = 1
    row = _write_title(ws, row, "竣工资料工程量 · 文字描述全文对照", span=7)
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 14
    ws.column_dimensions["C"].width = 10
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 90
    ws.column_dimensions["F"].width = 90
    row = _write_headers(ws, row + 1, ["状态", "原版位置", "相似度", "新版位置", "原版文字", "新版文字"])
    changes = comparison["documents"]["boq"].get("text_changes") or []
    for change in changes:
        values = [
            change.get("status", ""),
            change.get("old_label", "") or change.get("old_id", ""),
            change.get("similarity"),
            change.get("new_label", "") or change.get("new_id", ""),
            (change.get("old_text") or "")[:3000],
            (change.get("new_text") or "")[:3000],
        ]
        for column, value in enumerate(values, start=1):
            spec = "ratio" if column == 3 else "text"
            cell = _apply_spec(ws, row, column, value, spec)
            if column in (5, 6):
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[row].height = 120
        row += 1
    return row


def _sheet_risks(ws, comparison: dict):
    row = 1
    row = _write_title(ws, row, "风险与建议", span=8)
    for column, width in enumerate([8, 12, 34, 70, 16], start=1):
        ws.column_dimensions[get_column_letter(column)].width = width
    row = _write_section(ws, row + 1, "一、风险清单", span=8)
    row = _write_headers(ws, row, ["等级", "类别", "事项", "说明", "金额影响"])
    analysis = comparison.get("analysis") or {}
    for risk in analysis.get("risks") or []:
        values = [risk.get("level"), risk.get("category"), risk.get("title"), risk.get("detail"),
                  risk.get("amount_delta")]
        for column, value in enumerate(values, start=1):
            spec = "money" if column == 5 else "text"
            cell = _apply_spec(ws, row, column, value, spec)
            if column == 5 and isinstance(value, (int, float)):
                cell.font = POS_FONT if value > 0 else (NEG_FONT if value < 0 else None)
        fill = RISK_FILLS.get(risk.get("level"))
        if fill:
            for column in range(1, 6):
                ws.cell(row=row, column=column).fill = fill
        row += 1

    row = _write_section(ws, row + 1, "二、处理建议", span=8)
    suggestions = _suggestions(comparison)
    for index, suggestion in enumerate(suggestions, start=1):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
        cell = _cell(ws, row, 1, f"{index}. {suggestion}", border=False,
                     alignment=Alignment(wrap_text=True, vertical="top"))
        ws.row_dimensions[row].height = max(18, 16 * (len(suggestion) // 70 + 1))
        row += 1
    return row


def _suggestions(comparison: dict) -> list[str]:
    analysis = comparison.get("analysis") or {}
    suggestions: list[str] = []
    risks = analysis.get("risks") or []
    high_risks = [risk for risk in risks if risk.get("level") == RISK_HIGH]
    if high_risks:
        names = "、".join(risk.get("title", "") for risk in high_risks[:6])
        suggestions.append(f"对高风险差异逐项复核并补充依据（设计变更、现场签证、审核意见）：{names}。")
    suggestions.append("将本次差异结果交结算、物资、工程三方会签，差异金额纳入送审核减/核增说明。")
    findings = analysis.get("findings") or []
    if any("甲供" in finding for finding in findings):
        suggestions.append("补齐原版甲供材料清单，并按甲供/乙供口径分别核对材料领用与结算扣回。")
    if any("竣工资料" in finding or "空记录" in finding for finding in findings):
        suggestions.append("补齐原版竣工报告缺失的安健环、清理旧线路、接地/拉盘/底盘等记录后再做正式对比。")
    suggestions.append("正式使用前由业务人员对解析结果进行抽查复核；本表由规则比对生成，不代替人工签认。")
    return suggestions


def _sheet_readme(ws, comparison: dict):
    project = comparison.get("project") or {}
    row = 1
    row = _write_title(ws, row, "说明与数据口径", span=6)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 110
    notes = [
        ("项目名称", project.get("name", "")),
        ("生成时间", project.get("generated_at", "")),
        ("原版资料目录", project.get("old_dir", "")),
        ("新版资料目录", project.get("new_dir", "")),
        ("数据来源", "原版/新版竣工报告工作簿（竣工资料工程量）、检修结算书工作簿（审核结算、材料清册）"),
        ("对比口径", "原版 = 2025 年资料；新版 = 2026 年资料。审核结算最终金额优先取“下浮后送审金额”，无则取设备检修费。"),
        ("匹配规则", "按定额编号/名称+规格+单位匹配；同名同规格多行先聚合；名称相似/疑似改名会标注，需人工确认。"),
        ("风险规则", "高：金额差≥5000 元或（变化率≥10% 且金额差≥500 元）；中：金额差≥1000 元或（变化率≥3% 且金额差≥100 元）；新增/删除/规格/单位变化至少为中风险。"),
        ("免责声明", "本表为规则自动比对结果，解析与匹配可能存在误差，正式结算与签认前须人工复核。"),
    ]
    for label, value in notes:
        _apply_spec(ws, row, 1, label, "text")
        cell = _apply_spec(ws, row, 2, value, "text")
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[row].height = max(18, 16 * (len(str(value)) // 60 + 1))
        row += 1

    row = _write_section(ws, row + 1, "资料文件清单", span=6)
    row = _write_headers(ws, row, ["类别", "原版文件", "新版文件"])
    for category, label in (("settlement", "审核结算"), ("material", "材料清册"), ("boq", "竣工资料工程量")):
        old_files = comparison["documents"][category]["old"].get("files") or []
        new_files = comparison["documents"][category]["new"].get("files") or []
        _apply_spec(ws, row, 1, label, "text")
        _apply_spec(ws, row, 2, "、".join(str(item.get("file", "")) for item in old_files) or "—", "text")
        _apply_spec(ws, row, 3, "、".join(str(item.get("file", "")) for item in new_files) or "—", "text")
        row += 1
    return row


def build(comparison: dict) -> bytes:
    """生成 Excel 数据对比表，返回字节。"""
    wb = Workbook()
    wb.remove(wb.active)

    _sheet_overview(wb.create_sheet("总览"), comparison)
    _sheet_settlement_summary(wb.create_sheet("审核结算-汇总"), comparison)

    settlement_diffs = comparison["documents"]["settlement"].get("diffs") or []
    _sheet_diff(wb.create_sheet("审核结算-逐项差异"), "审核结算 · 逐项差异（仅列变化项）", settlement_diffs)
    _sheet_diff(wb.create_sheet("审核结算-全部明细"), "审核结算 · 全部条目对比", settlement_diffs,
                include_unchanged=True)
    _sheet_diff(wb.create_sheet("审核结算-新增删除"), "审核结算 · 新增 / 删除条目",
                [diff for diff in settlement_diffs if diff.get("status") in {STATUS_NEW, STATUS_REMOVED}])

    material_diffs = comparison["documents"]["material"].get("diffs") or []
    _sheet_diff(wb.create_sheet("材料清册-乙供差异"), "材料清册（乙供未计价材料）· 逐项差异", material_diffs)
    _sheet_diff(wb.create_sheet("材料清册-乙供全部"), "材料清册（乙供未计价材料）· 全部明细", material_diffs,
                include_unchanged=True)
    supplementary = comparison["documents"]["material"].get("supplementary") or {}
    _sheet_diff(wb.create_sheet("材料清册-甲供差异"), "材料清册（甲供未计价材料）· 逐项差异",
                (supplementary.get("甲供清单") or {}).get("diffs") or [])
    _sheet_diff(wb.create_sheet("材料清册-配件口径差异"), "材料清册 · 配件口径（表三甲配件行）逐项差异",
                (supplementary.get("配件口径") or {}).get("diffs") or [])
    _sheet_diff(wb.create_sheet("材料清册-主材口径差异"), "材料清册 · 表三甲主材/配件口径对比（补充）",
                (supplementary.get("主材口径") or {}).get("diffs") or [])

    boq_diffs = comparison["documents"]["boq"].get("diffs") or []
    grounding_diffs = [diff for diff in boq_diffs if diff.get("item_type") == "接地"]
    other_boq_diffs = [diff for diff in boq_diffs if diff.get("item_type") != "接地"]
    _sheet_diff(wb.create_sheet("竣工工程量-接地"), "竣工资料工程量 · 接地工程量差异", grounding_diffs)
    _sheet_diff(wb.create_sheet("竣工工程量-安健环拆旧"), "竣工资料工程量 · 安健环 / 拆旧 / 其他差异",
                other_boq_diffs)
    _sheet_boq_text(wb.create_sheet("竣工工程量-文字描述"), comparison)
    _sheet_boq_text_full(wb.create_sheet("竣工工程量-文字全文"), comparison)
    _sheet_risks(wb.create_sheet("风险与建议"), comparison)
    _sheet_readme(wb.create_sheet("说明"), comparison)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def write(comparison: dict, path: str | Path) -> Path:
    """写入文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build(comparison))
    return path
