"""竣工报告工作簿 reader（.XLS / .xlsx）。

把“竣工报告”工作簿解析成契约规定的 DocSet 片段：
``{"kind", "texts", "tables", "items", "totals", "meta", "warnings"}``
（见 ``.dev/contract.md`` 第 3、4.3 节）。

真实资料结构（只读验证过）：
- 报告正文：``12竣工报告2`` / ``11竣工报告1`` 等工作表，主要工程量是超长文本单元格；
- 隐蔽记录：``隐蔽-接地汇总``（台架接地）、``隐蔽-接地汇总 (2)``（杆塔设备接地）、
  ``3.台变地网`` / ``3.杆塔设备地网`` / ``3.设备地网-2``（多安装地点并排的多面板表）、
  ``隐蔽-拉盘`` / ``隐蔽-底盘``；
- 工程量登记：``安健环工程量见证表``、``清理旧线路设施工程量登记表``；
- 其他：``工程量签证表``、接地装置质量控制记录表（模板行，不产生 items/tables）。

实现取舍（与契约的对应说明）：
- texts：扫描所有工作表内长度 > 80 的字符串单元格；若该单元格位于已识别结构化表的
  记录区（例如 ``工程量签证表`` 的“签证内容”列），其完整内容已进入 tables，不再重复
  进入 texts，避免同一份内容在“文字描述”对比里重复出现。真实 2025 报告 texts=2、
  2026 报告 texts=4（2026 的 R2C1/R2C11 两格文本完全相同，去重后保留首个并 note 说明）。
- tables：接地汇总、安健环、清理旧线路、签证表、拉盘/底盘、多面板地网均导出为结构化表
  （columns + rows，保留原始中文表头）。
- items：接地汇总按“安装地点 × 材料”展开（item_type=接地）；安健环（item_type=安健环）；
  清理旧线路（item_type=拆旧）；拉盘/底盘的记录数聚合为计数类 items（item_type=工程量）。
  多面板地网只导出 tables，不再重复产出 items，避免与接地汇总重复计数。
- 记录为空（只有序号没有内容）的表格在 warnings 中报告“记录为空/原资料缺项”，不伪造数据。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .excel_utils import cell_number, cell_text, load_sheets

KIND = "report_workbook"

#: 判断“这是竣工报告工作簿”的 sheet 名特征（任一命中即匹配）
SHEET_KEYWORDS = (
    "竣工报告",
    "隐蔽",
    "安健环",
    "清理旧线路",
    "接地",
    "拉盘",
    "底盘",
    "地网",
    "工程量签证",
)
#: 判断“这是竣工报告工作簿”的文件名特征
FILENAME_KEYWORDS = ("竣工报告", "隐蔽", "安健环", "清理旧线路", "接地", "地网")

#: 长文本最小长度（清洗后）
LONG_TEXT_MIN_LEN = 80

_PLACEHOLDERS = {"", "-", "—", "——", "/", "／", "\\", "无", "N/A", "n/a", "None", "none"}
_FOOTER_MARKERS = ("签证", "签名", "签字", "负责人", "日期", "施工人员", "验收人员", "检查人")

# 接地汇总：列 -> (表头关键词, 取值类型)
_GROUNDING_COLUMNS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("seq", ("序号",), "seq"),
    ("site", ("安装地点",), "text"),
    ("rod_h", ("圆钢水平地极",), "num"),
    ("angle", ("镀锌角钢",), "num"),
    ("rod_out", ("圆钢引出线",), "num"),
    ("plate", ("连接板",), "num"),
    ("depth", ("埋深",), "num"),
    ("resistance", ("电阻实测值", "实测电阻"), "num"),
    ("note", ("备注", "备 注"), "text"),
)

# 接地材料：(名称, 单位, 取值列 key)
_GROUNDING_MATERIALS = (
    ("圆钢水平地极", "米", "rod_h"),
    ("镀锌角钢", "条", "angle"),
    ("圆钢引出线", "米", "rod_out"),
    ("连接板", "块", "plate"),
)

_HEALTH_COLUMNS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("seq", ("序号",), "seq"),
    ("name", ("材料名称",), "text"),
    ("spec", ("规格型号", "规格"), "text"),
    ("material", ("材质",), "text"),
    ("unit", ("单位",), "text"),
    ("qty", ("数量",), "num"),
)

_CLEARING_COLUMNS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("seq", ("序号",), "seq"),
    ("name", ("项目名称",), "text"),
    ("spec", ("规格、等级", "规格等级", "规格"), "text"),
    ("unit", ("单位",), "text"),
    ("qty", ("数量",), "num"),
    ("note", ("备注", "备 注"), "text"),
)

_VISA_COLUMNS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("seq", ("序号",), "seq"),
    ("place", ("工作地点", "地点"), "text"),
    ("content", ("签证内容", "内容"), "text"),
)

_GROUNDING_TITLE_RE = re.compile(r"接地")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _compact(value: Any) -> str:
    """去掉所有空白后的文本，用于表头/标签的宽松匹配。"""
    return re.sub(r"\s+", "", cell_text(value))


def _norm(value: Any) -> str:
    """干净文本（保留单空格）。"""
    return cell_text(value)


def _is_placeholder(text: str) -> bool:
    return text.strip() in _PLACEHOLDERS


def _num_or_text(value: Any) -> float | str:
    """数值列取值：能解析成数字给 float，否则给文本（占位符给空串）。"""
    number = cell_number(value)
    if number is not None:
        return number
    text = _norm(value)
    return "" if _is_placeholder(text) else text


def _row_has_text(row: Iterable[Any]) -> bool:
    return any(_norm(value) for value in row)


def _row_compact(row: Iterable[Any]) -> list[str]:
    return [_compact(value) for value in row]


def _at(row: list[Any], col: int | None) -> Any:
    """安全取单元格：越界返回 None。"""
    if col is None or col < 0:
        return None
    if col >= len(row):
        return None
    return row[col]


def _cell_text_at(row: list[Any], col: int | None) -> str:
    return _norm(_at(row, col))


def _cell_number_at(row: list[Any], col: int | None) -> float | None:
    return cell_number(_at(row, col))


# --------------------------------------------------------------------------- #
# 接口：matches
# --------------------------------------------------------------------------- #
def matches(sheet_names: Iterable[str] | None, filename: str | Path | None = None) -> bool:
    """是否竣工报告工作簿：sheet 名或文件名含竣工报告类特征。"""
    names = [str(name) for name in (sheet_names or []) if name is not None]
    stem = Path(str(filename)).stem if filename else ""
    for keyword in SHEET_KEYWORDS:
        if any(keyword in name for name in names):
            return True
    for keyword in FILENAME_KEYWORDS:
        if keyword in stem:
            return True
    return False


# --------------------------------------------------------------------------- #
# 通用表格解析辅助
# --------------------------------------------------------------------------- #
def _find_header(
    rows: list[list[Any]],
    column_specs: tuple[tuple[str, tuple[str, ...], str], ...],
    required: tuple[str, ...],
    max_scan: int = 25,
) -> tuple[int, dict[str, int]] | None:
    """在前 max_scan 行中找表头行，返回 ``(行号, {逻辑列: 列号})``。"""
    for row_index in range(min(max_scan, len(rows))):
        row = rows[row_index]
        compacts = _row_compact(row)
        found: dict[str, int] = {}
        for key, aliases, _kind in column_specs:
            for col, text in enumerate(compacts):
                if not text:
                    continue
                if any(alias in text for alias in aliases):
                    found[key] = col
                    break
        if all(key in found for key in required):
            return row_index, found
    return None


def _build_columns(
    rows: list[list[Any]],
    header_index: int,
    mapping: dict[str, int],
    column_specs: tuple[tuple[str, tuple[str, ...], str], ...],
) -> list[dict[str, Any]]:
    """按逻辑列顺序构造列定义（保留原始中文表头）。"""
    header_row = rows[header_index]
    columns: list[dict[str, Any]] = []
    for key, _aliases, kind in column_specs:
        if key not in mapping:
            continue
        header = _cell_text_at(header_row, mapping[key])
        columns.append({"key": key, "header": header or key, "kind": kind, "col": mapping[key]})
    return columns


def _row_values(
    row: list[Any], seq: float, columns: list[dict[str, Any]]
) -> list[Any]:
    values: list[Any] = []
    for column in columns:
        if column["kind"] == "seq":
            values.append(seq)
        elif column["kind"] == "num":
            values.append(_num_or_text(_at(row, column["col"])))
        else:
            values.append(_cell_text_at(row, column["col"]))
    return values


def _classify_records(
    rows: list[list[Any]],
    start_row: int,
    seq_col: int | None,
    key_cols: tuple[int, ...],
    other_cols: tuple[int, ...] = (),
) -> tuple[list[tuple[int, float, list[Any]]], int, list[int], int]:
    """扫描数据行：返回 ``(记录行, 空记录数, 零散行号, 最后扫描行)``。

    - 空白行跳过（便于穿过模板中的空行）；
    - 序号列为数字 → 候选记录；关键列全空 → 空记录（只有序号）；
    - 关键列全空但其它列有值 → 零散行（疑似残留数据）；
    - 序号列非数字且行非空 → 视为表尾，停止扫描。
    """
    records: list[tuple[int, float, list[Any]]] = []
    empty_rows = 0
    stray_rows: list[int] = []
    last_row = start_row - 1
    for row_index in range(start_row, len(rows)):
        row = rows[row_index]
        if not _row_has_text(row):
            continue
        last_row = row_index
        seq = _cell_number_at(row, seq_col)
        if seq is None:
            break
        key_filled = any(_norm(_at(row, col)) for col in key_cols)
        if key_filled:
            records.append((row_index, seq, row))
        elif any(_norm(_at(row, col)) for col in other_cols):
            stray_rows.append(row_index + 1)
        else:
            empty_rows += 1
    return records, empty_rows, stray_rows, last_row


def _empty_record_warning(
    sheet_name: str,
    table_label: str,
    empty_rows: int,
    stray_rows: list[int],
    record_count: int,
) -> list[str]:
    """空记录/残留数据的 warning。"""
    warnings: list[str] = []
    if record_count == 0:
        warnings.append(
            f"{sheet_name}：{table_label}记录为空（{empty_rows} 行仅有编号、内容为空），原资料缺项"
        )
    elif empty_rows:
        warnings.append(
            f"{sheet_name}：{table_label}有 {empty_rows} 行仅有编号、内容为空（原资料缺项）"
        )
    if stray_rows:
        shown = "、".join(str(row) for row in stray_rows[:10])
        more = "等" if len(stray_rows) > 10 else ""
        warnings.append(
            f"{sheet_name}：第 {shown}{more} 行存在零散数值但缺少关键字段，已跳过"
        )
    return warnings


def _sheet_title(rows: list[list[Any]], sheet_name: str) -> str:
    """取工作表标题（前两行最长的非空文本），回退 sheet 名。"""
    for row in rows[:2]:
        texts = [_norm(value) for value in row]
        candidates = [text for text in texts if len(text) >= 2]
        if candidates:
            return max(candidates, key=len)[:80]
    return sheet_name


# --------------------------------------------------------------------------- #
# 各类结构化表解析
# --------------------------------------------------------------------------- #
def _extract_grounding(
    sheet_name: str, rows: list[list[Any]], file_name: str
) -> dict[str, Any] | None:
    """隐蔽-接地汇总（水平接地极/杆塔设备接地）→ 表 + 按安装地点×材料的 items。"""
    found = _find_header(
        rows,
        _GROUNDING_COLUMNS,
        required=("seq", "site"),
        max_scan=20,
    )
    if found is None:
        return None
    header_index, mapping = found
    material_keys = ("rod_h", "angle", "rod_out", "plate")
    if not any(key in mapping for key in material_keys):
        return None

    specs: dict[str, str] = {}
    data_start = header_index + 1
    if data_start < len(rows):
        spec_row = rows[data_start]
        candidates = {
            key: _norm(_at(spec_row, mapping[key]))
            for key in material_keys
            if key in mapping and _norm(_at(spec_row, mapping[key]))
        }
        if len(candidates) >= 2 and all(cell_number(value) is None for value in candidates.values()):
            specs = candidates
            data_start += 1

    columns = _build_columns(rows, header_index, mapping, _GROUNDING_COLUMNS)
    key_cols = tuple(mapping[key] for key in ("site",) if key in mapping)
    other_cols = tuple(
        mapping[key]
        for key in ("rod_h", "angle", "rod_out", "plate", "depth", "resistance", "note")
        if key in mapping
    )
    records, empty_rows, stray_rows, last_row = _classify_records(
        rows, data_start, mapping.get("seq"), key_cols, other_cols
    )

    table_rows: list[list[Any]] = []
    items: list[dict[str, Any]] = []
    unparsed = 0
    for row_index, seq, row in records:
        site = _cell_text_at(row, mapping.get("site"))
        table_rows.append(_row_values(row, seq, columns))
        depth = _cell_number_at(row, mapping.get("depth"))
        resistance = _cell_number_at(row, mapping.get("resistance"))
        note_parts = [f"安装地点：{site}"]
        if depth is not None:
            note_parts.append(f"埋深 {cell_text(depth)} 米")
        if resistance is not None:
            note_parts.append(f"电阻实测 {cell_text(resistance)} Ω")
        for name, unit, key in _GROUNDING_MATERIALS:
            col = mapping.get(key)
            if col is None:
                continue
            qty = _cell_number_at(row, col)
            if qty is None:
                raw = _cell_text_at(row, col)
                if raw and not _is_placeholder(raw):
                    unparsed += 1
                continue
            if qty <= 0:
                continue
            items.append(
                {
                    "name": name,
                    "spec": specs.get(key, ""),
                    "unit": unit,
                    "qty": qty,
                    "unit_price": None,
                    "amount": None,
                    "code": "",
                    "section": "接地工程",
                    "item_type": "接地",
                    "supply": "",
                    "source": f"{file_name}!{sheet_name}",
                    "note": "；".join(note_parts),
                }
            )

    warnings = _empty_record_warning(
        sheet_name, "接地汇总", empty_rows, stray_rows, len(records)
    )
    if unparsed:
        warnings.append(f"{sheet_name}：接地汇总有 {unparsed} 个数量单元格无法解析为数值，已跳过")

    table_note = "；".join(
        f"{material[0]} {specs[material[2]]}"
        for material in _GROUNDING_MATERIALS
        if specs.get(material[2])
    )
    table = {
        "id": sheet_name,
        "title": _sheet_title(rows, sheet_name),
        "category": "接地工程",
        "sheet": sheet_name,
        "columns": [column["header"] for column in columns],
        "rows": table_rows,
    }
    if table_note:
        table["note"] = f"材料规格：{table_note}"
    covered = set(range(header_index, max(last_row, header_index) + 1))
    return {"table": table, "items": items, "warnings": warnings, "covered_rows": covered}


def _extract_health_safety(
    sheet_name: str, rows: list[list[Any]], file_name: str
) -> dict[str, Any] | None:
    """安健环工程量见证表 → 表 + items（item_type=安健环）。"""
    found = _find_header(rows, _HEALTH_COLUMNS, required=("name", "qty"), max_scan=20)
    if found is None:
        return None
    header_index, mapping = found
    columns = _build_columns(rows, header_index, mapping, _HEALTH_COLUMNS)
    key_cols = tuple(mapping[key] for key in ("name",) if key in mapping)
    other_cols = tuple(
        mapping[key]
        for key in ("spec", "material", "unit", "qty")
        if key in mapping
    )
    records, empty_rows, stray_rows, last_row = _classify_records(
        rows, header_index + 1, mapping.get("seq"), key_cols, other_cols
    )

    table_rows: list[list[Any]] = []
    items: list[dict[str, Any]] = []
    unparsed = 0
    for row_index, seq, row in records:
        table_rows.append(_row_values(row, seq, columns))
        name = _cell_text_at(row, mapping.get("name"))
        spec = _cell_text_at(row, mapping.get("spec"))
        material = _cell_text_at(row, mapping.get("material"))
        unit = _cell_text_at(row, mapping.get("unit"))
        qty = _cell_number_at(row, mapping.get("qty"))
        if qty is None:
            raw = _cell_text_at(row, mapping.get("qty"))
            if raw and not _is_placeholder(raw):
                unparsed += 1
            continue
        note_parts = [f"材质：{material}"] if material else []
        items.append(
            {
                "name": name,
                "spec": spec,
                "unit": unit,
                "qty": qty,
                "unit_price": None,
                "amount": None,
                "code": "",
                "section": "安健环工程",
                "item_type": "安健环",
                "supply": "",
                "source": f"{file_name}!{sheet_name}",
                "note": "；".join(note_parts),
            }
        )

    warnings = _empty_record_warning(
        sheet_name, "安健环材料", empty_rows, stray_rows, len(records)
    )
    if unparsed:
        warnings.append(f"{sheet_name}：安健环有 {unparsed} 个数量单元格无法解析为数值，已跳过")
    table = {
        "id": sheet_name,
        "title": _sheet_title(rows, sheet_name),
        "category": "安健环",
        "sheet": sheet_name,
        "columns": [column["header"] for column in columns],
        "rows": table_rows,
    }
    covered = set(range(header_index, max(last_row, header_index) + 1))
    return {"table": table, "items": items, "warnings": warnings, "covered_rows": covered}


def _extract_clearing(
    sheet_name: str, rows: list[list[Any]], file_name: str
) -> dict[str, Any] | None:
    """清理旧线路设施工程量登记表 → 表 + items（item_type=拆旧）。"""
    found = _find_header(
        rows, _CLEARING_COLUMNS, required=("name", "unit", "qty"), max_scan=20
    )
    if found is None:
        return None
    header_index, mapping = found
    columns = _build_columns(rows, header_index, mapping, _CLEARING_COLUMNS)
    key_cols = tuple(mapping[key] for key in ("name",) if key in mapping)
    other_cols = tuple(
        mapping[key] for key in ("spec", "unit", "qty", "note") if key in mapping
    )
    records, empty_rows, stray_rows, last_row = _classify_records(
        rows, header_index + 1, mapping.get("seq"), key_cols, other_cols
    )

    table_rows: list[list[Any]] = []
    items: list[dict[str, Any]] = []
    unparsed = 0
    for row_index, seq, row in records:
        table_rows.append(_row_values(row, seq, columns))
        name = _cell_text_at(row, mapping.get("name"))
        spec = _cell_text_at(row, mapping.get("spec"))
        unit = _cell_text_at(row, mapping.get("unit"))
        note = _cell_text_at(row, mapping.get("note"))
        qty = _cell_number_at(row, mapping.get("qty"))
        if qty is None:
            raw = _cell_text_at(row, mapping.get("qty"))
            if raw and not _is_placeholder(raw):
                unparsed += 1
            continue
        items.append(
            {
                "name": name,
                "spec": spec,
                "unit": unit,
                "qty": qty,
                "unit_price": None,
                "amount": None,
                "code": "",
                "section": "清理旧线路",
                "item_type": "拆旧",
                "supply": "",
                "source": f"{file_name}!{sheet_name}",
                "note": note,
            }
        )

    warnings = _empty_record_warning(
        sheet_name, "清理旧线路", empty_rows, stray_rows, len(records)
    )
    if unparsed:
        warnings.append(f"{sheet_name}：清理旧线路有 {unparsed} 个数量单元格无法解析为数值，已跳过")
    table = {
        "id": sheet_name,
        "title": _sheet_title(rows, sheet_name),
        "category": "拆旧",
        "sheet": sheet_name,
        "columns": [column["header"] for column in columns],
        "rows": table_rows,
    }
    covered = set(range(header_index, max(last_row, header_index) + 1))
    return {"table": table, "items": items, "warnings": warnings, "covered_rows": covered}


def _extract_rod_plate(
    sheet_name: str, rows: list[list[Any]], file_name: str
) -> dict[str, Any] | None:
    """隐蔽-拉盘 / 隐蔽-底盘 → 表 + 计数类 items（拉盘 个 / 底盘 块）。"""
    kind = ""
    if "拉盘" in sheet_name:
        kind = "拉盘"
    elif "底盘" in sheet_name:
        kind = "底盘"
    if not kind:
        # 表名不含特征时，按表头识别
        for row in rows[:20]:
            compacts = _row_compact(row)
            if any("电杆编号" in text for text in compacts):
                if any("拉盘规格" in text for text in compacts):
                    kind = "拉盘"
                elif any("底盘规格" in text for text in compacts):
                    kind = "底盘"
                break
    if not kind:
        return None

    columns_specs: tuple[tuple[str, tuple[str, ...], str], ...] = (
        ("seq", ("序号",), "seq"),
        ("pole", ("电杆编号",), "text"),
        ("pole_spec", ("电杆规格",), "text"),
        ("device_spec", (f"{kind}规格",), "text"),
        ("depth", (f"{kind}埋深",), "num"),
        ("note", ("备注", "备 注"), "text"),
    )
    found = _find_header(
        rows, columns_specs, required=("device_spec", "note"), max_scan=20
    )
    if found is None:
        return None
    header_index, mapping = found
    columns = _build_columns(rows, header_index, mapping, columns_specs)
    key_cols = tuple(
        mapping[key] for key in ("pole", "pole_spec") if key in mapping
    )
    other_cols = tuple(
        mapping[key]
        for key in ("device_spec", "depth", "note")
        if key in mapping
    )
    records, empty_rows, stray_rows, last_row = _classify_records(
        rows, header_index + 1, mapping.get("seq"), key_cols, other_cols
    )

    table_rows: list[list[Any]] = []
    aggregated: dict[str, list[float]] = {}
    record_counts: dict[str, int] = {}
    for row_index, seq, row in records:
        table_rows.append(_row_values(row, seq, columns))
        spec = _cell_text_at(row, mapping.get("device_spec"))
        note = _cell_text_at(row, mapping.get("note"))
        qty = 1.0
        if kind == "拉盘" and note:
            matched = re.search(r"(\d+)\s*个\s*拉盘", note)
            if matched:
                qty = float(matched.group(1))
        aggregated.setdefault(spec, []).append(qty)
        record_counts[spec] = record_counts.get(spec, 0) + 1

    items: list[dict[str, Any]] = []
    for spec, quantities in aggregated.items():
        items.append(
            {
                "name": kind,
                "spec": spec,
                "unit": "个" if kind == "拉盘" else "块",
                "qty": round(sum(quantities), 6),
                "unit_price": None,
                "amount": None,
                "code": "",
                "section": "隐蔽工程",
                "item_type": "工程量",
                "supply": "",
                "source": f"{file_name}!{sheet_name}",
                "note": f"共 {record_counts[spec]} 条隐蔽记录",
            }
        )

    warnings = _empty_record_warning(
        sheet_name, kind, empty_rows, stray_rows, len(records)
    )
    table = {
        "id": sheet_name,
        "title": _sheet_title(rows, sheet_name),
        "category": kind,
        "sheet": sheet_name,
        "columns": [column["header"] for column in columns],
        "rows": table_rows,
    }
    covered = set(range(header_index, max(last_row, header_index) + 1))
    return {"table": table, "items": items, "warnings": warnings, "covered_rows": covered}


def _extract_visa(
    sheet_name: str, rows: list[list[Any]], file_name: str
) -> dict[str, Any] | None:
    """工程量签证表 → 表（长文本记录区从 texts 中排除，避免重复）。"""
    found = _find_header(
        rows, _VISA_COLUMNS, required=("place", "content"), max_scan=20
    )
    if found is None:
        return None
    header_index, mapping = found
    columns = _build_columns(rows, header_index, mapping, _VISA_COLUMNS)
    key_cols = tuple(mapping[key] for key in ("place", "content") if key in mapping)
    records, empty_rows, stray_rows, last_row = _classify_records(
        rows, header_index + 1, mapping.get("seq"), key_cols
    )
    table_rows = [_row_values(row, seq, columns) for _row_index, seq, row in records]
    warnings = _empty_record_warning(
        sheet_name, "工程量签证", empty_rows, stray_rows, len(records)
    )
    table = {
        "id": sheet_name,
        "title": _sheet_title(rows, sheet_name),
        "category": "签证",
        "sheet": sheet_name,
        "columns": [column["header"] for column in columns],
        "rows": table_rows,
    }
    covered = set(range(header_index, max(last_row, header_index) + 1))
    return {"table": table, "items": [], "warnings": warnings, "covered_rows": covered}


def _extract_multi_panel_grounding(
    sheet_name: str, rows: list[list[Any]], file_name: str
) -> dict[str, Any] | None:
    """台变地网 / 杆塔设备地网 / 设备地网-2：多安装地点并排结构拆成行。"""
    header_index = None
    panel_starts: list[int] = []
    for row_index, row in enumerate(rows[:30]):
        compacts = _row_compact(row)
        starts = [col for col, text in enumerate(compacts) if text == "材料"]
        has_panel_header = any("角桩规格" in text for text in compacts)
        if starts and has_panel_header:
            header_index = row_index
            panel_starts = starts
            break
    if header_index is None:
        return None

    header_row = rows[header_index]
    # 电阻实测值表（部分页面在下方单独列出）
    resistance: dict[str, Any] = {}
    for row_index in range(header_index, len(rows)):
        compacts = _row_compact(rows[row_index])
        if any("电阻实测值" in text for text in compacts):
            seq_col = next((col for col, text in enumerate(compacts) if text == "序号"), None)
            site_col = next((col for col, text in enumerate(compacts) if text == "安装地点"), None)
            res_col = next(
                (col for col, text in enumerate(compacts) if "电阻实测值" in text), None
            )
            if seq_col is not None and site_col is not None and res_col is not None:
                for data_index in range(row_index + 1, len(rows)):
                    data_row = rows[data_index]
                    if not _row_has_text(data_row):
                        continue
                    seq = _cell_number_at(data_row, seq_col)
                    if seq is None:
                        break
                    site = _cell_text_at(data_row, site_col)
                    if site:
                        resistance[site] = _num_or_text(_at(data_row, res_col))
            break

    table_rows: list[list[Any]] = []
    for order, start in enumerate(panel_starts):
        end = panel_starts[order + 1] if order + 1 < len(panel_starts) else len(header_row)
        mapping: dict[str, int] = {}
        for col in range(start, end):
            text = _compact(header_row[col]) if col < len(header_row) else ""
            if not text:
                continue
            if text == "类别":
                mapping["category"] = col
            elif "角桩规格" in text:
                mapping["angle_spec"] = col
            elif "角桩根数" in text:
                mapping["angle_qty"] = col
            elif "角桩长度" in text:
                mapping["angle_len"] = col
            elif "圆钢规格" in text:
                mapping["rod_spec"] = col
            elif "圆钢长度" in text:
                mapping["rod_len"] = col
        if "angle_spec" not in mapping and "rod_len" not in mapping:
            continue
        # 安装地点：表头上方的“项目名称/安装地点”行
        site = ""
        for project_index in (header_index - 1, header_index - 2):
            if project_index < 0:
                continue
            project_row = rows[project_index]
            for col in range(start, min(end, len(project_row))):
                if _compact(project_row[col]) != "安装地点":
                    continue
                for value_col in range(col + 1, min(end, len(project_row))):
                    text = _norm(project_row[value_col])
                    if text:
                        site = text
                        break
                if site:
                    break
            if site:
                break
        for data_index in range(header_index + 1, min(len(rows), header_index + 20)):
            data_row = rows[data_index]
            compacts = _row_compact(data_row)
            if any(
                text.startswith(("附图", "结论", "验收"))
                for text in compacts
                if text
            ):
                break
            if not any(
                _norm(_at(data_row, mapping[key]))
                for key in ("angle_spec", "angle_qty", "angle_len", "rod_spec", "rod_len")
                if key in mapping
            ):
                continue
            table_rows.append(
                [
                    site,
                    _compact(_at(data_row, mapping.get("category"))),
                    _num_or_text(_at(data_row, mapping.get("angle_spec"))),
                    _num_or_text(_at(data_row, mapping.get("angle_qty"))),
                    _num_or_text(_at(data_row, mapping.get("angle_len"))),
                    _num_or_text(_at(data_row, mapping.get("rod_spec"))),
                    _num_or_text(_at(data_row, mapping.get("rod_len"))),
                    resistance.get(site, ""),
                ]
            )

    warnings: list[str] = []
    if not table_rows:
        warnings.append(f"{sheet_name}：地网记录为空（原资料缺项）")
    table = {
        "id": sheet_name,
        "title": _sheet_title(rows, sheet_name),
        "category": "地网",
        "sheet": sheet_name,
        "columns": [
            "安装地点",
            "类别",
            "角桩规格",
            "角桩根数",
            "角桩长度（米）",
            "圆钢规格",
            "圆钢长度（米）",
            "电阻实测值（Ω）",
        ],
        "rows": table_rows,
    }
    covered = set(range(header_index, len(rows)))
    return {"table": table, "items": [], "warnings": warnings, "covered_rows": covered}


_EXTRACTORS = (
    _extract_grounding,
    _extract_health_safety,
    _extract_clearing,
    _extract_rod_plate,
    _extract_visa,
    _extract_multi_panel_grounding,
)

# 资料完整性检查：特征 -> 说明
_SECTION_CHECKS: tuple[tuple[str, str], ...] = (
    ("接地汇总", "隐蔽-接地汇总记录"),
    ("安健环", "安健环工程量见证表"),
    ("清理旧线路", "清理旧线路设施工程量登记表"),
    ("拉盘", "隐蔽-拉盘记录"),
    ("底盘", "隐蔽-底盘记录"),
)


# --------------------------------------------------------------------------- #
# 长文本
# --------------------------------------------------------------------------- #
def _long_text_value(value: Any) -> str:
    """长文本清洗：保留换行（便于阅读/相似度对比），去掉多余行内空白。"""
    text = str(value).replace("\u3000", " ").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    result: list[str] = []
    for line in lines:
        if not line and result and not result[-1]:
            continue
        result.append(line)
    return "\n".join(result).strip()


def _text_label(sheet_name: str, text: str) -> str:
    """长文本 label：表名/文档类型 + 行内关键词。"""
    compact = _compact(text)
    keyword = ""
    for candidate in ("主要工程量", "新建工程量", "安装部分", "新建部分", "拆除部分"):
        if candidate in compact:
            keyword = candidate
            break
    if "竣工报告" in sheet_name:
        base = "竣工报告"
    elif "签证" in sheet_name:
        base = "工程量签证"
    else:
        base = sheet_name
    return f"{base}·{keyword}" if keyword else base


# --------------------------------------------------------------------------- #
# meta
# --------------------------------------------------------------------------- #
def _detect_title(sheets: dict[str, list[list[Any]]]) -> str:
    """从“工程名称”标签旁取项目名称（跳过数字/错误单元格/其他标签）。"""
    label_noise = ("安装地点", "建设单位", "施工单位", "设计单位", "监理单位", "运行单位")
    for rows in sheets.values():
        for row in rows[:12]:
            texts = [_norm(value) for value in row]
            for index, text in enumerate(texts):
                compact = re.sub(r"\s+", "", text)
                if compact not in ("工程名称", "工程名称:", "工程名称："):
                    continue
                for candidate in texts[index + 1:index + 4]:
                    if not candidate:
                        continue
                    if candidate.endswith(("：", ":")):
                        continue
                    if any(noise in candidate for noise in label_noise):
                        continue
                    if re.fullmatch(r"[\d.\-—/()（）\s]+", candidate):
                        continue
                    return candidate
    return ""


def _aggregate_totals(items: list[dict[str, Any]], texts: list[dict[str, Any]]) -> dict[str, Any]:
    """boq totals：按 item_type 归类统计条数与数量（按单位）。"""
    totals: dict[str, Any] = {}
    for item_type, key in (("接地", "接地工程"), ("安健环", "安健环"), ("拆旧", "拆旧")):
        selected = [item for item in items if item.get("item_type") == item_type]
        qty_by_unit: dict[str, float] = {}
        for item in selected:
            unit = str(item.get("unit") or "")
            qty = item.get("qty")
            if isinstance(qty, (int, float)):
                qty_by_unit[unit] = round(qty_by_unit.get(unit, 0.0) + float(qty), 6)
        totals[key] = {"count": len(selected), "qty_by_unit": qty_by_unit}
    counts: dict[str, int] = {}
    for item in items:
        item_type = str(item.get("item_type") or "")
        counts[item_type] = counts.get(item_type, 0) + 1
    totals["counts"] = counts
    totals["texts_count"] = len(texts)
    return totals


# --------------------------------------------------------------------------- #
# 接口：load
# --------------------------------------------------------------------------- #
def load(path: str | Path) -> dict[str, Any]:
    """解析竣工报告工作簿，返回契约 4.3 规定的 dict。"""
    path = Path(path)
    sheets = load_sheets(path)
    file_name = path.name

    texts: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_texts: dict[str, dict[str, Any]] = {}
    duplicate_targets: dict[str, list[str]] = {}

    for sheet_name, rows in sheets.items():
        covered_rows: set[int] = set()
        for extractor in _EXTRACTORS:
            result = extractor(sheet_name, rows, file_name)
            if result is None:
                continue
            tables.append(result["table"])
            items.extend(result["items"])
            warnings.extend(result["warnings"])
            covered_rows = set(result.get("covered_rows") or ())
            break

        for row_index, row in enumerate(rows):
            if row_index in covered_rows:
                continue
            for col_index, value in enumerate(row):
                if not isinstance(value, str):
                    continue
                if len(_norm(value)) <= LONG_TEXT_MIN_LEN:
                    continue
                text = _long_text_value(value)
                if not text:
                    continue
                text_key = re.sub(r"\s+", "", text)
                text_id = f"{sheet_name}!R{row_index + 1}C{col_index + 1}"
                if text_key in seen_texts:
                    duplicate_targets.setdefault(text_key, []).append(text_id)
                    continue
                entry = {
                    "id": text_id,
                    "label": _text_label(sheet_name, text),
                    "text": text,
                }
                texts.append(entry)
                seen_texts[text_key] = entry

    for text_key, duplicate_ids in duplicate_targets.items():
        entry = seen_texts.get(text_key)
        if entry is None:
            continue
        entry["note"] = "同文本另见：" + "、".join(duplicate_ids) + "（仅保留首个）"

    # 资料完整性：缺失的关键记录
    sheet_names = list(sheets.keys())
    missing_sections: list[str] = []
    for keyword, label in _SECTION_CHECKS:
        if not any(keyword in name for name in sheet_names):
            missing_sections.append(label)
            warnings.append(f"未发现{label}（原资料缺项）")
    if not texts:
        warnings.append("未提取到竣工报告主要工程量长文本（原资料缺项）")

    title = _detect_title(sheets)
    meta = {
        "file": str(path),
        "sheets": sheet_names,
        "title": title or path.stem,
        "texts_count": len(texts),
        "table_count": len(tables),
        "item_count": len(items),
        "missing_sections": missing_sections,
        "notes": [
            "长文本来自非结构化区域；结构化表（如工程量签证表）内的长文本计入 tables，不重复计入 texts",
            "拉盘/底盘同时输出 tables 与计数类 items（item_type=工程量）",
            "台变地网/杆塔设备地网/设备地网-2 仅输出 tables（多安装地点已拆成行），避免与接地汇总 items 重复计数",
        ],
    }
    return {
        "kind": KIND,
        "texts": texts,
        "tables": tables,
        "items": items,
        "totals": _aggregate_totals(items, texts),
        "meta": meta,
        "warnings": warnings,
    }
