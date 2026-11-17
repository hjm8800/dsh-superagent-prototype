"""组装工作流：扫描目录 → 识别工作簿 → 合并成 DocSet → 逐类对比 → 生成结论。

入口：build_comparison(old_dir, new_dir, project_name=None) -> Comparison dict
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import DOC_CATEGORIES, SIDES
from .diff import (
    RISK_HIGH,
    RISK_MID,
    STATUS_NEW,
    STATUS_QTY_DOWN,
    STATUS_QTY_UP,
    STATUS_REMOVED,
    STATUS_SAME,
    STATUS_SPEC,
    STATUS_UNIT,
    compare_items,
    compare_texts,
    item_amount,
    merge_summaries,
    summarize,
    top_diffs,
)
from .normalize import (
    format_delta,
    format_money,
    format_number,
    format_ratio,
    normalize_name,
    normalize_unit,
)
from .readers import excel_utils

WORKBOOK_SUFFIXES = {".xls", ".xlsx", ".xlsm"}

# reader 延迟导入，避免模块未就绪时影响其它模块导入
def _settlement_reader():
    from .readers import settlement

    return settlement


def _report_reader():
    from .readers import report

    return report


def _file_md5(path: Path) -> str | None:
    try:
        digest = hashlib.md5()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def list_workbook_files(directory: str | Path, files: list[str | Path] | None = None) -> list[Path]:
    """列出目录下可解析的工作簿文件（忽略临时/隐藏文件），按内容哈希去重。

    files 不为空时只使用给定文件列表（batch 场景按项目分组后传入）。
    """
    directory = Path(directory)
    if files is not None:
        candidates = [Path(item) for item in files]
    else:
        if not directory.exists():
            raise FileNotFoundError(f"目录不存在：{directory}")
        candidates = [
            path for path in sorted(directory.iterdir())
            if path.is_file() and not path.name.startswith("~$") and not path.name.startswith(".")
            and path.suffix.lower() in WORKBOOK_SUFFIXES
        ]
    result: list[Path] = []
    seen_hash: dict[str, Path] = {}
    for path in candidates:
        if path.suffix.lower() not in WORKBOOK_SUFFIXES:
            continue
        digest = _file_md5(path)
        if digest and digest in seen_hash:
            continue
        if digest:
            seen_hash[digest] = path
        result.append(path)
    return result


def classify_file(path: str | Path) -> str | None:
    """按工作表特征识别文件类型：settlement（结算书） / report（竣工报告）。"""
    path = Path(path)
    try:
        sheet_names = excel_utils.list_sheets(path)
    except Exception:
        return None
    settlement = _settlement_reader()
    report = _report_reader()
    try:
        if settlement.matches(sheet_names, path.name):
            return "settlement"
        if report.matches(sheet_names, path.name):
            return "report"
    except Exception:
        return None
    return None


def _doc_signature(doc: dict) -> str:
    """解析结果内容签名：用于识别“同一文件被复制/另存为两个文件名”的重复资料。"""
    import hashlib as _hashlib
    import json as _json

    def clean_items(items):
        cleaned = []
        for item in items or []:
            cleaned.append({key: value for key, value in item.items() if key not in ("source", "raw")})
        return cleaned

    payload = {
        "kind": doc.get("kind"),
        "totals": doc.get("totals"),
        "categories": doc.get("categories"),
        "materials": doc.get("materials"),
        "items": clean_items(doc.get("items")),
        "texts": doc.get("texts"),
        "tables": doc.get("tables"),
    }
    raw = _json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return _hashlib.md5(raw.encode("utf-8")).hexdigest()


def _dedupe_docs(docs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for doc in docs:
        signature = _doc_signature(doc)
        if signature in seen:
            continue
        seen.add(signature)
        result.append(doc)
    return result


def load_side(directory: str | Path, files: list[str | Path] | None = None) -> dict:
    """读取一个版本的文件集合：完整结算书优先，只有组合件汇总表时作为主口径。"""
    directory = Path(directory)
    loaded: dict[str, Any] = {
        "directory": str(directory), "settlement": [], "settlement_assembly": [],
        "report": [], "unknown": [], "errors": [],
    }
    for path in list_workbook_files(directory, files=files):
        kind = classify_file(path)
        if kind is None:
            loaded["unknown"].append(path.name)
            continue
        try:
            if kind == "settlement":
                loaded["settlement"].append(_settlement_reader().load(path))
            else:
                loaded["report"].append(_report_reader().load(path))
        except Exception as exc:  # 单个文件失败不影响整体流程
            loaded["errors"].append(f"{path.name}: {exc}")
    # 同一文件被复制/另存为多个文件名时，按解析内容去重
    loaded["settlement"] = _dedupe_docs(loaded["settlement"])
    loaded["report"] = _dedupe_docs(loaded["report"])
    # 完整结算书（表一/表三甲）优先；仅组合件汇总表的工作簿作为补充/回退
    full = [doc for doc in loaded["settlement"] if (doc.get("meta") or {}).get("is_full", True)]
    assembly = [doc for doc in loaded["settlement"] if not (doc.get("meta") or {}).get("is_full", True)]
    loaded["settlement_assembly"] = assembly
    loaded["settlement"] = full if full else assembly
    return loaded


def _merge_totals(totals_list: list[dict]) -> dict:
    """合并多个工作簿的 totals（同名字段求和；final_amount 取最后非空）。"""
    merged: dict[str, Any] = {}
    if not totals_list:
        return merged
    for key in totals_list[0].keys():
        values = [item.get(key) for item in totals_list if item.get(key) is not None]
        if not values:
            continue
        if key == "final_amount":
            merged[key] = values[-1]
        elif isinstance(values[0], (int, float)):
            merged[key] = sum(float(value) for value in values)
        else:
            merged[key] = values[-1]
    return merged


def _merge_warnings(loaded_docs: list[dict]) -> list[str]:
    warnings: list[str] = []
    for doc in loaded_docs:
        for warning in doc.get("warnings") or []:
            if warning not in warnings:
                warnings.append(warning)
    return warnings


def _merge_items(loaded_docs: list[dict]) -> list[dict]:
    items: list[dict] = []
    for doc in loaded_docs:
        items.extend(doc.get("items") or [])
    return items


def build_settlement_docset(loaded: dict, side: str) -> dict:
    docs = loaded.get("settlement") or []
    meta_docs = [doc.get("meta") or {} for doc in docs]
    format_name = next((meta.get("format") for meta in meta_docs if meta.get("format")), "full")
    is_full = all(meta.get("is_full", True) for meta in meta_docs) if meta_docs else True
    unit_scale = next((meta.get("unit_scale") for meta in meta_docs if meta.get("unit_scale")), 1.0)
    return {
        "category": "settlement",
        "side": side,
        "files": meta_docs,
        "items": _merge_items(docs),
        "totals": _merge_totals([doc.get("totals") or {} for doc in docs]),
        "categories": [category for doc in docs for category in (doc.get("categories") or [])],
        "texts": [],
        "tables": [],
        "warnings": _merge_warnings(docs),
        "meta": {
            "files": [meta.get("file", "") for meta in meta_docs],
            "format": format_name,
            "is_full": is_full,
            "unit_scale": unit_scale,
        },
    }


def build_material_docset(loaded: dict, side: str) -> dict:
    docs = loaded.get("settlement") or []
    all_items: list[dict] = []
    totals_list: list[dict] = []
    warnings = _merge_warnings(docs)
    for doc in docs:
        materials = doc.get("materials") or {}
        all_items.extend(materials.get("items") or [])
        if materials.get("totals"):
            totals_list.append(materials["totals"])
    # 主口径只看乙供未计价材料；甲供清单单独作为补充口径，避免把“原版没有甲供清单”误报成新增 96 项
    primary_items = [item for item in all_items if (item.get("supply") or "") != "甲供"]
    return {
        "category": "material",
        "side": side,
        "files": [doc.get("meta", {}) for doc in docs],
        "items": primary_items,
        "all_items": all_items,
        "totals": _merge_totals(totals_list),
        "categories": [],
        "texts": [],
        "tables": [],
        "warnings": warnings,
        "meta": {"files": [doc.get("meta", {}).get("file", "") for doc in docs]},
    }


def _settlement_comparable_items(docset: dict) -> list[dict]:
    """审核结算逐项对比只取可计量的项目行，取费构成行（费用）由汇总表对比。"""
    return [item for item in docset.get("items") or [] if item.get("item_type") != "费用"]


def _item_type_items(docset: dict, item_type: str) -> list[dict]:
    return [item for item in docset.get("items") or [] if item.get("item_type") == item_type]


def build_boq_docset(loaded: dict, side: str) -> dict:
    docs = loaded.get("report") or []
    texts: list[dict] = []
    tables: list[dict] = []
    for doc in docs:
        texts.extend(doc.get("texts") or [])
        tables.extend(doc.get("tables") or [])
    grounding_items = [item for item in _merge_items(docs) if item.get("item_type") == "接地"]
    grounding_amount = None
    return {
        "category": "boq",
        "side": side,
        "files": [doc.get("meta", {}) for doc in docs],
        "items": _merge_items(docs),
        "totals": {
            "接地工程条数": len(grounding_items),
            "文本段数": len(texts),
            "结构化表数": len(tables),
        },
        "categories": [],
        "texts": texts,
        "tables": tables,
        "warnings": _merge_warnings(docs),
        "meta": {"files": [doc.get("meta", {}).get("file", "") for doc in docs]},
    }


def _supply_items(material_docset: dict, supply: str) -> list[dict]:
    source = material_docset.get("all_items") or material_docset.get("items") or []
    return [item for item in source if (item.get("supply") or "") == supply]


def _clean_project_token(stem: str) -> str:
    """去掉文件名里的年份、类别后缀，得到项目名候选。"""
    text = stem
    text = re.sub(r"[-_\s]*\d{2,4}\s*年(?:度)?", " ", text)
    for _ in range(5):
        new_text = re.sub(
            r"[-_\s]*(旧年|新年|旧版|新版|送审|结算书|竣工报告|竣工资料|结算|资料|对比分析?|原版|新版|清单|旧|新)\s*$",
            " ", text,
        )
        if new_text == text:
            break
        text = new_text
    text = re.sub(r"[-_\s]+", " ", text)
    return text.strip(" -_")


def _guess_project_name(old_files: list[Path], new_files: list[Path]) -> str:
    """从文件名共同前缀推断项目名（去掉年份/送审等尾巴）。"""
    names = [_clean_project_token(path.stem) for path in old_files + new_files]
    names = [name for name in names if name]
    if not names:
        return ""
    prefix = os.path.commonprefix(names) if len(names) > 1 else names[0]
    prefix = re.sub(r"[-_\s]*\d{1,4}$", "", prefix)
    return prefix.strip(" -_（）()")


def _settlement_totals_pair(old_doc: dict, new_doc: dict) -> dict:
    old_totals = old_doc.get("totals") or {}
    new_totals = new_doc.get("totals") or {}

    def value(totals: dict, key: str) -> float | None:
        value = totals.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    old_final = value(old_totals, "final_amount")
    new_final = value(new_totals, "final_amount")
    delta = (new_final - old_final) if (old_final is not None and new_final is not None) else None
    return {
        "old_final": old_final,
        "new_final": new_final,
        "delta": delta,
        "ratio": (delta / old_final) if (delta is not None and old_final) else None,
        "old_equipment": value(old_totals, "设备检修费"),
        "new_equipment": value(new_totals, "设备检修费"),
        "old_vat": value(old_totals, "可抵扣增值税"),
        "new_vat": value(new_totals, "可抵扣增值税"),
        "old_price_diff": value(old_totals, "编制基准期价差"),
        "new_price_diff": value(new_totals, "编制基准期价差"),
        "old_parts": value(old_totals, "配件购置费"),
        "new_parts": value(new_totals, "配件购置费"),
        "new_discount_final": value(new_totals, "下浮后送审金额"),
        "new_ag_parts": value(new_totals, "甲供配件材料费"),
        "new_yigong_material": value(new_totals, "乙供物料费"),
    }


def _material_totals_pair(old_doc: dict, new_doc: dict) -> dict:
    old_totals = old_doc.get("totals") or {}
    new_totals = new_doc.get("totals") or {}

    def value(totals: dict, key: str) -> float | None:
        value = totals.get(key)
        return float(value) if isinstance(value, (int, float)) else None

    return {
        "old_yigong": value(old_totals, "乙供合计"),
        "new_yigong": value(new_totals, "乙供合计"),
        "old_ag": value(old_totals, "甲供合计"),
        "new_ag": value(new_totals, "甲供合计"),
        "old_parts": value(old_totals, "配件合计"),
        "new_parts": value(new_totals, "配件合计"),
        "old_ag_parts": value(old_totals, "甲供配件合计"),
        "new_ag_parts": value(new_totals, "甲供配件合计"),
    }


def _boq_grounding_totals(docset: dict) -> dict:
    totals: dict[str, float] = {}
    for item in docset.get("items") or []:
        if item.get("item_type") != "接地":
            continue
        name = re.sub(r"\s+", "", item.get("name") or "")
        qty = item.get("qty")
        if not name or qty is None:
            continue
        totals[name] = totals.get(name, 0.0) + float(qty)
    return totals


def _diff_summary_of_items(old_items: list[dict], new_items: list[dict], category: str) -> dict:
    diffs = compare_items(old_items, new_items, category)
    return {"diffs": diffs, "summary": summarize(diffs)}


def _price_and_amount(item: dict | None) -> tuple[float | None, float | None]:
    if not item:
        return None, None
    amount = item_amount(item)
    price = item.get("unit_price")
    qty = item.get("qty")
    if price is None and amount is not None and qty not in (None, 0):
        price = amount / qty
    return price, amount


def _category_amount(category: dict) -> float | None:
    for key in ("合计", "检修费", "amount"):
        value = category.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _effect_stats(diffs: list[dict], predicate, label: str) -> dict:
    """按条目子集做量差/价差/新增/删除拆分。"""
    stats = {
        "label": label,
        "qty_effect": 0.0, "price_effect": 0.0, "added": 0.0, "removed": 0.0,
        "other": 0.0, "count": 0, "computed": 0, "causes": [],
    }
    for diff in diffs:
        old, new = diff.get("old"), diff.get("new")
        if not old and not new:
            continue
        if not predicate(diff):
            continue
        status = diff.get("status")
        old_price, old_amount = _price_and_amount(old)
        new_price, new_amount = _price_and_amount(new)
        stats["count"] += 1
        qty_effect_i = price_effect_i = None
        effect = ""
        if status == STATUS_NEW:
            stats["added"] += new_amount or 0.0
            effect = "新增"
        elif status == STATUS_REMOVED:
            stats["removed"] += old_amount or 0.0
            effect = "删除"
        else:
            same_unit = normalize_unit((old or {}).get("unit", "")) == normalize_unit((new or {}).get("unit", ""))
            qty_delta = diff.get("qty_delta")
            if (
                same_unit and qty_delta is not None and old_price is not None
                and old_amount is not None and new_amount is not None
            ):
                qty_effect_i = qty_delta * old_price
                price_effect_i = new_amount - old_amount - qty_effect_i
                stats["qty_effect"] += qty_effect_i
                stats["price_effect"] += price_effect_i
                stats["computed"] += 1
                effect = "量差主导" if abs(qty_effect_i) >= abs(price_effect_i) else "价差主导"
            elif old_amount is not None and new_amount is not None:
                stats["other"] += new_amount - old_amount
                effect = "单位/规格/其他"
        if effect and (status in (STATUS_NEW, STATUS_REMOVED) or qty_effect_i is not None):
            stats["causes"].append({
                "name": diff.get("name", ""),
                "spec": diff.get("spec", ""),
                "unit": diff.get("unit", ""),
                "status": status,
                "risk": diff.get("risk", ""),
                "old_qty": (old or {}).get("qty"),
                "new_qty": (new or {}).get("qty"),
                "qty_delta": diff.get("qty_delta"),
                "qty_ratio": diff.get("qty_ratio"),
                "qtys_effect": qty_effect_i,
                "price_effect": price_effect_i,
                "amount_delta": diff.get("amount_delta"),
                "effect_kind": f"{label}{effect}",
            })
    stats["net"] = (
        stats["qty_effect"] + stats["price_effect"] + stats["added"] - stats["removed"] + stats["other"]
    )
    return stats


def construction_fee_analysis(comparison: dict) -> dict:
    """施工费差异成因：按定额直接费/乙供材料/甲供材料/配件/取费残差拆分。"""
    settlement = comparison["documents"]["settlement"]
    material = comparison["documents"]["material"]
    totals = _settlement_totals_pair(settlement["old"], settlement["new"])
    old_fee = totals.get("old_equipment") if totals.get("old_equipment") is not None else totals.get("old_final")
    new_fee = totals.get("new_equipment") if totals.get("new_equipment") is not None else totals.get("new_final")
    fee_delta = (new_fee - old_fee) if (old_fee is not None and new_fee is not None) else None
    diffs = settlement.get("diffs") or []

    def is_direct(diff: dict) -> bool:
        item_type = ((diff.get("new") or {}).get("item_type") or (diff.get("old") or {}).get("item_type") or "")
        return item_type in {"定额", "组合件"}

    def supply_of(diff: dict) -> str:
        return ((diff.get("new") or {}).get("supply") or (diff.get("old") or {}).get("supply") or "")

    def is_yigong_material(diff: dict) -> bool:
        item_type = ((diff.get("new") or {}).get("item_type") or (diff.get("old") or {}).get("item_type") or "")
        return item_type in {"主材", "材料"} and supply_of(diff) != "甲供"

    def is_agong_material(diff: dict) -> bool:
        item_type = ((diff.get("new") or {}).get("item_type") or (diff.get("old") or {}).get("item_type") or "")
        return item_type in {"主材", "材料"} and supply_of(diff) == "甲供"

    def is_parts(diff: dict) -> bool:
        item_type = ((diff.get("new") or {}).get("item_type") or (diff.get("old") or {}).get("item_type") or "")
        return item_type == "配件"

    direct = _effect_stats(diffs, is_direct, "定额")
    yigong = _effect_stats(diffs, is_yigong_material, "乙供材料")
    agong = _effect_stats(diffs, is_agong_material, "甲供材料")
    parts = _effect_stats(diffs, is_parts, "配件")

    direct_old = direct_new = 0.0
    for diff in diffs:
        if is_direct(diff):
            direct_old += item_amount(diff.get("old")) or 0.0
            direct_new += item_amount(diff.get("new")) or 0.0

    in_fee_effect = direct["net"] + yigong["net"]
    residual = (fee_delta - in_fee_effect) if fee_delta is not None else None
    causes = direct["causes"] + yigong["causes"]
    causes.sort(key=lambda item: abs(item.get("amount_delta") or 0.0), reverse=True)
    all_causes = direct["causes"] + yigong["causes"] + agong["causes"] + parts["causes"]
    top_added = sorted(
        [cause for cause in all_causes if cause.get("status") == STATUS_NEW],
        key=lambda item: abs(item.get("amount_delta") or 0.0), reverse=True,
    )[:5]
    top_removed = sorted(
        [cause for cause in all_causes if cause.get("status") == STATUS_REMOVED],
        key=lambda item: abs(item.get("amount_delta") or 0.0), reverse=True,
    )[:5]

    old_yigong = (material["old"].get("totals") or {}).get("乙供合计")
    new_yigong = (material["new"].get("totals") or {}).get("乙供合计")
    old_ag = (material["old"].get("totals") or {}).get("甲供合计")
    new_ag = (material["new"].get("totals") or {}).get("甲供合计")

    old_categories = settlement["old"].get("categories") or []
    new_categories = settlement["new"].get("categories") or []
    old_map: dict[str, float] = {}
    for category in old_categories:
        amount = _category_amount(category)
        name = normalize_name(category.get("name") or category.get("section") or "")
        if name and amount is not None:
            old_map[name] = amount
    new_map: dict[str, float] = {}
    for category in new_categories:
        amount = _category_amount(category)
        name = normalize_name(category.get("name") or category.get("section") or "")
        if name and amount is not None:
            new_map[name] = amount
    section_rows = []
    for name in set(old_map) | set(new_map):
        old_value = old_map.get(name)
        new_value = new_map.get(name)
        delta = (new_value - old_value) if (old_value is not None and new_value is not None) else None
        if delta is None or abs(delta) < 1:
            continue
        section_rows.append({
            "name": name,
            "old": old_value,
            "new": new_value,
            "delta": delta,
            "ratio": (delta / old_value) if old_value else None,
        })
    section_rows.sort(key=lambda row: abs(row["delta"]), reverse=True)

    fmt = ((settlement["old"].get("meta") or {}).get("format")
           or (settlement["new"].get("meta") or {}).get("format") or "full")
    components = {
        "编制基准期价差": (totals.get("old_price_diff"), totals.get("new_price_diff")),
        "可抵扣增值税": (totals.get("old_vat"), totals.get("new_vat")),
        "甲供配件材料费": (totals.get("old_ag_parts"), totals.get("new_ag_parts")),
        "下浮后送审金额": (None, totals.get("new_discount_final")),
        "乙供未计价材料": (old_yigong, new_yigong),
        "甲供未计价材料": (old_ag, new_ag),
    }
    component_rows = []
    for name, (old_value, new_value) in components.items():
        if old_value is None and new_value is None:
            continue
        delta = (new_value - old_value) if (old_value is not None and new_value is not None) else None
        component_rows.append({"name": name, "old": old_value, "new": new_value, "delta": delta})

    return {
        "old_fee": old_fee,
        "new_fee": new_fee,
        "fee_delta": fee_delta,
        "fee_ratio": (fee_delta / old_fee) if (fee_delta is not None and old_fee) else None,
        "direct": direct,
        "yigong_material": yigong,
        "agong_material": agong,
        "parts": parts,
        "direct_old": direct_old,
        "direct_new": direct_new,
        "in_fee_effect": in_fee_effect,
        "residual_vs_effects": residual,
        "item_causes": causes[:20],
        "top_added": top_added,
        "top_removed": top_removed,
        "sections": section_rows[:12],
        "components": component_rows,
        "old_yigong": old_yigong,
        "new_yigong": new_yigong,
        "yigong_delta": (new_yigong - old_yigong) if (old_yigong is not None and new_yigong is not None) else None,
        "format": fmt,
    }


def quantity_analysis(comparison: dict) -> dict:
    """工程量差异：数量变化 TOP、新增/删除、单位/规格变化、竣工工程量勾稽。"""
    settlement = comparison["documents"]["settlement"]
    boq = comparison["documents"]["boq"]
    diffs = settlement.get("diffs") or []
    qty_diffs = [diff for diff in diffs if diff.get("status") in (STATUS_QTY_UP, STATUS_QTY_DOWN)]
    qty_diffs.sort(key=lambda diff: abs(diff.get("qty_delta") or 0.0), reverse=True)
    added = [diff for diff in diffs if diff.get("status") == STATUS_NEW]
    removed = [diff for diff in diffs if diff.get("status") == STATUS_REMOVED]
    unit_changes = [diff for diff in diffs if diff.get("status") == STATUS_UNIT]
    spec_changes = [diff for diff in diffs if diff.get("status") == STATUS_SPEC]

    def brief(diff: dict) -> dict:
        old, new = diff.get("old") or {}, diff.get("new") or {}
        return {
            "name": diff.get("name", ""),
            "spec": diff.get("spec", ""),
            "unit": diff.get("unit", ""),
            "status": diff.get("status", ""),
            "old_qty": old.get("qty"),
            "new_qty": new.get("qty"),
            "qty_delta": diff.get("qty_delta"),
            "qty_ratio": diff.get("qty_ratio"),
            "amount_delta": diff.get("amount_delta"),
        }

    added.sort(key=lambda diff: abs(diff.get("amount_delta") or 0.0), reverse=True)
    removed.sort(key=lambda diff: abs(diff.get("amount_delta") or 0.0), reverse=True)
    grounding_old = _boq_grounding_totals(boq["old"])
    grounding_new = _boq_grounding_totals(boq["new"])
    return {
        "qty_changed_count": len(qty_diffs),
        "qty_increase_count": sum(1 for diff in qty_diffs if diff.get("status") == STATUS_QTY_UP),
        "qty_decrease_count": sum(1 for diff in qty_diffs if diff.get("status") == STATUS_QTY_DOWN),
        "added_count": len(added),
        "removed_count": len(removed),
        "unit_change_count": len(unit_changes),
        "spec_change_count": len(spec_changes),
        "top_qty_changes": [brief(diff) for diff in qty_diffs[:12]],
        "top_added": [brief(diff) for diff in added[:8]],
        "top_removed": [brief(diff) for diff in removed[:8]],
        "unit_changes": [brief(diff) for diff in unit_changes[:10]],
        "spec_changes": [brief(diff) for diff in spec_changes[:10]],
        "grounding": {"old": grounding_old, "new": grounding_new},
        "boq_item_counts": {
            "old": len(boq["old"].get("items") or []),
            "new": len(boq["new"].get("items") or []),
        },
    }


def analyze(comparison: dict) -> dict:
    """规则生成中文结论、风险清单、发现项。"""
    documents = comparison["documents"]
    settlement = documents["settlement"]
    material = documents["material"]
    boq = documents["boq"]

    conclusions: list[str] = []
    findings: list[str] = []
    risks: list[dict] = []

    # ---- 资料清单 ----
    old_files = (settlement["old"]["meta"] or {}).get("files", []) + (boq["old"]["meta"] or {}).get("files", [])
    new_files = (settlement["new"]["meta"] or {}).get("files", []) + (boq["new"]["meta"] or {}).get("files", [])
    conclusions.append(
        f"原版资料 {len(old_files)} 个文件：{'、'.join(Path(name).name for name in old_files) or '无'}；"
        f"新版资料 {len(new_files)} 个文件：{'、'.join(Path(name).name for name in new_files) or '无'}。"
    )

    # ---- 审核结算 ----
    settlement_totals = _settlement_totals_pair(settlement["old"], settlement["new"])
    s_summary = settlement["summary"]
    fee_analysis = construction_fee_analysis(comparison)
    old_equipment = settlement_totals["old_equipment"] if settlement_totals["old_equipment"] is not None else settlement_totals["old_final"]
    new_equipment = settlement_totals["new_equipment"] if settlement_totals["new_equipment"] is not None else settlement_totals["new_final"]
    if old_equipment is not None and new_equipment is not None:
        equipment_delta = new_equipment - old_equipment
        equipment_ratio = equipment_delta / old_equipment if old_equipment else None
        conclusions.append(
            f"审核结算（表一口径·设备检修费）：原版 {format_money(old_equipment)} 元 → "
            f"新版 {format_money(new_equipment)} 元，变化 {format_delta(equipment_delta)} 元"
            f"（{format_ratio(equipment_ratio)}）。"
        )
        settlement_totals["equipment_delta"] = equipment_delta
        settlement_totals["equipment_ratio"] = equipment_ratio

        if settlement_totals["new_discount_final"] is not None:
            discount_final = settlement_totals["new_discount_final"]
            discount_delta = discount_final - old_equipment
            conclusions.append(
                f"按下浮后送审口径：新版 {format_money(discount_final)} 元，较原版 "
                f"{format_delta(discount_delta)} 元（{format_ratio(discount_delta / old_equipment if old_equipment else None)}）；"
                f"新版下浮 {format_money(new_equipment - discount_final)} 元"
                f"（{format_ratio((new_equipment - discount_final) / new_equipment if new_equipment else None)}）。"
            )

        # 含配件完整口径（表二甲/送审书完整口径）；新版优先用表一“甲供配件材料费”原始值
        old_parts_total = (material["old"].get("totals") or {}).get("配件合计")
        new_parts_total = settlement_totals.get("new_ag_parts")
        if new_parts_total is None:
            new_parts_total = (material["new"].get("totals") or {}).get("配件合计")
        if old_parts_total is not None and new_parts_total is not None:
            complete_old = old_equipment + float(old_parts_total)
            complete_new = new_equipment + float(new_parts_total)
            complete_delta = complete_new - complete_old
            conclusions.append(
                f"含配件完整口径（设备检修费+配件购置费）：原版 {format_money(complete_old)} 元 → "
                f"新版 {format_money(complete_new)} 元，变化 {format_delta(complete_delta)} 元"
                f"（{format_ratio(complete_delta / complete_old if complete_old else None)}）。"
                "两版表一对配件/甲供配件的列示口径不一致，对外口径需统一后使用。"
            )
            settlement_totals["old_complete"] = complete_old
            settlement_totals["new_complete"] = complete_new
            settlement_totals["complete_delta"] = complete_delta
            settlement_totals["complete_ratio"] = complete_delta / complete_old if complete_old else None

        conclusions.append(
            f"审核结算明细（不含取费构成行）共 {s_summary['count']} 项，其中变化 {s_summary['changed_count']} 项，"
            f"高风险 {s_summary['risk_counts'].get(RISK_HIGH, 0)} 项、中风险 {s_summary['risk_counts'].get(RISK_MID, 0)} 项。"
        )

        top_causes = fee_analysis.get("item_causes") or []
        cause_parts = [
            f"{cause['name']}（{format_delta(cause['amount_delta'])} 元，{cause['effect_kind']}）"
            for cause in top_causes[:5] if cause.get("amount_delta") is not None
        ]
        if cause_parts:
            conclusions.append("施工费金额影响最大的项目：" + "、".join(cause_parts) + "。")

        for key, label in (
            ("old_vat", "可抵扣增值税"),
            ("old_price_diff", "编制基准期价差"),
        ):
            old_value = settlement_totals.get(key)
            new_value = settlement_totals.get(key.replace("old_", "new_"))
            if old_value is not None and new_value is not None and abs(new_value - old_value) > 1:
                conclusions.append(
                    f"{label}：{format_money(old_value)} 元 → {format_money(new_value)} 元"
                    f"（{format_delta(new_value - old_value)} 元）。"
                )
        if settlement_totals.get("new_ag_parts") is not None:
            findings.append(
                f"新版表一另列甲供配件材料费 {format_money(settlement_totals['new_ag_parts'])} 元，"
                "但未计入工程总费用合计；两版表一“配件购置费/甲供配件材料费”列示与合计口径不一致，对外口径需统一。"
            )

        # 施工费差异成因
        if fee_analysis.get("fee_delta") is not None:
            direct = fee_analysis.get("direct") or {}
            yigong = fee_analysis.get("yigong_material") or {}
            conclusions.append(
                f"施工费差异成因（表一口径）："
                f"定额直接费 {format_delta(direct.get('net'))} 元"
                f"（量差 {format_delta(direct.get('qty_effect'))}、价差 {format_delta(direct.get('price_effect'))}、"
                f"新增 {format_delta(direct.get('added'))}、删除 {format_delta(-(direct.get('removed') or 0))}）；"
                f"乙供材料 {format_delta(yigong.get('net'))} 元"
                f"（量差 {format_delta(yigong.get('qty_effect'))}、价差 {format_delta(yigong.get('price_effect'))}、"
                f"新增 {format_delta(yigong.get('added'))}、删除 {format_delta(-(yigong.get('removed') or 0))}）；"
                f"取费与汇总口径残差 {format_delta(fee_analysis.get('residual_vs_effects'))} 元；"
                f"合计与施工费变化 {format_delta(fee_analysis['fee_delta'])} 元相符。"
            )
            agong = fee_analysis.get("agong_material") or {}
            if abs(agong.get("net") or 0) > 1:
                conclusions.append(
                    f"甲供材料（不计入施工费）变化 {format_delta(agong.get('net'))} 元，属供料口径调整，"
                    "需结合甲供清单与含配件口径理解。"
                )
            parts = fee_analysis.get("parts") or {}
            if abs(parts.get("net") or 0) > 1:
                conclusions.append(f"配件（配件购置费口径）变化 {format_delta(parts.get('net'))} 元。")
        sections = fee_analysis.get("sections") or []
        if sections:
            conclusions.append(
                "分部金额变化最大的方向："
                + "、".join(f"{section['name'] or '（未命名分部）'} {format_delta(section['delta'])} 元"
                           for section in sections[:4])
                + "。"
            )
        if fee_analysis.get("yigong_delta") is not None:
            conclusions.append(
                f"乙供未计价材料 {format_money(fee_analysis['old_yigong'])} 元 → "
                f"{format_money(fee_analysis['new_yigong'])} 元"
                f"（{format_delta(fee_analysis['yigong_delta'])} 元）。乙供材料规模变化会直接影响施工费"
                "（含少量改列甲供）；旧版甲供材料规模也可能同步变化，判断施工费降幅时需结合甲供清单与含配件口径。"
            )
        if fee_analysis.get("format") == "assembly_only":
            conclusions.append(
                "结算资料仅有“组合件汇总表”（无表一/表二甲/表三甲/材料表），"
                "施工费按组合件合价合计口径比较，取费构成与甲供/乙供材料无法拆分，资料完整性不足。"
            )
        if fee_analysis.get("format") == "assembly_only":
            findings.append(
                "本版本结算书仅有“组合件汇总表”（无表一/表二甲/表三甲），施工费按组合件合价合计比较，"
                "取费构成与甲供/乙供材料无法拆分，建议补齐完整结算书后再做正式对比。"
            )
    else:
        findings.append("审核结算总额未能从文件中完整提取，请人工核对表一。")

    status_counts = s_summary.get("status_counts") or {}
    if status_counts.get(STATUS_NEW) or status_counts.get(STATUS_REMOVED):
        conclusions.append(
            f"审核结算明细中新增 {status_counts.get(STATUS_NEW, 0)} 项、删除 {status_counts.get(STATUS_REMOVED, 0)} 项，"
            f"需逐项确认变更依据（设计变更/现场签证/审核核减）。"
        )

    # ---- 材料清册 ----
    material_totals = _material_totals_pair(material["old"], material["new"])
    m_summary = material["summary"]
    if material_totals["old_yigong"] is not None or material_totals["new_yigong"] is not None:
        text = (
            f"材料清册（乙供未计价材料）：原版 {format_money(material_totals['old_yigong'])} 元 → "
            f"新版 {format_money(material_totals['new_yigong'])} 元"
        )
        if material_totals["old_yigong"] and material_totals["new_yigong"] is not None:
            delta = material_totals["new_yigong"] - material_totals["old_yigong"]
            text += f"（{format_delta(delta)} 元，{format_ratio(delta / material_totals['old_yigong'])}）"
        text += (
            f"；明细共 {m_summary['count']} 项，变化 {m_summary['changed_count']} 项，"
            f"高风险 {m_summary['risk_counts'].get(RISK_HIGH, 0)} 项。"
        )
        conclusions.append(text)

    if material_totals["new_ag"] is not None:
        if material_totals["old_ag"] is None:
            findings.append(
                f"原版资料未提供甲供未计价材料清单，新版新增甲供材料清单（合计 {format_money(material_totals['new_ag'])} 元），"
                "甲供材料无法做原/新逐项对比，需补充原版甲供清单后复核。"
            )
            risks.append({
                "level": RISK_MID,
                "category": "材料清册",
                "title": "原版缺少甲供材料清单",
                "detail": f"新版甲供材料合计 {format_money(material_totals['new_ag'])} 元，原版无对应清单，存在口径不可比风险。",
                "amount_delta": material_totals["new_ag"],
            })
        else:
            delta = material_totals["new_ag"] - material_totals["old_ag"]
            conclusions.append(
                f"甲供未计价材料：{format_money(material_totals['old_ag'])} 元 → "
                f"{format_money(material_totals['new_ag'])} 元（{format_delta(delta)} 元）。"
            )

    parts_old, parts_new = material_totals["old_parts"], material_totals["new_parts"]
    if parts_old is not None and parts_new is not None and abs(parts_new - parts_old) > 1:
        conclusions.append(
            f"配件购置费：{format_money(parts_old)} 元 → {format_money(parts_new)} 元"
            f"（{format_delta(parts_new - parts_old)} 元）。"
        )

    supplementary = material.get("supplementary") or {}
    parts_summary = (supplementary.get("配件口径") or {}).get("summary") or {}
    if parts_summary.get("amount_old") is not None and parts_summary.get("amount_new") is not None:
        delta = parts_summary["amount_new"] - parts_summary["amount_old"]
        conclusions.append(
            f"配件口径（表三甲配件行逐项对比）：{format_money(parts_summary['amount_old'])} 元 → "
            f"{format_money(parts_summary['amount_new'])} 元（{format_delta(delta)} 元，"
            f"{format_ratio(delta / parts_summary['amount_old'] if parts_summary['amount_old'] else None)}），"
            f"共 {parts_summary['count']} 项、变化 {parts_summary['changed_count']} 项。"
        )
    ag_info = supplementary.get("甲供清单") or {}
    if ag_info.get("old_count") == 0 and ag_info.get("new_count"):
        ag_all_items = material["new"].get("all_items") or material["new"].get("items") or []
        ag_parts_count = sum(
            1 for item in ag_all_items
            if (item.get("supply") or "") == "甲供" and item.get("item_type") == "配件"
        )
        ag_material_count = max(0, ag_info.get("new_count", 0) - ag_parts_count)
        ag_parts_amount = material_totals.get("new_parts")
        conclusions.append(
            f"甲供材料：新版甲供未计价材料 {ag_material_count} 项（合计 {format_money(material_totals['new_ag'])} 元）、"
            f"甲供配件 {ag_parts_count} 项（合计 {format_money(ag_parts_amount)} 元）；"
            "原版未提供清单，无法逐项对比。"
        )

    supply_changed = [diff for diff in material["diffs"] if "供料方式变化" in (diff.get("note") or "")]
    if supply_changed:
        conclusions.append(f"材料中有 {len(supply_changed)} 项发生甲供/乙供方式变化，需核对供料合同与领料记录。")

    matched_count = sum(1 for diff in material["diffs"] if diff.get("match_type") not in {"新增", "删除"})
    if material["diffs"] and matched_count / max(len(material["diffs"]), 1) < 0.5:
        findings.append("材料清单新旧版本匹配率偏低（<50%），可能存在分类口径或名称差异，建议人工逐项核对。")

    # ---- 竣工资料工程量 ----
    text_changes = boq.get("text_changes") or []
    changed_texts = [item for item in text_changes if item["status"] != STATUS_SAME]
    conclusions.append(
        f"竣工资料工程量描述：文本块 {len(text_changes)} 段，其中新增 {sum(1 for i in text_changes if i['status'] == STATUS_NEW)} 段、"
        f"修改 {sum(1 for i in text_changes if i['status'] == '修改')} 段、未变化 "
        f"{sum(1 for i in text_changes if i['status'] == STATUS_SAME)} 段。"
    )

    old_grounding = _boq_grounding_totals(boq["old"])
    new_grounding = _boq_grounding_totals(boq["new"])
    if old_grounding or new_grounding:
        parts = []
        for name in sorted(set(old_grounding) | set(new_grounding)):
            old_value = old_grounding.get(name, 0.0)
            new_value = new_grounding.get(name, 0.0)
            if abs(new_value - old_value) > 1e-9:
                parts.append(f"{name} {format_number(old_value, 2)} → {format_number(new_value, 2)}")
        if parts:
            conclusions.append("接地工程量变化：" + "；".join(parts) + "。")
        elif old_grounding and new_grounding:
            conclusions.append("接地工程量原/新一致。")

    new_tables = {table.get("id") for table in (boq["new"].get("tables") or [])}
    old_tables = {table.get("id") for table in (boq["old"].get("tables") or [])}
    only_new_tables = new_tables - old_tables
    if only_new_tables:
        findings.append(
            f"新版竣工报告新增 {len(only_new_tables)} 类结构化记录（如安健环、清理旧线路、拉盘/底盘、接地汇总等），"
            "原版资料缺少对应记录，无法做原/新逐项对比，建议按完整竣工资料要求补齐原版。"
        )
        risks.append({
            "level": RISK_MID,
            "category": "竣工资料",
            "title": "原版竣工资料不完整",
            "detail": "新版竣工报告含安健环、清理旧线路、接地/拉盘/底盘等记录，原版缺少，存在资料完整性与对比口径风险。",
            "amount_delta": None,
        })

    old_boq_warnings = boq["old"].get("warnings") or []
    for warning in old_boq_warnings:
        if "为空" in warning and warning not in findings:
            findings.append(f"原版竣工资料存在空记录：{warning}")

    if changed_texts:
        worst = max(changed_texts, key=lambda item: abs(len(item.get("new_text") or "") - len(item.get("old_text") or "")))
        added_texts = [item for item in changed_texts if item["status"] == STATUS_NEW]
        modified_texts = [item for item in changed_texts if item["status"] == "修改"]
        detail = (
            f"竣工报告主要工程量描述存在 {len(changed_texts)} 处文字变化"
            f"（修改 {len(modified_texts)} 段、新增 {len(added_texts)} 段；最大一段字数 "
            f"{len(worst.get('old_text') or '')} → {len(worst.get('new_text') or '')}），"
            "需对照设计变更/现场签证确认工程量调整依据。"
        )
        findings.append(detail)

    # ---- 工程量差异 ----
    quantity = quantity_analysis(comparison)
    if quantity["qty_changed_count"] or quantity["added_count"] or quantity["removed_count"]:
        conclusions.append(
            f"工程量差异：数量变化 {quantity['qty_changed_count']} 项（增加 {quantity['qty_increase_count']}、"
            f"减少 {quantity['qty_decrease_count']}），新增 {quantity['added_count']} 项、删除 {quantity['removed_count']} 项；"
            f"单位变化 {quantity['unit_change_count']} 项、规格变化 {quantity['spec_change_count']} 项。"
        )
        top_qty = quantity.get("top_qty_changes") or []
        if top_qty:
            parts = []
            for item in top_qty[:4]:
                old_qty = format_number(item.get("old_qty"), 3) if item.get("old_qty") is not None else "—"
                new_qty = format_number(item.get("new_qty"), 3) if item.get("new_qty") is not None else "—"
                parts.append(f"{item['name']} {old_qty}→{new_qty} {item.get('unit', '')}"
                             f"（{format_ratio(item.get('qty_ratio'))}）")
            conclusions.append("工程量变化最大的项目：" + "、".join(parts) + "。")
    if quantity.get("unit_change_count") or quantity.get("spec_change_count"):
        findings.append(
            f"结算工程量存在单位变化 {quantity['unit_change_count']} 项、规格变化 {quantity['spec_change_count']} 项，"
            "需核对换算与规格依据，避免把单位/规格变化误判为数量增减。"
        )

    # ---- 风险清单：从各类差异中提炼高/中风险 ----
    for category, payload in (("审核结算", settlement), ("材料清册", material), ("竣工工程量", boq)):
        for diff in payload.get("diffs") or []:
            if diff.get("risk") not in {RISK_HIGH, RISK_MID}:
                continue
            detail = "；".join(diff.get("reasons") or []) or (diff.get("note") or "")
            risks.append({
                "level": diff["risk"],
                "category": category,
                "title": f"{diff.get('name', '')} {diff.get('spec', '')}".strip(),
                "detail": detail,
                "amount_delta": diff.get("amount_delta"),
                "status": diff.get("status", ""),
            })
    level_order = {RISK_HIGH: 0, RISK_MID: 1}
    risks.sort(key=lambda risk: (level_order.get(risk["level"], 9), -abs(risk.get("amount_delta") or 0.0)))
    risks = risks[:40]

    if not conclusions:
        conclusions.append("未从资料中提取到可对比的数据，请检查文件与表结构。")

    totals = {
        "settlement": settlement_totals,
        "material": material_totals,
        "old_files": old_files,
        "new_files": new_files,
    }
    return {
        "conclusions": conclusions,
        "findings": findings,
        "risks": risks,
        "totals": totals,
        "construction_fee": fee_analysis,
        "quantity": quantity,
    }


def build_comparison(old_dir: str | Path, new_dir: str | Path, project_name: str | None = None,
                     old_files: list[str | Path] | None = None,
                     new_files: list[str | Path] | None = None) -> dict:
    """构建完整 Comparison。

    old_files/new_files 不为空时只比较给定文件（batch 按项目分组后使用）。
    """
    old_dir, new_dir = Path(old_dir), Path(new_dir)
    old_loaded = load_side(old_dir, files=old_files)
    new_loaded = load_side(new_dir, files=new_files)

    old_settlement = build_settlement_docset(old_loaded, "old")
    new_settlement = build_settlement_docset(new_loaded, "new")
    old_material = build_material_docset(old_loaded, "old")
    new_material = build_material_docset(new_loaded, "new")
    old_boq = build_boq_docset(old_loaded, "old")
    new_boq = build_boq_docset(new_loaded, "new")

    settlement_result = _diff_summary_of_items(
        _settlement_comparable_items(old_settlement), _settlement_comparable_items(new_settlement), "settlement"
    )
    material_result = _diff_summary_of_items(old_material["items"], new_material["items"], "material")

    # 补充口径：表三甲主材/配件（两年都有）、甲供清单、配件清单
    old_main = [item for item in old_settlement["items"] if item.get("item_type") in {"主材", "配件"}]
    new_main = [item for item in new_settlement["items"] if item.get("item_type") in {"主材", "配件"}]
    main_result = _diff_summary_of_items(old_main, new_main, "material")
    old_ag = _supply_items(old_material, "甲供")
    new_ag = _supply_items(new_material, "甲供")
    ag_result = _diff_summary_of_items(old_ag, new_ag, "material")
    old_parts = _item_type_items(old_settlement, "配件")
    new_parts = _item_type_items(new_settlement, "配件")
    parts_result = _diff_summary_of_items(old_parts, new_parts, "material")

    boq_result = _diff_summary_of_items(old_boq["items"], new_boq["items"], "boq")
    text_changes = compare_texts(old_boq["texts"], new_boq["texts"])
    table_changes = {
        "接地工程": {
            "old": _boq_grounding_totals(old_boq),
            "new": _boq_grounding_totals(new_boq),
        },
        "安健环": {"old_count": sum(1 for i in old_boq["items"] if i.get("item_type") == "安健环"),
                   "new_count": sum(1 for i in new_boq["items"] if i.get("item_type") == "安健环")},
        "拆旧": {"old_count": sum(1 for i in old_boq["items"] if i.get("item_type") == "拆旧"),
                 "new_count": sum(1 for i in new_boq["items"] if i.get("item_type") == "拆旧")},
    }

    if project_name is None or not str(project_name).strip():
        project_name = _guess_project_name(
            list_workbook_files(old_dir, old_files), list_workbook_files(new_dir, new_files)
        ) or "未命名项目"

    documents = {
        "settlement": {
            "old": old_settlement,
            "new": new_settlement,
            "diffs": settlement_result["diffs"],
            "summary": settlement_result["summary"],
            "supplementary": {},
        },
        "material": {
            "old": old_material,
            "new": new_material,
            "diffs": material_result["diffs"],
            "summary": material_result["summary"],
            "supplementary": {
                "主材口径": {
                    "diffs": main_result["diffs"],
                    "summary": main_result["summary"],
                    "old_count": len(old_main),
                    "new_count": len(new_main),
                },
                "甲供清单": {
                    "diffs": ag_result["diffs"],
                    "summary": ag_result["summary"],
                    "old_count": len(old_ag),
                    "new_count": len(new_ag),
                },
                "配件口径": {
                    "diffs": parts_result["diffs"],
                    "summary": parts_result["summary"],
                    "old_count": len(old_parts),
                    "new_count": len(new_parts),
                },
            },
        },
        "boq": {
            "old": old_boq,
            "new": new_boq,
            "diffs": boq_result["diffs"],
            "summary": boq_result["summary"],
            "text_changes": text_changes,
            "table_changes": table_changes,
        },
    }
    comparison = {
        "project": {
            "name": str(project_name),
            "old_dir": str(old_dir),
            "new_dir": str(new_dir),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "documents": documents,
        "summary": merge_summaries({
            "settlement": settlement_result["summary"],
            "material": material_result["summary"],
            "boq": boq_result["summary"],
        }),
        "analysis": {},
    }
    comparison["analysis"] = analyze(comparison)
    return comparison


def comparison_to_json(comparison: dict) -> str:
    """序列化为 JSON（去掉不可序列化对象）。"""
    import json

    def default(value: Any):
        if isinstance(value, Path):
            return str(value)
        return str(value)

    return json.dumps(comparison, ensure_ascii=False, indent=2, default=default)
