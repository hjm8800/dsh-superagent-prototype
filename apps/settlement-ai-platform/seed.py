"""SQLite schema and anonymized demo data for the settlement AI workbench."""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL,
  name TEXT NOT NULL,
  role TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  ptype TEXT,
  stage TEXT,
  t0 TEXT,
  owner TEXT,
  location TEXT,
  budget_total REAL DEFAULT 0,
  note TEXT
);
CREATE TABLE IF NOT EXISTS members (
  user_id INTEGER NOT NULL,
  project_id INTEGER NOT NULL,
  PRIMARY KEY (user_id, project_id)
);
CREATE TABLE IF NOT EXISTS profiles (
  project_id INTEGER NOT NULL,
  key TEXT NOT NULL,
  value TEXT,
  PRIMARY KEY (project_id, key)
);
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  category TEXT,
  filename TEXT,
  path TEXT,
  size INTEGER DEFAULT 0,
  status TEXT DEFAULT 'pending',
  summary TEXT,
  parsed_json TEXT,
  uploaded_by INTEGER,
  uploaded_at TEXT
);
CREATE TABLE IF NOT EXISTS materials (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  supply TEXT,
  code TEXT,
  name TEXT,
  spec TEXT,
  unit TEXT,
  qty TEXT,
  source TEXT
);
CREATE TABLE IF NOT EXISTS boq (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  code TEXT,
  name TEXT,
  unit TEXT,
  qty TEXT,
  part TEXT
);
CREATE TABLE IF NOT EXISTS budget_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  name TEXT,
  amount REAL,
  category TEXT
);
CREATE TABLE IF NOT EXISTS compare_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  item TEXT NOT NULL,
  unit TEXT,
  design_qty TEXT,
  budget_qty TEXT,
  settle_qty TEXT,
  reason TEXT,
  evidence TEXT,
  status TEXT DEFAULT '待确认'
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  name TEXT,
  owner TEXT,
  due TEXT,
  status TEXT DEFAULT '进行中'
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  level TEXT,
  title TEXT,
  detail TEXT,
  status TEXT DEFAULT '待处理'
);
CREATE TABLE IF NOT EXISTS ai_outputs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  scene TEXT,
  prompt TEXT,
  output TEXT,
  engine TEXT,
  status TEXT DEFAULT '初稿',
  created_by INTEGER,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ai_output_id INTEGER,
  project_id INTEGER,
  item TEXT,
  result TEXT,
  reviewer TEXT,
  reviewed_at TEXT
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER,
  user_id INTEGER,
  action TEXT,
  detail TEXT,
  created_at TEXT
);
"""


def hash_pw(pw: str) -> str:
    return hashlib.sha256(("settlement-demo:" + pw).encode("utf-8")).hexdigest()


def check_pw(pw: str, digest: str) -> bool:
    return hash_pw(pw) == digest


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


PROJECTS = [
    {
        "id": 1, "code": "DEMO-A", "name": "项目A·某台区改造工程", "ptype": "台区改造",
        "stage": "资料解析", "t0": "2026-11-30", "owner": "资料员", "location": "某供电所",
        "budget_total": 354080, "note": "改造台区，含拆除；演示数据已匿名化。",
        "profile": {
            "10kV线路": "0.057 km", "配变": "500 kVA", "低压线路": "3.15 km",
            "拆除": "低压导线2.5km、水泥杆3根", "甲供材料": "109 条", "乙供材料": "35 条",
            "工程量清单": "42 条", "拆旧清单": "7 条", "设计说明": "附件1 DOCX 已解析",
            "预算": "35.41 万元（XLSX/XML 已解析）",
        },
        "materials": [
            ("甲供", "020200300010001", "10kV钢芯铝绞线芯交联聚乙烯绝缘架空电缆", "JKLGYJ-70/10", "千米", "0.176", "附件4 甲供材料"),
            ("甲供", "040100100010012", "10kV油浸变压器", "SB20-M-500kVA", "台", "1", "附件4 甲供材料"),
            ("甲供", "040600400010003", "500kVA变压器配电箱", "XLW-10/500-4", "台", "1", "附件4 甲供材料"),
            ("甲供", "020200300010005", "10kV铝芯交联聚乙烯绝缘架空电缆", "JKLYJ-70(10kV)", "千米", "0.024", "附件4 甲供材料"),
            ("甲供", "020400200010002", "0.6/1kV铜芯阻燃交联聚乙烯绝缘电力电缆", "ZA-YJV-1×240", "千米", "0.070", "附件4 甲供材料"),
            ("乙供", "", "低压导线", "BLVV-240", "千米", "0.924", "附件4 乙供材料"),
            ("乙供", "", "低压导线", "BLVV-120", "千米", "0.640", "附件4 乙供材料"),
            ("乙供", "", "水泥杆", "Φ150×8m", "根", "4", "附件4 乙供材料"),
        ],
        "boq": [
            ("", "台架变安装（500kVA）", "套", "1", "台架变"),
            ("", "10kV架空线路架设", "千米", "0.057", "10kV线路"),
            ("", "低压线路架设", "千米", "3.15", "低压线路"),
            ("", "拆除低压导线", "千米", "2.5", "拆除"),
            ("", "拆除水泥杆", "根", "3", "拆除"),
            ("", "智能网关安装", "台", "1", "台架变"),
        ],
        "budget": [("设备购置费", 111536, "设备"), ("安装工程费", 197435, "安装"), ("其他费用", 42660, "其他")],
        "compare": [
            ("10kV导线", "千米", "0.057", "0.176", "0.171", "材料量按三相计", "架设记录/材料清册", "待确认"),
            ("配变", "台", "1", "1", "1", "无差异", "附件4/预算", "通过"),
            ("低压导线", "千米", "3.15", "3.24", "3.31", "路径调整/预留", "变更/签证待补", "待确认"),
            ("电缆", "千米", "0", "0.070", "0.070", "无差异", "附件4/预算", "通过"),
            ("土方", "m³", "—", "12", "15", "开挖量口径", "隐蔽验收记录", "待确认"),
        ],
        "docs": [
            ("附件1 施工图设计说明书", "设计说明书", "docx", "parsed", "DOCX 已解析：226 段、20 张表"),
            ("附件2 勘探及现场照片", "勘察", "jpg", "parsed", "47 张现场照片已编目"),
            ("附件3 施工图", "图纸", "dwg", "parsed", "ODA 转 DXF，提取文字"),
            ("附件4 主要设备材料清册", "材料清册", "xlsx", "parsed", "甲供109/乙供35/工程量42/拆旧7"),
            ("附件5 预算", "预算", "xlsx", "parsed", "XLSX/XML 已解析，BDY3需插件"),
            ("附件6 标准设计和典型造价", "评价", "xlsx", "parsed", "标准设计统计"),
            ("附件7 南网造价控制线", "评价", "xlsx", "parsed", "造价控制线"),
            ("附件8/9 绿色低碳与五新", "评价", "xls", "parsed", "评价表"),
        ],
        "tasks": [("资料完整性检查", "资料员", "2026-11-15", "进行中"), ("设计资料解析", "资料员", "2026-11-15", "进行中"), ("三算对比", "结算员", "2026-11-18", "待开始")],
        "alerts": [("中", "附件3 已解析", "DWG 经 ODA 转 DXF，共 15 个文件，文字已提取"), ("高", "低压导线量差", "设计3.15km、预算3.24km、结算3.31km，需变更/签证")],
    },
    {
        "id": 2, "code": "DEMO-B", "name": "项目B·某业扩配套工程", "ptype": "业扩配套",
        "stage": "结算编制", "t0": "2026-11-25", "owner": "结算员", "location": "某供电所",
        "budget_total": 892886, "note": "新建线路较长，无拆除；演示数据已匿名化。",
        "profile": {
            "10kV线路": "2.131 km", "配变": "200 kVA", "低压线路": "3.108 km",
            "拆除": "无", "甲供材料": "109 条", "乙供材料": "37 条",
            "工程量清单": "41 条", "拆旧清单": "0 条", "设计说明": "附件1 DOCX 已解析",
            "预算": "89.29 万元（XLSX/XML 已解析）",
        },
        "materials": [
            ("甲供", "020200300010001", "10kV钢芯铝绞线芯交联聚乙烯绝缘架空电缆", "JKLGYJ-70/10", "千米", "6.58479", "附件4 甲供材料"),
            ("甲供", "040100100010010", "10kV油浸变压器", "SB20-M-200kVA", "台", "1", "附件4 甲供材料"),
            ("甲供", "021100300020007", "10kV交流外串联间隙金属氧化物避雷器", "YH10CX-17/50", "只", "36", "附件4 甲供材料"),
            ("甲供", "020200300010023", "1kV铝芯交联聚乙烯绝缘架空电缆", "JKLYJ-120(1kV)", "千米", "1.476", "附件4 甲供材料"),
            ("乙供", "", "拉线棒", "LB20-3100", "吨", "0.009", "附件4 乙供材料"),
            ("乙供", "", "底盘", "600×600", "套", "1", "附件4 乙供材料"),
        ],
        "boq": [
            ("", "10kV架空线路架设", "千米", "2.131", "10kV线路"),
            ("", "台架变安装（200kVA）", "套", "1", "台架变"),
            ("", "低压线路架设", "千米", "3.108", "低压线路"),
            ("", "外串联间隙避雷器安装", "只", "36", "防雷"),
        ],
        "budget": [("设备购置费", 77193, "设备"), ("安装工程费", 693581, "安装"), ("其他费用", 122113, "其他")],
        "compare": [
            ("10kV导线", "千米", "2.131", "6.585", "6.40", "三相+预留", "架设记录", "待确认"),
            ("配变", "台", "1", "1", "1", "无差异", "附件4/预算", "通过"),
            ("低压导线", "千米", "3.108", "3.20", "3.24", "路径调整", "变更/签证待补", "待确认"),
            ("电缆", "千米", "0", "0", "0", "无差异", "附件4/预算", "通过"),
        ],
        "docs": [
            ("附件1 施工图设计说明书", "设计说明书", "docx", "parsed", "DOCX 已解析"),
            ("附件2 勘探及现场照片", "勘察", "jpg", "parsed", "现场照片已编目"),
            ("附件3 施工图", "图纸", "dwg", "parsed", "ODA 转 DXF，提取文字"),
            ("附件4 主要设备材料清册", "材料清册", "xlsx", "parsed", "甲供109/乙供37/工程量41"),
            ("附件5 预算", "预算", "xlsx", "parsed", "预算89.29万元"),
        ],
        "tasks": [("三算对比", "结算员", "2026-11-18", "进行中"), ("送审结算书", "结算员", "2026-11-22", "待开始")],
        "alerts": [("中", "导线量差", "设计路径2.131km，结算导线6.40km，需按三相复核")],
    },
    {
        "id": 3, "code": "DEMO-C", "name": "项目C·某业扩配套工程", "ptype": "业扩配套",
        "stage": "资料移交", "t0": "2026-11-20", "owner": "资料员", "location": "某供电所",
        "budget_total": 268650, "note": "小规模业扩，无拆除；演示数据已匿名化。",
        "profile": {
            "10kV线路": "0.077 km", "配变": "500 kVA", "低压线路": "0.060 km",
            "拆除": "无", "甲供材料": "81 条", "乙供材料": "27 条",
            "工程量清单": "35 条", "拆旧清单": "0 条", "设计说明": "附件1 DOCX 已解析",
            "预算": "26.87 万元（XLSX/XML 已解析）",
        },
        "materials": [
            ("甲供", "040100100010012", "10kV油浸变压器", "SB20-M-500kVA", "台", "1", "附件4 甲供材料"),
            ("甲供", "040600400010003", "500kVA变压器配电箱", "XLW-10/500-4", "台", "1", "附件4 甲供材料"),
            ("甲供", "050501700010001", "配电智能网关", "配电智能网关", "套", "1", "附件4 甲供材料"),
            ("甲供", "020200300010013", "1kV铝芯交联聚乙烯绝缘架空电缆", "JKLYJ-240(1kV)", "千米", "0.060", "附件4 甲供材料"),
            ("乙供", "", "电杆安健环支架铁件", "配套", "套", "1", "附件4 乙供材料"),
        ],
        "boq": [
            ("", "台架变安装（500kVA）", "套", "1", "台架变"),
            ("", "10kV架空线路架设", "千米", "0.077", "10kV线路"),
            ("", "低压线路架设", "千米", "0.060", "低压线路"),
            ("", "智能网关安装", "台", "1", "台架变"),
        ],
        "budget": [("设备购置费", 113807, "设备"), ("安装工程费", 123905, "安装"), ("其他费用", 30938, "其他")],
        "compare": [
            ("10kV导线", "千米", "0.077", "0.024", "0.024", "材料/路径口径不同", "核对清册", "待确认"),
            ("配变", "台", "1", "1", "1", "无差异", "附件4/预算", "通过"),
            ("低压导线", "千米", "0.060", "0.060", "0.060", "无差异", "附件4/预算", "通过"),
        ],
        "docs": [
            ("附件1 施工图设计说明书", "设计说明书", "docx", "parsed", "DOCX 已解析"),
            ("附件4 主要设备材料清册", "材料清册", "xlsx", "parsed", "甲供81/乙供27/工程量35"),
            ("附件5 预算", "预算", "xlsx", "parsed", "预算26.87万元"),
        ],
        "tasks": [("竣工资料移交", "资料员", "2026-11-12", "进行中"), ("业主送审", "结算员", "2026-11-20", "待开始")],
        "alerts": [("高", "监理签章页缺失", "竣工资料移交前补齐")],
    },
]


def _insert(conn, table, cols, rows):
    q = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})"
    conn.executemany(q, rows)


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) c FROM projects").fetchone()["c"] == 0:
        seed(conn)
    conn.commit()
    conn.close()


def seed(conn: sqlite3.Connection) -> None:
    users = [
        ("admin", hash_pw("admin123"), "系统管理员", "admin"),
        ("ziliao", hash_pw("123456"), "资料员", "engineer"),
        ("jiesuan", hash_pw("123456"), "结算员", "settlement"),
        ("wuzi", hash_pw("123456"), "物资员", "material"),
        ("jingli", hash_pw("123456"), "项目经理", "manager"),
    ]
    _insert(conn, "users", ["username", "password", "name", "role"], users)
    uid = {r["username"]: r["id"] for r in conn.execute("SELECT id, username FROM users")}
    for p in PROJECTS:
        conn.execute(
            "INSERT INTO projects (id, code, name, ptype, stage, t0, owner, location, budget_total, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (p["id"], p["code"], p["name"], p["ptype"], p["stage"], p["t0"], p["owner"], p["location"], p["budget_total"], p["note"]),
        )
        for k, v in p["profile"].items():
            conn.execute("INSERT INTO profiles (project_id, key, value) VALUES (?,?,?)", (p["id"], k, v))
        for m in p["materials"]:
            conn.execute("INSERT INTO materials (project_id, supply, code, name, spec, unit, qty, source) VALUES (?,?,?,?,?,?,?,?)", (p["id"], *m))
        for b in p["boq"]:
            conn.execute("INSERT INTO boq (project_id, code, name, unit, qty, part) VALUES (?,?,?,?,?,?)", (p["id"], *b))
        for name, amount, cat in p["budget"]:
            conn.execute("INSERT INTO budget_items (project_id, name, amount, category) VALUES (?,?,?,?)", (p["id"], name, amount, cat))
        for c in p["compare"]:
            conn.execute("INSERT INTO compare_items (project_id, item, unit, design_qty, budget_qty, settle_qty, reason, evidence, status) VALUES (?,?,?,?,?,?,?,?,?)", (p["id"], *c))
        for d in p["docs"]:
            conn.execute(
                "INSERT INTO documents (project_id, name, category, filename, status, summary, uploaded_by, uploaded_at) VALUES (?,?,?,?,?,?,?,?)",
                (p["id"], d[0], d[1], d[2], d[3], d[4], uid["ziliao"], now()),
            )
        for t in p["tasks"]:
            conn.execute("INSERT INTO tasks (project_id, name, owner, due, status) VALUES (?,?,?,?,?)", (p["id"], *t))
        for a in p["alerts"]:
            conn.execute("INSERT INTO alerts (project_id, level, title, detail) VALUES (?,?,?,?)", (p["id"], *a))
    members = {
        "ziliao": [1, 3], "jiesuan": [2], "wuzi": [1, 2], "jingli": [1, 2, 3],
        "admin": [1, 2, 3],
    }
    for uname, pids in members.items():
        for pid in pids:
            conn.execute("INSERT INTO members (user_id, project_id) VALUES (?,?)", (uid[uname], pid))
    conn.execute(
        "INSERT INTO ai_outputs (project_id, scene, prompt, output, engine, status, created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (1, "设计资料解析", "解析附件1/4/5并生成项目画像", "项目A：10kV线路0.057km，配变500kVA，低压3.15km，预算35.41万元；甲供109条、乙供35条、工程量42条。建议先做三算对比。", "规则初稿", "初稿", uid["ziliao"], now()),
    )
    conn.execute("INSERT INTO reviews (ai_output_id, project_id, item, result, reviewer, reviewed_at) VALUES (?,?,?,?,?,?)", (1, 1, "项目画像字段核对", "通过", "项目经理", now()))
    conn.execute("INSERT INTO audit (project_id, user_id, action, detail, created_at) VALUES (?,?,?,?,?)", (1, uid["ziliao"], "seed", "初始化演示数据", now()))
    conn.commit()
