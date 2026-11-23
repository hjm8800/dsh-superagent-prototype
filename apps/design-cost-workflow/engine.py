# -*- coding: utf-8 -*-
"""engine.py — 配网建模、模型校验、工程量计算、材料量对比与阶段状态机。

公共接口与内部契约第 6 节一致：

    STAGES / init_project_stages / get_stages / stage_editable
    submit_stage / review_stage / project_progress
    autofill_model / validate_model / compute_quantities
    material_compare / project_stats

本模块只依赖标准库与 rates.py，不感知 Web 层；写库函数内部 commit。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import rates
import typdesign

#: 四阶段定义（顺序即流程顺序）
STAGES = [
    ("drawings", "图纸解析"),
    ("model", "建模"),
    ("quantities", "算量"),
    ("pricing", "计价"),
]
STAGE_KEYS = [key for key, _ in STAGES]
STAGE_NAMES = dict(STAGES)
NEXT_STAGE = {STAGES[i][0]: STAGES[i + 1][0] for i in range(len(STAGES) - 1)}
LEVEL_ORDER = {"高": 0, "中": 1, "低": 2}

#: 系统身份：seed 初始化数据、material_compare 兜底计算时使用
SYSTEM_USER = {"id": 1, "username": "admin", "name": "管理员", "role": "admin"}

# 提交 / 复核通过 / 退回时 projects.stage 的展示标签
_SUBMIT_LABEL = {"drawings": "图纸解析", "model": "建模", "quantities": "算量复核", "pricing": "计价"}
_PASS_LABEL = {"drawings": "建模", "model": "算量", "quantities": "计价", "pricing": "计价定稿"}
_RETURN_LABEL = {"drawings": "图纸解析", "model": "建模", "quantities": "算量", "pricing": "计价"}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def now() -> str:
    """当前时间字符串（YYYY-MM-DD HH:MM）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _as_dict(row: Any) -> dict:
    """把 sqlite3.Row / dict 统一成 dict。"""
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


def _audit(conn, project_id: int, user: dict | None, action: str, detail: str = "") -> None:
    user = user or SYSTEM_USER
    conn.execute(
        "INSERT INTO audit (project_id, user_id, action, detail, created_at) VALUES (?,?,?,?,?)",
        (project_id, user.get("id"), action, detail, now()),
    )


def _fmt(value: float | int | None, ndigits: int = 4) -> str:
    """数字转文本：整数省略小数位，小数最多保留 ndigits 位。"""
    if value is None:
        return "0"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    text = f"{number:.{ndigits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _line_voltage(line: dict) -> str:
    """线路电压等级：'10kV' 或 '1kV'。"""
    text = str(line.get("voltage") or "")
    return "10kV" if "10kV" in text or "10KV" in text else "1kV"


def _audit_ok(conn, project_id: int, user: dict | None, action: str, detail: str = "") -> None:
    _audit(conn, project_id, user, action, detail)


# ---------------------------------------------------------------------------
# 阶段状态机
# ---------------------------------------------------------------------------

def init_project_stages(conn, project_id: int) -> None:
    """为项目初始化四个阶段：首个阶段“进行中”，其余“未开始”（已存在则忽略）。"""
    for index, (key, name) in enumerate(STAGES):
        initial_status = "进行中" if index == 0 else "未开始"
        conn.execute(
            "INSERT OR IGNORE INTO stages (project_id, key, name, status, progress, version) "
            "VALUES (?,?,?,?,0,0)",
            (project_id, key, name, initial_status),
        )
    conn.commit()


def get_stages(conn, project_id: int) -> list[dict]:
    """按 STAGES 顺序返回项目四阶段状态（缺行时补默认值）。"""
    rows = {row["key"]: row for row in _rows(conn, "SELECT * FROM stages WHERE project_id=?", (project_id,))}
    result = []
    for key, name in STAGES:
        row = rows.get(key) or {}
        result.append({
            "key": key,
            "name": row.get("name") or name,
            "status": row.get("status") or "未开始",
            "owner": row.get("owner") or "",
            "reviewer": row.get("reviewer") or "",
            "progress": int(row.get("progress") or 0),
            "submitted_at": row.get("submitted_at") or "",
            "reviewed_at": row.get("reviewed_at") or "",
            "version": int(row.get("version") or 0),
            "note": row.get("note") or "",
        })
    return result


def stage_editable(conn, project_id: int, key: str) -> bool:
    """该阶段当前是否允许造价员编辑（仅“进行中”可编辑）。"""
    row = _one(conn, "SELECT status FROM stages WHERE project_id=? AND key=?", (project_id, key))
    return bool(row and row["status"] == "进行中")


def _set_project_stage(conn, project_id: int, label: str) -> None:
    conn.execute("UPDATE projects SET stage=? WHERE id=?", (label, project_id))


def _check_submit_prereq(conn, project_id: int, key: str) -> tuple[bool, str]:
    """阶段提交前置条件校验，返回 (ok, 中文原因)。"""
    if key == "drawings":
        count = _one(
            conn,
            "SELECT COUNT(*) AS n FROM drawings WHERE project_id=? AND status='已识别'",
            (project_id,),
        )["n"]
        if count < 3:
            return False, f"图纸解析阶段至少需要 3 条“已识别”状态的图纸（当前 {count} 条），请先上传并识别图纸"
        return True, ""
    if key == "model":
        line_count = _one(conn, "SELECT COUNT(*) AS n FROM model_lines WHERE project_id=?", (project_id,))["n"]
        if line_count < 1:
            return False, "建模阶段至少需要 1 条线路，请先补全或从图纸生成模型初稿"
        equipment_count = _one(
            conn,
            "SELECT COUNT(*) AS n FROM model_equipment WHERE project_id=? AND category='设备'",
            (project_id,),
        )["n"]
        if equipment_count < 1:
            return False, "建模阶段至少需要 1 条设备，请先补全设备清单"
        high = [issue for issue in validate_model(conn, project_id) if issue["level"] == "高"]
        if high:
            first = high[0]
            return False, (
                f"模型校验存在 {len(high)} 个高级问题，请修正后再提交"
                f"（首个问题：{first['name']} — {first['problem']}）"
            )
        return True, ""
    if key == "quantities":
        count = _one(conn, "SELECT COUNT(*) AS n FROM quantities WHERE project_id=?", (project_id,))["n"]
        if count <= 0:
            return False, "算量结果为空，请先在算量页执行“重算工程量”"
        return True, ""
    if key == "pricing":
        count = _one(conn, "SELECT COUNT(*) AS n FROM pricing_fees WHERE project_id=?", (project_id,))["n"]
        if count <= 0:
            return False, "计价结果为空，请先在计价页执行“计价”"
        return True, ""
    return False, "未知阶段"


def submit_stage(conn, project_id: int, key: str, user: dict) -> tuple[bool, str]:
    """提交阶段复核：校验角色与前置条件 → 状态置“待复核”。失败不落库。"""
    if key not in STAGE_NAMES:
        return False, "未知的阶段"
    user = user or {}
    role = user.get("role") or ""
    if role not in ("admin", "zaojia"):
        return False, "当前角色无提交权限（需造价员或管理员）"
    stage = _one(conn, "SELECT * FROM stages WHERE project_id=? AND key=?", (project_id, key))
    if stage is None:
        return False, "阶段状态不存在，请重新初始化项目阶段"
    if stage["status"] == "待复核":
        return False, f"{STAGE_NAMES[key]}阶段已提交，正在等待复核"
    if stage["status"] == "已完成":
        return False, f"{STAGE_NAMES[key]}阶段已完成，无需重复提交"
    if stage["status"] == "未开始":
        return False, f"{STAGE_NAMES[key]}阶段尚未开始，请先完成上一阶段"
    ok, reason = _check_submit_prereq(conn, project_id, key)
    if not ok:
        return False, reason
    timestamp = now()
    progress = max(int(stage.get("progress") or 0), 60)
    conn.execute(
        "UPDATE stages SET status='待复核', owner=?, submitted_at=?, progress=? WHERE project_id=? AND key=?",
        (user.get("name") or "造价员", timestamp, progress, project_id, key),
    )
    _set_project_stage(conn, project_id, _SUBMIT_LABEL[key])
    _audit_ok(conn, project_id, user, f"提交{STAGE_NAMES[key]}阶段复核", f"提交人：{user.get('name') or '造价员'}")
    conn.commit()
    return True, f"{STAGE_NAMES[key]}阶段已提交复核"


def review_stage(conn, project_id: int, key: str, user: dict, result: str, comment: str) -> tuple[bool, str]:
    """复核阶段：通过 → 已完成并流转下一阶段；退回 → 回到进行中。"""
    if key not in STAGE_NAMES:
        return False, "未知的阶段"
    user = user or {}
    role = user.get("role") or ""
    if role not in ("admin", "fuhe"):
        return False, "当前角色无复核权限（需复核员或管理员）"
    stage = _one(conn, "SELECT * FROM stages WHERE project_id=? AND key=?", (project_id, key))
    if stage is None:
        return False, "阶段状态不存在，请重新初始化项目阶段"
    if stage["status"] != "待复核":
        return False, f"{STAGE_NAMES[key]}阶段当前状态为“{stage['status']}”，不能复核"
    reviewer_name = user.get("name") or "复核员"
    if role != "admin" and reviewer_name == (stage.get("owner") or ""):
        return False, "复核人不能与提交人相同"
    result = (result or "").strip()
    comment = (comment or "").strip()
    if result not in ("通过", "退回"):
        return False, "复核结论无效（应为“通过”或“退回”）"
    timestamp = now()
    conn.execute(
        "INSERT INTO reviews (project_id, stage_key, reviewer, result, comment, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (project_id, key, reviewer_name, result, comment, timestamp),
    )
    if result == "通过":
        conn.execute(
            "UPDATE stages SET status='已完成', reviewer=?, reviewed_at=?, progress=100, version=version+1 "
            "WHERE project_id=? AND key=?",
            (reviewer_name, timestamp, project_id, key),
        )
        next_key = NEXT_STAGE.get(key)
        if next_key:
            conn.execute(
                "UPDATE stages SET status='进行中' WHERE project_id=? AND key=? AND status='未开始'",
                (project_id, next_key),
            )
        _set_project_stage(conn, project_id, _PASS_LABEL[key])
        _audit_ok(conn, project_id, user, f"复核通过：{STAGE_NAMES[key]}",
                  f"复核人：{reviewer_name}；意见：{comment or '无'}")
        conn.commit()
        if next_key:
            return True, f"{STAGE_NAMES[key]}阶段复核通过，已流转至“{STAGE_NAMES[next_key]}”阶段"
        return True, f"{STAGE_NAMES[key]}阶段复核通过，项目全流程已完成"
    # 退回
    note = f"复核退回：{comment}" if comment else "复核退回"
    conn.execute(
        "UPDATE stages SET status='进行中', reviewer=?, reviewed_at=?, progress=?, note=? "
        "WHERE project_id=? AND key=?",
        (reviewer_name, timestamp, 40, note[:200], project_id, key),
    )
    _set_project_stage(conn, project_id, _RETURN_LABEL[key])
    _audit_ok(conn, project_id, user, f"复核退回：{STAGE_NAMES[key]}",
              f"复核人：{reviewer_name}；意见：{comment or '无'}")
    conn.commit()
    return True, f"{STAGE_NAMES[key]}阶段已退回，请按复核意见修改后重新提交"


def project_progress(conn, project_id: int) -> dict:
    """项目整体进度：完成阶段数 / 总数 / 百分比 / 当前阶段 key。"""
    stages = get_stages(conn, project_id)
    total = len(stages) or 1
    done = sum(1 for stage in stages if stage["status"] == "已完成")
    current = next((stage["key"] for stage in stages if stage["status"] != "已完成"), None)
    current_stage = next((stage for stage in stages if stage["status"] != "已完成"), None)
    running_ratio = (int(current_stage["progress"]) / 100.0) if current_stage else 0.0
    percent = int(round((done + running_ratio) * 100.0 / total))
    percent = max(0, min(100, percent))
    return {"done": done, "total": len(stages), "percent": percent, "current": current or "pricing"}


# ---------------------------------------------------------------------------
# 模型校验
# ---------------------------------------------------------------------------

def validate_model(conn, project_id: int) -> list[dict]:
    """模型校验：返回 [{level,name,problem,hint}]，按 高→中→低 排序。"""
    issues: list[dict] = []
    lines = _rows(conn, "SELECT * FROM model_lines WHERE project_id=? ORDER BY id", (project_id,))
    equipment = _rows(conn, "SELECT * FROM model_equipment WHERE project_id=? ORDER BY id", (project_id,))
    civils = _rows(conn, "SELECT * FROM model_civil WHERE project_id=? ORDER BY id", (project_id,))
    demolitions = _rows(conn, "SELECT * FROM model_demolition WHERE project_id=? ORDER BY id", (project_id,))

    for index, line in enumerate(lines, 1):
        name = line.get("name") or f"线路{index}"
        voltage = str(line.get("voltage") or "")
        phases = int(line.get("phases") or 0)
        poles = int(line.get("poles") or 0)
        length_km = float(line.get("length_km") or 0)
        span_m = float(line.get("span_m") or 0)
        stays = int(line.get("stays") or 0)
        if not (line.get("conductor") or "").strip():
            issues.append({"level": "高", "name": name, "problem": "线路缺少导线型号",
                           "hint": "在建模页补充导线型号，如 JKLGYJ-70/10 或 BLVV-240/BLVV-120"})
        if poles <= 0:
            issues.append({"level": "高", "name": name, "problem": "杆塔数量为 0",
                           "hint": "按杆塔明细表补充杆塔数量"})
        if length_km <= 0:
            issues.append({"level": "高", "name": name, "problem": "线路亘长为 0",
                           "hint": "按平面图补充线路亘长（km）"})
        if "10kV" in voltage and phases != 3:
            issues.append({"level": "高", "name": name,
                           "problem": f"相数与电压不符（10kV 应为 3 相，当前 {phases} 相）",
                           "hint": "核对线路相数"})
        if "1kV" in voltage and phases != 4:
            issues.append({"level": "高", "name": name,
                           "problem": f"相数与电压不符（1kV 低压应为 4 相，当前 {phases} 相）",
                           "hint": "低压四线制应为 4 相"})
        if span_m > 0 and poles > 0 and length_km > 0:
            calc_km = span_m * poles / 1000.0
            deviation = abs(calc_km - length_km) / length_km
            if deviation > 0.20:
                issues.append({"level": "高", "name": name,
                               "problem": f"档距×杆塔数（{calc_km:.4f}km）与线路亘长（{length_km}km）偏差超过 20%",
                               "hint": "核对杆塔数量、平均档距或线路亘长"})
        if span_m > 0 and not (30 <= span_m <= 80):
            issues.append({"level": "中", "name": name,
                           "problem": f"档距 {_fmt(span_m)}m 不在 30–80m 常用范围",
                           "hint": "核对杆塔明细表平均档距；特殊跨越可备注说明"})
        if stays == 0 and poles > 3:
            issues.append({"level": "中", "name": name,
                           "problem": f"杆塔 {poles} 基但未设置拉线",
                           "hint": "按杆塔明细表补充拉线数量（转角、终端杆需拉线）"})

    for equip in equipment:
        if equip.get("category") != "设备":
            continue
        name = equip.get("name") or f"设备{equip.get('id')}"
        if float(equip.get("unit_price") or 0) <= 0:
            issues.append({"level": "高", "name": name, "problem": "设备缺少单价",
                           "hint": "按设备材料清册补充设备单价（元/单位）"})
        if (equip.get("supply") or "").strip() not in ("甲供", "乙供"):
            issues.append({"level": "低", "name": name, "problem": "设备未标注甲供/乙供",
                           "hint": "在设备清单中标注供应方式"})

    has_foundation = any("基础" in (civil.get("name") or "") for civil in civils)
    if not has_foundation:
        issues.append({"level": "中", "name": "台架变工程", "problem": "缺少台架变基础土建项",
                       "hint": "在土建清单补充“台架变基础 C20混凝土”"})

    for demolition in demolitions:
        name = demolition.get("name") or f"拆除项{demolition.get('id')}"
        if not ((demolition.get("note") or "").strip() or (demolition.get("spec") or "").strip()):
            issues.append({"level": "低", "name": name, "problem": "拆除项缺少依据",
                           "hint": "补充拆除依据（图纸/现场勘察/拆除方案）"})

    issues.sort(key=lambda item: LEVEL_ORDER.get(item["level"], 9))
    return issues


# ---------------------------------------------------------------------------
# 由图纸生成模型初稿
# ---------------------------------------------------------------------------

def _parse_number(pattern: str, text: str) -> float | None:
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return None


def autofill_model(conn, project_id: int, user: dict) -> int:
    """依据已识别图纸的识别结论生成/补全模型初稿，返回新增行数；写审计。"""
    user = user or SYSTEM_USER
    drawings = _rows(
        conn,
        "SELECT * FROM drawings WHERE project_id=? AND status='已识别' ORDER BY id",
        (project_id,),
    )
    if not drawings:
        return 0
    text = " ".join((drawing.get("summary") or "") for drawing in drawings)
    length_10kv = _parse_number(r"10kV路径[^0-9]{0,10}([0-9]+(?:\.[0-9]+)?)\s*km", text)
    if length_10kv is None:
        length_10kv = _parse_number(r"10kV[^，。；;]{0,16}?([0-9]+(?:\.[0-9]+)?)\s*km", text)
    length_lv = _parse_number(r"低压路径[^0-9]{0,10}([0-9]+(?:\.[0-9]+)?)\s*km", text)
    if length_lv is None:
        length_lv = _parse_number(r"低压[^，。；;]{0,16}?([0-9]+(?:\.[0-9]+)?)\s*km", text)
    poles = _parse_number(r"杆塔\s*([0-9]+)\s*基", text)
    capacity = _parse_number(r"配变(?:容量)?\s*[：:]?\s*([0-9]+)\s*kVA", text)

    added = 0
    lines = _rows(conn, "SELECT * FROM model_lines WHERE project_id=? ORDER BY id", (project_id,))
    if not lines and (length_10kv is not None or length_lv is not None or capacity is not None):
        def estimate_poles(length: float) -> int:
            if poles:
                return max(2, int(round(poles)))
            return max(2, int(round(length * 1000 / 50.0)))
        if length_10kv is not None or length_lv is None:
            main_length = length_10kv if length_10kv is not None else 0.05
            main_poles = estimate_poles(main_length)
            conn.execute(
                "INSERT INTO model_lines (project_id, name, voltage, start_point, end_point, length_km, "
                "circuits, phases, conductor, poles, pole_type, span_m, stays, soil, source, note) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (project_id, "10kV线路", "10kV", "10kV线路T接点", "台架变1#杆", main_length, 1, 3,
                 "JKLGYJ-70/10", main_poles, "Φ150×8m",
                 round(main_length * 1000 / main_poles, 1), 0, "普通土", "图纸识别", "自动生成初稿，请人工校正"),
            )
            added += 1
        if length_lv is not None:
            lv_poles = max(2, int(round(poles))) if poles else max(2, int(round(length_lv * 1000 / 50.0)))
            conn.execute(
                "INSERT INTO model_lines (project_id, name, voltage, start_point, end_point, length_km, "
                "circuits, phases, conductor, poles, pole_type, span_m, stays, soil, source, note) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (project_id, "低压线路", "1kV", "台架变低压侧", "末梢杆", length_lv, 1, 4,
                 "JKLYJ-240(1kV)", max(2, lv_poles // 2 if length_10kv is not None else lv_poles),
                 "Φ150×8m", round(length_lv * 1000 / max(2, lv_poles // 2 if length_10kv is not None else lv_poles), 1),
                 0, "普通土", "图纸识别", "自动生成初稿，请人工校正"),
            )
            added += 1

    equipment = _rows(
        conn,
        "SELECT * FROM model_equipment WHERE project_id=? AND category='设备' ORDER BY id",
        (project_id,),
    )
    if not equipment and capacity:
        capacity = int(round(capacity))
        unit_price = 68000.0 if capacity >= 500 else 45000.0
        box_spec = "XLW-10/500-4" if capacity >= 500 else "XLW-10/200"
        box_price = 33000.0 if capacity >= 500 else 19000.0
        rows = [
            ("设备", "配电变压器", f"SB20-M-{capacity}kVA", "台", 1, unit_price, "甲供"),
            ("设备", "变压器配电箱", box_spec, "台", 1, box_price, "甲供"),
            ("设备", "配电智能网关", "", "套", 1, 9500.0, "甲供"),
        ]
        for category, name, spec, unit, qty, price, supply in rows:
            conn.execute(
                "INSERT INTO model_equipment (project_id, category, name, spec, unit, qty, unit_price, "
                "supply, source, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (project_id, category, name, spec, unit, qty, price, supply, "图纸识别", "自动生成初稿，请人工核对"),
            )
            added += 1

    civils = _rows(conn, "SELECT * FROM model_civil WHERE project_id=? ORDER BY id", (project_id,))
    if not civils and added > 0:
        conn.execute(
            "INSERT INTO model_civil (project_id, name, spec, unit, qty, note) VALUES (?,?,?,?,?,?)",
            (project_id, "台架变基础", "C20混凝土", "m³", 2.4, "图纸识别：台架变基础尺寸估算"),
        )
        added += 1

    _audit_ok(conn, project_id, user, "从已识别图纸生成模型初稿",
              f"识别图纸 {len(drawings)} 份，新增模型行 {added} 条")
    conn.commit()
    return added


# ---------------------------------------------------------------------------
# 工程量计算
# ---------------------------------------------------------------------------

class _QuantityBuilder:
    """工程量清单构建器：统一生成 seq 与字段。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.seq = 0

    def add(self, unit_name: str, name: str, spec: str, unit: str, qty: float,
            formula: str, basis: str, category: str, quota_code: str = "",
            note: str = "", code: str = "") -> None:
        self.seq += 1
        self.rows.append({
            "seq": self.seq,
            "unit_name": unit_name,
            "code": code,
            "name": name,
            "spec": spec or "",
            "unit": unit or "",
            "qty": float(qty or 0),
            "formula": formula,
            "basis": basis,
            "category": category,
            "quota_code": quota_code,
            "status": "已计算",
            "note": note,
        })


#: 规则 9：主材行 →（安装子目、安装清单名称、单位、单位工程）
_MAIN_INSTALL_RULES = [
    (("避雷器",), "YD3-0401", "10kV避雷器安装", "只", "台架变工程"),
    (("电力电缆",), "YD3-0501", "电力电缆敷设", "千米", "电缆工程"),
    (("终端头",), "YD3-0502", "电力电缆终端头制作", "个", "电缆工程"),
    (("接地装置",), "YD2-0401", "接地装置安装", "组", "台架变工程"),
    (("围栏",), "YD2-0501", "安全围栏安装", "套", "台架变工程"),
    (("台架变金具",), "YD2-0601", "台架变金具及支架安装", "套", "台架变工程"),
]


def _find_main_install_rule(name: str) -> tuple[str, str, str, str] | None:
    for keywords, quota, item_name, unit, unit_name in _MAIN_INSTALL_RULES:
        if any(keyword in (name or "") for keyword in keywords):
            return quota, item_name, unit, unit_name
    return None


def _line_conductor_equipment(line: dict, main_rows: list[dict]) -> list[dict]:
    """找出与线路导线匹配的主材行（用于以清册量为准生成导线主材）。"""
    voltage = _line_voltage(line)
    conductor = (line.get("conductor") or "").strip()
    parts = [part.strip() for part in re.split(r"[/、,，\s]+", conductor) if part.strip()]
    matched: list[dict] = []
    for equip in main_rows:
        if not rates.is_conductor_material(equip.get("name") or ""):
            continue
        if rates.conductor_voltage(equip.get("name") or "", equip.get("spec") or "") != voltage:
            continue
        text = f"{equip.get('name') or ''} {equip.get('spec') or ''}"
        if not parts or any(part in text for part in parts):
            matched.append(equip)
    return matched


def _build_quantities(conn, project_id: int) -> list[dict]:
    """按契约 6.1 的规则生成工程量清单行（不写库）。"""
    lines = _rows(conn, "SELECT * FROM model_lines WHERE project_id=? ORDER BY id", (project_id,))
    equipment = _rows(conn, "SELECT * FROM model_equipment WHERE project_id=? ORDER BY id", (project_id,))
    civils = _rows(conn, "SELECT * FROM model_civil WHERE project_id=? ORDER BY id", (project_id,))
    demolitions = _rows(conn, "SELECT * FROM model_demolition WHERE project_id=? ORDER BY id", (project_id,))
    main_rows = [row for row in equipment if row.get("category") == "主材"]
    equipment_rows = [row for row in equipment if row.get("category") == "设备"]

    builder = _QuantityBuilder()

    for line in lines:
        unit_name = line.get("name") or "线路工程"
        voltage = _line_voltage(line)
        phases = int(line.get("phases") or 0)
        length_km = float(line.get("length_km") or 0)
        poles = int(line.get("poles") or 0)
        stays = int(line.get("stays") or 0)
        pole_type = line.get("pole_type") or ""
        conductor = line.get("conductor") or ""

        # 规则 1：导线架设（定额按单线计 → 数量 = 亘长 × 单线根数）
        single_wires = phases if phases > 0 else (3 if voltage == "10kV" else 4)
        stringing_qty = round(length_km * single_wires, 4)
        if voltage == "10kV":
            builder.add(unit_name, "10kV架空线路架设", conductor, "km", stringing_qty,
                        f"图纸识别亘长 {_fmt(length_km)} km × {single_wires} 根（单线） = {_fmt(stringing_qty)} km",
                        f"图纸识别：10kV路径{_fmt(length_km)}km，{phases}相；"
                        f"定额按单线计算：单线根数 {single_wires} → 单线长 {_fmt(stringing_qty)}km",
                        rates.CATEGORY_INSTALL, "YD3-0301")
        else:
            builder.add(unit_name, "低压线路架设", conductor, "km", stringing_qty,
                        f"图纸识别亘长 {_fmt(length_km)} km × {single_wires} 根（单线） = {_fmt(stringing_qty)} km",
                        f"图纸识别：低压路径{_fmt(length_km)}km，{phases}相（线）；"
                        f"定额按单线计算：单线根数 {single_wires} → 单线长 {_fmt(stringing_qty)}km",
                        rates.CATEGORY_INSTALL, "YD3-0302")

        # 规则 2：导线主材（单位 km）——清册有同名导线则以清册量为准，否则按公式
        matched_conductors = _line_conductor_equipment(line, main_rows)
        if matched_conductors:
            for equip in matched_conductors:
                qty = float(equip.get("qty") or 0)
                builder.add(unit_name, equip.get("name") or "绝缘导线", equip.get("spec") or "",
                            equip.get("unit") or "千米", qty,
                            f"清册量 {_fmt(qty)} {equip.get('unit') or '千米'}",
                            "主要设备材料清册：导线主材按清册数量计入，不再按公式重复生成",
                            rates.CATEGORY_MATERIAL, rates.conductor_quota(voltage),
                            note="单价取主材清册")
        else:
            qty = length_km * phases * 1.03
            builder.add(unit_name, "10kV架空绝缘导线" if voltage == "10kV" else "低压绝缘导线",
                        conductor, "km", round(qty, 4),
                        f"{_fmt(length_km)} × {phases} × 1.03 = {_fmt(round(qty, 4))}",
                        f"图纸识别：{voltage}路径{_fmt(length_km)}km，{phases}相；预留损耗3%",
                        rates.CATEGORY_MATERIAL, rates.conductor_quota(voltage),
                        note="按公式计算并计入3%预留损耗")

        # 规则 3：电杆组立 + 水泥杆主材
        if poles > 0:
            pole_quota = rates.pole_quota(pole_type)
            builder.add(unit_name, "电杆组立", pole_type, "根", poles,
                        f"{poles} 基 × 1 = {poles}",
                        f"杆塔明细表：{pole_type or '水泥杆'} {poles} 基",
                        rates.CATEGORY_INSTALL, pole_quota)
            builder.add(unit_name, "水泥杆", pole_type, "根", poles,
                        f"{poles} × 1 = {poles}",
                        "杆塔明细表：杆型与数量；单价取主材清册",
                        rates.CATEGORY_MATERIAL, pole_quota, note="单价取主材清册")

        # 规则 4：拉线制作安装 + 拉线主材
        if stays > 0:
            builder.add(unit_name, "拉线制作安装", "", "条", stays,
                        f"{stays} 条 × 1 = {stays}",
                        f"杆塔明细表：拉线 {stays} 条",
                        rates.CATEGORY_INSTALL, "YD3-0201")
            builder.add(unit_name, "拉线", "含拉线棒/盘/钢绞线", "条", stays,
                        f"{stays} × 1 = {stays}",
                        "杆塔明细表：拉线数量；单价取主材清册",
                        rates.CATEGORY_MATERIAL, "YD3-0201", note="单价取主材清册")

        # 规则 5：杆坑/拉线坑土方（建筑）
        if poles > 0:
            qty = poles * 1.2
            builder.add(unit_name, "杆坑土方开挖", "普通土", "m³", round(qty, 4),
                        f"{poles} × 1.2 = {_fmt(round(qty, 4))}",
                        "电杆组立：每基杆坑土方 1.2m³（普通土）",
                        rates.CATEGORY_BUILD, "YD1-0101")
        if stays > 0:
            qty = stays * 0.8
            builder.add(unit_name, "拉线坑土方开挖", "普通土", "m³", round(qty, 4),
                        f"{stays} × 0.8 = {_fmt(round(qty, 4))}",
                        "拉线制作：每条拉线坑土方 0.8m³（普通土）",
                        rates.CATEGORY_BUILD, "YD1-0102")

        # 规则 6：金具安装 + 金具主材
        if poles > 0:
            if voltage == "10kV":
                builder.add(unit_name, "10kV线路金具安装", "横担/绝缘子/铁件", "套", poles,
                            f"{poles} 套 × 1 = {poles}",
                            f"杆塔明细表：10kV 线路金具 {poles} 套",
                            rates.CATEGORY_INSTALL, "YD3-0601")
                builder.add(unit_name, "10kV金具", "横担/绝缘子/铁件", "套", poles,
                            f"{poles} × 1 = {poles}",
                            "杆塔明细表：金具数量；单价取主材清册",
                            rates.CATEGORY_MATERIAL, "YD3-0601", note="单价取主材清册")
            else:
                builder.add(unit_name, "低压线路金具安装", "四线横担/铁件", "套", poles,
                            f"{poles} 套 × 1 = {poles}",
                            f"杆塔明细表：低压线路金具 {poles} 套",
                            rates.CATEGORY_INSTALL, "YD3-0602")
                builder.add(unit_name, "低压金具", "四线横担/铁件", "套", poles,
                            f"{poles} × 1 = {poles}",
                            "杆塔明细表：金具数量；单价取主材清册",
                            rates.CATEGORY_MATERIAL, "YD3-0602", note="单价取主材清册")

    # 规则 8：设备行 → 安装清单（设备本身计入设备购置费，不进安装主材）
    # 注意：先匹配“配电箱”，避免“变压器配电箱”被误判为变压器（契约 6.1 规则 8）
    for equip in equipment_rows:
        name = equip.get("name") or ""
        spec = equip.get("spec") or ""
        qty = float(equip.get("qty") or 0)
        unit = equip.get("unit") or "台"
        if "配电箱" in name:
            builder.add("台架变工程", "低压配电箱安装", spec, unit, qty,
                        f"清册量 {_fmt(qty)} {unit}（设备行）",
                        "主要设备材料清册：设备本身计入设备购置费，不计安装主材",
                        rates.CATEGORY_INSTALL, "YD2-0201")
        elif "变压器" in name:
            quota, item_name = ("YD2-0101", "台架式变压器安装") if ("500" in spec or "500" in name) \
                else ("YD2-0102", "台架式变压器安装")
            builder.add("台架变工程", item_name, spec, unit, qty,
                        f"清册量 {_fmt(qty)} {unit}（设备行）",
                        "主要设备材料清册：设备本身计入设备购置费，不计安装主材",
                        rates.CATEGORY_INSTALL, quota)
        elif "网关" in name:
            builder.add("台架变工程", "配电智能网关安装", spec, unit, qty,
                        f"清册量 {_fmt(qty)} {unit}（设备行）",
                        "主要设备材料清册：设备本身计入设备购置费，不计安装主材",
                        rates.CATEGORY_INSTALL, "YD2-0301")

    # 规则 9：主材行 → 安装清单 + 安装主材（导线/水泥杆/拉线/金具已在规则 2/3/4/6 处理）
    for equip in main_rows:
        name = equip.get("name") or ""
        if rates.is_conductor_material(name):
            continue
        if any(keyword in name for keyword in ("水泥杆", "拉线", "金具")):
            # 台架变金具及支架属于规则 9，需单独处理；此处跳过线路金具/拉线/水泥杆
            if not any(keyword in name for keyword in ("台架变金具", "支架")):
                continue
        rule = _find_main_install_rule(name)
        if rule is None:
            continue
        quota, item_name, install_unit, unit_name = rule
        qty = float(equip.get("qty") or 0)
        spec = equip.get("spec") or ""
        builder.add(unit_name, item_name, spec, install_unit, qty,
                    f"清册量 {_fmt(qty)} {install_unit}（主材行）",
                    "主要设备材料清册：安装主材按清册数量计入",
                    rates.CATEGORY_INSTALL, quota)
        builder.add(unit_name, name, spec, equip.get("unit") or install_unit, qty,
                    f"清册量 {_fmt(qty)} {equip.get('unit') or install_unit}",
                    "主要设备材料清册：主材数量与单价",
                    rates.CATEGORY_MATERIAL, quota, note="单价取主材清册")

    # 规则 10：土建
    for civil in civils:
        name = civil.get("name") or ""
        qty = float(civil.get("qty") or 0)
        unit = civil.get("unit") or ""
        spec = civil.get("spec") or ""
        if "沟" in name:
            builder.add("电缆工程", name, spec, unit, qty,
                        f"{_fmt(qty)} {unit}（土建清册）",
                        "土建工程量依据：施工图（电缆沟尺寸见平面图）",
                        rates.CATEGORY_BUILD, "YD1-0301")
        else:
            builder.add("台架变工程", name, spec, unit, qty,
                        f"{_fmt(qty)} {unit}（土建清册）",
                        "土建工程量依据：施工图（台架变基础）",
                        rates.CATEGORY_BUILD, "YD1-0201")

    # 规则 11：拆除
    for demolition in demolitions:
        name = demolition.get("name") or ""
        qty = float(demolition.get("qty") or 0)
        unit = demolition.get("unit") or ""
        spec = demolition.get("spec") or ""
        if "导线" in name:
            quota = "YD5-0101"
        elif "杆" in name:
            quota = "YD5-0102"
        else:
            quota = "YD5-0101"
        basis = demolition.get("note") or "拆除工程量依据：图纸及现场勘察，线路拆除不可利用"
        builder.add("拆除工程", name, spec, unit, qty,
                    f"{_fmt(qty)} {unit}（拆除清册）", basis,
                    rates.CATEGORY_DEMOLITION, quota)

    return builder.rows


def _resolve_model_qty(source: tuple, lines: list[dict], equipment: list[dict],
                       demolition: list[dict]) -> float | None:
    """按设计清册基线的 source 取出模型侧数量。"""
    kind = source[0] if source else ""
    arg = source[1] if len(source) > 1 else None
    if kind == "conductor":
        want = arg or "10kV"
        total = 0.0
        for line in lines:
            if _line_voltage(line) == want:
                total += float(line.get("length_km") or 0) * int(line.get("phases") or 0) * 1.03
        return total
    if kind == "poles":
        return float(sum(int(line.get("poles") or 0) for line in lines))
    if kind == "stays":
        return float(sum(int(line.get("stays") or 0) for line in lines))
    if kind in ("equipment", "main"):
        category = "设备" if kind == "equipment" else "主材"
        keyword = arg or ""
        total = 0.0
        for equip in equipment:
            if equip.get("category") != category:
                continue
            text = f"{equip.get('name') or ''} {equip.get('spec') or ''}"
            if keyword in text:
                total += float(equip.get("qty") or 0)
        return total
    if kind == "demolition":
        keyword = arg or ""
        total = 0.0
        for item in demolition:
            if keyword in (item.get("name") or ""):
                total += float(item.get("qty") or 0)
        return total
    return None


def _compare_model_qty(value: float) -> float:
    """材料量对比口径：公式结果为有限小数时保留 4 位，否则按 3 位展示。

    与契约 5.4 的演示数据一致：A 低压导线 3.2445（精确值）、C 低压导线 0.0618；
    其余由亘长×相数×1.03 得到的长小数按 3 位（如 6.5848→6.585、0.1761→0.176）。
    """
    rounded4 = round(float(value), 4)
    if abs(rounded4 - float(value)) < 1e-9:
        return rounded4
    return round(float(value), 3)


def _judge_compare(base: dict, design_qty: float, model_qty: float) -> tuple[float, float, str, str]:
    """判定材料量差等级与归因，返回 (diff, rate, level, reason)。"""
    diff = round(model_qty - design_qty, 4)
    rate = round(diff / design_qty * 100.0, 2) if design_qty else 0.0
    reserve = base.get("reserve_pct")
    if reserve is not None and abs(rate - float(reserve)) <= 0.5:
        return diff, rate, "正常", base.get("reason") or f"预留损耗{_fmt(reserve)}%，量差正常"
    if abs(rate) < 1.0:
        return diff, rate, "正常", f"量差{rate:+.2f}%，在±1%以内，符合清册口径"
    if abs(rate) <= 5.0:
        return diff, rate, "关注", f"量差{rate:+.2f}%，需核对清册与模型"
    return diff, rate, "异常", base.get("abnormal_reason") or f"量差{rate:+.2f}%，超出正常范围，需核实"


def _build_compare_materials(conn, project_id: int) -> list[dict]:
    """按设计清册基线生成材料量对比行（不写库）。"""
    project = _one(conn, "SELECT * FROM projects WHERE id=?", (project_id,))
    if project is None:
        return []
    baselines = rates.DESIGN_BASELINES.get(project.get("code") or "", [])
    lines = _rows(conn, "SELECT * FROM model_lines WHERE project_id=? ORDER BY id", (project_id,))
    equipment = _rows(conn, "SELECT * FROM model_equipment WHERE project_id=? ORDER BY id", (project_id,))
    demolition = _rows(conn, "SELECT * FROM model_demolition WHERE project_id=? ORDER BY id", (project_id,))
    rows: list[dict] = []
    for seq, base in enumerate(baselines, 1):
        design_qty = float(base.get("design_qty") or 0)
        model_qty = _resolve_model_qty(base.get("source") or (), lines, equipment, demolition)
        if model_qty is None:
            continue
        model_qty = _compare_model_qty(model_qty)
        diff, rate, level, reason = _judge_compare(base, design_qty, model_qty)
        rows.append({
            "seq": seq,
            "name": base.get("name") or "",
            "spec": base.get("spec") or "",
            "unit": base.get("unit") or "",
            "design_qty": round(design_qty, 4),
            "model_qty": round(float(model_qty), 4),
            "diff": diff,
            "rate": rate,
            "level": level,
            "reason": reason,
            "status": "待确认",
        })
    return rows


def compute_quantities(conn, project_id: int, user: dict) -> list[dict]:
    """重算工程量并写入 quantities / compare_materials，写审计，返回工程量清单行。"""
    user = user or SYSTEM_USER
    rows = _build_quantities(conn, project_id)
    compare_rows = _build_compare_materials(conn, project_id)

    conn.execute("DELETE FROM quantities WHERE project_id=?", (project_id,))
    conn.execute("DELETE FROM compare_materials WHERE project_id=?", (project_id,))
    for row in rows:
        conn.execute(
            "INSERT INTO quantities (project_id, seq, unit_name, code, name, spec, unit, qty, formula, "
            "basis, category, quota_code, status, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, row["seq"], row["unit_name"], row["code"], row["name"], row["spec"],
             row["unit"], row["qty"], row["formula"], row["basis"], row["category"],
             row["quota_code"], row["status"], row["note"]),
        )
    for row in compare_rows:
        conn.execute(
            "INSERT INTO compare_materials (project_id, seq, name, spec, unit, design_qty, model_qty, "
            "diff, rate, level, reason, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, row["seq"], row["name"], row["spec"], row["unit"], row["design_qty"],
             row["model_qty"], row["diff"], row["rate"], row["level"], row["reason"], row["status"]),
        )
    abnormal = sum(1 for row in compare_rows if row["level"] == "异常")
    _audit_ok(conn, project_id, user, "重算工程量",
              f"生成工程量清单 {len(rows)} 项，材料量对比 {len(compare_rows)} 行，异常差异 {abnormal} 行")
    conn.commit()
    return _rows(conn, "SELECT * FROM quantities WHERE project_id=? ORDER BY seq, id", (project_id,))


def material_compare(conn, project_id: int) -> list[dict]:
    """读取材料量对比行；若尚未生成则先执行一次算量。"""
    rows = _rows(conn, "SELECT * FROM compare_materials WHERE project_id=? ORDER BY seq, id", (project_id,))
    if rows:
        return rows
    compute_quantities(conn, project_id, SYSTEM_USER)
    return _rows(conn, "SELECT * FROM compare_materials WHERE project_id=? ORDER BY seq, id", (project_id,))


def project_stats(conn, project_id: int) -> dict:
    """项目统计卡数据：线路数、杆塔数、设备数、工程量行数、主材金额、典设统计。"""
    lines = _rows(conn, "SELECT * FROM model_lines WHERE project_id=?", (project_id,))
    equipment = _rows(conn, "SELECT * FROM model_equipment WHERE project_id=?", (project_id,))
    quantity_count = _one(conn, "SELECT COUNT(*) AS n FROM quantities WHERE project_id=?", (project_id,))["n"]
    material_amount = 0.0
    for equip in equipment:
        if equip.get("category") == "主材":
            material_amount += float(equip.get("qty") or 0) * float(equip.get("unit_price") or 0)
    typdesign_stats = typdesign.typdesign_stats(conn, project_id)
    return {
        "lines": len(lines),
        "poles": int(sum(int(line.get("poles") or 0) for line in lines)),
        "equipment": int(sum(1 for equip in equipment if equip.get("category") == "设备")),
        "quantities": int(quantity_count),
        "material_amount": round(material_amount, 2),
        # v2：典型设计匹配统计（工作台/项目页/图纸页统计卡）
        "typdesign_modules": typdesign_stats["modules"],
        "typdesign_matched": typdesign_stats["matched"],
        "typdesign_confirmed": typdesign_stats["confirmed"],
        "typdesign_pending": typdesign_stats["pending"],
        "typdesign_rate": typdesign_stats["rate"],
    }
