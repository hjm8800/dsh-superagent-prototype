"""逐项匹配：把原版/新版条目按业务键对齐。

匹配顺序（避免误配）：
1. 精确键：结算定额按 定额编号+单位（无编号时 名称+规格+单位）；材料/工程量按 名称+规格+单位。
2. 名称+单位（规格变化也能对上）。
3. 名称相似（difflib ≥ 0.82）且单位兼容。
4. 疑似改名（difflib ≥ 0.70）且单位兼容。
5. 剩余为新增 / 删除。

同一键出现多行时先聚合（数量、金额求和，单价按金额/数量重算），
这样同一材料/定额在表内多次出现时不会因行数不同被误判为新增/删除。
"""
from __future__ import annotations

from collections import OrderedDict
import re
from typing import Any

from .normalize import (
    normalize_name,
    normalize_spec,
    normalize_text,
    normalize_unit,
    similarity,
    unit_family,
)

MATCH_EXACT = "精确匹配"
MATCH_NAME_UNIT = "名称+单位匹配"
MATCH_FUZZY = "名称相似匹配"
MATCH_RENAMED = "疑似改名"
MATCH_NEW = "新增"
MATCH_REMOVED = "删除"

FUZZY_THRESHOLD = 0.82
RENAME_THRESHOLD = 0.70

# 名称中的规格型数字片段（Φ190×15、500*1000、S13-M-200kVA 等），比较名称相似度时先剔除
_SPECISH_RE = re.compile(r"(?:[φΦ])?\d+(?:\.\d+)?(?:[*×xX]\d+(?:\.\d+)?)*(?:[a-zA-Z]{1,4})?")

# 材质/工艺类通用修饰词：识别“同物异名”时剔除，避免因前缀不同漏配
_GENERIC_QUALIFIERS = (
    "普通混凝土", "预应力混凝土", "钢筋混凝土", "商品混凝土",
    "预应力", "普通", "混凝土杆", "镀锌", "热镀锌",
    "非典型设计", "标准",
)


def _core_name(value: Any) -> str:
    """去掉规格型数字与材质修饰词后的名称核心，用于识别同物异名。"""
    text = normalize_name(value)
    text = _SPECISH_RE.sub("", text)
    for qualifier in _GENERIC_QUALIFIERS:
        text = text.replace(qualifier, "")
    return text


def name_similarity(left: Any, right: Any) -> float:
    """名称相似度：兼顾原文与剔除规格数字后的核心名称。"""
    return max(similarity(left, right), similarity(_core_name(left), _core_name(right)))



def item_key(item: dict, category: str) -> tuple:
    """业务匹配键（已规范化）。"""
    name = normalize_name(item.get("name"))
    spec = normalize_spec(item.get("spec"))
    unit = normalize_unit(item.get("unit"))
    code = normalize_text(item.get("code"))
    if category == "settlement" and item.get("item_type") == "定额" and code:
        return ("code", code, unit)
    return ("name", name, spec, unit)


def units_compatible(left: str, right: str) -> bool:
    """单位是否可比较（相同、同量纲可换算、或一方为空）。"""
    left_u, right_u = normalize_unit(left), normalize_unit(right)
    if not left_u or not right_u or left_u == right_u:
        return True
    left_family, _ = unit_family(left_u)
    right_family, _ = unit_family(right_u)
    return bool(left_family) and left_family == right_family


def _unique_join(values: list[str]) -> str:
    seen: list[str] = []
    for value in values:
        value = (value or "").strip()
        if value and value not in seen:
            seen.append(value)
    return "、".join(seen)


def group_items(items: list[dict], category: str) -> list[dict]:
    """按业务键聚合条目；返回聚合后的条目（附 _group_count / _sources）。"""
    buckets: "OrderedDict[tuple, dict]" = OrderedDict()
    for item in items:
        key = item_key(item, category)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = {
                "name": item.get("name", ""),
                "spec": item.get("spec", ""),
                "unit": item.get("unit", ""),
                "qty": None,
                "unit_price": item.get("unit_price"),
                "amount": None,
                "code": item.get("code", ""),
                "section": "",
                "item_type": item.get("item_type", ""),
                "supply": item.get("supply", ""),
                "sources": [],
                "_group_count": 0,
            }
            buckets[key] = bucket
        for field in ("qty", "amount"):
            value = item.get(field)
            if value is not None:
                bucket[field] = (bucket[field] or 0.0) + float(value)
        bucket["_group_count"] += 1
        if item.get("section"):
            bucket["sources"].append(item.get("section"))
        if item.get("source"):
            bucket["sources"].append(item.get("source"))
        if not bucket["item_type"] and item.get("item_type"):
            bucket["item_type"] = item["item_type"]
        if not bucket["supply"] and item.get("supply"):
            bucket["supply"] = item["supply"]
        if not bucket["code"] and item.get("code"):
            bucket["code"] = item["code"]

    for bucket in buckets.values():
        if bucket["amount"] is None and bucket["qty"] is not None and bucket["unit_price"] is not None:
            bucket["amount"] = bucket["qty"] * bucket["unit_price"]
        bucket["section"] = _unique_join(bucket.pop("sources"))
    return list(buckets.values())


def _bucket_by(keys: list[tuple], used: set[int]) -> dict[tuple, list[int]]:
    buckets: dict[tuple, list[int]] = {}
    for index, key in enumerate(keys):
        if index in used:
            continue
        buckets.setdefault(key, []).append(index)
    return buckets


def match_items(old_items: list[dict], new_items: list[dict], category: str) -> list[dict]:
    """匹配两组条目；返回 [{"old","new","match_type","note"}]。"""
    old_groups = group_items(old_items, category)
    new_groups = group_items(new_items, category)

    old_keys = [item_key(item, category) for item in old_groups]
    new_keys = [item_key(item, category) for item in new_groups]

    pairs: list[dict] = []
    matched_old: set[int] = set()
    used_new: set[int] = set()

    # 1) 精确匹配
    by_key: dict[tuple, list[int]] = {}
    for index, key in enumerate(new_keys):
        by_key.setdefault(key, []).append(index)
    for index, key in enumerate(old_keys):
        candidates = by_key.get(key)
        if not candidates:
            continue
        while candidates and candidates[0] in used_new:
            candidates.pop(0)
        if candidates:
            new_index = candidates.pop(0)
            used_new.add(new_index)
            matched_old.add(index)
            pairs.append({"old": old_groups[index], "new": new_groups[new_index],
                          "match_type": MATCH_EXACT, "note": ""})

    # 2) 名称+单位（规格变化）
    left_old = [i for i in range(len(old_groups)) if i not in matched_old]
    fallback_keys_old = [(normalize_name(old_groups[i]["name"]), normalize_unit(old_groups[i]["unit"])) for i in left_old]
    fallback_keys_new = [(normalize_name(new_groups[j]["name"]), normalize_unit(new_groups[j]["unit"]))
                         for j in range(len(new_groups)) if j not in used_new]
    old_index_map = {pos: left_old[pos] for pos in range(len(left_old))}
    new_index_map = {pos: j for pos, j in enumerate(j for j in range(len(new_groups)) if j not in used_new)}
    bucket: dict[tuple, list[int]] = {}
    for pos, key in enumerate(fallback_keys_new):
        bucket.setdefault(key, []).append(pos)
    for old_pos, key in enumerate(fallback_keys_old):
        candidates = bucket.get(key)
        if not candidates:
            continue
        while candidates and new_index_map[candidates[0]] in used_new:
            candidates.pop(0)
        if candidates:
            new_pos = candidates.pop(0)
            new_index = new_index_map[new_pos]
            old_index = old_index_map[old_pos]
            used_new.add(new_index)
            matched_old.add(old_index)
            pairs.append({"old": old_groups[old_index], "new": new_groups[new_index],
                          "match_type": MATCH_NAME_UNIT,
                          "note": f"规格变化：{old_groups[old_index].get('spec') or '（空）'} → "
                                  f"{new_groups[new_index].get('spec') or '（空）'}"})

    # 3) 名称相似匹配
    left_old = [i for i in range(len(old_groups)) if i not in matched_old]
    left_new = [j for j in range(len(new_groups)) if j not in used_new]
    for old_index in left_old:
        if old_index in matched_old:
            continue
        best: tuple[float, int] | None = None
        for new_index in left_new:
            if new_index in used_new:
                continue
            if not units_compatible(old_groups[old_index].get("unit", ""), new_groups[new_index].get("unit", "")):
                continue
            score = name_similarity(old_groups[old_index].get("name", ""), new_groups[new_index].get("name", ""))
            if best is None or score > best[0]:
                best = (score, new_index)
        if best and best[0] >= FUZZY_THRESHOLD:
            matched_old.add(old_index)
            used_new.add(best[1])
            pairs.append({"old": old_groups[old_index], "new": new_groups[best[1]],
                          "match_type": MATCH_FUZZY,
                          "note": f"名称相似（{best[0]:.0%}），建议人工确认"})

    # 4) 疑似改名
    left_old = [i for i in range(len(old_groups)) if i not in matched_old]
    for old_index in left_old:
        if old_index in matched_old:
            continue
        best = None
        for new_index in range(len(new_groups)):
            if new_index in used_new:
                continue
            if not units_compatible(old_groups[old_index].get("unit", ""), new_groups[new_index].get("unit", "")):
                continue
            score = name_similarity(old_groups[old_index].get("name", ""), new_groups[new_index].get("name", ""))
            if best is None or score > best[0]:
                best = (score, new_index)
        if best and best[0] >= RENAME_THRESHOLD:
            matched_old.add(old_index)
            used_new.add(best[1])
            pairs.append({"old": old_groups[old_index], "new": new_groups[best[1]],
                          "match_type": MATCH_RENAMED,
                          "note": f"疑似改名（相似度 {best[0]:.0%}），建议人工确认"})

    # 5) 剩余新增 / 删除
    for index in range(len(old_groups)):
        if index not in matched_old:
            pairs.append({"old": old_groups[index], "new": None,
                          "match_type": MATCH_REMOVED, "note": ""})
    for index in range(len(new_groups)):
        if index not in used_new:
            pairs.append({"old": None, "new": new_groups[index],
                          "match_type": MATCH_NEW, "note": ""})

    return pairs
