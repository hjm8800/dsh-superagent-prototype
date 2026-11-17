# -*- coding: utf-8 -*-
"""把批量对比结果汇总成 Excel（一行一个项目），供 91 个项目批量交付使用。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)
TITLE_FONT = Font(bold=True, size=14, color="1F4E78")
POS_FONT = Font(color="C00000")
NEG_FONT = Font(color="2E7D32")
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

HEADERS = [
    "序号", "项目名称", "施工费原版（元）", "施工费新版（元）", "变化（元）", "变化率",
    "定额直接费变化", "量差效应", "价差效应", "乙供材料变化", "取费与口径残差",
    "工程量数量变化项", "工程量新增项", "工程量删除项", "主要成因 TOP3", "资料与口径提示",
]
WIDTHS = [6, 46, 16, 16, 16, 10, 16, 14, 14, 16, 16, 12, 12, 12, 60, 60]


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return f"{value:,.2f}"
    return str(value)


def _fmt_ratio(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return f"{value * 100:+.2f}%"
    return str(value)


def write_summary_xlsx(results: list[dict], path: str | Path) -> Path:
    path = Path(path)
    wb = Workbook()
    ws = wb.active
    ws.title = "施工费与工程量差异汇总"
    ws.merge_cells("A1:P1")
    ws["A1"] = "施工费与工程量差异汇总（多项目）"
    ws["A1"].font = TITLE_FONT
    ws.row_dimensions[1].height = 24

    for column, header in enumerate(HEADERS, start=1):
        cell = ws.cell(row=2, column=column, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(column)].width = WIDTHS[column - 1]
    ws.row_dimensions[2].height = 28

    rows = 0
    for index, result in enumerate(results, start=1):
        comparison = result["comparison"]
        analysis = comparison.get("analysis") or {}
        fee = analysis.get("construction_fee") or {}
        quantity = analysis.get("quantity") or {}
        direct = fee.get("direct") or {}
        yigong = fee.get("yigong_material") or {}
        causes = fee.get("item_causes") or []
        cause_text = "；".join(
            f"{cause.get('name', '')}（{cause.get('effect_kind', '')}，{_fmt(cause.get('amount_delta'))} 元）"
            for cause in causes[:3]
        )
        findings = analysis.get("findings") or []
        note = "；".join(findings[:2])
        if fee.get("format") == "assembly_only":
            note = "仅组合件汇总表、资料不完整；" + note
        values = [
            index,
            comparison["project"]["name"],
            fee.get("old_fee"),
            fee.get("new_fee"),
            fee.get("fee_delta"),
            fee.get("fee_ratio"),
            direct.get("net"),
            direct.get("qty_effect"),
            direct.get("price_effect"),
            yigong.get("net"),
            fee.get("residual_vs_effects"),
            quantity.get("qty_changed_count"),
            quantity.get("added_count"),
            quantity.get("removed_count"),
            cause_text,
            note,
        ]
        row_index = 3 + rows
        for column, value in enumerate(values, start=1):
            cell = ws.cell(row=row_index, column=column, value=value)
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=column in (2, 15, 16))
            if column in (3, 4, 5, 7, 8, 9, 10, 11):
                cell.number_format = "#,##0.00"
            if column == 6:
                cell.number_format = "0.00%"
            if column in (5, 7, 8, 9, 10, 11) and isinstance(value, (int, float)):
                cell.font = POS_FONT if value > 0 else (NEG_FONT if value < 0 else None)
        rows += 1

    ws.freeze_panes = "C3"
    if rows:
        ws.auto_filter.ref = f"A2:P{2 + rows}"
    wb.save(path)
    return path
