"""共享工作簿读取工具（.xls / .xlsx / .xlsm）。

reader 模块统一通过本文件读取工作簿，避免各自处理 xlrd/openpyxl 差异。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_XLS_SUFFIXES = {".xls"}
_XLSX_SUFFIXES = {".xlsx", ".xlsm"}

_FULLWIDTH = {ord("０"): "0", ord("１"): "1", ord("２"): "2", ord("３"): "3",
              ord("４"): "4", ord("５"): "5", ord("６"): "6", ord("７"): "7",
              ord("８"): "8", ord("９"): "9", ord("．"): ".", ord("，"): ",",
              ord("（"): "(", ord("）"): ")", ord("－"): "-", ord("％"): "%"}

_EMPTY_TOKENS = {"", "-", "—", "/", "／", "\\", "无", "None", "none", "N/A", "n/a"}


def _suffix(path: str | Path) -> str:
    return Path(path).suffix.lower()


def list_sheets(path: str | Path) -> list[str]:
    """返回工作簿全部工作表名（保持原始顺序）。"""
    path = Path(path)
    suffix = _suffix(path)
    if suffix in _XLS_SUFFIXES:
        import xlrd

        book = xlrd.open_workbook(str(path), on_demand=True)
        try:
            return list(book.sheet_names())
        finally:
            book.release_resources()
    if suffix in _XLSX_SUFFIXES:
        from openpyxl import load_workbook

        wb = load_workbook(str(path), read_only=True, data_only=True)
        try:
            return list(wb.sheetnames)
        finally:
            wb.close()
    raise ValueError(f"不支持的文件类型：{path.name}")


def load_sheet(path: str | Path, sheet_name: str) -> list[list[Any]]:
    """按名称读取一个工作表，返回二维原始值列表（行 -> 列）。"""
    path = Path(path)
    suffix = _suffix(path)
    if suffix in _XLS_SUFFIXES:
        import xlrd

        book = xlrd.open_workbook(str(path), on_demand=True)
        try:
            sh = book.sheet_by_name(sheet_name)
            return [[sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)]
        finally:
            book.release_resources()
    if suffix in _XLSX_SUFFIXES:
        from openpyxl import load_workbook

        wb = load_workbook(str(path), read_only=True, data_only=True)
        try:
            ws = wb[sheet_name]
            return [list(row) for row in ws.iter_rows(values_only=True)]
        finally:
            wb.close()
    raise ValueError(f"不支持的文件类型：{path.name}")


def load_sheets(path: str | Path) -> dict[str, list[list[Any]]]:
    """读取工作簿全部工作表：{sheet_name: rows}。"""
    path = Path(path)
    suffix = _suffix(path)
    result: dict[str, list[list[Any]]] = {}
    if suffix in _XLS_SUFFIXES:
        import xlrd

        book = xlrd.open_workbook(str(path), on_demand=True)
        try:
            for name in book.sheet_names():
                sh = book.sheet_by_name(name)
                result[name] = [
                    [sh.cell_value(r, c) for c in range(sh.ncols)] for r in range(sh.nrows)
                ]
        finally:
            book.release_resources()
        return result
    if suffix in _XLSX_SUFFIXES:
        from openpyxl import load_workbook

        wb = load_workbook(str(path), read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                result[ws.title] = [list(row) for row in ws.iter_rows(values_only=True)]
        finally:
            wb.close()
        return result
    raise ValueError(f"不支持的文件类型：{path.name}")


def cell_text(value: Any) -> str:
    """单元格转干净文本：None → ""，float 去多余 .0，合并空白。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        if abs(value - int(value)) < 1e-9:
            return str(int(value))
        return (f"{value:.10f}").rstrip("0").rstrip(".")
    text = str(value).replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def cell_number(value: Any) -> float | None:
    """单元格转数值；无法解析返回 None。支持千分位、全角、百分号、括号负数。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:
            return None
        return float(value)
    text = str(value).translate(_FULLWIDTH)
    text = text.replace("\u3000", " ").replace("\xa0", " ").strip()
    if text.lower() in _EMPTY_TOKENS:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    if text.startswith("-"):
        negative = True
        text = text[1:]
    text = text.rstrip("%")
    text = text.replace(",", "").replace(" ", "")
    if not text or not re.fullmatch(r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", text):
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def is_blank_row(row: list[Any]) -> bool:
    return all(not cell_text(value) for value in row)


def find_sheet_name(names: list[str], *keywords: str) -> str | None:
    """按关键词（全部命中）查找工作表名，忽略空格。"""
    for name in names:
        compact = re.sub(r"\s+", "", name)
        if all(re.sub(r"\s+", "", kw) in compact for kw in keywords):
            return name
    return None
