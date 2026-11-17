"""设计图纸 → 建模 → 算量 → 计价 工作流原型（FastAPI 单文件应用）。

运行方式::

    cd apps/design-cost-workflow
    python -m uvicorn app:app --host 127.0.0.1 --port 8091

（Windows 上如 PATH 中的 python 为应用商店别名，可直接运行 start.ps1，或使用完整解释器路径。）

演示账号：admin/admin123（管理员）、zaojia/123456（造价员）、fuhe/123456（复核员）。
环境变量：``WORKFLOW_DATA_DIR`` 数据目录（默认 ./data）、``WORKFLOW_DEMO=1`` 允许 ?as= 演示身份、
``WORKFLOW_SECRET`` 会话签名密钥。

职责边界：本文件只负责 Web 层（路由 / 模板上下文 / 权限 / Excel 导出 / SVG 示意图），
建模、算量、计价等业务规则分别调用 engine.py、pricing.py、rates.py（由 engine-dev 维护）。
"""
from __future__ import annotations

import html
import io
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from starlette.exceptions import HTTPException as StarletteHTTPException

import engine
import pricing
import seed

try:  # 典型设计（典设）匹配引擎（engine-dev 维护；并行开发期允许暂时缺失，页面降级为空态）
    import typdesign
except ImportError:  # pragma: no cover - 仅在 typdesign.py 未就绪时触发
    typdesign = None  # type: ignore[assignment]

from parsers import guess_meta, parse_drawing

# --------------------------------------------------------------------------- #
# 基础配置 / 进程内初始化
# --------------------------------------------------------------------------- #
BASE = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("WORKFLOW_DATA_DIR", str(BASE / "data"))).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "workflow.db"
SECRET = os.environ.get("WORKFLOW_SECRET", "design-cost-workflow-demo-secret")
DEMO = os.environ.get("WORKFLOW_DEMO", "1") == "1"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
seed.init_db(str(DB_PATH))

STAGES: list[tuple[str, str]] = [
    (str(key), str(name))
    for key, name in getattr(
        engine,
        "STAGES",
        [("drawings", "图纸解析"), ("model", "建模"), ("quantities", "算量"), ("pricing", "计价")],
    )
]
STAGE_KEYS = [key for key, _ in STAGES]
STAGE_NAMES = dict(STAGES)
CAN_EDIT_ROLES = ("admin", "zaojia")
CAN_REVIEW_ROLES = ("admin", "fuhe")
ROLE_NAMES = {"admin": "系统管理员", "zaojia": "造价员", "fuhe": "复核员"}
ALLOWED_UPLOAD_EXTS = {
    ".pdf", ".dwg", ".dxf", ".xlsx", ".xlsm", ".xls", ".docx", ".doc",
    ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp",
}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# 算量规则说明（页面“计算规则”卡片 + 导出“编制说明”页共用）
QUANTITY_RULES: list[tuple[str, str]] = [
    ("导线架设", "按线路亘长（km）计算：10kV → YD3-0301，1kV → YD3-0302；单位工程取线路名称。"),
    ("导线主材", "数量 = 亘长 × 相数 × 1.03（预留损耗 3%），单位 km；10kV 按 3 相，低压按 4 线。"),
    ("电杆组立", "数量 = 杆塔数（基）：Φ190×12m → YD3-0103，其余 → YD3-0101。"),
    ("拉线制作安装", "数量 = 拉线条数，定额 YD3-0201，主材按拉线主材单价计取。"),
    ("杆坑 / 拉线坑土方", "杆坑 = 杆塔数 × 1.2 m³（YD1-0101）；拉线坑 = 拉线条数 × 0.8 m³（YD1-0102）。"),
    ("金具", "10kV 按杆塔数套用 YD3-0601；1kV 按杆塔数套用 YD3-0602，主材取金具单价。"),
    ("设备安装", "变压器 YD2-0101/0102、配电箱 YD2-0201、智能网关 YD2-0301；设备费计入设备购置费，不进安装主材。"),
    ("其他主材", "避雷器 YD3-0401、电力电缆 YD3-0501、电缆终端头 YD3-0502、接地 YD2-0401、围栏 YD2-0501、支架 YD2-0601。"),
    ("土建", "台架变基础 YD1-0201（m³）；电缆直埋沟 YD1-0301（m），归入对应单位工程。"),
    ("拆除", "拆除低压导线 YD5-0101（km）、拆除水泥杆 YD5-0102（根），拆除工程不计主材。"),
]

app = FastAPI(title="设计图纸→建模→算量→计价 工作流原型", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))
serializer = URLSafeSerializer(SECRET, salt="design-cost-workflow-session")


# --------------------------------------------------------------------------- #
# 模板过滤器 / 全局函数
# --------------------------------------------------------------------------- #
def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def fmt_money(value) -> str:
    """金额：千分位 + 两位小数（元）。"""
    return f"{_num(value):,.2f}"


def fmt_wan(value) -> str:
    """金额：万元（两位小数）。"""
    return f"{_num(value) / 10000:,.2f}"


def fmt_num(value) -> str:
    """数量：最多 4 位小数，去掉无意义的 0。"""
    number = _num(value)
    if abs(number - round(number)) < 1e-9:
        return f"{int(round(number)):,}"
    return f"{number:,.4f}".rstrip("0").rstrip(".")


def fmt_pct(value) -> str:
    """差异率：带符号百分数。"""
    return f"{_num(value):+.2f}%"


def fmt_wan_pct(value) -> str:
    """展示用百分数（两位小数）。"""
    return f"{_num(value):.2f}%"


templates.env.filters["money"] = fmt_money
templates.env.filters["wan"] = fmt_wan
templates.env.filters["num"] = fmt_num
templates.env.filters["pct"] = fmt_pct
templates.env.filters["pct2"] = fmt_wan_pct

BADGE_MAP = {
    "已完成": "ok", "已识别": "ok", "正常": "ok", "通过": "ok", "计价定稿": "ok",
    "进行中": "info", "待复核": "warn", "需插件": "warn", "关注": "warn",
    "未开始": "muted", "待识别": "muted", "待确认": "muted", "已提交": "info",
    "解析失败": "danger", "异常": "danger", "高": "danger", "中": "warn", "低": "info",
    "甲供": "info", "乙供": "muted", "退回": "danger", "设备": "info", "主材": "muted",
    "已确认": "ok", "已忽略": "muted", "待确认": "warn", "未匹配": "muted",
}


def badge_class(text) -> str:
    """状态文本 → 徽标颜色类。"""
    label = str(text or "").strip()
    if label in BADGE_MAP:
        return BADGE_MAP[label]
    if "定稿" in label or "完成" in label:
        return "ok"
    if "复核" in label or label.startswith("待"):
        return "warn"
    return "info"


templates.env.globals["badge_class"] = badge_class


# --------------------------------------------------------------------------- #
# 数据库 / 身份 / 权限
# --------------------------------------------------------------------------- #
def _open_db() -> sqlite3.Connection:
    """打开 SQLite 连接（check_same_thread=False：sync 路由在线程池中执行）。"""
    conn = sqlite3.connect(str(DB_PATH), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.Error:  # pragma: no cover - 老版本 sqlite 兜底
        pass
    return conn


def get_db():
    """每个请求一个 SQLite 连接。"""
    conn = _open_db()
    try:
        yield conn
    finally:
        conn.close()


def now_text() -> str:
    """当前时间字符串 YYYY-MM-DD HH:MM（优先使用 seed.now）。"""
    fn = getattr(seed, "now", None)
    if callable(fn):
        try:
            return str(fn())
        except Exception:
            pass
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def verify_password(given: str, stored: str) -> bool:
    """校验密码：优先 seed.check_pw，兼容明文演示数据。"""
    stored = str(stored or "")
    if not stored:
        return False
    if stored == given:
        return True
    checker = getattr(seed, "check_pw", None)
    if callable(checker):
        try:
            if checker(given, stored):
                return True
        except Exception:
            pass
    return False


def current_user(request: Request, conn: sqlite3.Connection):
    """当前登录用户；DEMO=1 时 ?as=username 可切换演示身份（优先于 cookie）。"""
    user = None
    raw = request.cookies.get("session")
    if raw:
        try:
            uid = serializer.loads(raw, max_age=86400 * 7)
        except BadSignature:
            uid = None
        if uid:
            user = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if DEMO:
        as_name = (request.query_params.get("as") or "").strip()
        if as_name:
            row = conn.execute("SELECT * FROM users WHERE username=?", (as_name,)).fetchone()
            if row:
                user = row
    return user


def require_user(request: Request, conn: sqlite3.Connection):
    """要求登录，未登录抛出 303 → /login。"""
    user = current_user(request, conn)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def as_user_dict(user) -> dict:
    """engine/pricing 接口的 user 参数要求 dict（sqlite3.Row 不能直接传）。"""
    if user is None:
        return {}
    try:
        return dict(user)
    except (TypeError, ValueError):
        return {}


def require_role(user, roles: tuple[str, ...], action: str = "该操作") -> None:
    if user["role"] not in roles:
        label = ROLE_NAMES.get(str(user["role"]), str(user["role"]))
        raise HTTPException(
            status_code=403,
            detail=f"当前角色为{label}，无权{action}。请使用页面右上角“演示身份”切换对应角色。",
        )


def visible_project_ids(conn: sqlite3.Connection, user) -> list[int]:
    if user["role"] == "admin":
        return [int(r["id"]) for r in conn.execute("SELECT id FROM projects ORDER BY id")]
    rows = conn.execute(
        "SELECT p.id FROM projects p JOIN members m ON m.project_id=p.id "
        "WHERE m.user_id=? ORDER BY p.id",
        (user["id"],),
    )
    return [int(r["id"]) for r in rows]


def get_project(conn: sqlite3.Connection, pid: int, user):
    row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="项目不存在或已被删除。")
    if pid not in visible_project_ids(conn, user):
        raise HTTPException(status_code=403, detail="无权访问该项目，请联系管理员分配项目成员权限。")
    return row


def audit(conn: sqlite3.Connection, project_id, user, action: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO audit (project_id, user_id, action, detail, created_at) VALUES (?,?,?,?,?)",
        (project_id, user["id"] if user else None, action, detail, now_text()),
    )
    conn.commit()


def recent_audit(conn: sqlite3.Connection, pid: int, limit: int = 10):
    return conn.execute(
        "SELECT a.*, u.name AS user_name FROM audit a LEFT JOIN users u ON u.id=a.user_id "
        "WHERE a.project_id=? ORDER BY a.id DESC LIMIT ?",
        (pid, limit),
    ).fetchall()


# --------------------------------------------------------------------------- #
# 阶段状态封装（engine 接口 + 页面展示辅助）
# --------------------------------------------------------------------------- #
def get_stages(conn: sqlite3.Connection, pid: int) -> list[dict]:
    try:
        stages = engine.get_stages(conn, pid)
        return [dict(s) for s in stages]
    except Exception:
        rows = conn.execute("SELECT * FROM stages WHERE project_id=?", (pid,)).fetchall()
        by_key = {str(r["key"]): dict(r) for r in rows}
        return [by_key.get(key, {"key": key, "name": name, "status": "未开始"}) for key, name in STAGES]


def stage_map(conn: sqlite3.Connection, pid: int) -> dict[str, dict]:
    return {str(s["key"]): s for s in get_stages(conn, pid)}


def stage_of(conn: sqlite3.Connection, pid: int, key: str) -> dict:
    return stage_map(conn, pid).get(key, {"key": key, "name": STAGE_NAMES.get(key, key), "status": "未开始"})


def stage_editable(conn: sqlite3.Connection, pid: int, key: str) -> bool:
    """当前阶段是否允许造价员编辑（engine 判定，异常时以状态兜底）。"""
    try:
        return bool(engine.stage_editable(conn, pid, key))
    except Exception:
        return stage_of(conn, pid, key).get("status") == "进行中"


def require_stage_editable(conn: sqlite3.Connection, pid: int, key: str, label: str) -> None:
    if stage_editable(conn, pid, key):
        return
    status = stage_of(conn, pid, key).get("status", "未知")
    raise HTTPException(
        status_code=403,
        detail=f"{label}当前不可编辑：阶段状态为“{status}”。仅“进行中”阶段允许造价员修改，"
        f"如需调整请由复核员退回该阶段。",
    )


def workflow_ctx(conn: sqlite3.Connection, pid: int) -> dict:
    """项目页面通用上下文：四阶段、进度。"""
    stages = get_stages(conn, pid)
    return {
        "stages": stages,
        "stage_map": {str(s["key"]): s for s in stages},
        "progress": engine.project_progress(conn, pid),
    }


def can_edit_role(user) -> bool:
    return user["role"] in CAN_EDIT_ROLES


def can_review_role(user) -> bool:
    return user["role"] in CAN_REVIEW_ROLES


def can_autofill(conn: sqlite3.Connection, pid: int) -> bool:
    """建模初稿生成：图纸阶段已启动且建模阶段尚未完成时可用。"""
    drawings = stage_of(conn, pid, "drawings")
    model = stage_of(conn, pid, "model")
    return str(drawings.get("status")) != "未开始" and str(model.get("status")) != "已完成"


# --------------------------------------------------------------------------- #
# 典型设计（典设）匹配辅助（v2，辅助“图纸解析”阶段，不新增阶段门禁）
# --------------------------------------------------------------------------- #
def typdesign_ready() -> bool:
    """typdesign.py 是否已就绪。"""
    return typdesign is not None and hasattr(typdesign, "typdesign_summary")


def empty_typdesign_summary() -> dict:
    """typdesign 模块未就绪 / 未导入目录时的空态结构（页面渲染兜底）。"""
    return {
        "imports": [],
        "modules": 0,
        "matched": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "pending": 0,
        "confirmed": 0,
        "ignored": 0,
        "unmatched": 0,
        "rate": 0.0,
        "matches": [],
        "last_run": "",
    }


def td_summary(conn: sqlite3.Connection, pid: int) -> dict:
    """项目典设匹配汇总（页面上下文）。"""
    if not typdesign_ready():
        return empty_typdesign_summary()
    data = typdesign.typdesign_summary(conn, pid) or {}
    base = empty_typdesign_summary()
    base.update(data)
    return base


def td_stats(conn: sqlite3.Connection, pid: int) -> dict:
    """工作台/项目页/图纸页用的典设统计（轻量）。"""
    if not typdesign_ready():
        return {
            "modules": 0,
            "matched": 0,
            "confirmed": 0,
            "pending": 0,
            "ignored": 0,
            "unmatched": 0,
            "rate": 0.0,
        }
    data = typdesign.typdesign_stats(conn, pid) or {}
    return {
        "modules": int(_num(data.get("modules"))),
        "matched": int(_num(data.get("matched"))),
        "confirmed": int(_num(data.get("confirmed"))),
        "pending": int(_num(data.get("pending"))),
        "ignored": int(_num(data.get("ignored"))),
        "unmatched": int(_num(data.get("unmatched"))),
        "rate": _num(data.get("rate")),
    }


def td_confirmed(conn: sqlite3.Connection, pid: int) -> list[dict]:
    """已确认的典设模块（建模页只读参考面板）。"""
    if not typdesign_ready():
        return []
    return list(typdesign.confirmed_modules(conn, pid) or [])


# --------------------------------------------------------------------------- #
# 页面渲染 / 重定向 / 操作结果提示
# --------------------------------------------------------------------------- #
def page(request: Request, name: str, user, **context):
    """统一模板上下文（顶部导航、flash、演示身份、项目内导航）。"""
    project = context.get("p")
    pid = context.get("pid") or (project["id"] if project is not None else None)
    as_name = (request.query_params.get("as") or "").strip() if DEMO else ""
    data = {
        "user": user,
        "demo": DEMO,
        "demo_as": as_name,
        "keep_as": f"?as={as_name}" if as_name else "",
        "nav_p": project,
        "nav_pid": pid,
        "msg": request.query_params.get("msg") or "",
        "msg_level": request.query_params.get("level") or "ok",
        "role_names": ROLE_NAMES,
        "stages_meta": STAGES,
        "can_edit": can_edit_role(user) if user else False,
        "can_review": can_review_role(user) if user else False,
    }
    data.update(context)
    return templates.TemplateResponse(request, name, data)


def redirect_msg(request: Request, path: str, msg: str, level: str = "ok") -> RedirectResponse:
    """303 重定向并在 query 中携带中文提示（flash）。"""
    params: dict[str, str] = {"msg": msg}
    if level != "ok":
        params["level"] = level
    as_name = (request.query_params.get("as") or "").strip() if DEMO else ""
    if as_name:
        params["as"] = as_name
    sep = "&" if "?" in path else "?"
    return RedirectResponse(path + sep + urlencode(params), status_code=303)


def keep_as(request: Request) -> str:
    as_name = (request.query_params.get("as") or "").strip() if DEMO else ""
    return f"?as={as_name}" if as_name else ""


def engine_call(fn, *args) -> tuple[bool, object]:
    """调用 engine/pricing 接口：返回 (ok, 结果或错误提示)，异常转中文提示。"""
    try:
        return True, fn(*args)
    except Exception as exc:  # 引擎异常不应对用户表现为 500
        return False, f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
# 登录 / 退出 / 工作台
# --------------------------------------------------------------------------- #
@app.get("/login")
def login_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = current_user(request, conn)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "user": user,
            "demo": DEMO,
            "msg": request.query_params.get("msg") or "",
            "msg_level": request.query_params.get("level") or "ok",
            "role_names": ROLE_NAMES,
        },
    )


@app.post("/login")
async def login_submit(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    form = await request.form()
    username = str(form.get("username") or "").strip()
    password = str(form.get("password") or "")
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row or not verify_password(password, row["password"]):
        return redirect_msg(request, "/login", "用户名或密码错误，请重试（演示账号见登录页下方）", "error")
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        "session",
        serializer.dumps(int(row["id"])),
        max_age=86400 * 7,
        httponly=True,
        samesite="lax",
    )
    audit(conn, None, row, "登录", f"{ROLE_NAMES.get(str(row['role']), row['role'])} 登录工作台")
    return response


@app.get("/logout")
def logout(request: Request):
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("session")
    return response


@app.get("/")
def dashboard(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    projects = conn.execute("SELECT * FROM projects ORDER BY id").fetchall()
    cards: list[dict] = []
    todos: list[dict] = []
    risks: list[dict] = []
    design_total = 0.0
    model_total = 0.0
    for project in projects:
        pid = int(project["id"])
        stages = get_stages(conn, pid)
        progress = engine.project_progress(conn, pid)
        stats = engine.project_stats(conn, pid)
        summary = pricing.pricing_summary(conn, pid)
        model_amount = _num(summary.get("static_total"))
        design_amount = _num(project["design_total"])
        design_total += design_amount
        model_total += model_amount
        for stage in stages:
            if stage.get("status") == "待复核":
                todos.append(
                    {
                        "kind": "review",
                        "title": f"{project['name']} · {stage.get('name')}待复核",
                        "desc": f"提交人：{stage.get('owner') or '—'}，请复核员审核",
                        "pid": pid,
                        "stage": stage.get("key"),
                        "action": "去复核",
                    }
                )
            elif stage.get("status") == "进行中":
                todos.append(
                    {
                        "kind": "work",
                        "title": f"{project['name']} · {stage.get('name')}进行中",
                        "desc": f"负责人：{stage.get('owner') or '造价员'}，请继续完善并提交复核",
                        "pid": pid,
                        "stage": stage.get("key"),
                        "action": "继续处理",
                    }
                )
        for mat in conn.execute(
            "SELECT * FROM compare_materials WHERE project_id=? AND level IN ('异常','关注') ORDER BY seq",
            (pid,),
        ).fetchall():
            risks.append(
                {
                    "pid": pid,
                    "project": project["name"],
                    "level": mat["level"],
                    "title": f"{mat['name']}（设计 {fmt_num(mat['design_qty'])} / 模型 {fmt_num(mat['model_qty'])}）",
                    "reason": mat["reason"] or "差异待核实",
                }
            )
        for cmp_row in pricing.compare_budget(conn, pid):
            if str(cmp_row.get("item")) == "静态投资" and abs(_num(cmp_row.get("rate"))) > 3:
                risks.append(
                    {
                        "pid": pid,
                        "project": project["name"],
                        "level": "关注",
                        "title": f"静态投资差异 {fmt_pct(cmp_row.get('rate'))}",
                        "reason": "模型预算与设计预算差异超过 3%，请核对工程量与定额套用",
                    }
                )
        cards.append(
            {
                "p": project,
                "stages": stages,
                "progress": progress,
                "stats": stats,
                "summary": summary,
                "design_total": design_amount,
                "model_total": model_amount,
                "diff_rate": ((model_amount - design_amount) / design_amount * 100) if design_amount else 0.0,
                "priced": model_amount > 0,
            }
        )

    pending_review = sum(1 for t in todos if t["kind"] == "review")
    active_stages = sum(
        1
        for card in cards
        for stage in card["stages"]
        if stage.get("status") in ("进行中", "待复核")
    )
    kpis = [
        {"label": "项目总数", "value": str(len(cards)), "sub": "统一四阶段工作流", "cls": ""},
        {"label": "在建阶段", "value": str(active_stages), "sub": f"其中待复核 {pending_review} 个", "cls": "warn" if active_stages else "ok"},
        {"label": "设计静态投资合计", "value": fmt_wan(design_total), "sub": "万元（附件5 设计预算）", "cls": ""},
        {"label": "模型预算合计", "value": fmt_wan(model_total), "sub": "万元（已计价项目）", "cls": "ok" if model_total else ""},
    ]
    return page(
        request,
        "dashboard.html",
        user,
        active="dashboard",
        cards=cards,
        todos=todos[:8],
        risks=risks[:6],
        kpis=kpis,
        pending_review=pending_review,
    )


# --------------------------------------------------------------------------- #
# 项目工作流主页
# --------------------------------------------------------------------------- #
@app.get("/project/{pid}")
def project_home(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    project = get_project(conn, pid, user)
    ctx = workflow_ctx(conn, pid)
    summary = pricing.pricing_summary(conn, pid)
    compare_rows = pricing.compare_budget(conn, pid)
    stats = engine.project_stats(conn, pid)
    checks = engine.validate_model(conn, pid)
    check_counts = {"高": 0, "中": 0, "低": 0}
    for check in checks:
        check_counts[str(check.get("level"))] = check_counts.get(str(check.get("level")), 0) + 1
    materials = conn.execute(
        "SELECT * FROM compare_materials WHERE project_id=? ORDER BY seq,id", (pid,)
    ).fetchall()
    alerts = [m for m in materials if m["level"] in ("异常", "关注")]
    design_total = _num(project["design_total"])
    model_total = _num(summary.get("static_total"))
    return page(
        request,
        "project.html",
        user,
        active="flow",
        p=project,
        pid=pid,
        stage_state=ctx["stage_map"],
        check_counts=check_counts,
        checks=checks[:6],
        alerts=alerts[:6],
        audit=recent_audit(conn, pid, 8),
        stats=stats,
        summary=summary,
        compare_rows=compare_rows,
        design_total=design_total,
        model_total=model_total,
        diff_rate=((model_total - design_total) / design_total * 100) if design_total else 0.0,
        td_stats=td_stats(conn, pid),
        **ctx,
    )


# --------------------------------------------------------------------------- #
# 图纸解析
# --------------------------------------------------------------------------- #
@app.get("/project/{pid}/drawings")
def drawings_page(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    project = get_project(conn, pid, user)
    ctx = workflow_ctx(conn, pid)
    rows = conn.execute(
        "SELECT d.*, u.name AS uploader_name FROM drawings d "
        "LEFT JOIN users u ON u.id=d.uploaded_by WHERE d.project_id=? ORDER BY d.id",
        (pid,),
    ).fetchall()
    counts = {"total": len(rows), "已识别": 0, "需插件": 0, "解析失败": 0, "待识别": 0}
    for row in rows:
        status = str(row["status"])
        counts[status] = counts.get(status, 0) + 1
    identified = [row for row in rows if row["status"] == "已识别"]
    editable = can_edit_role(user) and stage_editable(conn, pid, "drawings")
    return page(
        request,
        "drawings.html",
        user,
        active="drawings",
        pid=pid,
        p=project,
        drawings=rows,
        counts=counts,
        identified=identified,
        editable=editable,
        autofill_ok=can_edit_role(user) and can_autofill(conn, pid),
        stage=ctx["stage_map"].get("drawings", {}),
        td_stats=td_stats(conn, pid),
        td_ready=typdesign_ready(),
        **ctx,
    )


@app.post("/project/{pid}/drawings/upload")
async def drawings_upload(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    get_project(conn, pid, user)
    require_role(user, CAN_EDIT_ROLES, "上传图纸")
    require_stage_editable(conn, pid, "drawings", "上传图纸：")

    form = await request.form()
    uploads = [
        item
        for item in form.getlist("files")
        if hasattr(item, "filename") and hasattr(item, "read") and (item.filename or "").strip()
    ]
    category_choice = str(form.get("category") or "自动识别").strip()
    if not uploads:
        return redirect_msg(request, f"/project/{pid}/drawings", "请选择至少一个图纸 / 清册文件再上传", "error")

    dest_dir = UPLOAD_DIR / str(pid)
    dest_dir.mkdir(parents=True, exist_ok=True)
    base_seq = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM drawings").fetchone()[0])
    saved = identified = pending = skipped = 0
    for index, upload in enumerate(uploads):
        filename = Path(upload.filename or "").name
        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_UPLOAD_EXTS:
            skipped += 1
            continue
        content = await upload.read()
        if not content or len(content) > MAX_UPLOAD_BYTES:
            skipped += 1
            continue
        stored_name = f"{datetime.now():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:6]}{ext}"
        dest = dest_dir / stored_name
        try:
            dest.write_bytes(content)
        except OSError:
            skipped += 1
            continue
        meta = guess_meta(filename)
        parsed = parse_drawing(dest, filename)
        category = category_choice if category_choice not in ("", "自动识别") else meta["category"]
        status = str(parsed.get("status") or "待识别")
        conn.execute(
            "INSERT INTO drawings (project_id, code, name, category, fmt, sheet_count, status, "
            "summary, file_path, uploaded_by, uploaded_at, parsed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                pid,
                f"UP-{pid}-{base_seq + index + 1:03d}",
                meta["name"],
                category,
                meta["fmt"],
                int(parsed.get("sheet_count") or 1),
                status,
                parsed.get("summary") or "",
                str(dest.relative_to(DATA_DIR)),
                int(user["id"]),
                now_text(),
                now_text() if status in ("已识别", "解析失败", "需插件") else None,
            ),
        )
        saved += 1
        if status == "已识别":
            identified += 1
        else:
            pending += 1
    conn.commit()
    audit(conn, pid, user, "上传图纸", f"上传 {saved} 个文件，识别成功 {identified} 个，需插件/失败 {pending} 个")
    msg = f"已上传 {saved} 个文件：识别成功 {identified} 个"
    if pending:
        msg += f"，需插件或解析失败 {pending} 个（可在表格中查看原因）"
    if skipped:
        msg += f"，跳过 {skipped} 个不支持或空文件"
    return redirect_msg(request, f"/project/{pid}/drawings", msg)


@app.post("/project/{pid}/drawings/autofill")
async def drawings_autofill(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    get_project(conn, pid, user)
    require_role(user, CAN_EDIT_ROLES, "生成模型初稿")
    if not can_autofill(conn, pid):
        return redirect_msg(
            request,
            f"/project/{pid}/drawings",
            "当前不可生成模型初稿：请先开始图纸解析，或在建模阶段完成前操作",
            "error",
        )
    ok, result = engine_call(engine.autofill_model, conn, pid, as_user_dict(user))
    if not ok:
        return redirect_msg(request, f"/project/{pid}/drawings", f"生成模型初稿失败：{result}", "error")
    added = result if isinstance(result, int) else 0
    if added > 0:
        msg = f"已由已识别图纸补全模型初稿，新增 {added} 行数据，请到“建模”页人工校正后执行校验"
    else:
        msg = "模型初稿已存在（本次补全 0 行），请到“建模”页人工校正并执行校验"
    return redirect_msg(request, f"/project/{pid}/model", msg)


# --------------------------------------------------------------------------- #
# 阶段提交 / 复核
# --------------------------------------------------------------------------- #
@app.post("/project/{pid}/stage/{key}/submit")
async def stage_submit(pid: int, key: str, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    get_project(conn, pid, user)
    if key not in STAGE_KEYS:
        raise HTTPException(status_code=404, detail="阶段不存在。")
    require_role(user, CAN_EDIT_ROLES, "提交阶段复核")
    stage = stage_of(conn, pid, key)
    if stage.get("status") != "进行中":
        return redirect_msg(
            request,
            f"/project/{pid}",
            f"{stage.get('name') or key}当前状态为“{stage.get('status')}”，仅“进行中”阶段可以提交复核",
            "error",
        )
    ok, result = engine_call(engine.submit_stage, conn, pid, key, as_user_dict(user))
    if not ok:
        return redirect_msg(request, f"/project/{pid}", f"提交失败：{result}", "error")
    ok, msg = _unpack_engine_result(result, "提交成功，已进入待复核")
    return redirect_msg(
        request,
        f"/project/{pid}",
        msg if ok else f"提交失败：{msg}",
        "ok" if ok else "error",
    )


@app.post("/project/{pid}/stage/{key}/review")
async def stage_review(pid: int, key: str, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    get_project(conn, pid, user)
    if key not in STAGE_KEYS:
        raise HTTPException(status_code=404, detail="阶段不存在。")
    require_role(user, CAN_REVIEW_ROLES, "复核阶段")
    form = await request.form()
    result = str(form.get("result") or "").strip()
    comment = str(form.get("comment") or "").strip()
    if result not in ("通过", "退回"):
        return redirect_msg(request, f"/project/{pid}", "复核结论无效，请选择“通过”或“退回”", "error")
    stage = stage_of(conn, pid, key)
    if stage.get("status") != "待复核":
        return redirect_msg(
            request,
            f"/project/{pid}",
            f"{stage.get('name') or key}当前状态为“{stage.get('status')}”，无需复核",
            "error",
        )
    ok, res = engine_call(engine.review_stage, conn, pid, key, as_user_dict(user), result, comment)
    if not ok:
        return redirect_msg(request, f"/project/{pid}", f"复核失败：{res}", "error")
    ok2, msg = _unpack_engine_result(res, f"复核{result}，阶段状态已更新")
    return redirect_msg(
        request, f"/project/{pid}", msg if ok2 else f"复核失败：{msg}", "ok" if ok2 else "error"
    )


def _unpack_engine_result(result, default_msg: str) -> tuple[bool, str]:
    """engine 返回 (ok, msg) 或 None / int 时统一成 (ok, msg)。"""
    if isinstance(result, tuple) and len(result) >= 2:
        return bool(result[0]), str(result[1])
    if result is None:
        return True, default_msg
    return True, str(result) if isinstance(result, str) and result else default_msg


# --------------------------------------------------------------------------- #
# 建模
# --------------------------------------------------------------------------- #
MODEL_SPECS: dict[str, dict] = {
    "line": {
        "table": "model_lines",
        "label": "线路",
        "text": ["name", "voltage", "start_point", "end_point", "conductor", "pole_type", "soil", "source", "note"],
        "real": ["length_km", "span_m"],
        "int": ["circuits", "phases", "poles", "stays"],
        "defaults": {"voltage": "10kV", "circuits": 1, "phases": 3, "soil": "普通土", "pole_type": "Φ150×8m"},
        "required": "name",
    },
    "equipment": {
        "table": "model_equipment",
        "label": "设备 / 主材",
        "text": ["category", "name", "spec", "unit", "supply", "source", "note"],
        "real": ["qty", "unit_price"],
        "int": [],
        "defaults": {"category": "设备", "supply": "甲供", "unit": "台"},
        "required": "name",
    },
    "civil": {
        "table": "model_civil",
        "label": "土建",
        "text": ["name", "spec", "unit", "note"],
        "real": ["qty"],
        "int": [],
        "defaults": {"unit": "m³"},
        "required": "name",
    },
    "demolition": {
        "table": "model_demolition",
        "label": "拆除",
        "text": ["name", "spec", "unit", "note"],
        "real": ["qty"],
        "int": [],
        "defaults": {"unit": "km"},
        "required": "name",
    },
}


def _form_to_row(spec: dict, form) -> dict:
    data: dict[str, object] = {}
    for field in spec["text"]:
        value = str(form.get(field) or "").strip()
        if field in spec["defaults"] and value == "":
            value = str(spec["defaults"][field])
        data[field] = value
    for field in spec["real"]:
        raw = str(form.get(field) or "").strip()
        try:
            data[field] = float(raw) if raw else 0.0
        except ValueError:
            data[field] = 0.0
    for field in spec["int"]:
        raw = str(form.get(field) or "").strip()
        try:
            data[field] = int(float(raw)) if raw else int(spec["defaults"].get(field, 0))
        except ValueError:
            data[field] = int(spec["defaults"].get(field, 0))
    for field, value in spec["defaults"].items():
        data.setdefault(field, value)
    return data


@app.get("/project/{pid}/model")
def model_page(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    project = get_project(conn, pid, user)
    ctx = workflow_ctx(conn, pid)
    lines = conn.execute("SELECT * FROM model_lines WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    equipment = conn.execute("SELECT * FROM model_equipment WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    civil = conn.execute("SELECT * FROM model_civil WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    demolition = conn.execute("SELECT * FROM model_demolition WHERE project_id=? ORDER BY id", (pid,)).fetchall()
    checks = engine.validate_model(conn, pid)
    check_counts = {"高": 0, "中": 0, "低": 0}
    for check in checks:
        check_counts[str(check.get("level"))] = check_counts.get(str(check.get("level")), 0) + 1
    editable = can_edit_role(user) and stage_editable(conn, pid, "model")
    stats = {
        "lines": len(lines),
        "length_km": sum(_num(line["length_km"]) for line in lines),
        "poles": sum(int(_num(line["poles"])) for line in lines),
        "equipment": sum(1 for row in equipment if row["category"] == "设备"),
        "materials": sum(1 for row in equipment if row["category"] == "主材"),
        "civil": len(civil),
        "demolition": len(demolition),
    }
    return page(
        request,
        "model.html",
        user,
        active="model",
        pid=pid,
        p=project,
        lines=lines,
        equipment=equipment,
        civil=civil,
        demolition=demolition,
        checks=checks,
        check_counts=check_counts,
        editable=editable,
        autofill_ok=can_edit_role(user) and can_autofill(conn, pid),
        svg=build_model_svg(project, lines, equipment),
        model_stats=stats,
        stage=ctx["stage_map"].get("model", {}),
        td_stats=td_stats(conn, pid),
        td_confirmed=td_confirmed(conn, pid),
        **ctx,
    )


@app.post("/project/{pid}/model/autofill")
async def model_autofill(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    get_project(conn, pid, user)
    require_role(user, CAN_EDIT_ROLES, "生成模型初稿")
    if not can_autofill(conn, pid):
        return redirect_msg(
            request,
            f"/project/{pid}/model",
            "当前不可生成模型初稿：图纸解析尚未开始，或建模阶段已完成",
            "error",
        )
    ok, result = engine_call(engine.autofill_model, conn, pid, as_user_dict(user))
    if not ok:
        return redirect_msg(request, f"/project/{pid}/model", f"生成模型初稿失败：{result}", "error")
    added = result if isinstance(result, int) else 0
    msg = (
        f"已由已识别图纸补全模型初稿，新增 {added} 行，请人工校正后执行校验"
        if added > 0
        else "模型初稿已存在（本次补全 0 行），请人工校正后执行校验"
    )
    return redirect_msg(request, f"/project/{pid}/model", msg)


@app.post("/project/{pid}/model/validate")
async def model_validate(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    get_project(conn, pid, user)
    checks = engine.validate_model(conn, pid)
    counts = {"高": 0, "中": 0, "低": 0}
    for check in checks:
        counts[str(check.get("level"))] = counts.get(str(check.get("level")), 0) + 1
    audit(conn, pid, user, "模型校验", f"高级 {counts['高']} 项、中级 {counts['中']} 项、低级 {counts['低']} 项")
    msg = f"校验完成：高级问题 {counts['高']} 项、中级 {counts['中']} 项、低级 {counts['低']} 项"
    if counts["高"] == 0:
        msg += "，无高级问题，可提交复核"
    return redirect_msg(request, f"/project/{pid}/model", msg, "ok" if counts["高"] == 0 else "error")


async def _model_add(request: Request, conn: sqlite3.Connection, pid: int, kind: str):
    user = require_user(request, conn)
    get_project(conn, pid, user)
    require_role(user, CAN_EDIT_ROLES, "编辑模型")
    require_stage_editable(conn, pid, "model", "编辑模型：")
    spec = MODEL_SPECS[kind]
    form = await request.form()
    data = _form_to_row(spec, form)
    if not str(data.get(spec["required"]) or "").strip():
        return redirect_msg(request, f"/project/{pid}/model", f"新增{spec['label']}失败：名称不能为空", "error")
    columns = list(data.keys())
    placeholders = ",".join("?" for _ in columns)
    conn.execute(
        f"INSERT INTO {spec['table']} (project_id, {','.join(columns)}) VALUES (?,{placeholders})",
        (pid, *[data[column] for column in columns]),
    )
    conn.commit()
    audit(conn, pid, user, f"新增{spec['label']}", str(data.get("name")))
    return redirect_msg(request, f"/project/{pid}/model", f"已新增{spec['label']}：{data.get('name')}")


async def _model_update(request: Request, conn: sqlite3.Connection, pid: int, kind: str, mid: int):
    user = require_user(request, conn)
    get_project(conn, pid, user)
    require_role(user, CAN_EDIT_ROLES, "编辑模型")
    require_stage_editable(conn, pid, "model", "编辑模型：")
    spec = MODEL_SPECS[kind]
    row = conn.execute(
        f"SELECT * FROM {spec['table']} WHERE id=? AND project_id=?", (mid, pid)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"{spec['label']}记录不存在。")
    form = await request.form()
    data = _form_to_row(spec, form)
    if not str(data.get(spec["required"]) or "").strip():
        return redirect_msg(request, f"/project/{pid}/model", f"保存失败：{spec['label']}名称不能为空", "error")
    assignments = ",".join(f"{column}=?" for column in data)
    conn.execute(
        f"UPDATE {spec['table']} SET {assignments} WHERE id=? AND project_id=?",
        (*[data[column] for column in data], mid, pid),
    )
    conn.commit()
    audit(conn, pid, user, f"修改{spec['label']}", str(data.get("name")))
    return redirect_msg(request, f"/project/{pid}/model", f"已保存{spec['label']}：{data.get('name')}")


async def _model_delete(request: Request, conn: sqlite3.Connection, pid: int, kind: str, mid: int):
    user = require_user(request, conn)
    get_project(conn, pid, user)
    require_role(user, CAN_EDIT_ROLES, "编辑模型")
    require_stage_editable(conn, pid, "model", "编辑模型：")
    spec = MODEL_SPECS[kind]
    row = conn.execute(
        f"SELECT * FROM {spec['table']} WHERE id=? AND project_id=?", (mid, pid)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"{spec['label']}记录不存在。")
    conn.execute(f"DELETE FROM {spec['table']} WHERE id=? AND project_id=?", (mid, pid))
    conn.commit()
    audit(conn, pid, user, f"删除{spec['label']}", str(row["name"]))
    return redirect_msg(request, f"/project/{pid}/model", f"已删除{spec['label']}：{row['name']}")


@app.post("/project/{pid}/model/line/add")
async def model_line_add(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return await _model_add(request, conn, pid, "line")


@app.post("/project/{pid}/model/line/{mid}/update")
async def model_line_update(pid: int, mid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return await _model_update(request, conn, pid, "line", mid)


@app.post("/project/{pid}/model/line/{mid}/delete")
async def model_line_delete(pid: int, mid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return await _model_delete(request, conn, pid, "line", mid)


@app.post("/project/{pid}/model/equipment/add")
async def model_equipment_add(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return await _model_add(request, conn, pid, "equipment")


@app.post("/project/{pid}/model/equipment/{mid}/update")
async def model_equipment_update(pid: int, mid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return await _model_update(request, conn, pid, "equipment", mid)


@app.post("/project/{pid}/model/equipment/{mid}/delete")
async def model_equipment_delete(pid: int, mid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return await _model_delete(request, conn, pid, "equipment", mid)


@app.post("/project/{pid}/model/civil/add")
async def model_civil_add(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return await _model_add(request, conn, pid, "civil")


@app.post("/project/{pid}/model/civil/{mid}/update")
async def model_civil_update(pid: int, mid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return await _model_update(request, conn, pid, "civil", mid)


@app.post("/project/{pid}/model/civil/{mid}/delete")
async def model_civil_delete(pid: int, mid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return await _model_delete(request, conn, pid, "civil", mid)


@app.post("/project/{pid}/model/demolition/add")
async def model_demolition_add(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return await _model_add(request, conn, pid, "demolition")


@app.post("/project/{pid}/model/demolition/{mid}/update")
async def model_demolition_update(pid: int, mid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return await _model_update(request, conn, pid, "demolition", mid)


@app.post("/project/{pid}/model/demolition/{mid}/delete")
async def model_demolition_delete(pid: int, mid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    return await _model_delete(request, conn, pid, "demolition", mid)


# --------------------------------------------------------------------------- #
# 算量
# --------------------------------------------------------------------------- #
def compare_material_rows(conn: sqlite3.Connection, pid: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM compare_materials WHERE project_id=? ORDER BY seq,id", (pid,)
    ).fetchall()
    return [dict(row) for row in rows]


@app.get("/project/{pid}/quantities")
def quantities_page(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    project = get_project(conn, pid, user)
    ctx = workflow_ctx(conn, pid)
    rows = conn.execute(
        "SELECT * FROM quantities WHERE project_id=? ORDER BY seq,id", (pid,)
    ).fetchall()
    by_category: dict[str, int] = {}
    for row in rows:
        key = str(row["category"] or "其他")
        by_category[key] = by_category.get(key, 0) + 1
    stats = engine.project_stats(conn, pid)
    editable = can_edit_role(user) and stage_editable(conn, pid, "quantities")
    return page(
        request,
        "quantities.html",
        user,
        active="quantities",
        pid=pid,
        p=project,
        rows=rows,
        by_category=by_category,
        compare_rows=compare_material_rows(conn, pid),
        stats=stats,
        editable=editable,
        rules=QUANTITY_RULES,
        stage=ctx["stage_map"].get("quantities", {}),
        **ctx,
    )


@app.post("/project/{pid}/quantities/compute")
async def quantities_compute(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    get_project(conn, pid, user)
    require_role(user, CAN_EDIT_ROLES, "计算工程量")
    require_stage_editable(conn, pid, "quantities", "重新计算工程量：")
    ok, result = engine_call(engine.compute_quantities, conn, pid, as_user_dict(user))
    if not ok:
        return redirect_msg(request, f"/project/{pid}/quantities", f"工程量计算失败：{result}", "error")
    count = len(result) if isinstance(result, (list, tuple)) else 0
    msg = f"已按规则重算工程量：生成清单 {count} 行，并同步生成材料量对比（设计清册 vs 模型量）"
    return redirect_msg(request, f"/project/{pid}/quantities", msg)


# --------------------------------------------------------------------------- #
# 计价
# --------------------------------------------------------------------------- #
def pricing_page_data(conn: sqlite3.Connection, pid: int, project) -> dict:
    """计价页所需数据（summary / 定额库 / 主材明细 / 对比 / AI 差异说明）。"""
    summary = pricing.pricing_summary(conn, pid)
    quota_lib = pricing.quota_library()
    item_materials: dict[int, list] = {}
    for key, value in (summary.get("materials") or {}).items():
        try:
            item_materials[int(key)] = list(value)
        except (TypeError, ValueError):
            continue
    compare_rows = summary.get("compare") or pricing.compare_budget(conn, pid)
    static_row = next(
        (dict(row) for row in compare_rows if str(row.get("item")) == "静态投资"), None
    )
    return {
        "summary": summary,
        "quota_lib": quota_lib,
        "item_materials": item_materials,
        "compare_rows": compare_rows,
        "static_row": static_row,
        "insights": pricing_insights(summary, compare_rows, project),
    }


def pricing_insights(summary: dict, compare_rows: list, project) -> list[dict]:
    """基于模型预算与设计预算的差异，生成中文差异说明（只使用真实计算值）。"""
    insights: list[dict] = []
    static_total = _num(summary.get("static_total"))
    if static_total <= 0:
        return [
            {
                "level": "info",
                "title": "尚未完成计价",
                "text": "点击“重新计价”后，系统将按 2022 版编制规定的费用构成与费率生成预算，并自动与设计预算（附件5）对比。",
            }
        ]
    for row in compare_rows:
        item = str(row.get("item") or "")
        rate = _num(row.get("rate"))
        diff = _num(row.get("diff"))
        level = "warn" if abs(rate) >= 3 else "ok"
        direction = "高于" if diff >= 0 else "低于"
        insights.append(
            {
                "level": level,
                "title": f"{item}：模型预算 {fmt_wan(row.get('model_amount'))} 万元，{direction}设计预算 {fmt_wan(abs(diff))} 万元（{fmt_pct(rate)}）",
                "text": str(row.get("note") or "差异在可控范围内，建议复核工程量与定额套用。"),
            }
        )
    equipment = _num(summary.get("equipment"))
    other = _num(summary.get("other"))
    if static_total:
        insights.append(
            {
                "level": "info",
                "title": "费用结构",
                "text": (
                    f"设备购置费占静态投资 {fmt_wan_pct(equipment / static_total * 100)}%，"
                    f"其他费用占 {fmt_wan_pct(other / static_total * 100)}%，"
                    f"基本预备费按（建安+设备+其他）×3% 计取。"
                ),
            }
        )
    insights.append(
        {
            "level": "info",
            "title": "编制口径说明",
            "text": "费率为 2022 版《20kV及以下配电网工程建设预算编制与计算规定》江西Ⅰ类地区县城/镇演示取值，"
            "定额库与材料价为示例数据，正式项目需替换为正式定额库并人工复核。",
        }
    )
    return insights


@app.get("/project/{pid}/pricing")
def pricing_page(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    project = get_project(conn, pid, user)
    ctx = workflow_ctx(conn, pid)
    data = pricing_page_data(conn, pid, project)
    editable = can_edit_role(user) and stage_editable(conn, pid, "pricing")
    return page(
        request,
        "pricing.html",
        user,
        active="pricing",
        pid=pid,
        p=project,
        editable=editable,
        stage=ctx["stage_map"].get("pricing", {}),
        **data,
        **ctx,
    )


@app.post("/project/{pid}/pricing/compute")
async def pricing_compute(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    get_project(conn, pid, user)
    require_role(user, CAN_EDIT_ROLES, "计价")
    require_stage_editable(conn, pid, "pricing", "重新计价：")
    ok, result = engine_call(pricing.compute_pricing, conn, pid, as_user_dict(user))
    if not ok:
        return redirect_msg(request, f"/project/{pid}/pricing", f"计价失败：{result}", "error")
    data = result if isinstance(result, dict) else {}
    total = _num(data.get("static_total"))
    items = int(_num(data.get("items")))
    msg = f"已按示例定额库与 2022 版费率完成计价：定额子目 {items} 项，静态投资 {fmt_wan(total)} 万元"
    return redirect_msg(request, f"/project/{pid}/pricing", msg)


@app.post("/project/{pid}/pricing/item/{iid}/quota")
async def pricing_change_quota(pid: int, iid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    get_project(conn, pid, user)
    require_role(user, CAN_EDIT_ROLES, "更换定额子目")
    require_stage_editable(conn, pid, "pricing", "更换定额子目：")
    form = await request.form()
    quota_code = str(form.get("quota_code") or "").strip()
    library = {str(q.get("code")): q for q in pricing.quota_library()}
    if quota_code not in library:
        return redirect_msg(request, f"/project/{pid}/pricing", "定额子目不存在，请重新选择", "error")
    item = conn.execute(
        "SELECT * FROM pricing_items WHERE id=? AND project_id=?", (iid, pid)
    ).fetchone()
    if not item:
        raise HTTPException(status_code=404, detail="计价子目不存在。")
    # 优先使用 pricing.update_item_quota（会重算该行并更新费用汇总 / 预算对比）
    changer = getattr(pricing, "update_item_quota", None)
    if callable(changer):
        ok, result = engine_call(changer, conn, pid, iid, quota_code, as_user_dict(user))
        if not ok:
            return redirect_msg(request, f"/project/{pid}/pricing", f"更换定额失败：{result}", "error")
        ok2, msg = _unpack_engine_result(result, f"已更换定额子目为 {quota_code}，费用汇总已重算")
        return redirect_msg(
            request, f"/project/{pid}/pricing", msg if ok2 else f"更换定额失败：{msg}", "ok" if ok2 else "error"
        )
    # 兜底：把定额写回 quantities 后整项目重算（pricing.compute_pricing 会优先采用 quantities.quota_code）
    if item["quantity_id"]:
        conn.execute(
            "UPDATE quantities SET quota_code=? WHERE id=? AND project_id=?",
            (quota_code, item["quantity_id"], pid),
        )
        conn.commit()
    conn.execute(
        "UPDATE pricing_items SET quota_code=?, quota_name=? WHERE id=? AND project_id=?",
        (quota_code, library[quota_code].get("name"), iid, pid),
    )
    conn.commit()
    pricing.compute_pricing(conn, pid, as_user_dict(user))
    audit(conn, pid, user, "更换定额", f"{item['name']} → {quota_code}")
    return redirect_msg(request, f"/project/{pid}/pricing", f"已更换定额子目为 {quota_code}，费用汇总已重算")


# --------------------------------------------------------------------------- #
# 对比分析
# --------------------------------------------------------------------------- #
def submission_conditions(
    conn: sqlite3.Connection,
    pid: int,
    stages: list[dict],
    checks,
    materials,
    compare_rows,
    td: dict | None = None,
) -> list[dict]:
    """送审条件检查清单（含 v2 典设匹配条件）。"""
    stages_by_key = {str(s.get("key")): s for s in stages}
    done = sum(1 for s in stages if s.get("status") == "已完成")
    high = sum(1 for c in checks if str(c.get("level")) == "高")
    abnormal = [m for m in materials if m.get("level") == "异常"]
    warned = [m for m in materials if m.get("level") == "关注"]
    static_row = next((row for row in compare_rows if str(row.get("item")) == "静态投资"), None)
    max_rate = abs(_num(static_row.get("rate"))) if static_row else 0.0
    pricing_done = str(stages_by_key.get("pricing", {}).get("status")) == "已完成"
    td = td or {}
    td_modules = int(_num(td.get("modules")))
    td_pending = int(_num(td.get("pending")))
    td_confirmed_count = int(_num(td.get("confirmed")))
    td_ok = td_modules > 0 and td_pending == 0 and td_confirmed_count >= 1
    if td_modules == 0:
        td_detail = "尚未导入典设目录"
    elif td_pending:
        td_detail = f"待确认 {td_pending} 个模块（已确认 {td_confirmed_count}/{td_modules}）"
    else:
        td_detail = f"已确认 {td_confirmed_count}/{td_modules} 个模块"
    conditions = [
        {
            "name": "四阶段全部完成",
            "ok": done == len(stages),
            "detail": f"当前已完成 {done}/{len(stages)} 个阶段",
        },
        {
            "name": "模型校验无高级问题",
            "ok": high == 0,
            "detail": f"高级问题 {high} 项" if high else "模型校验通过（无高级问题）",
        },
        {
            "name": "材料量差异已核实",
            "ok": len(abnormal) == 0,
            "detail": f"异常 {len(abnormal)} 项、关注 {len(warned)} 项" if abnormal or warned else "无异常差异",
        },
        {
            "name": "预算差异率在 ±3% 以内",
            "ok": static_row is not None and max_rate <= 3,
            "detail": (
                f"模型静态投资与设计预算差异 {fmt_pct(static_row.get('rate'))}"
                if static_row
                else "尚未完成计价，无法对比"
            ),
        },
        {
            "name": "计价已完成并复核",
            "ok": pricing_done,
            "detail": f"计价阶段状态：{stages_by_key.get('pricing', {}).get('status', '未开始')}",
        },
        {
            "name": "典型设计应用匹配已完成",
            "ok": td_ok,
            "detail": td_detail,
        },
    ]
    return conditions


@app.get("/project/{pid}/compare")
def compare_page(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    project = get_project(conn, pid, user)
    ctx = workflow_ctx(conn, pid)
    summary = pricing.pricing_summary(conn, pid)
    compare_rows = pricing.compare_budget(conn, pid)
    checks = engine.validate_model(conn, pid)
    # 对比页允许在缺少对比数据时由引擎按当前算量结果补算（仅当已有工程量）
    quantity_count = int(
        conn.execute("SELECT COUNT(*) FROM quantities WHERE project_id=?", (pid,)).fetchone()[0]
    )
    if quantity_count:
        ok, result = engine_call(engine.material_compare, conn, pid)
        materials = result if ok and isinstance(result, list) else compare_material_rows(conn, pid)
    else:
        materials = compare_material_rows(conn, pid)
    design_total = _num(project["design_total"])
    model_total = _num(summary.get("static_total"))
    static_row = next((dict(row) for row in compare_rows if str(row.get("item")) == "静态投资"), None)
    td_data = td_stats(conn, pid)
    conditions = submission_conditions(
        conn, pid, ctx["stages"], checks, materials, compare_rows, td=td_data
    )
    return page(
        request,
        "compare.html",
        user,
        active="compare",
        pid=pid,
        p=project,
        materials=materials,
        compare_rows=compare_rows,
        checks=checks,
        check_counts={
            "高": sum(1 for c in checks if str(c.get("level")) == "高"),
            "中": sum(1 for c in checks if str(c.get("level")) == "中"),
            "低": sum(1 for c in checks if str(c.get("level")) == "低"),
        },
        conditions=conditions,
        summary=summary,
        design_total=design_total,
        model_total=model_total,
        static_row=static_row,
        diff_rate=((model_total - design_total) / design_total * 100) if design_total else 0.0,
        td_stats=td_data,
        **ctx,
    )


# --------------------------------------------------------------------------- #
# 典型设计（典设）匹配（v2）
# --------------------------------------------------------------------------- #
TYPEDESIGN_UPLOAD_EXTS = {".xlsx", ".xlsm", ".csv"}
TYPEDESIGN_MAX_BYTES = 20 * 1024 * 1024
TYPEDESIGN_FILTERS = {
    "all": ("全部", None),
    "pending": ("待确认", "status:待确认"),
    "confirmed": ("已确认", "status:已确认"),
    "ignored": ("已忽略", "status:已忽略"),
    "unmatched": ("未匹配", "unmatched"),
}


def _is_unmatched(match: dict) -> bool:
    """未匹配行：新口径 status='未匹配'，兼容旧数据 match_type='未匹配'。"""
    status = str(match.get("status") or "")
    if status in ("已确认", "已忽略"):
        return False
    return status == "未匹配" or str(match.get("match_type") or "") == "未匹配"


def _typdesign_filtered(summary: dict, filter_key: str) -> list[dict]:
    """按状态/匹配方式筛选匹配明细。"""
    matches = list(summary.get("matches") or [])
    kind = TYPEDESIGN_FILTERS.get(filter_key, TYPEDESIGN_FILTERS["all"])[1]
    if not kind:
        return matches
    if kind == "unmatched":
        return [m for m in matches if _is_unmatched(m)]
    field, value = kind.split(":", 1)
    if field == "status":
        return [m for m in matches if str(m.get("status")) == value]
    return [m for m in matches if str(m.get("match_type")) == value]


def _typdesign_counts(summary: dict) -> dict:
    matches = list(summary.get("matches") or [])
    return {
        "all": len(matches),
        "pending": sum(1 for m in matches if str(m.get("status")) == "待确认"),
        "confirmed": sum(1 for m in matches if str(m.get("status")) == "已确认"),
        "ignored": sum(1 for m in matches if str(m.get("status")) == "已忽略"),
        "unmatched": sum(1 for m in matches if _is_unmatched(m)),
    }


@app.get("/project/{pid}/typdesign")
def typdesign_page(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """典型设计（典设）匹配页：统计 + 导入目录 + 执行匹配 + 明细确认 + 导出。"""
    user = require_user(request, conn)
    project = get_project(conn, pid, user)
    ctx = workflow_ctx(conn, pid)
    summary = td_summary(conn, pid)
    filter_key = str(request.query_params.get("filter") or "all")
    if filter_key not in TYPEDESIGN_FILTERS:
        filter_key = "all"
    return page(
        request,
        "typdesign.html",
        user,
        active="typdesign",
        pid=pid,
        p=project,
        summary=summary,
        matches=_typdesign_filtered(summary, filter_key),
        filter_key=filter_key,
        filter_counts=_typdesign_counts(summary),
        filters=[(key, label) for key, (label, _) in TYPEDESIGN_FILTERS.items()],
        editable=can_edit_role(user),
        td_ready=typdesign_ready(),
        drawings_stage=ctx["stage_map"].get("drawings", {}),
        **ctx,
    )


@app.post("/project/{pid}/typdesign/import")
async def typdesign_import(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """上传典设模块目录（.xlsx/.csv）→ 重建项目典设目录。"""
    user = require_user(request, conn)
    get_project(conn, pid, user)
    require_role(user, CAN_EDIT_ROLES, "导入典设目录")
    if not typdesign_ready():
        return redirect_msg(
            request, f"/project/{pid}/typdesign", "典设匹配引擎尚未就绪，请稍后重试", "error"
        )

    form = await request.form()
    uploads = [
        item
        for item in form.getlist("file")
        if hasattr(item, "filename") and hasattr(item, "read") and (item.filename or "").strip()
    ]
    if not uploads:
        return redirect_msg(
            request, f"/project/{pid}/typdesign", "请选择典设目录文件（.xlsx / .csv）再导入", "error"
        )
    upload = uploads[0]
    filename = Path(upload.filename or "").name
    ext = Path(filename).suffix.lower()
    if ext not in TYPEDESIGN_UPLOAD_EXTS:
        return redirect_msg(
            request, f"/project/{pid}/typdesign", "仅支持 .xlsx / .xlsm / .csv 格式的典设目录", "error"
        )
    content = await upload.read()
    if not content:
        return redirect_msg(request, f"/project/{pid}/typdesign", "文件为空，请重新选择", "error")
    if len(content) > TYPEDESIGN_MAX_BYTES:
        return redirect_msg(request, f"/project/{pid}/typdesign", "文件超过 20MB，请拆分后导入", "error")

    dest_dir = UPLOAD_DIR / str(pid) / "typdesign"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{datetime.now():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:6]}{ext}"
    try:
        dest.write_bytes(content)
    except OSError as exc:
        return redirect_msg(request, f"/project/{pid}/typdesign", f"文件保存失败：{exc}", "error")

    ok, result = engine_call(
        typdesign.import_catalog, conn, pid, str(dest), filename, as_user_dict(user)
    )
    if not ok:
        return redirect_msg(request, f"/project/{pid}/typdesign", f"导入失败：{result}", "error")
    data = result if isinstance(result, dict) else {}
    if data.get("ok") is False:
        return redirect_msg(
            request,
            f"/project/{pid}/typdesign",
            f"导入失败：{data.get('message') or '未识别到有效典设模块'}",
            "error",
        )
    modules = int(_num(data.get("modules")))
    msg = data.get("message") or f"已导入典设目录：{filename}，共 {modules} 个模块"
    return redirect_msg(request, f"/project/{pid}/typdesign", msg)


@app.post("/project/{pid}/typdesign/match")
async def typdesign_match(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """一键执行典设匹配（重写匹配明细，保留已确认/已忽略状态）。"""
    user = require_user(request, conn)
    get_project(conn, pid, user)
    require_role(user, CAN_EDIT_ROLES, "执行典设匹配")
    if not typdesign_ready():
        return redirect_msg(
            request, f"/project/{pid}/typdesign", "典设匹配引擎尚未就绪，请稍后重试", "error"
        )
    ok, result = engine_call(typdesign.match_project, conn, pid, as_user_dict(user))
    if not ok:
        return redirect_msg(request, f"/project/{pid}/typdesign", f"执行匹配失败：{result}", "error")
    data = result if isinstance(result, dict) else {}
    modules = int(_num(data.get("modules")))
    matched = int(_num(data.get("matched")))
    if modules == 0:
        return redirect_msg(
            request,
            f"/project/{pid}/typdesign",
            "尚未导入典设目录，无法执行匹配；请先上传/导入典设目录文件",
            "error",
        )
    msg = (
        f"已执行典型设计匹配：目录 {modules} 个模块，匹配 {matched} 个"
        f"（高 {int(_num(data.get('high')))} / 中 {int(_num(data.get('medium')))} / "
        f"低 {int(_num(data.get('low')))}），待确认 {int(_num(data.get('pending')))} 个"
    )
    return redirect_msg(request, f"/project/{pid}/typdesign", msg)


async def _typdesign_change(pid: int, mid: int, request: Request, conn, status: str):
    """确认应用 / 忽略 共用的状态变更处理。"""
    user = require_user(request, conn)
    get_project(conn, pid, user)
    action = "确认典设应用" if status == "已确认" else "忽略典设匹配"
    require_role(user, CAN_EDIT_ROLES, action)
    if not typdesign_ready():
        return redirect_msg(
            request, f"/project/{pid}/typdesign", "典设匹配引擎尚未就绪，请稍后重试", "error"
        )
    form = await request.form()
    qty_raw = str(form.get("qty") or "").strip()
    try:
        qty = float(qty_raw) if qty_raw else 0.0
    except ValueError:
        return redirect_msg(request, f"/project/{pid}/typdesign", "应用数量必须为数字", "error")
    note = str(form.get("note") or "").strip()
    ok, result = engine_call(
        typdesign.confirm_match, conn, pid, mid, as_user_dict(user), qty, note, status
    )
    if not ok:
        return redirect_msg(request, f"/project/{pid}/typdesign", f"{action}失败：{result}", "error")
    ok2, msg = _unpack_engine_result(result, f"{action}成功")
    return redirect_msg(
        request,
        f"/project/{pid}/typdesign",
        msg if ok2 else f"{action}失败：{msg}",
        "ok" if ok2 else "error",
    )


@app.api_route("/project/{pid}/typdesign/{mid}/confirm", methods=["GET", "POST"])
async def typdesign_confirm(pid: int, mid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """确认应用（POST）；GET 访问不存在的确认资源返回 404，避免误触写操作。"""
    if request.method != "POST":
        require_user(request, conn)
        raise HTTPException(status_code=404, detail="确认操作需在典设匹配页通过“确认应用”按钮提交。")
    return await _typdesign_change(pid, mid, request, conn, "已确认")


@app.api_route("/project/{pid}/typdesign/{mid}/ignore", methods=["GET", "POST"])
async def typdesign_ignore(pid: int, mid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """忽略匹配（POST）；GET 访问返回 404。"""
    if request.method != "POST":
        require_user(request, conn)
        raise HTTPException(status_code=404, detail="忽略操作需在典设匹配页通过“忽略”按钮提交。")
    return await _typdesign_change(pid, mid, request, conn, "已忽略")


@app.get("/project/{pid}/typdesign/export.xlsx")
def typdesign_export(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    """导出典型设计匹配清单（Excel，单 sheet）。"""
    user = require_user(request, conn)
    project = get_project(conn, pid, user)
    if not typdesign_ready():
        return redirect_msg(
            request, f"/project/{pid}/typdesign", "典设匹配引擎尚未就绪，无法导出", "error"
        )
    summary = td_summary(conn, pid)
    if int(_num(summary.get("modules"))) == 0:
        return redirect_msg(
            request, f"/project/{pid}/typdesign", "尚未导入典设目录，暂无可导出的匹配清单", "error"
        )
    workbook = build_typdesign_workbook(conn, project)
    return _xlsx_response(workbook, _export_filename("典型设计匹配清单", project))


# --------------------------------------------------------------------------- #
# 演示路线 / 健康检查
# --------------------------------------------------------------------------- #
@app.get("/demo")
def demo_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    project = conn.execute("SELECT * FROM projects ORDER BY id LIMIT 1").fetchone()
    demo_project = conn.execute("SELECT * FROM projects WHERE code='DEMO-C'").fetchone() or project
    finished = conn.execute("SELECT * FROM projects WHERE code='DEMO-A'").fetchone() or project
    pending = conn.execute("SELECT * FROM projects WHERE code='DEMO-B'").fetchone() or project
    stages = get_stages(conn, int(demo_project["id"])) if demo_project else []
    demo_td = {
        "c": td_stats(conn, int(demo_project["id"])) if demo_project else {},
        "a": td_stats(conn, int(finished["id"])) if finished else {},
        "b": td_stats(conn, int(pending["id"])) if pending else {},
    }
    return page(
        request,
        "demo.html",
        user,
        active="demo",
        demo_project=demo_project,
        finished_project=finished,
        pending_project=pending,
        demo_stages={str(s["key"]): s for s in stages},
        demo_td=demo_td,
        demo_accounts=[("admin", "admin123", "管理员"), ("zaojia", "123456", "造价员"), ("fuhe", "123456", "复核员")],
    )


@app.get("/health")
def health(conn: sqlite3.Connection = Depends(get_db)):
    count = int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
    return JSONResponse({"ok": True, "projects": count})


# --------------------------------------------------------------------------- #
# 错误页
# --------------------------------------------------------------------------- #
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 303:
        return RedirectResponse(exc.headers.get("Location", "/login") if exc.headers else "/login", status_code=303)
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": exc.status_code,
            "detail": exc.detail or "页面不存在或没有访问权限",
            "user": None,
            "demo": DEMO,
        },
        status_code=exc.status_code,
    )


# --------------------------------------------------------------------------- #
# SVG 平面示意图（服务端拼接字符串）
# --------------------------------------------------------------------------- #
def build_model_svg(project, lines: list, equipment: list) -> str:
    """由模型数据生成配电网平面示意图（10kV 主线 + 台架变 + 低压分支 + 杆塔）。"""
    width, height = 1180, 330
    font = "Microsoft YaHei, PingFang SC, Segoe UI, sans-serif"
    esc = html.escape
    out = [
        f'<svg class="model-svg" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="配电网模型平面示意图" font-family="{font}">',
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="10" fill="#fbfdff" stroke="#e2e8f0"/>',
        '<text x="20" y="26" font-size="13" font-weight="700" fill="#334155">'
        f'{esc(str(project["name"]))} · 模型平面示意图（示意，非施工图）</text>',
        '<line x1="20" y1="292" x2="1160" y2="292" stroke="#cbd5e1" stroke-dasharray="6 4"/>',
        '<text x="1120" y="310" font-size="10" fill="#94a3b8">地面线（示意）</text>',
    ]
    ten_kv = [line for line in lines if "10" in str(line["voltage"] or "")]
    low_kv = [line for line in lines if line not in ten_kv]
    if not lines:
        out += [
            f'<text x="{width / 2}" y="150" text-anchor="middle" font-size="16" fill="#94a3b8">暂无模型数据</text>',
            f'<text x="{width / 2}" y="178" text-anchor="middle" font-size="12" fill="#a8b3c2">'
            "请先在图纸解析页上传图纸，或在建模页点击“由图纸生成模型初稿”</text>",
            "</svg>",
        ]
        return "".join(out)

    transformer = next((row for row in equipment if "变压器" in str(row["name"] or "")), None)
    transformer_label = "台架变"
    if transformer:
        found = re.search(r"(\d+)\s*kVA", f"{transformer['name']} {transformer['spec']}", re.IGNORECASE)
        transformer_label = f"{found.group(1)}kVA" if found else str(transformer["spec"] or "台架变")

    # 10kV 主线
    y10, x_start, x_end = 108, 130, 690
    if ten_kv:
        line = ten_kv[0]
        poles = max(int(_num(line["poles"])), 2)
        shown = min(poles, 10)
        xs = [x_start + (x_end - x_start) * i / (shown - 1) for i in range(shown)]
        out.append(
            f'<line x1="{x_start}" y1="{y10}" x2="{x_end}" y2="{y10}" stroke="#2563eb" stroke-width="3"/>'
        )
        for index, x in enumerate(xs):
            out.append(
                f'<line x1="{x:.1f}" y1="{y10 - 13}" x2="{x:.1f}" y2="{y10 + 13}" '
                'stroke="#1e40af" stroke-width="2"/>'
            )
            if shown <= 8 or index % 2 == 0:
                out.append(
                    f'<text x="{x:.1f}" y="{y10 + 30}" text-anchor="middle" font-size="10" fill="#475569">'
                    f"{index + 1}#</text>"
                )
        out += [
            f'<text x="{(x_start + x_end) / 2}" y="{y10 - 42}" text-anchor="middle" font-size="13" '
            f'font-weight="700" fill="#1e40af">{esc(str(line["name"] or "10kV线路"))} · 亘长 '
            f"{fmt_num(line['length_km'])}km · {esc(str(line['conductor'] or '—'))}</text>",
            f'<text x="{(x_start + x_end) / 2}" y="{y10 - 24}" text-anchor="middle" font-size="11" fill="#64748b">'
            f"{esc(str(line['start_point'] or ''))} → {esc(str(line['end_point'] or ''))} · 杆塔 {poles} 基 · "
            f"档距 {fmt_num(line['span_m'])}m · {esc(str(line['pole_type'] or ''))}</text>",
        ]
        # 台架变
        box_x, box_y, box_w, box_h = x_end + 36, y10 - 46, 74, 92
        out += [
            f'<line x1="{x_end}" y1="{y10}" x2="{box_x}" y2="{y10}" stroke="#2563eb" stroke-width="3"/>',
            f'<rect x="{box_x}" y="{box_y}" width="{box_w}" height="{box_h}" rx="8" fill="#eff6ff" '
            'stroke="#2563eb" stroke-width="2"/>',
            f'<text x="{box_x + box_w / 2}" y="{box_y + 34}" text-anchor="middle" font-size="12" '
            f'font-weight="700" fill="#1e40af">台架变</text>',
            f'<text x="{box_x + box_w / 2}" y="{box_y + 54}" text-anchor="middle" font-size="11" '
            f'fill="#1e40af">{esc(transformer_label)}</text>',
            f'<text x="{box_x + box_w / 2}" y="{box_y + 74}" text-anchor="middle" font-size="10" '
            'fill="#64748b">配变装置</text>',
        ]
        trunk_x = box_x + box_w / 2
    else:
        trunk_x = 420
        out.append(
            f'<text x="{trunk_x}" y="{y10}" text-anchor="middle" font-size="12" fill="#94a3b8">'
            "无 10kV 线路数据</text>"
        )

    # 低压分支
    y_low, x_low_end = 232, 1090
    if low_kv:
        line = low_kv[0]
        poles = max(int(_num(line["poles"])), 2)
        shown = min(poles, 12)
        out += [
            f'<polyline points="{trunk_x:.0f},182 {trunk_x:.0f},{y_low} {x_low_end},{y_low}" fill="none" '
            'stroke="#f59e0b" stroke-width="2.5"/>',
        ]
        low_xs = [trunk_x + (x_low_end - trunk_x) * i / (shown - 1) for i in range(shown)]
        for index, x in enumerate(low_xs):
            out.append(
                f'<line x1="{x:.1f}" y1="{y_low - 11}" x2="{x:.1f}" y2="{y_low + 11}" '
                'stroke="#b45309" stroke-width="2"/>'
            )
            if shown <= 8 or index % 2 == 0:
                out.append(
                    f'<text x="{x:.1f}" y="{y_low + 26}" text-anchor="middle" font-size="10" fill="#92400e">'
                    f"L{index + 1}#</text>"
                )
        out += [
            f'<text x="{(trunk_x + x_low_end) / 2}" y="{y_low + 48}" text-anchor="middle" font-size="12" '
            f'font-weight="700" fill="#b45309">{esc(str(line["name"] or "低压线路"))} · 亘长 '
            f"{fmt_num(line['length_km'])}km · {esc(str(line['conductor'] or '—'))} · {poles} 基</text>",
            f'<text x="{trunk_x}" y="166" text-anchor="middle" font-size="10" fill="#64748b">低压侧出线</text>',
        ]

    # 图例
    legend_y = 44
    legend = [
        ("#2563eb", "10kV 架空线路"),
        ("#f59e0b", "低压线路"),
        ("#1e40af", "杆塔 / 杆位"),
        ("#eff6ff", "台架式变压器"),
    ]
    for index, (color, label) in enumerate(legend):
        lx = 690 + index * 122
        fill = color if color.startswith("#2563") or color.startswith("#f59") else "none"
        out.append(
            f'<rect x="{lx}" y="{legend_y - 10}" width="16" height="10" rx="2" fill="{fill}" '
            f'stroke="{color}" stroke-width="2"/>'
        )
        out.append(
            f'<text x="{lx + 22}" y="{legend_y}" font-size="10" fill="#64748b">{esc(label)}</text>'
        )
    out.append("</svg>")
    return "".join(out)


# --------------------------------------------------------------------------- #
# Excel 导出（openpyxl）
# --------------------------------------------------------------------------- #
HEADER_FILL = PatternFill("solid", fgColor="DCE8FB")
GROUP_FILL = PatternFill("solid", fgColor="F1F5F9")
TITLE_FONT = Font(bold=True, size=16, color="1E3A8A")
HEADER_FONT = Font(bold=True, color="1F2937")
_THIN = Side(style="thin", color="CBD5E1")
BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _write_sheet(ws, title: str, subtitle: str, headers: list[str], rows: list[list], widths: list[int],
                 number_formats: dict[int, str] | None = None) -> None:
    """统一样式：标题行、说明行、表头（填充/加粗/冻结/筛选）、数据行、列宽。"""
    ncols = len(headers)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    cell = ws.cell(row=1, column=1, value=title)
    cell.font = TITLE_FONT
    cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
    sub = ws.cell(row=2, column=1, value=subtitle)
    sub.font = Font(size=10, color="6B7280")
    sub.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 18

    header_row = 3
    for index, header in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=index, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[header_row].height = 26

    for row_index, row in enumerate(rows, start=header_row + 1):
        for col_index, value in enumerate(row, start=1):
            cell = ws.cell(row=row_index, column=col_index, value=value)
            cell.border = BORDER
            cell.alignment = Alignment(vertical="center", wrap_text=col_index == len(headers))
            fmt = (number_formats or {}).get(col_index)
            if fmt:
                cell.number_format = fmt

    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    if rows:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(ncols)}{header_row + len(rows)}"


def _xlsx_response(workbook: Workbook, filename: str) -> StreamingResponse:
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    quoted = quote(filename)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
        "Cache-Control": "no-store",
    }
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )


def _export_filename(prefix: str, project) -> str:
    short = str(project["name"] or project["code"] or "项目").split("·")[0].strip() or str(project["code"])
    return f"{prefix}-{short}.xlsx"


def build_quantities_workbook(conn: sqlite3.Connection, project) -> Workbook:
    """工程量清单导出：清单页 + 编制说明页。"""
    pid = int(project["id"])
    rows = conn.execute(
        "SELECT * FROM quantities WHERE project_id=? ORDER BY seq,id", (pid,)
    ).fetchall()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "工程量清单"
    data = [
        [
            row["seq"] if row["seq"] is not None else index,
            row["unit_name"] or "",
            row["name"] or "",
            row["spec"] or "",
            row["unit"] or "",
            _num(row["qty"]),
            row["formula"] or "",
            row["basis"] or "",
            row["category"] or "",
            row["quota_code"] or "",
            row["status"] or "",
            row["note"] or "",
        ]
        for index, row in enumerate(rows, start=1)
    ]
    _write_sheet(
        sheet,
        f"工程量清单 · {project['name']}",
        f"项目编码：{project['code']}    工程类型：{project['ptype'] or '—'}    "
        f"导出时间：{now_text()}    （系统按建模数据自动计算，示例数据仅供演示）",
        ["序号", "单位工程", "项目名称", "规格型号", "单位", "数量", "计算式", "计算依据", "类别", "定额编号", "状态", "备注"],
        data,
        [6, 18, 26, 20, 8, 11, 24, 30, 8, 12, 9, 18],
        {6: "#,##0.0000"},
    )
    rules_sheet = workbook.create_sheet("编制说明")
    rule_rows = [[index, title, text] for index, (title, text) in enumerate(QUANTITY_RULES, start=1)]
    _write_sheet(
        rules_sheet,
        "工程量计算规则与依据",
        "依据：2022 版《20kV及以下配电网工程预算定额》及本原型算量规则（演示口径）",
        ["序号", "规则", "说明"],
        rule_rows,
        [6, 18, 90],
    )
    return workbook


def build_pricing_workbook(conn: sqlite3.Connection, project) -> Workbook:
    """施工图预算书导出：费用汇总 / 安装工程预算表 / 主要材料表 / 其他费用明细 / 预算对比。"""
    pid = int(project["id"])
    exporter = getattr(pricing, "build_export_rows", None)
    export_rows: dict = {}
    if callable(exporter):
        try:
            export_rows = exporter(conn, pid) or {}
        except Exception:
            export_rows = {}
    summary = export_rows or pricing.pricing_summary(conn, pid)
    totals = dict(summary.get("totals") or {}) or {
        key: _num(summary.get(key))
        for key in ("install", "build", "demolition", "equipment", "other", "reserve", "static_total")
    }
    fees = summary.get("summary") or summary.get("fees") or []
    items = summary.get("items") or summary.get("pricing_items") or []
    materials = summary.get("materials") or []
    other_fees = summary.get("other_fees") or [row for row in fees if row.get("category") == "其他费用"]
    compare = summary.get("compare") or pricing.compare_budget(conn, pid)
    design = summary.get("design") or {
        "equipment": _num(project["design_equipment"]),
        "install": _num(project["design_install"]),
        "other": _num(project["design_other"]),
        "reserve": _num(project["design_reserve"]),
        "total": _num(project["design_total"]),
    }
    subtitle = (
        f"项目编码：{project['code']}    编制依据：2022 版《20kV及以下配电网工程建设预算编制与计算规定》"
        f"（江西Ⅰ类地区演示费率）    导出时间：{now_text()}"
    )

    workbook = Workbook()
    # 1. 费用汇总表
    sheet = workbook.active
    sheet.title = "费用汇总表"
    fee_rows = [
        [
            index,
            row.get("category") or "",
            row.get("item") or "",
            row.get("base_desc") or "",
            row.get("formula") or "",
            _num(row.get("rate")),
            _num(row.get("amount")),
        ]
        for index, row in enumerate(fees, start=1)
    ]
    _write_sheet(
        sheet,
        f"施工图预算书 · {project['name']}",
        subtitle,
        ["序号", "费用构成", "费用项目", "计算基数说明", "计算式", "费率(%)", "金额（元）"],
        fee_rows,
        [6, 16, 32, 34, 34, 10, 16],
        {6: "0.0000", 7: "#,##0.00"},
    )
    total_row = sheet.max_row + 1
    sheet.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=6)
    cell = sheet.cell(row=total_row, column=1, value="静态投资合计（元）")
    cell.font = Font(bold=True, size=12)
    cell.alignment = Alignment(horizontal="right", vertical="center")
    cell.fill = GROUP_FILL
    cell.border = BORDER
    amount_cell = sheet.cell(row=total_row, column=7, value=_num(totals.get("static_total")))
    amount_cell.font = Font(bold=True, size=12, color="1D4ED8")
    amount_cell.number_format = "#,##0.00"
    amount_cell.border = BORDER

    # 2. 安装工程预算表
    item_sheet = workbook.create_sheet("安装工程预算表")
    item_rows = [
        [
            item.get("seq") or index,
            item.get("unit_name") or "",
            item.get("category") or "",
            item.get("quota_code") or "",
            item.get("quota_name") or "",
            item.get("name") or "",
            item.get("spec") or "",
            item.get("unit") or "",
            _num(item.get("qty")),
            _num(item.get("labor")),
            _num(item.get("material")),
            _num(item.get("machine")),
            _num(item.get("main_material")),
            _num(item.get("direct_cost")),
        ]
        for index, item in enumerate(items, start=1)
    ]
    _write_sheet(
        item_sheet,
        "安装工程预算表（含建筑 / 拆除工程）",
        "金额单位：元；人工费、材料费、机械费按示例定额库单价 × 工程量计取",
        ["序号", "单位工程", "类别", "定额编号", "定额名称", "项目名称", "规格型号", "单位", "数量",
         "人工费", "材料费", "机械费", "主材费", "直接工程费"],
        item_rows,
        [6, 16, 8, 11, 30, 26, 18, 8, 10, 12, 12, 12, 13, 14],
        {9: "#,##0.0000", **{column: "#,##0.00" for column in range(10, 15)}},
    )

    # 3. 主要材料表
    material_sheet = workbook.create_sheet("主要材料表")
    material_rows = [
        [
            index,
            row.get("unit_name") or "",
            row.get("quota_code") or "",
            row.get("name") or "",
            row.get("spec") or "",
            row.get("unit") or "",
            _num(row.get("qty")),
            _num(row.get("unit_price")),
            _num(row.get("amount")),
            row.get("supply") or "",
        ]
        for index, row in enumerate(materials, start=1)
    ]
    _write_sheet(
        material_sheet,
        "主要材料（设备）用量与价差表",
        "主材价按示例材料价格库（可在建模页设备/主材表调整），甲供/乙供标注来自模型",
        ["序号", "单位工程", "定额编号", "材料名称", "规格型号", "单位", "数量", "单价（元）", "金额（元）", "供应方式"],
        material_rows,
        [6, 16, 11, 30, 22, 8, 12, 13, 14, 10],
        {7: "#,##0.0000", 8: "#,##0.00", 9: "#,##0.00"},
    )

    # 4. 其他费用明细
    other_sheet = workbook.create_sheet("其他费用明细")
    other_rows = [
        [
            index,
            row.get("item") or "",
            row.get("base_desc") or "",
            row.get("formula") or "",
            _num(row.get("rate")),
            _num(row.get("amount")),
        ]
        for index, row in enumerate(other_fees, start=1)
    ]
    reserve_row = [
        len(other_rows) + 1,
        "基本预备费",
        "（建筑安装工程费 + 设备购置费 + 其他费用）× 3%",
        "按规定费率计取",
        3.0,
        _num(totals.get("reserve")),
    ]
    other_rows.append(reserve_row)
    _write_sheet(
        other_sheet,
        "其他费用明细表",
        "其他费用按 2022 版编制规定逐项计取；费率为演示取值，正式项目以造价管理机构发布文件为准",
        ["序号", "费用项目", "计算基数", "计算说明", "费率(%)", "金额（元）"],
        other_rows,
        [6, 34, 38, 30, 10, 16],
        {5: "0.0000", 6: "#,##0.00"},
    )

    # 5. 设计预算对比
    compare_sheet = workbook.create_sheet("预算对比")
    compare_rows = [
        [
            index,
            row.get("item") or "",
            _num(row.get("design_amount")),
            _num(row.get("model_amount")),
            _num(row.get("diff")),
            _num(row.get("rate")),
            row.get("note") or "",
        ]
        for index, row in enumerate(compare, start=1)
    ]
    _write_sheet(
        compare_sheet,
        "设计预算（附件5）与模型预算对比",
        f"设计静态投资 {_num(design.get('total')):,.2f} 元；模型静态投资 {_num(totals.get('static_total')):,.2f} 元",
        ["序号", "费用构成", "设计预算（元）", "模型预算（元）", "差异（元）", "差异率(%)", "说明"],
        compare_rows,
        [6, 24, 18, 18, 16, 11, 40],
        {3: "#,##0.00", 4: "#,##0.00", 5: "#,##0.00", 6: "0.00"},
    )
    return workbook


TYPEDESIGN_EXPORT_WIDTHS = {
    "序号": 6, "卷册": 20, "类别": 12, "模块编号": 32, "模块名称": 34, "模块单位": 9,
    "匹配方式": 11, "置信度": 9, "证据摘要": 52, "状态": 9, "应用数量": 10, "备注": 22,
}


def build_typdesign_workbook(conn: sqlite3.Connection, project) -> Workbook:
    """典型设计匹配清单导出（单 sheet“典型设计匹配清单”）。"""
    rows = typdesign.export_rows(conn, int(project["id"])) if typdesign_ready() else []
    header = (
        list(rows[0])
        if rows
        else ["序号", "卷册", "类别", "模块编号", "模块名称", "模块单位",
              "匹配方式", "置信度", "证据摘要", "状态", "应用数量", "备注"]
    )
    data = [list(row) for row in rows[1:]] if len(rows) > 1 else []
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "典型设计匹配清单"
    subtitle = (
        f"项目编码：{project['code']}    工程类型：{project['ptype'] or '—'}    "
        f"导出时间：{now_text()}    说明：演示用典设目录节选（40 条，非完整 V3.1 库），"
        "可作为附件6-1《标准设计应用情况统计表》的图纸依据底稿"
    )
    widths = [TYPEDESIGN_EXPORT_WIDTHS.get(str(item), 14) for item in header]
    number_formats: dict[int, str] = {}
    for index, item in enumerate(header, start=1):
        if str(item) == "应用数量":
            number_formats[index] = "#,##0.####"
    _write_sheet(
        sheet,
        f"典型设计匹配清单 · {project['name']}",
        subtitle,
        [str(item) for item in header],
        data,
        widths,
        number_formats,
    )
    return workbook


@app.get("/project/{pid}/quantities/export.xlsx")
def export_quantities(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    project = get_project(conn, pid, user)
    workbook = build_quantities_workbook(conn, project)
    return _xlsx_response(workbook, _export_filename("工程量清单", project))


@app.get("/project/{pid}/pricing/export.xlsx")
def export_pricing(pid: int, request: Request, conn: sqlite3.Connection = Depends(get_db)):
    user = require_user(request, conn)
    project = get_project(conn, pid, user)
    summary = pricing.pricing_summary(conn, pid)
    if _num(summary.get("static_total")) <= 0:
        return redirect_msg(
            request, f"/project/{pid}/pricing", "尚未完成计价，暂无预算书可导出", "error"
        )
    workbook = build_pricing_workbook(conn, project)
    return _xlsx_response(workbook, _export_filename("施工图预算", project))
