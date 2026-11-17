"""差异计算、风险分级、汇总与文本块对比。"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .matching import (
    MATCH_FUZZY,
    MATCH_NAME_UNIT,
    MATCH_NEW,
    MATCH_RENAMED,
    MATCH_REMOVED,
    match_items,
)
from .normalize import (
    convert_qty,
    format_delta,
    format_money,
    format_number,
    format_ratio,
    normalize_name,
    normalize_spec,
    normalize_unit,
    similarity,
    text_similarity,
)

STATUS_NEW = "新增"
STATUS_REMOVED = "删除"
STATUS_QTY_UP = "数量增加"
STATUS_QTY_DOWN = "数量减少"
STATUS_PRICE = "单价变化"
STATUS_AMOUNT = "金额变化"
STATUS_SPEC = "规格变化"
STATUS_UNIT = "单位变化"
STATUS_SAME = "无变化"

STATUS_ORDER = [
    STATUS_NEW,
    STATUS_REMOVED,
    STATUS_QTY_UP,
    STATUS_QTY_DOWN,
    STATUS_SPEC,
    STATUS_UNIT,
    STATUS_PRICE,
    STATUS_AMOUNT,
    STATUS_SAME,
]

RISK_HIGH = "高"
RISK_MID = "中"
RISK_LOW = "低"
RISK_ORDER = [RISK_HIGH, RISK_MID, RISK_LOW, ""]

# 判定容差
QTY_REL_TOLERANCE = 0.005
QTY_ABS_TOLERANCE = 0.001
PRICE_REL_TOLERANCE = 0.005
PRICE_ABS_TOLERANCE = 0.01
AMOUNT_ABS_TOLERANCE = 1.0

# 风险阈值
RISK_HIGH_AMOUNT = 5000.0
RISK_MID_AMOUNT = 1000.0
RISK_HIGH_RATIO = 0.10
RISK_MID_RATIO = 0.03
RISK_RATIO_AMOUNT_FLOOR_HIGH = 500.0
RISK_RATIO_AMOUNT_FLOOR_MID = 100.0


def item_amount(item: dict | None) -> float | None:
    """条目金额：优先 amount，其次 qty * unit_price。"""
    if not item:
        return None
    if item.get("amount") is not None:
        return float(item["amount"])
    qty, price = item.get("qty"), item.get("unit_price")
    if qty is not None and price is not None:
        return float(qty) * float(price)
    return None


def _ratio(delta: float | None, base: float | None) -> float | None:
    if delta is None or base is None or abs(base) < 1e-12:
        return None
    return delta / base


def _risk_level(
    status: str,
    qty_ratio: float | None,
    amount_delta: float | None,
    amount_ratio: float | None,
    price_ratio: float | None,
    match_type: str,
) -> str:
    """按契约规则给风险等级。"""
    if status == STATUS_SAME:
        return ""
    level = 0  # 0=低
    abs_amount = abs(amount_delta) if amount_delta is not None else None
    ratio = amount_ratio if amount_ratio is not None else (qty_ratio if qty_ratio is not None else price_ratio)
    if abs_amount is not None and ratio is not None:
        if abs_amount >= RISK_HIGH_AMOUNT or (abs(ratio) >= RISK_HIGH_RATIO and abs_amount >= RISK_RATIO_AMOUNT_FLOOR_HIGH):
            level = 2
        elif abs_amount >= RISK_MID_AMOUNT or (abs(ratio) >= RISK_MID_RATIO and abs_amount >= RISK_RATIO_AMOUNT_FLOOR_MID):
            level = 1
    elif abs_amount is not None:
        if abs_amount >= RISK_HIGH_AMOUNT:
            level = 2
        elif abs_amount >= RISK_MID_AMOUNT:
            level = 1
    elif ratio is not None:
        if abs(ratio) >= RISK_HIGH_RATIO:
            level = 2
        elif abs(ratio) >= RISK_MID_RATIO:
            level = 1

    if status in {STATUS_NEW, STATUS_REMOVED, STATUS_SPEC, STATUS_UNIT}:
        level = max(level, 1)
    if status in {STATUS_SPEC, STATUS_UNIT} and abs_amount is not None and abs_amount >= RISK_HIGH_AMOUNT:
        level = 2
    if match_type in {MATCH_FUZZY, MATCH_RENAMED}:
        level = max(level, 1)
    return {2: RISK_HIGH, 1: RISK_MID, 0: RISK_LOW}[level]


def _fmt_qty(value: float | None) -> str:
    return format_number(value, digits=3)


def _build_changed_diff(old: dict, new: dict, pair: dict, category: str) -> dict:
    old_unit = normalize_unit(old.get("unit", ""))
    new_unit = normalize_unit(new.get("unit", ""))
    old_spec = normalize_spec(old.get("spec", ""))
    new_spec = normalize_spec(new.get("spec", ""))

    reasons: list[str] = []
    notes: list[str] = []
    if pair.get("note"):
        notes.append(pair["note"])

    # 数量对比（单位不同先尝试换算）
    old_qty, new_qty = old.get("qty"), new.get("qty")
    qty_delta = qty_ratio = None
    qty_changed = False
    unit_changed = old_unit != new_unit
    converted_qty = None
    if old_qty is not None and new_qty is not None:
        if unit_changed:
            converted_qty, conversion_note = convert_qty(float(old_qty), old_unit, new_unit)
            if conversion_note:
                notes.append(conversion_note)
        if converted_qty is None:
            converted_qty = float(old_qty) if not unit_changed else None
        if converted_qty is not None:
            qty_delta = float(new_qty) - converted_qty
            qty_ratio = _ratio(qty_delta, converted_qty)
            tolerance = max(abs(converted_qty) * QTY_REL_TOLERANCE, QTY_ABS_TOLERANCE)
            qty_changed = abs(qty_delta) > tolerance
            if qty_changed:
                reasons.append(
                    f"数量 {_fmt_qty(converted_qty)} → {_fmt_qty(float(new_qty))}"
                    f"（{format_delta(qty_delta)}，{format_ratio(qty_ratio)}）"
                )

    # 规格对比
    spec_changed = old_spec != new_spec and bool(old_spec or new_spec)
    if spec_changed:
        reasons.append(f"规格 {old.get('spec') or '（空）'} → {new.get('spec') or '（空）'}")

    # 单价对比
    old_price, new_price = old.get("unit_price"), new.get("unit_price")
    price_delta = price_ratio = None
    price_changed = False
    if old_price is not None and new_price is not None:
        price_delta = float(new_price) - float(old_price)
        price_ratio = _ratio(price_delta, float(old_price))
        tolerance = max(abs(float(old_price)) * PRICE_REL_TOLERANCE, PRICE_ABS_TOLERANCE)
        price_changed = abs(price_delta) > tolerance
        if price_changed:
            reasons.append(
                f"单价 {format_money(float(old_price))} → {format_money(float(new_price))}"
                f"（{format_delta(price_delta)}，{format_ratio(price_ratio)}）"
            )

    # 金额对比
    old_amount, new_amount = item_amount(old), item_amount(new)
    amount_delta = amount_ratio = None
    amount_changed = False
    if old_amount is not None and new_amount is not None:
        amount_delta = new_amount - old_amount
        amount_ratio = _ratio(amount_delta, old_amount)
        amount_changed = abs(amount_delta) > AMOUNT_ABS_TOLERANCE
        if amount_changed:
            reasons.append(
                f"金额 {format_money(old_amount)} → {format_money(new_amount)}"
                f"（{format_delta(amount_delta)}，{format_ratio(amount_ratio)}）"
            )

    # 状态优先级：数量 > 规格 > 单位 > 单价 > 金额 > 无变化
    if qty_changed:
        status = STATUS_QTY_UP if (qty_delta or 0) > 0 else STATUS_QTY_DOWN
    elif spec_changed:
        status = STATUS_SPEC
    elif unit_changed:
        status = STATUS_UNIT
    elif price_changed:
        status = STATUS_PRICE
    elif amount_changed:
        status = STATUS_AMOUNT
    else:
        status = STATUS_SAME

    # 供料方式变化、聚合信息
    old_supply, new_supply = (old.get("supply") or ""), (new.get("supply") or "")
    if old_supply and new_supply and old_supply != new_supply:
        notes.append(f"供料方式变化：{old_supply} → {new_supply}")
        if status == STATUS_SAME:
            status = STATUS_AMOUNT if amount_changed else STATUS_SPEC
    if old.get("_group_count", 1) > 1 or new.get("_group_count", 1) > 1:
        notes.append(f"表内多行合并：原版 {old.get('_group_count', 1)} 行 / 新版 {new.get('_group_count', 1)} 行")

    risk = _risk_level(status, qty_ratio, amount_delta, amount_ratio, price_ratio, pair.get("match_type", ""))
    if not reasons and status == STATUS_SAME:
        note_text = "；".join(notes)
    else:
        note_text = "；".join(notes)
    return {
        "status": status,
        "risk": risk,
        "name": new.get("name") or old.get("name") or "",
        "spec": new.get("spec") or old.get("spec") or "",
        "unit": new.get("unit") or old.get("unit") or "",
        "section": new.get("section") or old.get("section") or "",
        "item_type": new.get("item_type") or old.get("item_type") or "",
        "supply": new.get("supply") or old.get("supply") or "",
        "old": {k: v for k, v in old.items() if not k.startswith("_") and k != "sources"},
        "new": {k: v for k, v in new.items() if not k.startswith("_") and k != "sources"},
        "qty_delta": qty_delta,
        "qty_ratio": qty_ratio,
        "price_delta": price_delta,
        "price_ratio": price_ratio,
        "amount_delta": amount_delta,
        "amount_ratio": amount_ratio,
        "match_type": pair.get("match_type", ""),
        "category": category,
        "reasons": reasons,
        "note": note_text,
    }


def _build_single_diff(item: dict, category: str, added: bool, match_type: str) -> dict:
    amount = item_amount(item)
    status = STATUS_NEW if added else STATUS_REMOVED
    detail = "新增条目" if added else "删除条目"
    if amount is not None:
        detail += f"，金额 {format_money(amount)}"
    if not added:
        detail = "删除条目" + (f"，金额 {format_money(amount)}" if amount is not None else "")
    diff = {
        "status": status,
        "risk": _risk_level(status, None, None, None, None, match_type),
        "name": item.get("name", ""),
        "spec": item.get("spec", ""),
        "unit": item.get("unit", ""),
        "section": item.get("section", ""),
        "item_type": item.get("item_type", ""),
        "supply": item.get("supply", ""),
        "old": None if added else {k: v for k, v in item.items() if not k.startswith("_") and k != "sources"},
        "new": {k: v for k, v in item.items() if not k.startswith("_") and k != "sources"} if added else None,
        "qty_delta": None,
        "qty_ratio": None,
        "price_delta": None,
        "price_ratio": None,
        "amount_delta": None,
        "amount_ratio": None,
        "match_type": match_type,
        "category": category,
        "reasons": [detail],
        "note": "",
    }
    return diff


def compare_items(old_items: list[dict], new_items: list[dict], category: str = "settlement") -> list[dict]:
    """逐项对比，返回 Diff 列表（已按展示顺序排序）。"""
    pairs = match_items(old_items or [], new_items or [], category)
    diffs: list[dict] = []
    for pair in pairs:
        if pair.get("old") is None and pair.get("new") is not None:
            diffs.append(_build_single_diff(pair["new"], category, added=True, match_type=pair["match_type"]))
        elif pair.get("new") is None and pair.get("old") is not None:
            diffs.append(_build_single_diff(pair["old"], category, added=False, match_type=pair["match_type"]))
        else:
            diffs.append(_build_changed_diff(pair["old"], pair["new"], pair, category))

    def sort_key(diff: dict):
        status_index = STATUS_ORDER.index(diff["status"]) if diff["status"] in STATUS_ORDER else 99
        risk_index = RISK_ORDER.index(diff["risk"]) if diff["risk"] in RISK_ORDER else 99
        amount = abs(diff["amount_delta"]) if diff["amount_delta"] is not None else 0.0
        return (status_index, risk_index, -amount)

    diffs.sort(key=sort_key)
    return diffs


def summarize(diffs: list[dict]) -> dict:
    """汇总差异统计与金额。"""
    status_counts = Counter()
    risk_counts = Counter()
    amount_old = 0.0
    amount_new = 0.0
    amount_available = False
    high_risk_amount = 0.0

    for diff in diffs or []:
        status_counts[diff["status"]] += 1
        if diff.get("risk"):
            risk_counts[diff["risk"]] += 1
        old_amount = item_amount(diff.get("old"))
        new_amount = item_amount(diff.get("new"))
        if old_amount is not None:
            amount_old += old_amount
            amount_available = True
        if new_amount is not None:
            amount_new += new_amount
            amount_available = True
        if diff.get("risk") == RISK_HIGH and diff.get("amount_delta") is not None:
            high_risk_amount += abs(diff["amount_delta"])

    changed = sum(count for status, count in status_counts.items() if status != STATUS_SAME)
    summary = {
        "count": len(diffs or []),
        "changed_count": changed,
        "status_counts": dict(status_counts),
        "risk_counts": {risk: risk_counts.get(risk, 0) for risk in (RISK_HIGH, RISK_MID, RISK_LOW)},
        "amount_old": amount_old if amount_available else None,
        "amount_new": amount_new if amount_available else None,
        "amount_delta": (amount_new - amount_old) if amount_available else None,
        "amount_ratio": _ratio((amount_new - amount_old), amount_old) if amount_available else None,
        "high_risk_count": risk_counts.get(RISK_HIGH, 0),
        "high_risk_amount": high_risk_amount,
    }
    return summary


def top_diffs(diffs: list[dict], limit: int = 10) -> list[dict]:
    """按金额影响（绝对差）排序的变化项。"""
    changed = [d for d in diffs or [] if d["status"] != STATUS_SAME]
    return sorted(changed, key=lambda d: abs(d.get("amount_delta") or 0.0), reverse=True)[:limit]


def _cell_position(text_id: str) -> str | None:
    """从文本块 id 中取单元格坐标（如 R11C3），用于同位置配对。"""
    import re

    match = re.search(r"R\d+C\d+", text_id or "")
    return match.group(0) if match else None


def compare_texts(old_texts: list[dict], new_texts: list[dict], unchanged_threshold: float = 0.995) -> list[dict]:
    """对比长文本块：先同单元格位置配对，再按相似度配对，输出 无变化/修改/新增/删除。"""
    old_texts = old_texts or []
    new_texts = new_texts or []
    used_new: set[int] = set()
    used_old: set[int] = set()
    changes: list[dict] = []

    # 1) 同单元格位置配对（同一张附页的新旧版本）
    by_position: dict[str, list[int]] = {}
    for new_index, new in enumerate(new_texts):
        position = _cell_position(new.get("id", ""))
        if position:
            by_position.setdefault(position, []).append(new_index)
    for old_index, old in enumerate(old_texts):
        position = _cell_position(old.get("id", ""))
        if not position or position not in by_position:
            continue
        candidates = [index for index in by_position[position] if index not in used_new]
        if not candidates:
            continue
        new_index = candidates[0]
        old_text, new_text = old, new_texts[new_index]
        score = text_similarity(old_text.get("text", ""), new_text.get("text", ""))
        used_old.add(old_index)
        used_new.add(new_index)
        changes.append(_text_change(old_text, new_text, score, unchanged_threshold))

    # 2) 其余按相似度配对
    scored: list[tuple[float, int, int]] = []
    for old_index, old in enumerate(old_texts):
        if old_index in used_old:
            continue
        for new_index, new in enumerate(new_texts):
            if new_index in used_new:
                continue
            scored.append((text_similarity(old.get("text", ""), new.get("text", "")), old_index, new_index))
    scored.sort(reverse=True)
    for score, old_index, new_index in scored:
        if old_index in used_old or new_index in used_new:
            continue
        if score < 0.45:
            continue
        used_old.add(old_index)
        used_new.add(new_index)
        changes.append(_text_change(old_texts[old_index], new_texts[new_index], score, unchanged_threshold))

    for old_index, old_text in enumerate(old_texts):
        if old_index in used_old:
            continue
        changes.append({
            "old_id": old_text.get("id", ""), "new_id": "", "old_label": old_text.get("label", ""),
            "new_label": "", "similarity": 0.0, "status": STATUS_REMOVED,
            "old_text": old_text.get("text", ""), "new_text": "",
            "summary": "新版中未找到对应文本块",
        })
    for new_index, new_text in enumerate(new_texts):
        if new_index in used_new:
            continue
        changes.append({
            "old_id": "", "new_id": new_text.get("id", ""), "old_label": "", "new_label": new_text.get("label", ""),
            "similarity": 0.0, "status": STATUS_NEW,
            "old_text": "", "new_text": new_text.get("text", ""),
            "summary": "新增文本块",
        })
    order = {STATUS_NEW: 0, STATUS_REMOVED: 1, "修改": 2, STATUS_SAME: 3}
    changes.sort(key=lambda item: (order.get(item["status"], 9), -item["similarity"]))
    return changes


def _text_change(old_text: dict, new_text: dict, score: float, unchanged_threshold: float) -> dict:
    status = STATUS_SAME if score >= unchanged_threshold else "修改"
    return {
        "old_id": old_text.get("id", ""),
        "new_id": new_text.get("id", ""),
        "old_label": old_text.get("label", ""),
        "new_label": new_text.get("label", ""),
        "similarity": score,
        "status": status,
        "old_text": old_text.get("text", ""),
        "new_text": new_text.get("text", ""),
        "summary": f"字数 {len(old_text.get('text', ''))} → {len(new_text.get('text', ''))}，相似度 {score:.0%}",
    }


def merge_summaries(summaries: dict[str, dict]) -> dict:
    """把多类资料的 summary 合并为项目级汇总（金额只合并有金额的类别）。"""
    status_counts = Counter()
    risk_counts = Counter()
    amount_old = 0.0
    amount_new = 0.0
    amount_available = False
    diff_count = 0
    changed_count = 0
    high_risk_amount = 0.0
    for summary in summaries.values():
        if not summary:
            continue
        diff_count += summary.get("count", 0)
        changed_count += summary.get("changed_count", 0)
        for status, count in (summary.get("status_counts") or {}).items():
            status_counts[status] += count
        for risk, count in (summary.get("risk_counts") or {}).items():
            risk_counts[risk] += count
        if summary.get("amount_old") is not None:
            amount_old += summary["amount_old"]
            amount_new += summary.get("amount_new") or 0.0
            amount_available = True
        high_risk_amount += summary.get("high_risk_amount") or 0.0
    return {
        "count": diff_count,
        "changed_count": changed_count,
        "status_counts": dict(status_counts),
        "risk_counts": {risk: risk_counts.get(risk, 0) for risk in (RISK_HIGH, RISK_MID, RISK_LOW)},
        "amount_old": amount_old if amount_available else None,
        "amount_new": amount_new if amount_available else None,
        "amount_delta": (amount_new - amount_old) if amount_available else None,
        "amount_ratio": _ratio((amount_new - amount_old), amount_old) if amount_available else None,
        "high_risk_count": risk_counts.get(RISK_HIGH, 0),
        "high_risk_amount": high_risk_amount,
    }
