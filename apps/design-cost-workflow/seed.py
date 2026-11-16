# -*- coding: utf-8 -*-
"""seed.py — SQLite 数据库结构 + 演示数据（项目A/B/C）。

应用启动时调用 ``init_db(db_path)``：建库建表；库为空时写入演示数据
（用户、项目、图纸目录、模型、工程量、材料量对比、计价、阶段、审计）。

演示数据全部匿名化：项目A/B/C、某供电所、造价员/复核员，不包含真实业务数据。
设计预算（附件5）在 seed 时由模型预算按契约系数生成，保证口径自洽。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from pathlib import Path

import engine
import pricing
import rates

# ---------------------------------------------------------------------------
# 数据库结构（与内部契约第 4 节完全一致）
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
  name TEXT NOT NULL, role TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE, name TEXT, ptype TEXT, stage TEXT, location TEXT, t0 TEXT,
  design_equipment REAL DEFAULT 0,
  design_install REAL DEFAULT 0,
  design_other REAL DEFAULT 0,
  design_reserve REAL DEFAULT 0,
  design_total REAL DEFAULT 0,
  note TEXT);

CREATE TABLE IF NOT EXISTS members (
  user_id INTEGER, project_id INTEGER, PRIMARY KEY (user_id, project_id));

CREATE TABLE IF NOT EXISTS drawings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
  code TEXT, name TEXT NOT NULL, category TEXT, fmt TEXT, sheet_count INTEGER DEFAULT 1,
  status TEXT DEFAULT '待识别',
  summary TEXT, file_path TEXT, uploaded_by INTEGER, uploaded_at TEXT, parsed_at TEXT);

CREATE TABLE IF NOT EXISTS model_lines (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
  name TEXT, voltage TEXT, start_point TEXT, end_point TEXT,
  length_km REAL DEFAULT 0, circuits INTEGER DEFAULT 1, phases INTEGER DEFAULT 3,
  conductor TEXT, poles INTEGER DEFAULT 0, pole_type TEXT, span_m REAL DEFAULT 0,
  stays INTEGER DEFAULT 0, soil TEXT DEFAULT '普通土', source TEXT, note TEXT);

CREATE TABLE IF NOT EXISTS model_equipment (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
  category TEXT NOT NULL,
  name TEXT, spec TEXT, unit TEXT, qty REAL DEFAULT 0, unit_price REAL DEFAULT 0,
  supply TEXT,
  source TEXT, note TEXT);

CREATE TABLE IF NOT EXISTS model_civil (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
  name TEXT, spec TEXT, unit TEXT, qty REAL DEFAULT 0, note TEXT);

CREATE TABLE IF NOT EXISTS model_demolition (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
  name TEXT, spec TEXT, unit TEXT, qty REAL DEFAULT 0, note TEXT);

CREATE TABLE IF NOT EXISTS quantities (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, seq INTEGER,
  unit_name TEXT, code TEXT, name TEXT, spec TEXT, unit TEXT, qty REAL DEFAULT 0,
  formula TEXT, basis TEXT,
  category TEXT,
  quota_code TEXT, status TEXT DEFAULT '已计算', note TEXT);

CREATE TABLE IF NOT EXISTS pricing_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, seq INTEGER,
  quantity_id INTEGER, unit_name TEXT, name TEXT, spec TEXT, unit TEXT, qty REAL DEFAULT 0,
  category TEXT,
  quota_code TEXT, quota_name TEXT,
  labor_unit REAL DEFAULT 0, material_unit REAL DEFAULT 0, machine_unit REAL DEFAULT 0,
  labor REAL DEFAULT 0, material REAL DEFAULT 0, machine REAL DEFAULT 0,
  main_material REAL DEFAULT 0, direct_cost REAL DEFAULT 0);

CREATE TABLE IF NOT EXISTS pricing_materials (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, pricing_item_id INTEGER,
  name TEXT, spec TEXT, unit TEXT, qty REAL DEFAULT 0, unit_price REAL DEFAULT 0,
  amount REAL DEFAULT 0, supply TEXT);

CREATE TABLE IF NOT EXISTS pricing_fees (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, seq INTEGER,
  category TEXT, item TEXT, base_desc TEXT, formula TEXT, rate REAL, amount REAL);

CREATE TABLE IF NOT EXISTS compare_materials (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, seq INTEGER,
  name TEXT, spec TEXT, unit TEXT, design_qty REAL DEFAULT 0, model_qty REAL DEFAULT 0,
  diff REAL DEFAULT 0, rate REAL DEFAULT 0, level TEXT,
  reason TEXT, status TEXT DEFAULT '待确认');

CREATE TABLE IF NOT EXISTS compare_budget (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, seq INTEGER,
  item TEXT, design_amount REAL DEFAULT 0, model_amount REAL DEFAULT 0,
  diff REAL DEFAULT 0, rate REAL DEFAULT 0, note TEXT);

CREATE TABLE IF NOT EXISTS stages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
  key TEXT NOT NULL, name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '未开始',
  owner TEXT, reviewer TEXT, progress INTEGER DEFAULT 0,
  submitted_at TEXT, reviewed_at TEXT, version INTEGER DEFAULT 0, note TEXT,
  UNIQUE(project_id, key));

CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER, stage_key TEXT,
  reviewer TEXT, result TEXT, comment TEXT, created_at TEXT);

CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER, user_id INTEGER,
  action TEXT, detail TEXT, created_at TEXT);
"""

# ---------------------------------------------------------------------------
# 连接、密码与时间
# ---------------------------------------------------------------------------

_PBKDF2_ITERATIONS = 120_000
_PBKDF2_PREFIX = "pbkdf2_sha256"


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开数据库连接（row_factory=sqlite3.Row）。"""
    conn = sqlite3.connect(str(db_path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def now() -> str:
    """当前时间（YYYY-MM-DD HH:MM）。"""
    return engine.now()


def hash_pw(password: str) -> str:
    """PBKDF2-SHA256 密码摘要，格式 pbkdf2_sha256$iter$salt$hash。"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", (password or "").encode("utf-8"),
                                 bytes.fromhex(salt), _PBKDF2_ITERATIONS).hex()
    return f"{_PBKDF2_PREFIX}${_PBKDF2_ITERATIONS}${salt}${digest}"


def check_pw(password: str, stored: str) -> bool:
    """校验密码；兼容明文存储（便于演示数据导入/迁移）。"""
    if not stored:
        return False
    if not stored.startswith(f"{_PBKDF2_PREFIX}$"):
        return hmac.compare_digest(password or "", stored)
    try:
        _, iterations, salt, digest = stored.split("$", 3)
        calculated = hashlib.pbkdf2_hmac(
            "sha256", (password or "").encode("utf-8"), bytes.fromhex(salt), int(iterations)
        ).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calculated, digest)


# ---------------------------------------------------------------------------
# 初始化入口
# ---------------------------------------------------------------------------

def init_db(db_path: str | Path) -> None:
    """建库建表并在空库时写入演示数据；可重复调用（幂等）。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        project_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        if project_count == 0:
            _seed_demo(conn)
        conn.commit()
    finally:
        conn.close()


def _insert(conn: sqlite3.Connection, table: str, data: dict) -> int:
    columns = ", ".join(data.keys())
    marks = ", ".join("?" for _ in data)
    cursor = conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(data.values()))
    return int(cursor.lastrowid)


# ---------------------------------------------------------------------------
# 演示数据：用户 / 项目 / 图纸 / 模型
# ---------------------------------------------------------------------------

_USERS = [
    {"username": "admin", "password": "admin123", "name": "管理员", "role": "admin"},
    {"username": "zaojia", "password": "123456", "name": "造价员", "role": "zaojia"},
    {"username": "fuhe", "password": "123456", "name": "复核员", "role": "fuhe"},
]

_PROJECTS = [
    {"code": "DEMO-A", "name": "项目A·某台区改造工程", "ptype": "台区改造",
     "stage": "计价定稿", "location": "某供电所", "t0": "2026-10-26 09:00",
     "note": "全流程已完成：图纸→建模→算量→计价复核通过，可送审"},
    {"code": "DEMO-B", "name": "项目B·某业扩配套工程", "ptype": "业扩配套",
     "stage": "算量复核", "location": "某供电所", "t0": "2026-11-04 08:50",
     "note": "算量阶段待复核，用于演示复核门禁与退回流程"},
    {"code": "DEMO-C", "name": "项目C·某业扩配套工程", "ptype": "业扩配套",
     "stage": "图纸解析", "location": "某供电所", "t0": "2026-11-15 09:10",
     "note": "演示路线起点：图纸已识别，待提交复核"},
]

#: 图纸目录（每项目 7 条基础资料；项目A 另加电缆路径图与现场照片）
_DRAWINGS = {
    "DEMO-A": [
        ("S-A01", "施工图设计说明书", "设计说明书", "PDF", 12,
         "识别：项目画像 台区改造；10kV路径0.057km、低压路径0.7875km；配变500kVA；工期60天", "2026-10-26 10:10"),
        ("S-A02", "10kV线路平面图", "平面图", "DWG", 1,
         "识别：10kV路径0.057km、杆塔2基、导线JKLGYJ-70/10", "2026-10-26 10:12"),
        ("S-A03", "台架变安装图", "安装图", "DWG", 2,
         "识别：台架变1座、配变SB20-M-500kVA、低压出线1回、智能网关1套", "2026-10-26 10:15"),
        ("S-A04", "0.4kV线路平面图", "平面图", "DWG", 1,
         "识别：低压路径0.7875km、杆塔14基、拉线12条", "2026-10-26 10:18"),
        ("S-A05", "杆塔明细表", "明细表", "XLSX", 1,
         "识别：杆塔16基（Φ150×8m），拉线12条，档距28.5/56.25m", "2026-10-26 10:20"),
        ("S-A06", "主要设备材料清册", "材料清册", "XLSX", 2,
         "识别：材料/设备18行；导线3.245km、杆塔16基、电缆0.0742km", "2026-10-26 10:22"),
        ("S-A07", "施工图预算（博微导出）", "预算", "XLSX", 3,
         "博微预算书（用于预算对比，不参与建模）", "2026-10-26 10:25"),
        ("S-A08", "电缆路径图", "平面图", "DWG", 1,
         "识别：电缆路径0.0742km，直埋沟70m（0.4m宽×0.8m深）", "2026-10-26 10:28"),
        ("S-A09", "勘探及现场照片", "现场照片", "JPG", 6,
         "已识别-编目：现场照片6张，地质普通土", "2026-10-26 10:30"),
    ],
    "DEMO-B": [
        ("S-B01", "施工图设计说明书", "设计说明书", "PDF", 16,
         "识别：项目画像 业扩配套；10kV路径2.131km、低压路径0.777km；配变200kVA", "2026-11-04 09:30"),
        ("S-B02", "10kV线路平面图", "平面图", "DWG", 3,
         "识别：10kV路径2.131km、杆塔36基、导线JKLGYJ-70/10", "2026-11-04 09:32"),
        ("S-B03", "台架变安装图", "安装图", "DWG", 2,
         "识别：台架变1座、配变SB20-M-200kVA、低压出线1回", "2026-11-04 09:35"),
        ("S-B04", "0.4kV线路平面图", "平面图", "DWG", 1,
         "识别：低压路径0.777km、杆塔14基、拉线6条", "2026-11-04 09:38"),
        ("S-B05", "杆塔明细表", "明细表", "XLSX", 1,
         "识别：杆塔50基（Φ190×12m 36基、Φ150×8m 14基），拉线24条", "2026-11-04 09:40"),
        ("S-B06", "主要设备材料清册", "材料清册", "XLSX", 2,
         "识别：材料/设备17行；导线9.786km、杆塔50基", "2026-11-04 09:42"),
        ("S-B07", "施工图预算（博微导出）", "预算", "XLSX", 4,
         "博微预算书（用于预算对比，不参与建模）", "2026-11-04 09:45"),
    ],
    "DEMO-C": [
        ("S-C01", "施工图设计说明书", "设计说明书", "PDF", 10,
         "识别：项目画像 业扩配套；10kV路径0.077km、低压路径0.015km；配变500kVA", "2026-11-15 10:05"),
        ("S-C02", "10kV线路平面图", "平面图", "DWG", 1,
         "识别：10kV路径0.077km、杆塔2基、导线JKLGYJ-70/10", "2026-11-15 10:08"),
        ("S-C03", "台架变安装图", "安装图", "DWG", 2,
         "识别：台架变1座、配变SB20-M-500kVA、低压出线1回", "2026-11-15 10:10"),
        ("S-C04", "0.4kV线路平面图", "平面图", "DWG", 1,
         "识别：低压路径0.015km、杆塔2基", "2026-11-15 10:12"),
        ("S-C05", "杆塔明细表", "明细表", "XLSX", 1,
         "识别：杆塔4基（Φ150×8m），档距38.5/7.5m", "2026-11-15 10:15"),
        ("S-C06", "主要设备材料清册", "材料清册", "XLSX", 2,
         "识别：材料/设备13行；导线0.2998km、杆塔4基", "2026-11-15 10:18"),
        ("S-C07", "施工图预算（博微导出）", "预算", "XLSX", 3,
         "博微预算书（用于预算对比，不参与建模）", "2026-11-15 10:20"),
    ],
}

#: 线路模型（项目A/B/C）
_MODEL_LINES = {
    "DEMO-A": [
        {"name": "10kV线路", "voltage": "10kV", "start_point": "10kV线路T接点",
         "end_point": "台架变1#杆", "length_km": 0.057, "circuits": 1, "phases": 3,
         "conductor": "JKLGYJ-70/10", "poles": 2, "pole_type": "Φ150×8m",
         "span_m": 28.5, "stays": 0, "soil": "普通土"},
        {"name": "低压线路", "voltage": "1kV", "start_point": "台架变低压侧",
         "end_point": "末梢杆", "length_km": 0.7875, "circuits": 1, "phases": 4,
         "conductor": "BLVV-240/BLVV-120", "poles": 14, "pole_type": "Φ150×8m",
         "span_m": 56.25, "stays": 12, "soil": "普通土"},
    ],
    "DEMO-B": [
        {"name": "10kV线路", "voltage": "10kV", "start_point": "变电站10kV出线间隔",
         "end_point": "台架变1#杆", "length_km": 2.131, "circuits": 1, "phases": 3,
         "conductor": "JKLGYJ-70/10", "poles": 36, "pole_type": "Φ190×12m",
         "span_m": 59.2, "stays": 18, "soil": "普通土"},
        {"name": "低压线路", "voltage": "1kV", "start_point": "台架变低压侧",
         "end_point": "末梢杆", "length_km": 0.777, "circuits": 1, "phases": 4,
         "conductor": "JKLYJ-120(1kV)", "poles": 14, "pole_type": "Φ150×8m",
         "span_m": 55.5, "stays": 6, "soil": "普通土"},
    ],
    "DEMO-C": [
        {"name": "10kV线路", "voltage": "10kV", "start_point": "10kV线路T接点",
         "end_point": "台架变1#杆", "length_km": 0.077, "circuits": 1, "phases": 3,
         "conductor": "JKLGYJ-70/10", "poles": 2, "pole_type": "Φ150×8m",
         "span_m": 38.5, "stays": 0, "soil": "普通土"},
        {"name": "低压线路", "voltage": "1kV", "start_point": "台架变低压侧",
         "end_point": "末梢杆", "length_km": 0.015, "circuits": 1, "phases": 4,
         "conductor": "JKLYJ-240(1kV)", "poles": 2, "pole_type": "Φ150×8m",
         "span_m": 7.5, "stays": 0, "soil": "普通土"},
    ],
}

#: 设备/主材模型：(category, name, spec, unit, qty, unit_price, supply)
_MODEL_EQUIPMENT = {
    "DEMO-A": [
        ("设备", "配电变压器", "SB20-M-500kVA", "台", 1, 68000, "甲供"),
        ("设备", "变压器配电箱", "XLW-10/500-4", "台", 1, 33000, "甲供"),
        ("设备", "配电智能网关", "", "台", 1, 9500, "甲供"),
        ("主材", "10kV架空绝缘导线", "JKLGYJ-70/10", "千米", 0.176, 12500, "甲供"),
        ("主材", "低压绝缘导线", "BLVV-240", "千米", 1.648, 9000, "乙供"),
        ("主材", "低压绝缘导线", "BLVV-120", "千米", 1.597, 7000, "乙供"),
        ("主材", "水泥杆", "Φ150×8m", "根", 16, 800, "乙供"),
        ("主材", "拉线（含拉线棒/盘/钢绞线）", "", "条", 12, 380, "乙供"),
        ("主材", "10kV避雷器", "YH10CX-17/50", "只", 2, 380, "甲供"),
        ("主材", "10kV金具", "横担/绝缘子/铁件", "套", 2, 450, "乙供"),
        ("主材", "低压金具", "四线横担/铁件", "套", 14, 120, "乙供"),
        ("主材", "台架变金具及支架", "", "套", 1, 3500, "甲供"),
        ("主材", "接地装置材料", "角钢/扁钢", "组", 2, 900, "乙供"),
        ("主材", "安全围栏", "", "套", 1, 1200, "乙供"),
        ("主材", "电力电缆", "ZA-YJV-1×240", "千米", 0.0742, 180000, "甲供"),
        ("主材", "电缆终端头", "1kV 240mm²", "个", 2, 450, "乙供"),
    ],
    "DEMO-B": [
        ("设备", "配电变压器", "SB20-M-200kVA", "台", 1, 45000, "甲供"),
        ("设备", "变压器配电箱", "XLW-10/200", "台", 1, 19000, "甲供"),
        ("设备", "配电智能网关", "", "套", 1, 9500, "甲供"),
        ("主材", "10kV架空绝缘导线", "JKLGYJ-70/10", "千米", 6.585, 12500, "甲供"),
        ("主材", "低压绝缘导线", "JKLYJ-120(1kV)", "千米", 3.201, 9000, "甲供"),
        ("主材", "水泥杆", "Φ190×12m", "根", 36, 1450, "乙供"),
        ("主材", "水泥杆", "Φ150×8m", "根", 14, 800, "乙供"),
        ("主材", "拉线（含拉线棒/盘/钢绞线）", "", "条", 24, 380, "乙供"),
        ("主材", "10kV避雷器", "YH10CX-17/50", "只", 36, 380, "甲供"),
        ("主材", "10kV金具", "", "套", 36, 450, "乙供"),
        ("主材", "低压金具", "", "套", 14, 120, "乙供"),
        ("主材", "台架变金具及支架", "", "套", 1, 3500, "甲供"),
        ("主材", "接地装置材料", "", "组", 2, 900, "乙供"),
        ("主材", "安全围栏", "", "套", 1, 1200, "乙供"),
    ],
    "DEMO-C": [
        ("设备", "配电变压器", "SB20-M-500kVA", "台", 1, 68000, "甲供"),
        ("设备", "变压器配电箱", "XLW-10/500-4", "台", 1, 33000, "甲供"),
        ("设备", "配电智能网关", "", "套", 1, 9500, "甲供"),
        ("主材", "10kV架空绝缘导线", "JKLGYJ-70/10", "千米", 0.238, 12500, "甲供"),
        ("主材", "低压绝缘导线", "JKLYJ-240(1kV)", "千米", 0.0618, 14000, "甲供"),
        ("主材", "水泥杆", "Φ150×8m", "根", 4, 800, "乙供"),
        ("主材", "10kV避雷器", "", "只", 2, 380, "甲供"),
        ("主材", "10kV金具", "", "套", 2, 450, "乙供"),
        ("主材", "低压金具", "", "套", 2, 120, "乙供"),
        ("主材", "台架变金具及支架", "", "套", 1, 3500, "甲供"),
        ("主材", "接地装置材料", "", "组", 2, 900, "乙供"),
        ("主材", "安全围栏", "", "套", 1, 1200, "乙供"),
    ],
}

#: 土建模型：(name, spec, unit, qty, note)
_MODEL_CIVIL = {
    "DEMO-A": [
        ("台架变基础", "C20混凝土", "m³", 2.4, "施工图：台架变基础尺寸"),
        ("电缆直埋沟", "0.4m宽×0.8m深", "m", 70, "施工图：电缆直埋沟70m"),
    ],
    "DEMO-B": [
        ("台架变基础", "C20混凝土", "m³", 2.4, "施工图：台架变基础尺寸"),
    ],
    "DEMO-C": [
        ("台架变基础", "C20混凝土", "m³", 2.4, "施工图：台架变基础尺寸"),
    ],
}

#: 拆除模型：(name, spec, unit, qty, note)
_MODEL_DEMOLITION = {
    "DEMO-A": [
        ("拆除低压导线", "", "千米", 2.5, "图纸及现场勘察：线路拆除不可利用"),
        ("拆除水泥杆", "Φ150×8m", "根", 3, "图纸及现场勘察：旧杆拆除"),
    ],
    "DEMO-B": [],
    "DEMO-C": [],
}


def _seed_demo(conn: sqlite3.Connection) -> None:
    """写入全部演示数据（调用方负责最终 commit）。"""
    # --- 用户 ---
    user_ids: dict[str, int] = {}
    for item in _USERS:
        user_ids[item["username"]] = _insert(conn, "users", {
            "username": item["username"],
            "password": hash_pw(item["password"]),
            "name": item["name"],
            "role": item["role"],
        })

    # --- 项目 ---
    project_ids: dict[str, int] = {}
    for item in _PROJECTS:
        project_ids[item["code"]] = _insert(conn, "projects", {
            "code": item["code"], "name": item["name"], "ptype": item["ptype"],
            "stage": item["stage"], "location": item["location"], "t0": item["t0"],
            "note": item["note"],
            "design_equipment": 0, "design_install": 0, "design_other": 0,
            "design_reserve": 0, "design_total": 0,
        })
    # --- 成员（三个演示账号均可访问三个项目） ---
    for username in ("admin", "zaojia", "fuhe"):
        for project_id in project_ids.values():
            conn.execute(
                "INSERT OR IGNORE INTO members (user_id, project_id) VALUES (?,?)",
                (user_ids[username], project_id),
            )

    # --- 图纸目录 ---
    for code, drawings in _DRAWINGS.items():
        project_id = project_ids[code]
        uploader = user_ids["zaojia"]
        for seq, (drawing_code, name, category, fmt, sheets, summary, parsed_at) in enumerate(drawings, 1):
            _insert(conn, "drawings", {
                "project_id": project_id, "code": drawing_code, "name": name,
                "category": category, "fmt": fmt, "sheet_count": sheets,
                "status": "已识别", "summary": summary,
                "file_path": f"demo/{code}/{drawing_code}.{fmt.lower()}",
                "uploaded_by": uploader, "uploaded_at": parsed_at, "parsed_at": parsed_at,
            })

    # --- 模型：线路 / 设备主材 / 土建 / 拆除 ---
    for code in project_ids:
        project_id = project_ids[code]
        for line in _MODEL_LINES[code]:
            _insert(conn, "model_lines", {
                "project_id": project_id,
                "name": line["name"], "voltage": line["voltage"],
                "start_point": line["start_point"], "end_point": line["end_point"],
                "length_km": line["length_km"], "circuits": line.get("circuits", 1),
                "phases": line["phases"], "conductor": line["conductor"],
                "poles": line["poles"], "pole_type": line["pole_type"],
                "span_m": line["span_m"], "stays": line["stays"], "soil": line["soil"],
                "source": "图纸识别", "note": "由图纸识别结果生成初稿，已人工校正",
            })
        for category, name, spec, unit, qty, unit_price, supply in _MODEL_EQUIPMENT[code]:
            _insert(conn, "model_equipment", {
                "project_id": project_id, "category": category, "name": name,
                "spec": spec, "unit": unit, "qty": qty, "unit_price": unit_price,
                "supply": supply, "source": "主材清册", "note": "",
            })
        for name, spec, unit, qty, note in _MODEL_CIVIL[code]:
            _insert(conn, "model_civil", {
                "project_id": project_id, "name": name, "spec": spec,
                "unit": unit, "qty": qty, "note": note,
            })
        for name, spec, unit, qty, note in _MODEL_DEMOLITION[code]:
            _insert(conn, "model_demolition", {
                "project_id": project_id, "name": name, "spec": spec,
                "unit": unit, "qty": qty, "note": note,
            })
        engine.init_project_stages(conn, project_id)

    # --- 算量（三个项目均生成工程量与材料量对比，支撑演示与统计卡） ---
    admin = _user_dict(conn, "admin")
    for project_id in project_ids.values():
        engine.compute_quantities(conn, project_id, admin)

    # --- 设计预算（附件5）：模型预算 × 契约系数，先算模型预算再回填设计预算 ---
    for code, project_id in project_ids.items():
        data = pricing._build_pricing_data(conn, project_id)
        summary = data["summary"]
        factors = rates.DESIGN_FACTORS[code]
        design_equipment = round(summary["equipment"] * factors["equipment"])
        design_install = round((summary["install"] + summary["build"]) * factors["install"])
        design_other = round(summary["other"] * factors["other"])
        design_reserve = round((design_equipment + design_install + design_other) * 0.03)
        design_total = design_equipment + design_install + design_other + design_reserve
        conn.execute(
            "UPDATE projects SET design_equipment=?, design_install=?, design_other=?, "
            "design_reserve=?, design_total=? WHERE id=?",
            (design_equipment, design_install, design_other, design_reserve, design_total, project_id),
        )

    # --- 计价：项目A 已完成（计价定稿），写入 pricing_* 与预算对比 ---
    pricing.compute_pricing(conn, project_ids["DEMO-A"], admin)

    # --- 阶段状态与复核记录、审计日志 ---
    _seed_stages(conn, project_ids)
    _seed_reviews(conn, project_ids)
    _seed_audit(conn, project_ids, user_ids)
    conn.commit()


def _user_dict(conn: sqlite3.Connection, username: str) -> dict:
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    return dict(row) if row is not None else dict(engine.SYSTEM_USER)


def _set_stage(conn: sqlite3.Connection, project_id: int, key: str, **fields) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{name}=?" for name in fields)
    conn.execute(
        f"UPDATE stages SET {assignments} WHERE project_id=? AND key=?",
        (*fields.values(), project_id, key),
    )


def _seed_stages(conn: sqlite3.Connection, project_ids: dict[str, int]) -> None:
    """按契约 5.5 设置三个项目的阶段状态。"""
    project_a = project_ids["DEMO-A"]
    _set_stage(conn, project_a, "drawings", status="已完成", owner="造价员", reviewer="复核员",
               progress=100, submitted_at="2026-10-27 10:20", reviewed_at="2026-10-27 15:40",
               version=1, note="")
    _set_stage(conn, project_a, "model", status="已完成", owner="造价员", reviewer="复核员",
               progress=100, submitted_at="2026-10-29 16:05", reviewed_at="2026-10-30 09:30",
               version=1, note="")
    _set_stage(conn, project_a, "quantities", status="已完成", owner="造价员", reviewer="复核员",
               progress=100, submitted_at="2026-11-02 11:10", reviewed_at="2026-11-02 17:20",
               version=1, note="")
    _set_stage(conn, project_a, "pricing", status="已完成", owner="造价员", reviewer="复核员",
               progress=100, submitted_at="2026-11-10 10:00", reviewed_at="2026-11-12 16:40",
               version=2, note="计价复核通过，可送审")

    project_b = project_ids["DEMO-B"]
    _set_stage(conn, project_b, "drawings", status="已完成", owner="造价员", reviewer="复核员",
               progress=100, submitted_at="2026-11-05 09:30", reviewed_at="2026-11-05 15:10",
               version=1, note="")
    _set_stage(conn, project_b, "model", status="已完成", owner="造价员", reviewer="复核员",
               progress=100, submitted_at="2026-11-08 14:00", reviewed_at="2026-11-08 17:45",
               version=1, note="")
    _set_stage(conn, project_b, "quantities", status="待复核", owner="造价员",
               progress=70, submitted_at="2026-11-14 16:20", version=0,
               note="算量已提交，等待复核")
    _set_stage(conn, project_b, "pricing", status="未开始", progress=0, version=0)

    project_c = project_ids["DEMO-C"]
    _set_stage(conn, project_c, "drawings", status="进行中", owner="造价员",
               progress=40, version=0, note="图纸已识别，待提交复核")
    for key in ("model", "quantities", "pricing"):
        _set_stage(conn, project_c, key, status="未开始", progress=0, version=0)


def _seed_reviews(conn: sqlite3.Connection, project_ids: dict[str, int]) -> None:
    """演示用复核记录（含一次退回后重新提交）。"""
    rows = [
        (project_ids["DEMO-A"], "drawings", "复核员", "通过", "图纸目录与识别结论核对无误", "2026-10-27 15:40"),
        (project_ids["DEMO-A"], "model", "复核员", "通过", "模型与图纸一致，校验无高级问题", "2026-10-30 09:30"),
        (project_ids["DEMO-A"], "quantities", "复核员", "通过", "工程量与计算式核对无误", "2026-11-02 17:20"),
        (project_ids["DEMO-A"], "pricing", "复核员", "退回", "其他费用中勘察费计算基数需复核", "2026-11-10 17:00"),
        (project_ids["DEMO-A"], "pricing", "复核员", "通过", "已按意见调整，复核通过", "2026-11-12 16:40"),
        (project_ids["DEMO-B"], "drawings", "复核员", "通过", "图纸识别结论完整", "2026-11-05 15:10"),
        (project_ids["DEMO-B"], "model", "复核员", "通过", "模型校验通过", "2026-11-08 17:45"),
    ]
    conn.execute("DELETE FROM reviews")
    for project_id, stage_key, reviewer, result, comment, created_at in rows:
        _insert(conn, "reviews", {
            "project_id": project_id, "stage_key": stage_key, "reviewer": reviewer,
            "result": result, "comment": comment, "created_at": created_at,
        })


def _seed_audit(conn: sqlite3.Connection, project_ids: dict[str, int],
                user_ids: dict[str, int]) -> None:
    """演示用审计动态（项目A 8 条，项目B 3 条，项目C 1 条）。"""
    rows = [
        (project_ids["DEMO-A"], user_ids["zaojia"], "导入图纸",
         "导入施工图及主要设备材料清册 9 份", "2026-10-26 16:20"),
        (project_ids["DEMO-A"], user_ids["zaojia"], "图纸识别完成",
         "识别 10kV路径0.057km、杆塔16基、配变500kVA、低压路径0.7875km", "2026-10-27 09:12"),
        (project_ids["DEMO-A"], user_ids["zaojia"], "生成模型初稿",
         "从图纸识别结果生成模型初稿并人工校正", "2026-10-28 10:05"),
        (project_ids["DEMO-A"], user_ids["zaojia"], "模型校验",
         "模型校验通过（高级问题 0 个），提交建模复核", "2026-10-29 16:05"),
        (project_ids["DEMO-A"], user_ids["fuhe"], "复核通过",
         "建模阶段复核通过", "2026-10-30 09:30"),
        (project_ids["DEMO-A"], user_ids["zaojia"], "重算工程量",
         "重算工程量并生成材料量对比，提交算量复核", "2026-11-02 11:10"),
        (project_ids["DEMO-A"], user_ids["zaojia"], "计价完成",
         "套用示例定额库完成计价并生成费用汇总", "2026-11-10 10:00"),
        (project_ids["DEMO-A"], user_ids["fuhe"], "复核通过",
         "计价复核通过，可送审", "2026-11-12 16:40"),
        (project_ids["DEMO-B"], user_ids["zaojia"], "导入图纸",
         "导入施工图及主要设备材料清册 7 份", "2026-11-04 16:40"),
        (project_ids["DEMO-B"], user_ids["zaojia"], "生成模型初稿",
         "从图纸识别结果生成模型初稿并人工校正", "2026-11-06 10:00"),
        (project_ids["DEMO-B"], user_ids["zaojia"], "重算工程量",
         "重算工程量并提交算量复核", "2026-11-14 16:20"),
        (project_ids["DEMO-C"], user_ids["zaojia"], "上传图纸",
         "上传施工图及杆塔明细表，图纸识别完成，待提交复核", "2026-11-15 15:30"),
    ]
    conn.execute("DELETE FROM audit")
    for project_id, user_id, action, detail, created_at in rows:
        _insert(conn, "audit", {
            "project_id": project_id, "user_id": user_id, "action": action,
            "detail": detail, "created_at": created_at,
        })
