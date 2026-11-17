"""文本 / 单位 / 数字的规范化工具。

对比引擎只使用这里的规范化结果做匹配，不改变展示内容。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

_FULLWIDTH_TABLE = str.maketrans(
    "０１２３４５６７８９．，（）【】［］｛｝％－＋／：；",
    "0123456789.,()[][]{}%-+/:;",
)

# 单位规范化：原文 -> 统一写法
_UNIT_ALIASES = {
    "m": "m", "米": "m", "延米": "m", "公尺": "m", "m/": "m",
    "km": "km", "千米": "km", "公里": "km", "km/": "km",
    "m2": "m2", "㎡": "m2", "平方米": "m2", "平米": "m2",
    "m3": "m3", "m³": "m3", "立方米": "m3", "方": "m3",
    "kg": "kg", "千克": "kg", "公斤": "kg",
    "t": "t", "吨": "t", "吨/": "t",
    "g": "g", "克": "g",
    "根": "根", "条": "条", "支": "支", "只": "只", "个": "个",
    "套": "套", "台": "台", "组": "组", "块": "块", "片": "片",
    "付": "付", "副": "副", "处": "处", "座": "座", "基": "基",
    "项": "项", "次": "次", "孔": "孔", "条/公里": "条",
    "工日": "工日", "人·日": "工日", "人日": "工日",
    "km/三相": "km", "km/单相": "km", "km/相": "km",
    "t·km": "t·km", "t/km": "t·km",
    "kv": "kv", "kva": "kva", "kw": "kw",
}

_LENGTH_TO_M = {"m": 1.0, "km": 1000.0}
_WEIGHT_TO_KG = {"kg": 1.0, "t": 1000.0, "g": 0.001}
_UNIT_FAMILIES = {
    "m": ("length", _LENGTH_TO_M),
    "km": ("length", _LENGTH_TO_M),
    "kg": ("weight", _WEIGHT_TO_KG),
    "t": ("weight", _WEIGHT_TO_KG),
    "g": ("weight", _WEIGHT_TO_KG),
    "m2": ("area", {"m2": 1.0}),
    "m3": ("volume", {"m3": 1.0}),
}

_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def normalize_text(value: Any) -> str:
    """通用文本规范化：全角转半角、小写、合并空白、去首尾。"""
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        text = str(int(value))
    else:
        text = str(value)
    text = unicodedata.normalize("NFKC", text).translate(_FULLWIDTH_TABLE)
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_name(value: Any) -> str:
    """名称规范化：去掉序号、括号备注、所有空白与常见标点，便于匹配。"""
    text = normalize_text(value).lower()
    text = re.sub(r"^[0-9一二三四五六七八九十]+[.、,，)）]\s*", "", text)
    text = text.replace("（", "(").replace("）", ")")
    # 去掉纯标注括号内容：（甲）、(甲供)、（新）等
    text = re.sub(r"[(（]\s*(甲供?|乙供?|新|旧|原|利旧|拆除?)\s*[)）]", "", text)
    text = re.sub(r"[\s\-_—–·•:：,，。;；、/\\|()（）\[\]【】{}<>《》\"'“”‘’]+", "", text)
    return text


def normalize_spec(value: Any) -> str:
    """规格规范化：统一乘号、φ/Φ、全半角，压缩空白。"""
    text = normalize_text(value)
    text = text.replace("φ", "Φ").replace("Ф", "Φ").replace("ø", "Φ")
    text = text.replace("×", "*").replace("x", "*").replace("X", "*")
    text = re.sub(r"\s+", "", text)
    return text.lower()


def normalize_unit(value: Any) -> str:
    """计量单位规范化（统一写法，不做数值换算）。"""
    text = normalize_text(value).lower()
    text = text.replace("m²", "m2").replace("m³", "m3").replace("㎡", "m2")
    text = re.sub(r"[\s]+", "", text)
    if not text:
        return ""
    if text in _UNIT_ALIASES:
        return _UNIT_ALIASES[text]
    # 带括号备注的单位，如 "m(沿墙)" 取括号前
    text = re.sub(r"[（(].*?[)）]", "", text)
    return _UNIT_ALIASES.get(text, text)


def unit_family(unit: str) -> tuple[str | None, dict[str, float]]:
    """返回 (量纲, 换算到基准单位的系数表)；未知单位返回 (None, {})。"""
    unit = normalize_unit(unit)
    return _UNIT_FAMILIES.get(unit, (None, {}))


def convert_qty(qty: float | None, from_unit: str, to_unit: str) -> tuple[float | None, str | None]:
    """同量纲单位换算；无法换算返回 (None, None)。"""
    if qty is None:
        return None, None
    from_unit = normalize_unit(from_unit)
    to_unit = normalize_unit(to_unit)
    if from_unit == to_unit:
        return qty, None
    from_family, from_map = unit_family(from_unit)
    to_family, to_map = unit_family(to_unit)
    if not from_family or from_family != to_family or from_unit not in from_map or to_unit not in to_map:
        return None, None
    base = qty * from_map[from_unit]
    converted = base / to_map[to_unit]
    return converted, f"单位换算：{format_number(qty)} {from_unit} = {format_number(converted)} {to_unit}"


def parse_number(value: Any) -> float | None:
    """从任意值解析数字（支持千分位、全角、括号负数）；失败返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:
            return None
        return float(value)
    text = normalize_text(value)
    if not text or text in {"-", "/", "—", "无"}:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    match = _NUM_RE.search(text.replace(",", ""))
    if not match:
        return None
    try:
        number = float(match.group(0))
    except ValueError:
        return None
    return -abs(number) if negative else number


def format_number(value: float | None, digits: int = 2) -> str:
    """数值格式化：千分位、去多余 0。"""
    if value is None:
        return ""
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.{digits}f}".rstrip("0").rstrip(".")


def format_money(value: float | None) -> str:
    """金额格式化（2 位小数）。"""
    if value is None:
        return ""
    return f"{value:,.2f}"


def format_ratio(value: float | None) -> str:
    """百分比格式化，如 +12.3%。"""
    if value is None:
        return ""
    return f"{value * 100:+.1f}%"


def format_delta(value: float | None) -> str:
    """带符号的数值格式化。"""
    if value is None:
        return ""
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):+,}"
    return f"{value:+,.2f}"


def similarity(left: str, right: str) -> float:
    """两条文本的相似度（0~1），用于名称模糊匹配与文本块对比。"""
    from difflib import SequenceMatcher

    a = normalize_name(left)
    b = normalize_name(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def text_similarity(left: str, right: str) -> float:
    """长文本相似度：先去空白标点再按字符比较。"""
    from difflib import SequenceMatcher

    a = re.sub(r"[\s\-_—–·•:：,，。;；、/\\|()（）\[\]【】{}<>《》\"'“”‘’]+", "", left or "")
    b = re.sub(r"[\s\-_—–·•:：,，。;；、/\\|()（）\[\]【】{}<>《》\"'“”‘’]+", "", right or "")
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()
