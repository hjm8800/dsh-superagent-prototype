# -*- coding: utf-8 -*-
"""typdesign.py — 南网 V3.1 典型设计（典设）匹配引擎（纯引擎模块）。

职责：
- 解析典设模块目录文件（XLSX / CSV，兼容附件6-1 双表结构）；
- 导入目录到项目（typdesign_modules / typdesign_imports）；
- 从项目图纸识别结论、模型、工程量等证据中提取典设编号并按规则匹配；
- 人工确认 / 忽略、统计汇总、导出匹配清单行。

本模块不 import app.py / seed.py / engine.py，只依赖标准库（openpyxl/xlrd
在函数内按需导入），可独立单元测试。
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from pathlib import Path
from typing import Any

# 罗马数字（典设编号中用于区分 Ⅰ/Ⅱ/Ⅲ 型）
ROMAN_CHARS = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ"

#: 证据文本中的典设编号正则（兼容 CSG-10B-TB-SB 旧写法）
CODE_RE = re.compile(r"CSG[-_ ]?[A-Z0-9][A-Za-z0-9.\-ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ×]{2,60}")

#: 电压等级标签（用于防止 10B 与 0.4K 等不同电压等级误匹配）
VOLTAGE_RE = re.compile(r"0\.4[KD]|10[BDK]|20[BD]")

#: 名称匹配的最短公共子串长度
NAME_MATCH_MIN = 4

MATCH_TYPE_EXACT = "编号精确"
MATCH_TYPE_SERIES = "图号序列"
MATCH_TYPE_VARIANT = "编号变体"
MATCH_TYPE_NAME = "名称推断"
MATCH_TYPE_NONE = "未匹配"

CONF_HIGH = "高"
CONF_MEDIUM = "中"
CONF_LOW = "低"

STATUS_PENDING = "待确认"
STATUS_CONFIRMED = "已确认"
STATUS_IGNORED = "已忽略"
#: 目录模块在证据中完全找不到对应编号时的状态（不计入“待确认”，避免完成态项目显示未完成）
STATUS_UNMATCHED = "未匹配"

#: 目录解析表头别名（顺序即优先级）
_HEADER_ALIASES = {
    "volume": ("卷册", "卷册名称", "所属卷册", "册"),
    "chapter": ("篇", "篇章", "篇名", "章节"),
    "category": ("类别", "模块类别", "分类", "类别名称"),
    "code": ("模块编号", "编号", "模块编码", "典设编号", "图号", "图纸编号"),
    "name": ("模块名称", "名称", "模块名", "图纸名称"),
    "unit": ("模块单位", "单位", "计量单位"),
    "file_path": ("图纸文件", "库内图纸", "图纸路径", "文件路径", "file_path", "library_path"),
    "note": ("备注", "说明"),
}
_PARSE_COLUMNS = ("volume", "chapter", "category", "code", "name", "unit", "file_path")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def now() -> str:
    """当前时间（YYYY-MM-DD HH:MM）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _as_dict(row: Any) -> dict:
    if isinstance(row, dict):
        return dict(row)
    keys = getattr(row, "keys", None)
    if keys is not None:
        return {key: row[key] for key in keys()}
    return dict(row)


def _rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    return [_as_dict(row) for row in conn.execute(sql, params).fetchall()]


def _one(conn, sql: str, params: tuple = ()) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return _as_dict(row) if row is not None else None


def _user_dict(user: Any) -> dict:
    """把 sqlite3.Row / dict / None 统一成 user dict。"""
    if user is None:
        return {}
    return _as_dict(user)


def _audit(conn, project_id: int, user: dict | None, action: str, detail: str = "") -> None:
    user = _user_dict(user)
    conn.execute(
        "INSERT INTO audit (project_id, user_id, action, detail, created_at) VALUES (?,?,?,?,?)",
        (project_id, user.get("id"), action, detail, now()),
    )


def _clean_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip().replace("\u3000", " ")


# ---------------------------------------------------------------------------
# 编号归一化 / 变体 / 匹配算法（与离线分析一致的核心算法）
# ---------------------------------------------------------------------------

def normalize_code(code: str) -> str:
    """编号归一：去空白、统一大写、下划线转连字符、合并多余连字符。"""
    text = _clean_cell(code).upper()
    text = re.sub(r"[\s_]+", "-", text)
    text = text.replace("－", "-")
    text = re.sub(r"-{2,}", "-", text)
    return text.strip("-")


def _canonical(code: str) -> str:
    """去掉 CSG- / CSG-GD- 前缀，便于识别旧写法（前缀差异单独处理）。"""
    text = normalize_code(code)
    for prefix in ("CSG-GD-", "CSG-"):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def _voltage_tags(code: str) -> set[str]:
    return set(VOLTAGE_RE.findall(normalize_code(code)))


def _strip_variant_tails(code: str) -> list[str]:
    """生成去掉尾部数字/罗马序/括号序号后的候选编号（×N 由 76 分规则处理，不在此剥离）。"""
    candidates: list[str] = []
    patterns = (
        r"\d+$",                       # 尾部数字（含 -01 / 2 / G01 的情况）
        r"[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+$",          # 尾部罗马序
        r"[(（]\d+[)）]$",              # 尾部括号序号
    )
    for pattern in patterns:
        match = re.search(pattern, code)
        if not match:
            continue
        prefix = code[: match.start()].rstrip("-")
        if prefix and prefix != code:
            candidates.append(prefix)
    return candidates


def code_variants(code: str) -> set[str]:
    """返回编号的所有变体（含自身），用于「去尾部数字/罗马序」的变体匹配。"""
    base = normalize_code(code)
    if not base:
        return set()
    variants = {base}
    frontier = [base]
    for _ in range(4):
        next_frontier: list[str] = []
        for current in frontier:
            for candidate in _strip_variant_tails(current):
                if candidate not in variants:
                    variants.add(candidate)
                    next_frontier.append(candidate)
        if not next_frontier:
            break
        frontier = next_frontier
    return variants


def _code_score(evidence: str, module: str) -> int:
    """不含电压守卫的编号相似度评分（100/90/88/85/78/76/0）。"""
    if evidence == module:
        return 100
    if _canonical(evidence) == _canonical(module):
        # CSG- 与 CSG-GD- 前缀差异：按编号变体处理，不判为精确
        return 88
    if evidence.startswith(module + "-"):
        return 90
    if code_variants(evidence) & code_variants(module):
        return 85
    evidence_segments = [seg for seg in evidence.split("-") if seg]
    if module.startswith(evidence + "-") and len(evidence_segments) >= 4:
        return 78
    if (module.startswith(evidence) and len(module) > len(evidence)
            and module[len(evidence)] in "×xX(（" + ROMAN_CHARS):
        return 76
    return 0


def code_match(evidence_code: str, module_code: str) -> int:
    """比较证据编号与目录模块编号，返回匹配得分（0 表示不匹配）。

    100 精确相等；90 图号序列（证据编号是目录编号的细化图号）；
    88 CSG-/CSG-GD- 前缀差异；85 编号变体（去尾部数字/罗马序）；
    78 目录编号比图号细（证据至少 4 段）；76 前缀相同且下一字符为 ×/(/罗马序。
    两边均带电压等级标签且不同时直接返回 0。
    """
    evidence = normalize_code(evidence_code)
    module = normalize_code(module_code)
    if not evidence or not module:
        return 0
    evidence_tags = _voltage_tags(evidence)
    module_tags = _voltage_tags(module)
    if evidence_tags and module_tags and evidence_tags != module_tags:
        return 0
    return _code_score(evidence, module)


def _longest_common_substring(left: str, right: str) -> tuple[int, str]:
    """最长公共子串（返回长度与子串），用于名称推断。"""
    left = (left or "").strip()
    right = (right or "").strip()
    if not left or not right:
        return 0, ""
    previous = [0] * (len(right) + 1)
    best = 0
    best_end = 0
    for i in range(1, len(left) + 1):
        current = [0] * (len(right) + 1)
        char = left[i - 1]
        for j in range(1, len(right) + 1):
            if char == right[j - 1]:
                current[j] = previous[j - 1] + 1
                if current[j] > best:
                    best = current[j]
                    best_end = i
        previous = current
    if best == 0:
        return 0, ""
    return best, left[best_end - best:best_end]


def name_match(module_name: str, text: str) -> int:
    """名称推断：返回模块名称与证据文本的最长公共子串长度。"""
    if len((module_name or "").strip()) < NAME_MATCH_MIN:
        return 0
    length, _ = _longest_common_substring(module_name or "", text or "")
    return length


def _classify_score(score: int) -> tuple[str, str]:
    """评分 → (匹配方式, 置信度)。"""
    if score >= 100:
        return MATCH_TYPE_EXACT, CONF_HIGH
    if score >= 90:
        return MATCH_TYPE_SERIES, CONF_HIGH
    if score >= 76:
        return MATCH_TYPE_VARIANT, CONF_MEDIUM
    return MATCH_TYPE_NONE, ""


# ---------------------------------------------------------------------------
# 目录文件解析（XLSX / CSV）
# ---------------------------------------------------------------------------

def _find_header(rows: list[list[str]]) -> tuple[int, dict[str, int]] | None:
    """在数据行中定位表头行，返回 (行号, {字段: 列号})。"""
    for index, row in enumerate(rows[:20]):
        normalized = [_clean_cell(cell) for cell in row]
        mapping: dict[str, int] = {}
        for column, cell in enumerate(normalized):
            if not cell:
                continue
            for field, aliases in _HEADER_ALIASES.items():
                if field in mapping:
                    continue
                if any(cell == alias for alias in aliases):
                    mapping[field] = column
                    break
                if any(alias in cell for alias in aliases):
                    mapping[field] = column
                    break
        if "code" in mapping and "name" in mapping:
            return index, mapping
    return None


def _rows_to_modules(rows: list[list[str]]) -> list[dict]:
    """把二维表格行转成模块字典（表头定位、空行跳过、按编号去重、卷册前向填充）。"""
    header = _find_header(rows)
    if header is None:
        return []
    header_index, mapping = header
    modules: list[dict] = []
    seen: set[str] = set()
    carry = {"volume": "", "chapter": "", "category": ""}
    for row in rows[header_index + 1:]:
        values = [_clean_cell(cell) for cell in row]
        if not any(values):
            continue

        def value_of(field: str) -> str:
            column = mapping.get(field)
            if column is None or column >= len(values):
                return ""
            return values[column]

        code = normalize_code(value_of("code"))
        if not code:
            continue
        for field in ("volume", "chapter", "category"):
            current = value_of(field)
            if current:
                carry[field] = current
        if code in seen:
            continue
        seen.add(code)
        modules.append({
            "volume": carry["volume"],
            "chapter": carry["chapter"],
            "category": carry["category"],
            "code": code,
            "name": value_of("name"),
            "unit": value_of("unit"),
            "file_path": value_of("file_path"),
        })
    return modules


def _read_xlsx_rows(path: str) -> list[list[str]]:
    """读取 XLSX：优先“V3.1模块汇总表（参考）”，其次“标准设计应用情况统计表”。"""
    from openpyxl import load_workbook

    workbook = load_workbook(filename=path, read_only=True, data_only=True)
    try:
        names = list(workbook.sheetnames)

        def priority(name: str) -> int:
            text = (name or "").upper()
            score = 0
            if "V3.1" in text or "模块汇总" in text:
                score += 3
            if "标准设计应用情况" in text:
                score += 2
            if "模块" in text:
                score += 1
            return score

        ordered = sorted(names, key=lambda name: (-priority(name), names.index(name)))
        for sheet_name in ordered:
            worksheet = workbook[sheet_name]
            rows = [[_clean_cell(cell) for cell in row] for row in worksheet.iter_rows(values_only=True)]
            rows = [row for row in rows if any(row)]
            if _find_header(rows) is not None:
                return rows
        return []
    finally:
        workbook.close()


def _read_csv_rows(path: str) -> list[list[str]]:
    """读取 CSV（UTF-8/GBK 自动识别，逗号/制表符分隔）。"""
    raw = Path(path).read_bytes()
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        return []
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;，")
        reader = csv.reader(io.StringIO(text), dialect)
    except csv.Error:
        reader = csv.reader(io.StringIO(text))
    return [[_clean_cell(cell) for cell in row] for row in reader]


def parse_catalog_file(path: str, filename: str = "") -> list[dict]:
    """解析典设模块目录文件，返回 [{volume, chapter, category, code, name, unit}]。

    支持 .xlsx（openpyxl，兼容附件6-1 双表结构）与 .csv（UTF-8/GBK）。
    自动定位表头、跳过空行、按模块编号去重。
    """
    suffix = Path(filename or path).suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        rows = _read_xlsx_rows(path)
    elif suffix == ".csv":
        rows = _read_csv_rows(path)
    elif suffix == ".xls":
        rows = _read_xls_rows(path)
    else:
        raise ValueError(f"不支持的目录文件格式：{suffix or filename or path}（仅支持 .xlsx/.xls/.csv）")
    return _rows_to_modules(rows)


def _read_xls_rows(path: str) -> list[list[str]]:
    """读取旧版 .xls（xlrd），尽力而为。"""
    import xlrd

    workbook = xlrd.open_workbook(filename=path)
    rows: list[list[str]] = []
    for sheet in workbook.sheets():
        for row_index in range(sheet.nrows):
            rows.append([_clean_cell(sheet.cell_value(row_index, column)) for column in range(sheet.ncols)])
    return rows


# ---------------------------------------------------------------------------
# 导入目录
# ---------------------------------------------------------------------------

def import_catalog(conn, project_id: int, path: str, filename: str, user: dict | None = None) -> dict:
    """解析并导入典设目录：清空旧目录/匹配 → 写入新目录 → 记录导入历史 + 审计。"""
    try:
        modules = parse_catalog_file(path, filename)
    except Exception as exc:  # noqa: BLE001 - 上传文件格式不可控，失败要给页面提示
        return {"ok": False, "modules": 0, "message": f"典设目录解析失败：{exc}"}
    if not modules:
        return {"ok": False, "modules": 0,
                "message": "未解析到典设模块，请检查文件表头（需包含“模块编号”“模块名称”等列）"}
    conn.execute("DELETE FROM typdesign_modules WHERE project_id=?", (project_id,))
    conn.execute("DELETE FROM typdesign_matches WHERE project_id=?", (project_id,))
    for module in modules:
        conn.execute(
            "INSERT INTO typdesign_modules (project_id, volume, chapter, category, code, name, unit, file_path, note) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (project_id, module["volume"], module["chapter"], module["category"],
             module["code"], module["name"], module["unit"], module.get("file_path") or "", ""),
        )
    user = _user_dict(user)
    conn.execute(
        "INSERT INTO typdesign_imports (project_id, filename, modules, imported_by, imported_at) "
        "VALUES (?,?,?,?,?)",
        (project_id, filename or Path(path).name, len(modules), user.get("id"), now()),
    )
    _audit(conn, project_id, user, "导入典型设计目录",
           f"{filename or Path(path).name}：{len(modules)} 个模块")
    conn.commit()
    return {"ok": True, "modules": len(modules),
            "message": f"典设目录导入成功：{filename or Path(path).name}，共 {len(modules)} 个模块"}


# ---------------------------------------------------------------------------
# 匹配
# ---------------------------------------------------------------------------

def _evidence_sources(conn, project_id: int) -> list[dict]:
    """收集项目证据来源：[{label, text, codes}]，用于编号匹配与名称推断。"""
    sources: list[dict] = []

    def add(label: str, text: str) -> None:
        text = " ".join((text or "").split())
        if not text:
            return
        sources.append({"label": label, "text": text, "codes": _extract_codes(text)})

    drawing_columns = {row[1] for row in conn.execute("PRAGMA table_info(drawings)")}
    for drawing in _rows(conn, "SELECT * FROM drawings WHERE project_id=? ORDER BY id", (project_id,)):
        text = drawing.get("summary") or ""
        if "text_excerpt" in drawing_columns and drawing.get("text_excerpt"):
            text = f"{text} {drawing['text_excerpt']}"
        name = drawing.get("name") or "图纸"
        fmt = (drawing.get("fmt") or "").upper()
        category = drawing.get("category") or ""
        if fmt in ("DWG", "DXF"):
            label = f"附件3施工图:{name}.{fmt.lower()}"
        elif "说明书" in category or fmt == "PDF":
            label = f"附件1说明书:{name}"
        elif "清册" in category:
            label = f"附件2材料清册:{name}"
        else:
            label = f"图纸:{name}" + (f".{fmt.lower()}" if fmt else "")
        add(label, text)

    for line in _rows(conn, "SELECT * FROM model_lines WHERE project_id=? ORDER BY id", (project_id,)):
        text = " ".join(str(line.get(key) or "") for key in
                        ("name", "voltage", "conductor", "pole_type", "start_point", "end_point", "note"))
        add(f"模型线路:{line.get('name') or '线路'}", text)
    for equip in _rows(conn, "SELECT * FROM model_equipment WHERE project_id=? ORDER BY id", (project_id,)):
        category = "设备" if equip.get("category") == "设备" else "主材"
        text = " ".join(str(equip.get(key) or "") for key in ("name", "spec", "note"))
        add(f"模型{category}:{equip.get('name') or ''}", text)
    for civil in _rows(conn, "SELECT * FROM model_civil WHERE project_id=? ORDER BY id", (project_id,)):
        add(f"模型土建:{civil.get('name') or ''}",
            " ".join(str(civil.get(key) or "") for key in ("name", "spec", "note")))
    for demolition in _rows(conn, "SELECT * FROM model_demolition WHERE project_id=? ORDER BY id", (project_id,)):
        add(f"模型拆除:{demolition.get('name') or ''}",
            " ".join(str(demolition.get(key) or "") for key in ("name", "spec", "note")))
    for quantity in _rows(conn, "SELECT * FROM quantities WHERE project_id=? ORDER BY seq, id", (project_id,)):
        add(f"工程量:{quantity.get('name') or ''}",
            " ".join(str(quantity.get(key) or "") for key in ("name", "spec", "basis", "note")))
    return sources


def _extract_codes(text: str) -> list[str]:
    """从证据文本中提取典设编号（去重、保持出现顺序）。"""
    seen: set[str] = set()
    codes: list[str] = []
    for match in CODE_RE.finditer(text or ""):
        raw = match.group(0).rstrip(".-_")
        normalized = normalize_code(raw)
        if normalized and normalized not in seen:
            seen.add(normalized)
            codes.append(raw)
    return codes


def _best_code_match(module_code: str, sources: list[dict]) -> tuple[int, list[str]]:
    """在证据来源中找模块的最佳编号匹配，返回 (最高分, 证据摘要列表)。"""
    best_score = 0
    best_hits: list[tuple[int, str, str]] = []  # (来源序号, 证据编号, 来源标签)
    for index, source in enumerate(sources):
        for evidence_code in source["codes"]:
            score = code_match(evidence_code, module_code)
            if score > best_score:
                best_score = score
                best_hits = [(index, evidence_code, source["label"])]
            elif score == best_score and score > 0:
                best_hits.append((index, evidence_code, source["label"]))
    evidence_lines: list[str] = []
    seen_labels: set[str] = set()
    for _, evidence_code, label in best_hits:
        if label in seen_labels:
            continue
        seen_labels.add(label)
        evidence_lines.append(f"{label} → {evidence_code}")
        if len(evidence_lines) >= 3:
            break
    if len(evidence_lines) == 3:
        evidence_lines.append("等")
    return best_score, evidence_lines


def _best_name_inference(module_name: str, sources: list[dict]) -> tuple[int, str]:
    """名称推断：返回 (最长公共子串长度, 证据摘要)。"""
    best_length = 0
    best_text = ""
    for source in sources:
        length, substring = _longest_common_substring(module_name, source["text"])
        if length > best_length:
            best_length = length
            best_text = f"{source['label']}（名称匹配：{substring}）"
    return best_length, best_text


def match_project(conn, project_id: int, user: dict | None = None) -> dict:
    """对项目执行典设匹配：重写 typdesign_matches，保留已确认/已忽略状态，写审计。"""
    user = _user_dict(user)
    modules = _rows(conn, "SELECT * FROM typdesign_modules WHERE project_id=? ORDER BY id", (project_id,))
    existing = {
        row["code"]: row
        for row in _rows(conn, "SELECT * FROM typdesign_matches WHERE project_id=?", (project_id,))
    }
    sources = _evidence_sources(conn, project_id)
    timestamp = now()
    rows: list[dict] = []
    for module in modules:
        code = module["code"]
        score, evidence_lines = _best_code_match(code, sources)
        match_type, confidence = _classify_score(score)
        evidence = ""
        if score >= 76:
            evidence = "；".join(evidence_lines)[:200]
        else:
            name_length, name_evidence = _best_name_inference(module.get("name") or "", sources)
            if name_length >= NAME_MATCH_MIN:
                match_type, confidence = MATCH_TYPE_NAME, CONF_LOW
                evidence = name_evidence[:200]
            else:
                match_type, confidence = MATCH_TYPE_NONE, ""
                evidence = ""
        previous = existing.get(code) or {}
        previous_status = previous.get("status") or ""
        if previous_status in (STATUS_CONFIRMED, STATUS_IGNORED):
            # 重新匹配时沿用人工确认/忽略结果
            status = previous_status
        elif match_type == MATCH_TYPE_NONE:
            # 完全未匹配的目录模块单独标记，不计入“待确认”
            status = STATUS_UNMATCHED
        else:
            status = STATUS_PENDING
        rows.append({
            "module_id": module.get("id"),
            "code": code,
            "name": module.get("name") or "",
            "volume": module.get("volume") or "",
            "category": module.get("category") or "",
            "unit": module.get("unit") or "",
            "file_path": module.get("file_path") or "",
            "match_type": match_type,
            "confidence": confidence,
            "evidence": evidence,
            "applied": 1 if status == STATUS_CONFIRMED else 0,
            "status": status,
            "qty": float(previous.get("qty") or 0),
            "note": previous.get("note") or "",
            "updated_at": timestamp,
        })
    conn.execute("DELETE FROM typdesign_matches WHERE project_id=?", (project_id,))
    for row in rows:
        conn.execute(
            "INSERT INTO typdesign_matches (project_id, module_id, code, name, volume, category, unit, file_path, "
            "match_type, confidence, evidence, applied, status, qty, note, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, row["module_id"], row["code"], row["name"], row["volume"],
             row["category"], row["unit"], row["file_path"], row["match_type"], row["confidence"],
             row["evidence"], row["applied"], row["status"], row["qty"], row["note"],
             row["updated_at"]),
        )
    summary = _count_rows(rows)
    _audit(conn, project_id, user, "执行典型设计匹配",
           (f"目录 {summary['modules']} 条，匹配 {summary['matched']} 条"
            f"（高 {summary['high']} / 中 {summary['medium']} / 低 {summary['low']}），"
            f"未匹配 {summary['unmatched']} 条"))
    conn.commit()
    return {
        "modules": summary["modules"],
        "matched": summary["matched"],
        "high": summary["high"],
        "medium": summary["medium"],
        "low": summary["low"],
        "pending": summary["pending"],
        "confirmed": summary["confirmed"],
        "ignored": summary["ignored"],
        "unmatched": summary["unmatched"],
    }


def _count_rows(rows: list[dict]) -> dict:
    """对匹配行做统计计数；待确认只统计 status='待确认'，未匹配单独计数。"""
    matched_rows = [row for row in rows if row["match_type"] != MATCH_TYPE_NONE]
    return {
        "modules": len(rows),
        "matched": len(matched_rows),
        "high": sum(1 for row in matched_rows if row["confidence"] == CONF_HIGH),
        "medium": sum(1 for row in matched_rows if row["confidence"] == CONF_MEDIUM),
        "low": sum(1 for row in matched_rows if row["confidence"] == CONF_LOW),
        "pending": sum(1 for row in rows if row["status"] == STATUS_PENDING),
        "confirmed": sum(1 for row in rows if row["status"] == STATUS_CONFIRMED),
        "ignored": sum(1 for row in rows if row["status"] == STATUS_IGNORED),
        "unmatched": sum(1 for row in rows
                         if row["status"] == STATUS_UNMATCHED
                         or row["match_type"] == MATCH_TYPE_NONE),
    }


# ---------------------------------------------------------------------------
# 统计 / 查询
# ---------------------------------------------------------------------------

_IMPORT_SELECT = (
    "SELECT id, filename, modules, imported_by, imported_at FROM typdesign_imports "
    "WHERE project_id=? ORDER BY id DESC"
)

_MATCH_SELECT = (
    "SELECT id, module_id, code, name, volume, category, unit, file_path, match_type, confidence, "
    "evidence, applied, status, qty, note, updated_at FROM typdesign_matches "
    "WHERE project_id=? ORDER BY id"
)


def _summary_from_db(conn, project_id: int) -> dict:
    imports = _rows(conn, _IMPORT_SELECT, (project_id,))
    matches = _rows(conn, _MATCH_SELECT, (project_id,))
    module_count = _one(
        conn, "SELECT COUNT(*) AS n FROM typdesign_modules WHERE project_id=?", (project_id,)
    )["n"]
    summary = _count_rows(matches)
    modules = int(module_count)
    matched = summary["matched"]
    rate = round(matched / modules * 100.0, 1) if modules else 0.0
    last_run = max((row.get("updated_at") or "" for row in matches), default="")
    return {
        "imports": [
            {
                "filename": row.get("filename") or "",
                "modules": int(row.get("modules") or 0),
                "imported_by": row.get("imported_by"),
                "imported_at": row.get("imported_at") or "",
            }
            for row in imports
        ],
        "modules": modules,
        "matched": matched,
        "high": summary["high"],
        "medium": summary["medium"],
        "low": summary["low"],
        "pending": summary["pending"],
        "confirmed": summary["confirmed"],
        "ignored": summary["ignored"],
        "unmatched": summary["unmatched"],
        "rate": rate,
        "matches": [
            {
                "id": row.get("id"),
                "code": row.get("code") or "",
                "name": row.get("name") or "",
                "volume": row.get("volume") or "",
                "category": row.get("category") or "",
                "unit": row.get("unit") or "",
                "file_path": row.get("file_path") or "",
                "match_type": row.get("match_type") or "",
                "confidence": row.get("confidence") or "",
                "evidence": row.get("evidence") or "",
                "status": row.get("status") or STATUS_PENDING,
                "qty": float(row.get("qty") or 0),
                "note": row.get("note") or "",
            }
            for row in matches
        ],
        "last_run": last_run,
    }


def typdesign_summary(conn, project_id: int) -> dict:
    """典设匹配汇总（页面统计卡 + 明细表）。"""
    return _summary_from_db(conn, project_id)


def typdesign_stats(conn, project_id: int) -> dict:
    """工作台/项目页用的典设统计（待确认只含 status='待确认'，未匹配单独计数）。"""
    summary = _summary_from_db(conn, project_id)
    return {
        "modules": summary["modules"],
        "matched": summary["matched"],
        "confirmed": summary["confirmed"],
        "pending": summary["pending"],
        "unmatched": summary["unmatched"],
        "rate": summary["rate"],
    }


def confirmed_modules(conn, project_id: int) -> list[dict]:
    """已确认应用的典设模块（供建模页/算量页引用）。"""
    rows = _rows(
        conn,
        "SELECT id, module_id, code, name, volume, category, unit, file_path, match_type, confidence, "
        "evidence, qty, note, updated_at FROM typdesign_matches "
        "WHERE project_id=? AND status=? ORDER BY code",
        (project_id, STATUS_CONFIRMED),
    )
    return rows


# ---------------------------------------------------------------------------
# 人工确认 / 忽略
# ---------------------------------------------------------------------------

def confirm_match(conn, project_id: int, match_id: int, user: dict | None = None,
                  qty: float = 0, note: str = "", status: str = STATUS_CONFIRMED) -> tuple[bool, str]:
    """确认应用 / 忽略 / 重置某条典设匹配，写 updated_at 与审计。"""
    if status not in (STATUS_CONFIRMED, STATUS_IGNORED, STATUS_PENDING):
        return False, "状态无效（应为“已确认”“已忽略”或“待确认”）"
    row = _one(
        conn,
        "SELECT * FROM typdesign_matches WHERE id=? AND project_id=?",
        (match_id, project_id),
    )
    if row is None:
        return False, "典设匹配项不存在"
    try:
        qty_value = float(qty or 0)
    except (TypeError, ValueError):
        qty_value = 0.0
    if status == STATUS_CONFIRMED and qty_value <= 0:
        qty_value = float(row.get("qty") or 0) or 1.0
    elif status == STATUS_PENDING:
        qty_value = 0.0
    applied = 1 if status == STATUS_CONFIRMED else 0
    user = _user_dict(user)
    conn.execute(
        "UPDATE typdesign_matches SET status=?, qty=?, note=?, applied=?, updated_at=? "
        "WHERE id=? AND project_id=?",
        (status, qty_value, (note or "")[:200], applied, now(), match_id, project_id),
    )
    code = row.get("code") or ""
    name = row.get("name") or ""
    if status == STATUS_CONFIRMED:
        action = "确认典型设计应用"
        detail = f"{code} {name}；应用数量 {qty_value:g}"
    elif status == STATUS_IGNORED:
        action = "忽略典型设计匹配"
        detail = f"{code} {name}"
    else:
        action = "重置典型设计匹配"
        detail = f"{code} {name} 恢复待确认"
    if note:
        detail += f"；备注：{note}"
    _audit(conn, project_id, user, action, detail)
    conn.commit()
    if status == STATUS_CONFIRMED:
        return True, f"已确认应用：{code} {name}（数量 {qty_value:g}）"
    if status == STATUS_IGNORED:
        return True, f"已忽略匹配项：{code} {name}"
    return True, f"已重置为待确认：{code} {name}"


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

EXPORT_HEADER = [
    "序号", "卷册", "类别", "模块编号", "模块名称", "模块单位", "图纸文件",
    "匹配方式", "置信度", "证据摘要", "状态", "应用数量", "备注",
]


def export_rows(conn, project_id: int) -> list[list]:
    """导出匹配清单的行（含表头行），供 Web 层用 openpyxl 写 xlsx。"""
    modules = _rows(
        conn, "SELECT * FROM typdesign_modules WHERE project_id=? ORDER BY volume, id", (project_id,)
    )
    matches = {
        row["code"]: row
        for row in _rows(conn, "SELECT * FROM typdesign_matches WHERE project_id=?", (project_id,))
    }
    rows: list[list] = [list(EXPORT_HEADER)]
    for index, module in enumerate(modules, 1):
        match = matches.get(module["code"]) or {}
        rows.append([
            index,
            module.get("volume") or "",
            module.get("category") or match.get("category") or "",
            module.get("code") or "",
            module.get("name") or match.get("name") or "",
            module.get("unit") or match.get("unit") or "",
            module.get("file_path") or "",
            match.get("match_type") or MATCH_TYPE_NONE,
            match.get("confidence") or "",
            match.get("evidence") or "",
            match.get("status") or STATUS_PENDING,
            float(match.get("qty") or 0),
            match.get("note") or "",
        ])
    return rows
