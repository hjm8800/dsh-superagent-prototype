# -*- coding: utf-8 -*-
"""检修结算书 reader：把检修结算书工作簿解析成统一的 DocSet 片段。

数据来源（真实样例已勘察，缺表时容错）：
- ``检修工程总表(表一)``：总结算金额（设备检修费/施工费/可抵扣增值税/编制基准期价差/
  配件购置费/下浮后送审金额/甲供配件材料费等）。
- ``设备检修专业汇总表(表二甲)``：分部汇总 categories；没有时用表三甲的 ``项目划分`` 行聚合。
- ``设备检修单位工程表(表三甲)``：逐项 items（定额/主材/配件/一笔性费用），
  ``类型`` 列通过行内查找定位。
- ``甲供未计价材料汇总表`` / ``乙供未计价材料汇总表`` / ``甲供配件汇总表`` /
  ``配件汇总表`` / ``材料汇总表``：材料清册 items 与合计。

所有解析均为只读，返回 JSON 可序列化 dict（见契约 4.2）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .excel_utils import cell_number, cell_text, load_sheets

KIND = "settlement_workbook"

SOURCE_SEP = "!"

#: 表一 中需要提取的金额项（工程或费用名称 -> totals key）
_TOTALS_KEYS = (
    "设备检修费",
    "配件购置费",
    "下浮后送审金额",
    "可抵扣增值税",
    "编制基准期价差",
    "施工费",
    "甲供配件材料费",
    "乙供物料费",
)

#: 表三甲 中跳过的行类型
_SKIP_TYPES = {"项目划分", "取费表", "小计", ""}

#: 材料表中的分组标题行（不是数据行）
_GROUP_HEADER_NAMES = {
    "设备检修",
    "设备检修工程",
    "建筑修缮",
    "建筑修缮工程",
}

_CN_NUMERALS = "一二三四五六七八九十百零"


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------


def _compact(text: str) -> str:
    """压缩空白，便于跨工作表名比较。"""
    return re.sub(r"\s+", "", text or "")


def _find_sheet(
    names: list[str], *keywords: str, exclude: tuple[str, ...] = ()
) -> str | None:
    """按关键词（全部命中、忽略空白）查找工作表名；exclude 命中的跳过。"""
    for name in names or []:
        compact = _compact(name)
        if all(_compact(keyword) in compact for keyword in keywords) and not any(
            _compact(item) in compact for item in exclude
        ):
            return name
    return None


def _find_sheet_exact(names: list[str], target: str) -> str | None:
    """按工作表名精确匹配（忽略空白）。"""
    wanted = _compact(target)
    for name in names or []:
        if _compact(name) == wanted:
            return name
    return None


def _make_item(
    *,
    name: str,
    spec: str = "",
    unit: str = "",
    qty: float | None = None,
    unit_price: float | None = None,
    amount: float | None = None,
    code: str = "",
    section: str = "",
    item_type: str = "",
    supply: str = "",
    source: str = "",
    note: str = "",
) -> dict[str, Any]:
    """构造契约 3.1 规定的 Item dict。"""
    return {
        "name": name,
        "spec": spec,
        "unit": unit,
        "qty": qty,
        "unit_price": unit_price,
        "amount": amount,
        "code": code,
        "section": section,
        "item_type": item_type,
        "supply": supply,
        "source": source,
        "note": note,
    }


def _number(
    value: Any,
    warnings: list[str],
    source: str,
    row_no: int,
    field: str,
) -> float | None:
    """清洗数值；纯文本却无法解析时记 warning（不静默丢数据）。"""
    number = cell_number(value)
    if number is None:
        text = cell_text(value)
        if text:
            warnings.append(
                f"{source} 第{row_no}行：{field}无法解析为数值（{text[:24]}）"
            )
    return number


def _strip_label(text: str) -> str:
    """去掉“一、”“其中：”等前缀，得到费用名称本体。"""
    cleaned = re.sub(rf"^[{_CN_NUMERALS}]+[、.．]?", "", (text or "").strip())
    cleaned = re.sub(r"^其中[：:]?", "", cleaned.strip())
    return cleaned.strip()


def _section_level(sequence: str) -> int:
    """由“序号”推断层级：空=0（根），中文数字=1，纯数字=2，点分=2+点号个数。"""
    seq = (sequence or "").strip()
    if not seq:
        return 0
    if re.fullmatch(rf"[{_CN_NUMERALS}]+", seq):
        return 1
    if re.fullmatch(r"\d+", seq):
        return 2
    if re.fullmatch(r"\d+(?:\.\d+)+", seq):
        return 2 + seq.count(".")
    return 0


def _find_column(
    cells: list[tuple[int, int, str]],
    *keywords: str,
    exclude: tuple[str, ...] = (),
) -> int | None:
    """在表头单元格中按关键词找列；优先精确等于关键词的单元格，避免标题行误命中。"""
    compact_keywords = tuple(_compact(keyword) for keyword in keywords)
    exact_hits: list[int] = []
    contains_hits: list[int] = []
    for _row, column, text in cells:
        if any(_compact(item) in text for item in exclude):
            continue
        if all(keyword in text for keyword in compact_keywords):
            contains_hits.append(column)
            if text in compact_keywords:
                exact_hits.append(column)
    if exact_hits:
        return min(exact_hits)
    return min(contains_hits) if contains_hits else None


class _SectionTracker:
    """维护“最近的项目划分”层级，生成 items 的 section 路径。"""

    def __init__(self) -> None:
        self._stack: dict[int, str] = {}

    def enter(self, sequence: str, name: str) -> str:
        """进入一个项目划分行，返回其父级 section（不含自身）。"""
        level = _section_level(sequence)
        if level == 0:
            self._stack.clear()
            return ""
        for key in [key for key in self._stack if key >= level]:
            del self._stack[key]
        parent = self.section()
        self._stack[level] = name
        return parent

    def section(self) -> str:
        """当前 section（含最近项目划分自身）。"""
        return "/".join(self._stack[key] for key in sorted(self._stack) if key >= 1)


# ---------------------------------------------------------------------------
# 表一：总结算金额
# ---------------------------------------------------------------------------


def _parse_summary_totals(
    rows: list[list[Any]], warnings: list[str], source: str
) -> dict[str, float | None]:
    """解析表一：按“工程或费用名称”取金额。"""
    name_col: int | None = None
    amount_col: int | None = None
    for row in rows[:10]:
        texts = [_compact(cell_text(value)) for value in row]
        for index, text in enumerate(texts):
            if "工程或费用名称" in text:
                name_col = index
                # 金额列：表头里“金额”的列；找不到时取名称列右侧第一个数值列
                for other, other_text in enumerate(texts):
                    if other_text == "金额":
                        amount_col = other
                        break
                break
        if name_col is not None:
            break

    totals: dict[str, float | None] = {key: None for key in _TOTALS_KEYS}
    for row_no, row in enumerate(rows, start=1):
        if name_col is None or name_col >= len(row):
            # 兜底：整行查找关键词
            cells = [(index, cell_text(value)) for index, value in enumerate(row)]
        else:
            cells = [(name_col, cell_text(row[name_col]))]
        if not cells:
            continue
        name = _strip_label(cells[0][1])
        if not name:
            continue

        matched: str | None = None
        if name == "设备检修费":
            matched = "设备检修费"
        elif name == "配件购置费":
            matched = "配件购置费"
        elif name == "施工费":
            matched = "施工费"
        elif "编制基准期价差" in name:
            matched = "编制基准期价差"
        elif "可抵扣增值税" in name:
            matched = "可抵扣增值税"
        elif "下浮后送审金额" in name:
            matched = "下浮后送审金额"
        elif "甲供配件材料费" in name:
            matched = "甲供配件材料费"
        elif "乙供物料费" in name:
            matched = "乙供物料费"
        if matched is None:
            continue

        value: float | None = None
        raw_amount = _cell(row, amount_col) if amount_col is not None else None
        if cell_text(raw_amount) or isinstance(raw_amount, (int, float)):
            value = cell_number(raw_amount)
            if value is None:
                warnings.append(
                    f"{source} 表一“{name}”金额无法解析（{cell_text(raw_amount)[:24]}）"
                )
        if value is None:
            # 兜底：名称列右侧第一个可解析数值
            start = (name_col or 0) + 1
            for cell in row[start:]:
                value = cell_number(cell)
                if value is not None:
                    break
        if value is None:
            # 表一允许费用行为空（如旧版没有甲供配件材料费），不记 warning
            continue
        totals[matched] = value
    return totals


# ---------------------------------------------------------------------------
# 表三甲：逐项 items + 项目划分 categories
# ---------------------------------------------------------------------------


def _locate_type_column(rows: list[list[Any]]) -> tuple[int | None, int | None]:
    """行内查找“类型”列（不稳定下标，扫描前若干行）。"""
    for row_index, row in enumerate(rows[:12]):
        for column_index, value in enumerate(row):
            text = _compact(cell_text(value))
            if "类型" in text and len(text) <= 12:
                return row_index, column_index
    return None, None


def _header_cells(rows: list[list[Any]], header_row: int) -> list[tuple[int, int, str]]:
    """收集表头区域（含表头下面两行子表头）全部非空单元格。"""
    cells: list[tuple[int, int, str]] = []
    for row_index in range(0, min(len(rows), header_row + 3)):
        for column_index, value in enumerate(rows[row_index]):
            text = _compact(cell_text(value))
            if text:
                cells.append((row_index, column_index, text))
    return cells


def _build_table3_columns(
    rows: list[list[Any]], header_row: int, type_col: int
) -> dict[str, int]:
    """定位表三甲各列。优先按表头关键词，缺失时按标准版式推算/兜底。"""
    cells = _header_cells(rows, header_row)

    def find(*keywords: str, exclude: tuple[str, ...] = ()) -> int | None:
        return _find_column(cells, *keywords, exclude=exclude)

    seq_col = find("序号")
    code_col = find("编制依据")
    name_col = find("项目名称")
    unit_col = find("单位", exclude=("金额单位",))
    qty_col = find("数量")
    part_price_col = find("配件单价")
    part_amount_col = find("配件合价")
    quota_price_col = find("定额基价")
    quota_amount_col = find("费用金额")
    unpriced_columns = sorted(
        column for _row, column, text in cells if "未计价材料" in text
    )

    columns: dict[str, int] = {}
    columns["序号"] = seq_col if seq_col is not None else 0
    columns["编制依据"] = code_col if code_col is not None else 1
    columns["项目名称"] = name_col if name_col is not None else 2
    columns["单位"] = unit_col if unit_col is not None else 3
    columns["数量"] = qty_col if qty_col is not None else 4
    columns["定额单价"] = quota_price_col if quota_price_col is not None else 6
    columns["定额合价"] = quota_amount_col if quota_amount_col is not None else 11
    # 标准 27 列版式中：配件单价=定额单价左侧 1 列，配件合价=定额合价左侧 1 列。
    # 个别手工工作簿（如演示数据）表头标签可能错位到合价分组列上，按相对位置校验修正。
    derived_part_price = columns["定额单价"] - 1
    if part_price_col is not None and 0 <= part_price_col < columns["定额单价"]:
        derived_part_price = part_price_col
    columns["配件单价"] = derived_part_price
    derived_part_amount = columns["定额合价"] - 1
    if part_amount_col is not None and 0 <= part_amount_col < columns["定额合价"]:
        derived_part_amount = part_amount_col
    columns["配件合价"] = derived_part_amount
    if len(unpriced_columns) >= 2:
        columns["主材单价"], columns["主材合价"] = (
            unpriced_columns[0],
            unpriced_columns[1],
        )
    elif len(unpriced_columns) == 1:
        columns["主材单价"] = unpriced_columns[0]
        columns["主材合价"] = columns["定额合价"] + (
            unpriced_columns[0] - columns["定额单价"]
        )
    else:
        columns["主材单价"] = columns["定额单价"] + 3
        columns["主材合价"] = columns["定额合价"] + 3
    columns["类型"] = type_col
    return columns


def _cell(row: list[Any], column: int) -> Any:
    if 0 <= column < len(row):
        return row[column]
    return None


def _parse_table3(
    rows: list[list[Any]],
    sheet_name: str,
    source: str,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """解析表三甲，返回 (items, categories, 类型定位列)。"""
    items: list[dict[str, Any]] = []
    categories: list[dict[str, Any]] = []
    header_row, type_col = _locate_type_column(rows)
    if header_row is None or type_col is None:
        warnings.append(f"{source}：未找到“类型”列，无法解析表三甲明细")
        return items, categories, -1

    columns = _build_table3_columns(rows, header_row, type_col)
    tracker = _SectionTracker()
    for row_index in range(header_row + 1, len(rows)):
        row = rows[row_index]
        row_no = row_index + 1
        type_text = _compact(cell_text(_cell(row, type_col)))
        if not type_text:
            # 表头子行/空行：只要名称列有内容才视为异常
            name_text = cell_text(_cell(row, columns["项目名称"]))
            if name_text and any(
                cell_number(_cell(row, column)) is not None
                for column in (columns["数量"], columns["定额合价"])
            ):
                warnings.append(f"{source} 第{row_no}行：缺少“类型”标识，已跳过")
            continue

        if type_text == "项目划分":
            sequence = cell_text(_cell(row, columns["序号"]))
            name = cell_text(_cell(row, columns["项目名称"]))
            if not name:
                warnings.append(f"{source} 第{row_no}行：项目划分缺少名称，已跳过")
                continue
            parent = tracker.enter(sequence, name)
            labor = _number(
                _cell(row, columns["定额合价"]), warnings, source, row_no, "检修费"
            )
            unpriced = _number(
                _cell(row, columns["主材合价"]), warnings, source, row_no, "未计价材料费"
            )
            parts = _number(
                _cell(row, columns["配件合价"]), warnings, source, row_no, "配件购置费"
            )
            components = [value for value in (labor, unpriced, parts) if value is not None]
            total = round(sum(components), 2) if components else None
            categories.append(
                {
                    "section": parent,
                    "name": name,
                    "检修费": labor,
                    "未计价材料费": unpriced,
                    "配件购置费": parts,
                    "合计": total,
                }
            )
            continue

        if type_text in _SKIP_TYPES:
            continue

        name = cell_text(_cell(row, columns["项目名称"]))
        if not name:
            warnings.append(f"{source} 第{row_no}行：类型为 {type_text} 但缺少名称，已跳过")
            continue

        section = tracker.section()
        code_raw = cell_text(_cell(row, columns["编制依据"]))
        supply = "甲供" if ("（甲）" in code_raw or "(甲)" in code_raw) else ""
        code = re.sub(r"[（(]甲[)）]", "", code_raw).strip()

        if type_text in ("定额", "主材", "配件", "一笔性费用", "费用"):
            unit = cell_text(_cell(row, columns["单位"]))
            qty = _number(_cell(row, columns["数量"]), warnings, source, row_no, "数量")
            if type_text == "定额":
                unit_price = _number(
                    _cell(row, columns["定额单价"]), warnings, source, row_no, "单价"
                )
                amount = _number(
                    _cell(row, columns["定额合价"]), warnings, source, row_no, "合价"
                )
                item_type = "定额"
            elif type_text == "主材":
                unit_price = _number(
                    _cell(row, columns["主材单价"]), warnings, source, row_no, "单价"
                )
                amount = _number(
                    _cell(row, columns["主材合价"]), warnings, source, row_no, "合价"
                )
                item_type = "主材"
                if supply == "":
                    supply = "乙供"
            elif type_text == "配件":
                unit_price = _number(
                    _cell(row, columns["配件单价"]), warnings, source, row_no, "单价"
                )
                amount = _number(
                    _cell(row, columns["配件合价"]), warnings, source, row_no, "合价"
                )
                item_type = "配件"
                if supply == "":
                    supply = "甲供" if "（甲）" in name or "(甲)" in name else "乙供"
            else:  # 一笔性费用 / 费用
                unit_price = _number(
                    _cell(row, columns["定额单价"]), warnings, source, row_no, "单价"
                )
                amount = _number(
                    _cell(row, columns["定额合价"]), warnings, source, row_no, "合价"
                )
                item_type = "费用"
                supply = ""
        else:
            warnings.append(f"{source} 第{row_no}行：未识别的行类型“{type_text}”，已跳过")
            continue

        # 契约要求：配件合价 > 0 的行归为“配件”
        if item_type == "定额":
            part_amount = cell_number(_cell(row, columns["配件合价"]))
            if part_amount is not None and part_amount > 0:
                item_type = "配件"
                unit_price = cell_number(_cell(row, columns["配件单价"])) or unit_price
                amount = part_amount

        items.append(
            _make_item(
                name=name,
                spec="",
                unit=unit,
                qty=qty,
                unit_price=unit_price,
                amount=amount,
                code=code,
                section=section,
                item_type=item_type,
                supply=supply,
                source=source,
            )
        )
    return items, categories, type_col


# ---------------------------------------------------------------------------
# 表二甲：分部汇总 categories
# ---------------------------------------------------------------------------


def _parse_table2_categories(
    rows: list[list[Any]], source: str, warnings: list[str]
) -> list[dict[str, Any]]:
    """解析表二甲（设备检修专业汇总表）为 categories。"""
    header_row: int | None = None
    name_col: int | None = None
    seq_col: int | None = None
    for row_index, row in enumerate(rows[:10]):
        texts = [_compact(cell_text(value)) for value in row]
        if any("工程或费用名称" in text for text in texts):
            header_row = row_index
            break
    if header_row is None:
        return []
    cells: list[tuple[int, int, str]] = []
    for row_index in range(0, min(len(rows), header_row + 3)):
        for column_index, value in enumerate(rows[row_index]):
            text = _compact(cell_text(value))
            if text:
                cells.append((row_index, column_index, text))

    def find(*keywords: str, exclude: tuple[str, ...] = ()) -> int | None:
        return _find_column(cells, *keywords, exclude=exclude)

    seq_col = find("序号")
    name_col = find("工程或费用名称")
    labor_col = find("检修费")
    unpriced_col = find("未计价材料费")
    parts_col = find("配件购置费")
    total_col = find("合计")
    if name_col is None:
        warnings.append(f"{source}：表二甲未找到“工程或费用名称”列")
        return []

    categories: list[dict[str, Any]] = []
    tracker = _SectionTracker()
    for row_index in range(header_row + 1, len(rows)):
        row = rows[row_index]
        row_no = row_index + 1
        name = cell_text(_cell(row, name_col))
        if not name:
            continue
        if name.strip() in ("合计", "总计", "小计"):
            break
        if name.strip().startswith(("设备检修专业汇总", "金额单位")):
            continue
        sequence = cell_text(_cell(row, seq_col)) if seq_col is not None else ""
        parent = tracker.enter(sequence, name)
        labor = _number(
            _cell(row, labor_col), warnings, source, row_no, "检修费"
        ) if labor_col is not None else None
        unpriced = _number(
            _cell(row, unpriced_col), warnings, source, row_no, "未计价材料费"
        ) if unpriced_col is not None else None
        parts = _number(
            _cell(row, parts_col), warnings, source, row_no, "配件购置费"
        ) if parts_col is not None else None
        total = (
            _number(_cell(row, total_col), warnings, source, row_no, "合计")
            if total_col is not None
            else None
        )
        components = [value for value in (labor, unpriced, parts) if value is not None]
        if total is None and components:
            total = round(sum(components), 2)
        categories.append(
            {
                "section": parent,
                "name": name,
                "检修费": labor,
                "未计价材料费": unpriced,
                "配件购置费": parts,
                "合计": total,
            }
        )
    return categories


# ---------------------------------------------------------------------------
# 材料清册（未计价材料 / 配件 汇总表）
# ---------------------------------------------------------------------------


def _find_material_header_row(rows: list[list[Any]]) -> int | None:
    for row_index, row in enumerate(rows[:10]):
        texts = [_compact(cell_text(value)) for value in row]
        if any(text in ("材料名称", "配件名称") for text in texts):
            return row_index
    return None


def _parse_material_sheet(
    rows: list[list[Any]],
    *,
    sheet_name: str,
    source: str,
    supply: str,
    item_type: str,
    warnings: list[str],
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """解析一张材料/配件汇总表，返回 (items, {小计/合计/总计: 金额})。"""
    header_row = _find_material_header_row(rows)
    if header_row is None:
        warnings.append(f"{source}：未识别材料表头，已跳过该工作表")
        return [], {}

    cells: list[tuple[int, int, str]] = []
    for row_index in range(0, min(len(rows), header_row + 2)):
        for column_index, value in enumerate(rows[row_index]):
            text = _compact(cell_text(value))
            if text:
                cells.append((row_index, column_index, text))

    def find(*keywords: str, exclude: tuple[str, ...] = ()) -> int | None:
        return _find_column(cells, *keywords, exclude=exclude)

    code_col = find("编码")
    name_col = find("材料名称")
    if name_col is None:
        name_col = find("配件名称")
    spec_col = find("规格")
    unit_col = find("单位", exclude=("金额单位",))
    qty_col = find("设计用量")
    if qty_col is None:
        qty_col = find("数量")
    supply_col = find("供货方")
    price_candidates = [column for _row, column, text in cells if "单价" in text]
    price_col = min(price_candidates) if price_candidates else None

    total_columns = [column for _row, column, text in cells if "总价" in text]
    if total_columns:
        amount_col = min(total_columns)
    else:
        total_columns = [column for _row, column, text in cells if "合计" in text]
        if total_columns:
            amount_col = max(total_columns)
        else:
            total_columns = [column for _row, column, text in cells if "合价" in text]
            amount_col = max(total_columns) if total_columns else None

    if name_col is None:
        name_col = 1
    if qty_col is None:
        qty_col = (unit_col + 1) if unit_col is not None else None
    if price_col is None:
        price_col = (qty_col + 1) if qty_col is not None else None
    if amount_col is None:
        amount_col = (price_col + 1) if price_col is not None else None

    items: list[dict[str, Any]] = []
    subtotals: dict[str, float] = {}
    for row_index in range(header_row + 1, len(rows)):
        row = rows[row_index]
        row_no = row_index + 1
        texts = [_compact(cell_text(value)) for value in row]

        label: str | None = None
        for text in texts:
            for prefix in ("小计", "合计", "总计"):
                if text.startswith(prefix) and len(text) <= len(prefix) + 2:
                    label = prefix
                    break
            if label:
                break
            if text.startswith("其他材料费") and ("：" in text or ":" in text):
                label = "其他材料费"
                break
        if label:
            numbers = [cell_number(value) for value in row]
            numbers = [number for number in numbers if number is not None]
            if numbers:
                subtotals[label] = numbers[-1]
            continue

        name = cell_text(_cell(row, name_col))
        if not name:
            continue
        compact_name = _compact(name)
        if compact_name in _GROUP_HEADER_NAMES:
            continue

        unit = cell_text(_cell(row, unit_col)) if unit_col is not None else ""
        spec = cell_text(_cell(row, spec_col)) if spec_col is not None else ""
        code = cell_text(_cell(row, code_col)) if code_col is not None else ""
        qty = (
            _number(_cell(row, qty_col), warnings, source, row_no, "数量")
            if qty_col is not None
            else None
        )
        unit_price = (
            _number(_cell(row, price_col), warnings, source, row_no, "单价")
            if price_col is not None
            else None
        )
        if amount_col is not None:
            amount = _number(_cell(row, amount_col), warnings, source, row_no, "合价")
        else:
            amount = None
        if qty is None and amount is None:
            warnings.append(
                f"{source} 第{row_no}行：材料行缺少数量与金额，已跳过（{name[:20]}）"
            )
            continue
        row_supply = supply
        if supply_col is not None:
            sheet_supply = cell_text(_cell(row, supply_col))
            if sheet_supply:
                row_supply = sheet_supply
        items.append(
            _make_item(
                name=name,
                spec=spec,
                unit=unit,
                qty=qty,
                unit_price=unit_price,
                amount=amount,
                code=code,
                section="",
                item_type=item_type,
                supply=row_supply,
                source=source,
                note=f"{sheet_name}",
            )
        )
    return items, subtotals


def _pick_subtotal(subtotals: dict[str, float]) -> float | None:
    for key in ("小计", "合计", "总计"):
        value = subtotals.get(key)
        if value is not None:
            return round(value, 2)
    return None


def _pick_overall_subtotal(subtotals: dict[str, float]) -> float | None:
    """取“合计/总计”（含其他材料费等全部费用），再退化为“小计”。"""
    for key in ("合计", "总计", "小计"):
        value = subtotals.get(key)
        if value is not None:
            return round(value, 2)
    return None


def _sum_amounts(items: list[dict[str, Any]], **filters: str) -> float | None:
    total = 0.0
    found = False
    for item in items:
        if any(item.get(key) != value for key, value in filters.items()):
            continue
        amount = item.get("amount")
        if amount is None:
            continue
        total += float(amount)
        found = True
    return round(total, 2) if found else None


# ---------------------------------------------------------------------------
# 其他辅助（标题 / 施工费明细 / 材料汇总表统计）
# ---------------------------------------------------------------------------


def _extract_title(sheets: dict[str, list[list[Any]]], path: Path) -> str:
    cover_name = _find_sheet(list(sheets), "封面")
    candidates = [cover_name] if cover_name else []
    candidates.extend(
        name for name in sheets if not candidates or name != candidates[0]
    )
    for sheet_name in candidates:
        if not sheet_name:
            continue
        for row in sheets[sheet_name][:15]:
            for value in row:
                text = cell_text(value)
                match = re.search(r"项目名称[：:]\s*(.+)", text)
                if match:
                    return match.group(1).strip()
    return path.stem


def _parse_construction_detail(
    sheets: dict[str, list[list[Any]]], warnings: list[str], source_prefix: str
) -> dict[str, float]:
    """从施工费明细表取“乙供物料费”等补充金额（缺表返回空）。"""
    sheet_name = _find_sheet(list(sheets), "施工费明细")
    if not sheet_name:
        return {}
    rows = sheets[sheet_name]
    source = f"{source_prefix}{SOURCE_SEP}{sheet_name}"
    name_col: int | None = None
    amount_col: int | None = None
    for row in rows[:10]:
        texts = [_compact(cell_text(value)) for value in row]
        for column, text in enumerate(texts):
            if text == "名称":
                name_col = column
            if text in ("合价", "金额"):
                amount_col = column
        if name_col is not None and amount_col is not None:
            break
    if name_col is None or amount_col is None:
        warnings.append(f"{source}：未识别施工费明细表表头，跳过补充金额")
        return {}
    result: dict[str, float] = {}
    for row_index, row in enumerate(rows):
        name = cell_text(_cell(row, name_col))
        if name in ("乙供物料费", "本体施工费", "最终施工费(元)"):
            value = cell_number(_cell(row, amount_col))
            if value is not None:
                key = "乙供物料费" if name == "乙供物料费" else name
                result[key] = value
    return result


def _parse_material_summary_stats(
    sheets: dict[str, list[list[Any]]], warnings: list[str], source_prefix: str
) -> dict[str, Any]:
    """读取“材料汇总表”（定额材料，2026 独有）：只统计条数与合计，不并入材料清册。"""
    sheet_name = _find_sheet_exact(list(sheets), "材料汇总表")
    if not sheet_name:
        sheet_name = _find_sheet(
            list(sheets), "材料汇总表", exclude=("甲供", "乙供", "未计价")
        )
    if not sheet_name:
        return {}
    rows = sheets[sheet_name]
    source = f"{source_prefix}{SOURCE_SEP}{sheet_name}"
    items, subtotals = _parse_material_sheet(
        rows,
        sheet_name=sheet_name,
        source=source,
        supply="乙供",
        item_type="主材",
        warnings=warnings,
    )
    total = _pick_overall_subtotal(subtotals)
    if total is None:
        total = _sum_amounts(items)
    return {"sheet": sheet_name, "count": len(items), "total": total}


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def matches(sheet_names: list[str], filename: str = "") -> bool:
    """结算书特征：工作表名含“检修工程总表”或“设备检修单位工程表”。"""
    for sheet_name in sheet_names or []:
        compact = _compact(sheet_name)
        if "检修工程总表" in compact or "设备检修单位工程表" in compact:
            return True
    compact_file = _compact(str(filename))
    return any(
        keyword in compact_file for keyword in ("结算书", "送审结算", "检修工程结算")
    )


def load(path: str | Path) -> dict[str, Any]:
    """解析检修结算书工作簿，返回契约 4.2 规定的 dict。"""
    path = Path(path)
    sheets = load_sheets(path)
    names = list(sheets)
    filename = path.name
    warnings: list[str] = []

    # --- 表一：总结算金额 --------------------------------------------------
    summary_name = (
        _find_sheet(names, "检修工程总表")
        or _find_sheet(names, "表一")
        or _find_sheet(names, "总表")
    )
    totals: dict[str, float | None] = {key: None for key in _TOTALS_KEYS}
    if summary_name:
        source = f"{filename}{SOURCE_SEP}{summary_name}"
        totals = _parse_summary_totals(sheets[summary_name], warnings, source)
    else:
        warnings.append(f"{filename}：未找到“检修工程总表(表一)”，无法提取总结算金额")

    # --- 表三甲：items / 项目划分 / 类型列 ---------------------------------
    table3_name = _find_sheet(names, "设备检修单位工程表") or _find_sheet(
        names, "单位工程表"
    )
    items: list[dict[str, Any]] = []
    table3_categories: list[dict[str, Any]] = []
    if table3_name:
        source = f"{filename}{SOURCE_SEP}{table3_name}"
        items, table3_categories, _type_col = _parse_table3(
            sheets[table3_name], table3_name, source, warnings
        )
        if not items:
            warnings.append(f"{filename}：表三甲未解析到任何明细行")
    else:
        warnings.append(f"{filename}：未找到“设备检修单位工程表(表三甲)”")

    # --- 表二甲：categories（优先） ---------------------------------------
    categories: list[dict[str, Any]] = []
    categories_source = ""
    table2_name = _find_sheet(names, "专业汇总表") or _find_sheet(names, "表二甲")
    if table2_name:
        source = f"{filename}{SOURCE_SEP}{table2_name}"
        categories = _parse_table2_categories(sheets[table2_name], source, warnings)
        if categories:
            categories_source = table2_name
    if not categories and table3_categories:
        categories = table3_categories
        categories_source = table3_name or "表三甲"

    # --- 材料清册 -----------------------------------------------------------
    material_items: list[dict[str, Any]] = []
    material_sheets: dict[str, str | None] = {}
    subtotals: dict[str, dict[str, float]] = {}

    supply_sheets = (
        ("乙供未计价", "乙供", "主材", _find_sheet(names, "乙供", "未计价")),
        ("甲供未计价", "甲供", "主材", _find_sheet(names, "甲供", "未计价")),
    )
    for key, supply, item_type, sheet_name in supply_sheets:
        material_sheets[key] = sheet_name
        if not sheet_name:
            continue
        source = f"{filename}{SOURCE_SEP}{sheet_name}"
        parsed, parsed_subtotals = _parse_material_sheet(
            sheets[sheet_name],
            sheet_name=sheet_name,
            source=source,
            supply=supply,
            item_type=item_type,
            warnings=warnings,
        )
        material_items.extend(parsed)
        subtotals[key] = parsed_subtotals

    parts_sheet = _find_sheet(names, "配件", "汇总", exclude=("甲供", "购置费", "明细"))
    supplied_parts_sheet = _find_sheet(
        names, "甲供", "配件", exclude=("购置费", "明细")
    )
    material_sheets["配件汇总表"] = parts_sheet
    material_sheets["甲供配件汇总表"] = supplied_parts_sheet
    if supplied_parts_sheet:
        source = f"{filename}{SOURCE_SEP}{supplied_parts_sheet}"
        parsed, parsed_subtotals = _parse_material_sheet(
            sheets[supplied_parts_sheet],
            sheet_name=supplied_parts_sheet,
            source=source,
            supply="甲供",
            item_type="配件",
            warnings=warnings,
        )
        material_items.extend(parsed)
        subtotals["甲供配件"] = parsed_subtotals
    if parts_sheet:
        source = f"{filename}{SOURCE_SEP}{parts_sheet}"
        parsed, parsed_subtotals = _parse_material_sheet(
            sheets[parts_sheet],
            sheet_name=parts_sheet,
            source=source,
            supply="乙供",
            item_type="配件",
            warnings=warnings,
        )
        subtotals["配件"] = parsed_subtotals
        # 若没有单独的甲供配件表，配件汇总表就是配件条目的来源
        if not supplied_parts_sheet:
            material_items.extend(parsed)

    # 材料汇总表（定额材料，2026 独有）：只记录统计信息，不并入清册 items
    material_summary = _parse_material_summary_stats(sheets, warnings, filename)

    # --- 材料合计 -----------------------------------------------------------
    table3_items = items
    material_totals: dict[str, float | None] = {
        "乙供合计": None,
        "甲供合计": None,
        "配件合计": None,
        "甲供配件合计": None,
    }
    if subtotals.get("乙供未计价"):
        material_totals["乙供合计"] = _pick_subtotal(subtotals["乙供未计价"])
    if material_totals["乙供合计"] is None:
        material_totals["乙供合计"] = _sum_amounts(
            material_items, supply="乙供", item_type="主材"
        )
    if subtotals.get("甲供未计价"):
        material_totals["甲供合计"] = _pick_subtotal(subtotals["甲供未计价"])
    if subtotals.get("配件"):
        material_totals["配件合计"] = _pick_subtotal(subtotals["配件"])
    elif subtotals.get("甲供配件"):
        material_totals["配件合计"] = _pick_subtotal(subtotals["甲供配件"])
    if material_totals["配件合计"] is None:
        # 旧版没有配件汇总表时，用表三甲“配件”行的合价合计（契约示例：2025 → 40287）
        material_totals["配件合计"] = _sum_amounts(
            table3_items, item_type="配件"
        )
    if subtotals.get("甲供配件"):
        material_totals["甲供配件合计"] = _pick_subtotal(subtotals["甲供配件"])

    # --- 补充金额（施工费明细表里的乙供物料费） ----------------------------
    construction = _parse_construction_detail(sheets, warnings, filename)
    if construction.get("乙供物料费") is not None and totals.get("乙供物料费") is None:
        totals["乙供物料费"] = construction["乙供物料费"]

    # --- final_amount 规则 --------------------------------------------------
    final_amount = totals.get("下浮后送审金额")
    if final_amount is None:
        final_amount = totals.get("设备检修费")
    if final_amount is None:
        final_amount = totals.get("施工费")
    totals["final_amount"] = final_amount

    title = _extract_title(sheets, path)
    meta = {
        "file": filename,
        "sheets": names,
        "title": title,
        "sheet_map": {
            "表一": summary_name,
            "表二甲": table2_name,
            "表三甲": table3_name,
            **material_sheets,
        },
        "categories_source": categories_source,
        "counts": {
            "items": len(items),
            "categories": len(categories),
            "materials": len(material_items),
        },
    }
    if material_summary:
        meta["material_summary"] = material_summary

    return {
        "kind": KIND,
        "items": items,
        "totals": totals,
        "categories": categories,
        "materials": {"items": material_items, "totals": material_totals},
        "meta": meta,
        "warnings": warnings,
    }
