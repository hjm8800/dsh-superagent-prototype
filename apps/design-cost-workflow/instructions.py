# -*- coding: utf-8 -*-
"""instructions.py — 作业指导书工序知识库（读取本机索引，纯读）。

索引文件由 `scripts/build_work_instruction_index.py` 从本机“作业指导书”资料生成，
默认放在应用数据目录 `data/instructions.json`（已 gitignore），也可用环境变量
`WORKFLOW_INSTRUCTION_INDEX` 指定。**原文与版权内容不随仓库分发**，本模块只读取
本地生成的要点摘录，用于在建模/算量/计价阶段引用相关施工工序。

提供：
- load_index()：读取并缓存索引（按文件修改时间失效）；
- summary()：条目总数与按类别统计；
- search(query, category)：按关键词/类别过滤；
- related_entries(keywords, limit)：按项目模型/工程量中的名称或标签匹配相关工序条目；
- demo_entries()：没有本地索引时的演示条目（自写通用示例，非南网原文）。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("WORKFLOW_DATA_DIR", str(APP_DIR / "data"))).resolve()
# 索引查找顺序：环境变量 → 应用 data 目录 → 本机作业指导书目录旁的“工序索引”目录
_LOCAL_INDEX_DIR = Path(r"D:\知识库\作业指导书\工序索引")


def _index_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.environ.get("WORKFLOW_INSTRUCTION_INDEX")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(DATA_DIR / "instructions.json")
    candidates.append(_LOCAL_INDEX_DIR / "instructions.json")
    return candidates


def _resolve_index_path() -> Path:
    for candidate in _index_candidates():
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return _index_candidates()[0]


INDEX_PATH = _resolve_index_path()

_CACHE: dict = {"mtime": None, "data": None}

DEMO_ENTRIES = [
    {
        "code": "DEMO-GC-01",
        "title": "杆塔基础开挖与混凝土浇筑（演示条目）",
        "category": "基础与土建",
        "version": "演示数据",
        "source_file": "演示（非作业指导书原文）",
        "tags": ["基础", "土石方", "混凝土", "钢筋", "水泥杆"],
        "steps": ["施工测量 → 定位放线 → 挖土 → 验槽 → 支模 → 钢筋绑扎 → 浇筑 → 养护 → 回填",
                  "挖土深度以设计基础尺寸为准，坑底持力层不得扰动；超挖后按设计要求处理。"],
        "quality": ["基础中心位移与断面尺寸应符合设计；混凝土强度、试块留置按规范执行。",
                    "回填分层夯实，无积水、无杂物。"],
        "safety": ["坑口周边设置围栏与警示，夜间设置警示灯；机械与人工同时作业时划分作业区。",
                    "浇筑与吊装作业设专人监护。"],
        "materials": ["水泥杆、底盘、拉盘、卡盘、钢筋、混凝土、模板。"],
        "standards": ["按现行配网工程施工与验收规范及设计文件执行。"],
        "needs_review": False,
    },
    {
        "code": "DEMO-LX-01",
        "title": "拉线制作与安装（演示条目）",
        "category": "杆塔与拉线",
        "version": "演示数据",
        "source_file": "演示（非作业指导书原文）",
        "tags": ["拉线", "水泥杆", "抱箍", "金具", "接地"],
        "steps": ["拉线盘埋设 → 拉线制作 → 上下把安装 → 调节 → 紧线 → 护线 → 接地连接。"],
        "quality": ["拉线与电杆夹角、拉线坑埋深、上下把制作符合设计与规范；拉线受力正常不松弛。"],
        "safety": ["拉线制作与紧线设专人监护；作业人员不得站在拉线受力方向内侧。"],
        "materials": ["拉线、镀锌铁线、楔形线夹、螺栓、拉线盘。"],
        "standards": ["按设计拉线型式与规格施工。"],
        "needs_review": False,
    },
    {
        "code": "DEMO-DL-01",
        "title": "10kV 架空线路架设与导线安装（演示条目）",
        "category": "架线与导线",
        "version": "演示数据",
        "source_file": "演示（非作业指导书原文）",
        "tags": ["导线", "绝缘子", "金具", "横担", "架线"],
        "steps": ["导线展放 → 紧线 → 弧垂观测 → 耐张线夹安装 → 绝缘子挂设 → 附件安装 → 导线固定。"],
        "quality": ["弧垂、交叉跨越、对地距离符合设计与规范；绝缘子无破损、金具无锈蚀。"],
        "safety": ["放线与紧线设专人指挥，统一信号；跨越带电线路或道路时落实相应安全措施。"],
        "materials": ["导线、绝缘子、金具、横担、线夹、绑扎线。"],
        "standards": ["按设计导线型号与弧垂表施工。"],
        "needs_review": False,
    },
    {
        "code": "DEMO-DL-02",
        "title": "电力电缆敷设与终端/中间接头制作（演示条目）",
        "category": "电缆",
        "version": "演示数据",
        "source_file": "演示（非作业指导书原文）",
        "tags": ["电缆", "终端", "中间接头", "电缆沟", "电缆井", "桥架", "防火封堵"],
        "steps": ["电缆敷设 → 固定 → 终端头/中间接头制作 → 试验 → 防火封堵 → 标识。"],
        "quality": ["电缆弯曲半径、敷设固定间距、终端与接头的制作工艺符合规范；绝缘试验合格后送电。"],
        "safety": ["电缆盘放置与敷设设专人指挥；进入电缆井前通风并检测气体，使用安全带与照明。"],
        "materials": ["电力电缆、终端头、中间接头、支架、桥架、防火封堵材料、标志牌。"],
        "standards": ["按设计电缆路径与断面敷设，穿越防火分区做封堵。"],
        "needs_review": False,
    },
    {
        "code": "DEMO-TB-01",
        "title": "配变（台架变/箱变）安装与接地（演示条目）",
        "category": "变压器与台区",
        "version": "演示数据",
        "source_file": "演示（非作业指导书原文）",
        "tags": ["变压器", "台架变", "箱变", "接地", "接地网", "围栏"],
        "steps": ["基础与支架验收 → 变压器就位 → 固定 → 接地连接 → 高低压接线 → 联锁与防护 → 试验送电。"],
        "quality": ["安装垂直度、接地电阻、净距、油位/气压与联锁装置符合设计与规范；交接试验合格。"],
        "safety": ["吊装设专人指挥，作业人员不得站在吊物下方；带电作业严格按审批执行。"],
        "materials": ["配变、台架或基础、接地体、围栏、标识牌、绝缘护罩。"],
        "standards": ["按设计容量与接线组别安装，交接试验按现行试验规程执行。"],
        "needs_review": False,
    },
    {
        "code": "DEMO-KG-01",
        "title": "接地网与防雷接地安装（演示条目）",
        "category": "接地与防雷",
        "version": "演示数据",
        "source_file": "演示（非作业指导书原文）",
        "tags": ["接地", "接地网", "接地体", "避雷器", "连接"],
        "steps": ["接地体敷设 → 焊接连接 → 防腐处理 → 接地引下线 → 接地电阻测试 → 完善标识。"],
        "quality": ["接地体规格、连接可靠性与接地电阻值符合设计与规范；连接处防腐处理到位。"],
        "safety": ["深沟作业设监护与围栏；焊接作业做好防火与防护。"],
        "materials": ["接地体、接地引下线、连接件、防腐材料、警示标识。"],
        "standards": ["按设计接地电阻要求施工并实测。"],
        "needs_review": False,
    },
]


def _index_mtime() -> float:
    try:
        return INDEX_PATH.stat().st_mtime
    except OSError:
        return 0.0


def load_index() -> dict:
    """读取本地索引（按 mtime 缓存）；没有索引时返回演示条目。"""
    global INDEX_PATH
    resolved = _resolve_index_path()
    if resolved != INDEX_PATH:
        INDEX_PATH = resolved
        _CACHE["mtime"] = None
    mtime = _index_mtime()
    if _CACHE["mtime"] == mtime and _CACHE["data"] is not None:
        return _CACHE["data"]
    data: dict
    if mtime:
        try:
            data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    else:
        data = {}
    if not data.get("entries"):
        data = {
            "generated_at": "",
            "source": "",
            "note": "未找到本机作业指导书索引，当前为演示条目（通用示例，非作业指导书原文）。",
            "stats": {"entries": len(DEMO_ENTRIES), "demo": True},
            "entries": DEMO_ENTRIES,
        }
    _CACHE["mtime"] = mtime
    _CACHE["data"] = data
    return data


def is_demo() -> bool:
    return bool(load_index().get("stats", {}).get("demo"))


def all_entries() -> list[dict]:
    return list(load_index().get("entries") or [])


def summary() -> dict:
    entries = all_entries()
    categories: dict[str, int] = {}
    for entry in entries:
        name = entry.get("category") or "其它"
        categories[name] = categories.get(name, 0) + 1
    stats = load_index().get("stats", {})
    return {
        "entries": len(entries),
        "categories": categories,
        "generated_at": load_index().get("generated_at") or "",
        "source": load_index().get("source") or "",
        "note": load_index().get("note") or "",
        "demo": is_demo(),
        "needs_review": int(stats.get("needs_review") or 0),
        "documents": int(stats.get("documents") or 0),
    }


def search(query: str = "", category: str = "") -> list[dict]:
    """按关键词与类别过滤条目。"""
    entries = all_entries()
    keyword = (query or "").strip().lower()
    result = []
    for entry in entries:
        if category and (entry.get("category") or "") != category:
            continue
        if keyword:
            haystack = " ".join([
                str(entry.get("code") or ""), str(entry.get("title") or ""),
                str(entry.get("category") or ""), str(entry.get("version") or ""),
                " ".join(entry.get("tags") or []),
                " ".join(entry.get("steps") or []),
                " ".join(entry.get("quality") or []),
                " ".join(entry.get("safety") or []),
                " ".join(entry.get("materials") or []),
            ]).lower()
            if keyword not in haystack:
                continue
        result.append(entry)
    return result


def related_entries(keywords, limit: int = 6) -> list[dict]:
    """按项目里的名称/规格关键词找出相关工序条目（标题或标签命中即算）。"""
    words = [str(word).strip() for word in (keywords or []) if str(word).strip()]
    if not words:
        return []
    scored: list[tuple[int, dict]] = []
    for entry in all_entries():
        title = str(entry.get("title") or "")
        tags = [str(tag) for tag in (entry.get("tags") or [])]
        score = 0
        for word in words:
            if word and word in title:
                score += 3
            elif any(word in tag or tag in word for tag in tags):
                score += 2
            elif word and word in str(entry.get("category") or ""):
                score += 1
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("title") or "")))
    return [entry for _, entry in scored[:limit]]


def entry_payload(entry: dict) -> dict:
    """模板用到的精简字段。"""
    return {
        "code": entry.get("code") or "",
        "title": entry.get("title") or "",
        "category": entry.get("category") or "",
        "version": entry.get("version") or "",
        "source_file": entry.get("source_file") or "",
        "tags": entry.get("tags") or [],
        "steps": entry.get("steps") or [],
        "quality": entry.get("quality") or [],
        "safety": entry.get("safety") or [],
        "materials": entry.get("materials") or [],
        "standards": entry.get("standards") or [],
        "needs_review": bool(entry.get("needs_review")),
    }


def keyword_tokens(text: str) -> list[str]:
    """从一行文字里抽出可用于匹配的关键词（中文词块 + 常见材料名）。"""
    tokens = re.findall(r"[一-龥]{2,6}|[A-Za-z0-9\-]{3,}", text or "")
    return [token for token in tokens if len(token) >= 2][:12]
