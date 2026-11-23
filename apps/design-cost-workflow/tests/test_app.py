"""Web 层测试：FastAPI TestClient + 临时数据目录（不写仓库 data/）。

运行（在 apps/design-cost-workflow 目录下）::

    python -m pytest -q tests/test_app.py
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote

# 必须在导入 app 之前设置环境变量（app.py 在导入时初始化数据库）
_TEST_DATA_DIR = tempfile.mkdtemp(prefix="design-cost-test-")
os.environ["WORKFLOW_DATA_DIR"] = _TEST_DATA_DIR
os.environ["WORKFLOW_DEMO"] = "1"
os.environ["WORKFLOW_SECRET"] = "test-session-secret"

# 允许从 app 目录或仓库任意位置运行
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import Workbook, load_workbook  # noqa: E402

from app import app  # noqa: E402

client = TestClient(app)


def login(username: str = "admin", password: str = "admin123") -> TestClient:
    """登录并返回同一 client（cookie 持久化在 TestClient 中）。"""
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return client


def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(os.path.join(_TEST_DATA_DIR, "workflow.db"))
    conn.row_factory = sqlite3.Row
    return conn


def project_count() -> int:
    """当前项目总数（用于校验非法创建不写库）。"""
    conn = db_conn()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
    finally:
        conn.close()


def drawing_count(pid: int) -> int:
    """某个项目的图纸数量。"""
    conn = db_conn()
    try:
        return int(
            conn.execute("SELECT COUNT(*) FROM drawings WHERE project_id=?", (pid,)).fetchone()[0]
        )
    finally:
        conn.close()


def create_project_via_http(name: str, code: str = "", ptype: str = "业扩配套") -> int:
    """以当前登录身份创建项目，返回项目 id（要求调用方已登录造价员/管理员）。"""
    response = client.post(
        "/project/new",
        data={"name": name, "code": code, "ptype": ptype, "location": "某供电所"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    match = re.match(r"^/project/(\d+)/drawings", response.headers["location"])
    assert match, response.headers["location"]
    return int(match.group(1))


def xlsx_bytes() -> bytes:
    """构造一个小的材料清册 XLSX，用于上传解析测试。"""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "主要设备材料清册"
    sheet.append(["序号", "材料名称", "规格型号", "单位", "数量"])
    sheet.append([1, "10kV架空绝缘导线", "JKLGYJ-70/10", "千米", 0.238])
    sheet.append([2, "水泥杆", "Φ150×8m", "根", 4])
    detail = workbook.create_sheet("杆塔明细表")
    detail.append(["杆号", "杆型", "档距(m)"])
    detail.append(["1#", "Φ150×8m", 38.5])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 基础可用性
# ---------------------------------------------------------------------------
def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["projects"] == 3


def test_login_page_and_unauthenticated_redirect():
    anonymous = TestClient(app)
    response = anonymous.get("/login")
    assert response.status_code == 200
    assert "演示账号" in response.text
    assert "造价员" in response.text

    response = anonymous.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert "/login" in response.headers["location"]

    response = anonymous.get("/project/1", follow_redirects=False)
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_dashboard_after_login():
    login("admin", "admin123")
    response = client.get("/")
    assert response.status_code == 200
    assert "项目A" in response.text
    assert "工作台" in response.text
    assert "待办事项" in response.text
    assert "风险提醒" in response.text


def test_demo_page():
    login()
    response = client.get("/demo")
    assert response.status_code == 200
    assert "5 分钟演示路线" in response.text
    assert "图纸解析" in response.text
    assert "典型设计匹配（v2 新增）" in response.text
    assert "计价" in response.text


# ---------------------------------------------------------------------------
# 项目各阶段页面
# ---------------------------------------------------------------------------
def test_project_pages_render_chinese_content():
    login("admin", "admin123")
    pages = {
        "/project/1": ["四阶段工作流", "设计预算", "审计动态"],
        "/project/1/drawings": ["图纸解析", "图纸目录", "识别结果概览"],
        "/project/1/model": ["模型平面示意图", "线路与杆塔", "模型校验结果"],
        "/project/1/quantities": ["工程量清单", "材料量对比", "计算规则"],
        "/project/1/pricing": ["费用汇总表", "定额套用表", "设计预算（附件5）对比"],
        "/project/1/compare": ["对比分析", "送审条件检查", "材料量对比"],
    }
    for path, keywords in pages.items():
        response = client.get(path)
        assert response.status_code == 200, path
        for keyword in keywords:
            assert keyword in response.text, f"{path} 缺少关键词 {keyword}"
    # 建模页服务器端生成 SVG 示意图
    model_page = client.get("/project/1/model")
    assert "<svg" in model_page.text
    assert "台架变" in model_page.text
    # 项目A 已完成计价：预算对比表应有 5 行
    project_page = client.get("/project/1")
    assert "设备购置费" in project_page.text
    assert "静态投资" in project_page.text


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------
def test_role_permissions():
    login("fuhe", "123456")
    # 复核员不能提交阶段
    response = client.post(
        "/project/3/stage/drawings/submit", follow_redirects=False
    )
    assert response.status_code == 403
    assert "没有访问权限" in response.text

    client.get("/logout")
    login("zaojia", "123456")
    # 造价员不能复核阶段
    response = client.post(
        "/project/3/stage/drawings/review",
        data={"result": "通过", "comment": "越权测试"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert "没有访问权限" in response.text

    # 阶段状态不允许时编辑模型返回 403（项目A 建模已完成）
    response = client.post(
        "/project/1/model/line/add",
        data={"name": "越权新增线路", "voltage": "10kV"},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert "没有访问权限" in response.text
    client.get("/logout")


def test_upload_drawing_and_parse():
    login("zaojia", "123456")
    data = xlsx_bytes()
    response = client.post(
        "/project/3/drawings/upload?as=zaojia",
        files=[("files", ("某项目材料清册.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        data={"category": "自动识别"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    page = client.get("/project/3/drawings?as=zaojia")
    assert page.status_code == 200
    assert "某项目材料清册" in page.text
    assert "已识别" in page.text
    assert "工作表2个" in page.text

    # 空上传给出错误提示而不是 500
    response = client.post(
        "/project/3/drawings/upload?as=zaojia", data={"category": "自动识别"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert "请选择" in unquote(response.headers["location"])
    client.get("/logout")


# ---------------------------------------------------------------------------
# 全链路：项目C 图纸 → 建模 → 算量 → 计价 → 对比
# ---------------------------------------------------------------------------
def test_full_workflow_project_c():
    login("zaojia", "123456")

    # 1) 图纸解析提交复核 → 复核员通过 → 建模进行中
    response = client.post(
        "/project/3/stage/drawings/submit?as=zaojia", follow_redirects=False
    )
    assert response.status_code == 303
    project_page = client.get("/project/3?as=zaojia")
    assert "待复核" in project_page.text

    response = client.post(
        "/project/3/stage/drawings/review?as=fuhe",
        data={"result": "通过", "comment": "图纸识别结果与清册一致，同意进入建模"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    project_page = client.get("/project/3?as=fuhe")
    assert "已完成" in project_page.text

    # 2) 建模：生成初稿 → 校验 → 增删改 → 提交复核 → 通过
    response = client.post("/project/3/model/autofill?as=zaojia", follow_redirects=False)
    assert response.status_code == 303

    response = client.post("/project/3/model/validate?as=zaojia", follow_redirects=False)
    assert response.status_code == 303

    response = client.post(
        "/project/3/model/line/add?as=zaojia",
        data={
            "name": "测试联络线路",
            "voltage": "10kV",
            "start_point": "1#杆",
            "end_point": "2#杆",
            "length_km": "0.12",
            "circuits": "1",
            "phases": "3",
            "conductor": "JKLGYJ-70/10",
            "poles": "2",
            "pole_type": "Φ150×8m",
            "span_m": "60",
            "stays": "0",
            "soil": "普通土",
            "source": "测试",
            "note": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    model_page = client.get("/project/3/model?as=zaojia")
    assert "测试联络线路" in model_page.text

    conn = db_conn()
    line_id = conn.execute(
        "SELECT id FROM model_lines WHERE project_id=3 AND name='测试联络线路'"
    ).fetchone()["id"]
    response = client.post(
        f"/project/3/model/line/{line_id}/update?as=zaojia",
        data={
            "name": "测试联络线路（改）",
            "voltage": "10kV",
            "start_point": "1#杆",
            "end_point": "2#杆",
            "length_km": "0.15",
            "circuits": "1",
            "phases": "3",
            "conductor": "JKLGYJ-70/10",
            "poles": "3",
            "pole_type": "Φ150×8m",
            "span_m": "50",
            "stays": "0",
            "soil": "普通土",
            "source": "测试",
            "note": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "测试联络线路（改）" in client.get("/project/3/model?as=zaojia").text

    response = client.post(
        f"/project/3/model/line/{line_id}/delete?as=zaojia", follow_redirects=False
    )
    assert response.status_code == 303
    assert "测试联络线路（改）" not in client.get("/project/3/model?as=zaojia").text

    response = client.post(
        "/project/3/stage/model/submit?as=zaojia", follow_redirects=False
    )
    assert response.status_code == 303
    assert "待复核" in client.get("/project/3?as=zaojia").text

    response = client.post(
        "/project/3/stage/model/review?as=fuhe",
        data={"result": "通过", "comment": "模型校验无高级问题"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "进行中" in client.get("/project/3?as=fuhe").text

    # 3) 算量：重算 → 清单与对比 → 导出 → 提交复核 → 通过
    response = client.post("/project/3/quantities/compute?as=zaojia", follow_redirects=False)
    assert response.status_code == 303
    quantities_page = client.get("/project/3/quantities?as=zaojia")
    assert quantities_page.status_code == 200
    assert "10kV架空线路架设" in quantities_page.text
    assert "计算式" in quantities_page.text

    export = client.get("/project/3/quantities/export.xlsx?as=zaojia")
    assert export.status_code == 200
    assert "spreadsheetml" in export.headers["content-type"]
    assert "filename*=UTF-8" in export.headers["content-disposition"]
    workbook = load_workbook(BytesIO(export.content))
    assert "工程量清单" in workbook.sheetnames
    assert "编制说明" in workbook.sheetnames

    response = client.post(
        "/project/3/stage/quantities/submit?as=zaojia", follow_redirects=False
    )
    assert response.status_code == 303
    response = client.post(
        "/project/3/stage/quantities/review?as=fuhe",
        data={"result": "通过", "comment": "工程量与清册对比无异常"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    # 4) 计价：套价 → 费用汇总 → 更换定额 → 导出 → 提交复核
    response = client.post("/project/3/pricing/compute?as=zaojia", follow_redirects=False)
    assert response.status_code == 303
    pricing_page = client.get("/project/3/pricing?as=zaojia")
    assert pricing_page.status_code == 200
    assert "费用汇总表" in pricing_page.text
    assert "定额套用表" in pricing_page.text

    conn = db_conn()
    item = conn.execute(
        "SELECT id, quota_code FROM pricing_items WHERE project_id=3 ORDER BY id LIMIT 1"
    ).fetchone()
    assert item is not None
    new_code = "YD3-0302" if item["quota_code"] != "YD3-0302" else "YD3-0301"
    response = client.post(
        f"/project/3/pricing/item/{item['id']}/quota?as=zaojia",
        data={"quota_code": new_code},
        follow_redirects=False,
    )
    assert response.status_code == 303
    changed_page = client.get("/project/3/pricing?as=zaojia")
    assert changed_page.status_code == 200
    assert new_code in changed_page.text

    export = client.get("/project/3/pricing/export.xlsx?as=zaojia")
    assert export.status_code == 200
    assert "spreadsheetml" in export.headers["content-type"]
    workbook = load_workbook(BytesIO(export.content))
    for sheet_name in ("费用汇总表", "安装工程预算表", "主要材料表", "其他费用明细"):
        assert sheet_name in workbook.sheetnames

    response = client.post(
        "/project/3/stage/pricing/submit?as=zaojia", follow_redirects=False
    )
    assert response.status_code == 303
    response = client.post(
        "/project/3/stage/pricing/review?as=fuhe",
        data={"result": "通过", "comment": "计价复核通过，可送审"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    project_page = client.get("/project/3?as=fuhe")
    assert "计价定稿" in project_page.text

    # 5) 对比页面：材料量对比 + 预算对比 + 送审条件
    compare_page = client.get("/project/3/compare?as=fuhe")
    assert compare_page.status_code == 200
    assert "材料量对比" in compare_page.text
    assert "预算对比" in compare_page.text
    assert "送审条件检查" in compare_page.text
    # v2：新增“典型设计应用匹配已完成”辅助条件；项目C 演示数据为“仅导入目录未匹配”，
    # 因此该条件不满足（其余条件仍应满足），与 contract2 §5 一致
    assert "典型设计应用匹配已完成" in compare_page.text
    # v6 起上传图纸会自动执行典设匹配，项目C 可能出现待确认项，两种文案都成立
    assert ("已确认 0/40 个模块" in compare_page.text
            or "待确认" in compare_page.text and "已确认 0/40" in compare_page.text)
    conn.close()
    client.get("/logout")


# ---------------------------------------------------------------------------
# 已完成项目 A 的导出 / 只读展示
# ---------------------------------------------------------------------------
def test_project_a_exports_and_readonly():
    login("admin", "admin123")

    quantities = client.get("/project/1/quantities/export.xlsx")
    assert quantities.status_code == 200
    assert "spreadsheetml" in quantities.headers["content-type"]
    workbook = load_workbook(BytesIO(quantities.content))
    sheet = workbook["工程量清单"]
    titles = [cell.value for cell in sheet[3]]
    assert "计算式" in titles and "计算依据" in titles
    assert sheet.max_row > 4

    budget = client.get("/project/1/pricing/export.xlsx")
    assert budget.status_code == 200
    assert "spreadsheetml" in budget.headers["content-type"]
    workbook = load_workbook(BytesIO(budget.content))
    for sheet_name in ("费用汇总表", "安装工程预算表", "主要材料表", "其他费用明细", "预算对比"):
        assert sheet_name in workbook.sheetnames

    # 项目A 已完成：图纸页显示只读提示，上传接口返回 403
    drawings_page = client.get("/project/1/drawings")
    assert drawings_page.status_code == 200
    assert "当前不可上传" in drawings_page.text
    response = client.post(
        "/project/1/drawings/upload", data={"category": "自动识别"}, follow_redirects=False
    )
    assert response.status_code == 403
    client.get("/logout")


def test_not_found_project():
    login("admin", "admin123")
    response = client.get("/project/999")
    assert response.status_code == 404
    assert "不存在" in response.text
    client.get("/logout")


# ---------------------------------------------------------------------------
# v2：典型设计（典设）匹配
# ---------------------------------------------------------------------------
def test_typdesign_page_project_a():
    login("admin", "admin123")
    response = client.get("/project/1/typdesign")
    assert response.status_code == 200
    assert "典型设计匹配" in response.text
    assert "CSG-GD" in response.text
    assert "匹配明细" in response.text
    assert "已确认" in response.text
    assert "未匹配" in response.text
    assert "导出匹配清单" in response.text
    # 状态筛选
    response = client.get("/project/1/typdesign?filter=confirmed")
    assert response.status_code == 200
    assert "已确认" in response.text
    client.get("/logout")


def test_typdesign_readonly_for_reviewer():
    login("fuhe", "123456")
    page = client.get("/project/1/typdesign")
    assert page.status_code == 200
    assert "只读" in page.text
    # 复核员越权：执行匹配 / 确认 / 导入 → 403
    response = client.post("/project/1/typdesign/match", follow_redirects=False)
    assert response.status_code == 403
    response = client.post(
        "/project/1/typdesign/1/confirm", data={"qty": "1"}, follow_redirects=False
    )
    assert response.status_code == 403
    response = client.post(
        "/project/1/typdesign/import",
        files=[("file", ("t.csv", b"x", "text/csv"))],
        follow_redirects=False,
    )
    assert response.status_code == 403
    client.get("/logout")


def test_typdesign_integrations_visible():
    login("admin", "admin123")
    # 图纸解析页：典设匹配状态卡
    drawings = client.get("/project/3/drawings")
    assert drawings.status_code == 200
    assert "典型设计匹配（自动）" in drawings.text
    assert "重新匹配" in drawings.text
    # 项目工作流页：典设匹配卡片
    project_page = client.get("/project/1")
    assert project_page.status_code == 200
    assert "典型设计匹配" in project_page.text
    # 建模页：典设参考面板（项目A 已确认 25 个模块）
    model_page = client.get("/project/1/model")
    assert model_page.status_code == 200
    assert "典设匹配参考" in model_page.text
    assert "CSG-GD" in model_page.text
    # 对比页：送审条件 + 风险清单（项目B 已匹配 25 条待确认，未匹配不计入 pending）
    compare_page = client.get("/project/2/compare")
    assert compare_page.status_code == 200
    assert "典型设计应用匹配已完成" in compare_page.text
    assert "典型设计待确认" in compare_page.text
    assert "待确认 25 个模块" in compare_page.text
    client.get("/logout")


def test_typdesign_import_match_confirm_export():
    login("zaojia", "123456")
    # 项目C 种子：目录已导入、尚未匹配
    page = client.get("/project/3/typdesign")
    assert page.status_code == 200
    # v6 起上传图纸会自动执行匹配，页面可能是“尚未执行匹配”也可能有匹配明细
    assert ("尚未执行匹配" in page.text) or ("匹配明细" in page.text)

    csv_text = (
        "序号,卷册,类别,模块编号,模块名称,模块单位\n"
        "1,第三册 台架变,台架变,CSG-GD-10B-TB-GZ-Z-Ⅰ,Ⅰ型台架安装总图,座\n"
        "2,第三册 台架变,台架变,CSG-GD-10B-TB-GT-SB,配电箱系统结线图,套\n"
        "3,第七册 低压线路,低压线路,CSG-GD-0.4K-HD,横担架线,套\n"
    )
    response = client.post(
        "/project/3/typdesign/import",
        files=[("file", ("演示典设目录节选.csv", csv_text.encode("utf-8"), "text/csv"))],
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/project/3/typdesign")
    assert "演示典设目录节选" in page.text
    assert "3" in page.text  # 目录模块数

    # 执行匹配
    response = client.post("/project/3/typdesign/match", follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/project/3/typdesign")
    assert "匹配明细" in page.text
    assert "CSG-GD-0.4K-HD" in page.text
    assert "已匹配" in page.text

    # 确认一项（写数量与备注）
    conn = db_conn()
    row = conn.execute(
        "SELECT id FROM typdesign_matches WHERE project_id=3 AND status='待确认' ORDER BY id LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None
    match_id = int(row["id"])
    response = client.post(
        f"/project/3/typdesign/{match_id}/confirm",
        data={"qty": "2", "note": "演示确认应用"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    conn = db_conn()
    confirmed = conn.execute(
        "SELECT status, qty, note FROM typdesign_matches WHERE id=?", (match_id,)
    ).fetchone()
    assert confirmed["status"] == "已确认"
    assert float(confirmed["qty"]) == 2.0
    assert "演示确认应用" in confirmed["note"]
    # 忽略另一项
    row = conn.execute(
        "SELECT id FROM typdesign_matches WHERE project_id=3 AND status='待确认' ORDER BY id LIMIT 1"
    ).fetchone()
    conn.close()
    response = client.post(
        f"/project/3/typdesign/{int(row['id'])}/ignore",
        data={"note": "演示忽略该模块"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    conn = db_conn()
    ignored = conn.execute(
        "SELECT status FROM typdesign_matches WHERE id=?", (int(row["id"]),)
    ).fetchone()
    conn.close()
    assert ignored["status"] == "已忽略"

    # 导出典型设计匹配清单 xlsx
    export = client.get("/project/3/typdesign/export.xlsx")
    assert export.status_code == 200
    assert "spreadsheet" in export.headers["content-type"]
    assert "filename*=UTF-8" in export.headers["content-disposition"]
    assert "典型设计匹配清单" in unquote(export.headers["content-disposition"])
    workbook = load_workbook(BytesIO(export.content))
    assert "典型设计匹配清单" in workbook.sheetnames
    sheet = workbook["典型设计匹配清单"]
    headers = [cell.value for cell in sheet[3]]
    assert "模块编号" in headers and "状态" in headers and "应用数量" in headers
    assert sheet.max_row == 3 + 3  # 标题 + 说明 + 表头 + 3 个模块

    # GET 确认路径（非 POST）→ 404；不存在的项目 → 403
    response = client.get("/project/3/typdesign/999/confirm", follow_redirects=False)
    assert response.status_code in (403, 404)
    response = client.get("/project/999/typdesign", follow_redirects=False)
    assert response.status_code in (403, 404)
    client.get("/logout")


def test_typdesign_pending_blocks_submission_condition():
    """项目B（待确认 25）对比页的典设送审条件不满足；项目A 已完成匹配不再有风险提示。"""
    login("admin", "admin123")
    compare_b = client.get("/project/2/compare")
    assert compare_b.status_code == 200
    assert "典型设计应用匹配已完成" in compare_b.text
    assert "待确认 25 个模块" in compare_b.text
    assert "典型设计待确认" in compare_b.text

    # 项目A：A 的中/低置信匹配已忽略、pending=0 → 典设送审条件满足、无风险提示
    compare_a = client.get("/project/1/compare")
    assert compare_a.status_code == 200
    assert "典型设计应用匹配已完成" in compare_a.text
    assert "已确认 25/40 个模块" in compare_a.text
    assert "典型设计待确认（" not in compare_a.text
    client.get("/logout")


# ---------------------------------------------------------------------------
# v3：工作流“开始”入口 + 新建项目
# ---------------------------------------------------------------------------
def test_dashboard_start_guide():
    login("zaojia", "123456")
    response = client.get("/")
    assert response.status_code == 200
    for keyword in ("开始工作流", "三步走", "＋ 新建项目", "5 分钟演示路线", "从这里开始"):
        assert keyword in response.text, keyword
    client.get("/logout")


def test_new_project_page():
    login("zaojia", "123456")
    response = client.get("/project/new")
    assert response.status_code == 200
    assert "新建项目" in response.text
    assert "项目名称" in response.text
    assert "图纸解析" in response.text
    client.get("/logout")


def test_new_project_create_and_next_action():
    login("zaojia", "123456")
    before = project_count()
    response = client.post(
        "/project/new",
        data={
            "name": "演示新建项目",
            "ptype": "业扩配套",
            "location": "某供电所",
            "note": "v3 新建项目测试",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    match = re.match(r"^/project/(\d+)/drawings", location)
    assert match, location
    pid = int(match.group(1))
    assert project_count() == before + 1

    conn = db_conn()
    project = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    assert project is not None
    assert project["name"] == "演示新建项目"
    assert project["ptype"] == "业扩配套"
    assert project["stage"] == "图纸解析"
    assert re.match(r"^PRJ-\d{8}-\d{2}$", project["code"]), project["code"]
    assert float(project["design_total"]) == 0.0

    stage_rows = conn.execute(
        "SELECT key, status FROM stages WHERE project_id=? ORDER BY id", (pid,)
    ).fetchall()
    status_map = {row["key"]: row["status"] for row in stage_rows}
    assert status_map == {
        "drawings": "进行中",
        "model": "未开始",
        "quantities": "未开始",
        "pricing": "未开始",
    }
    member_count = conn.execute(
        "SELECT COUNT(*) AS c FROM members WHERE project_id=?", (pid,)
    ).fetchone()["c"]
    assert member_count == 3
    audit_count = conn.execute(
        "SELECT COUNT(*) AS c FROM audit WHERE project_id=? AND action='新建项目'", (pid,)
    ).fetchone()["c"]
    assert audit_count >= 1
    conn.close()

    page = client.get(f"/project/{pid}")
    assert page.status_code == 200
    assert "下一步：打开图纸解析" in page.text
    drawings = client.get(f"/project/{pid}/drawings")
    assert drawings.status_code == 200
    assert "图纸目录" in drawings.text
    client.get("/logout")


def test_new_project_forbidden_for_reviewer():
    login("fuhe", "123456")
    page = client.get("/project/new")
    assert page.status_code == 200  # 所有登录用户可查看创建页
    assert "仅管理员 / 造价员可创建项目" in page.text
    before = project_count()
    response = client.post(
        "/project/new", data={"name": "复核员越权项目"}, follow_redirects=False
    )
    assert response.status_code == 403
    assert project_count() == before
    client.get("/logout")


def test_new_project_validation_empty_name():
    login("zaojia", "123456")
    before = project_count()
    response = client.post(
        "/project/new", data={"name": "   ", "ptype": "台区改造"}, follow_redirects=False
    )
    assert response.status_code == 200
    assert "项目名称不能为空" in response.text
    assert project_count() == before
    client.get("/logout")


def test_next_action_states_on_project_pages():
    login("admin", "admin123")
    # 新建一个项目，验证“进行中 → 打开阶段页”的下一步引导
    response = client.post(
        "/project/new",
        data={"name": "下一步状态测试项目", "ptype": "台区改造"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    pid = int(re.match(r"^/project/(\d+)/drawings", response.headers["location"]).group(1))
    new_page = client.get(f"/project/{pid}")
    assert new_page.status_code == 200
    assert "下一步：打开图纸解析" in new_page.text

    # 项目A：四阶段全完成 → 引导到对比分析
    page_a = client.get("/project/1")
    assert page_a.status_code == 200
    assert "工作流已完成：查看对比分析" in page_a.text
    # 项目B：算量待复核 → 引导复核
    page_b = client.get("/project/2")
    assert page_b.status_code == 200
    assert "下一步：算量待复核" in page_b.text
    client.get("/logout")


def test_start_hints_on_login_and_demo():
    anonymous = TestClient(app)
    login_page = anonymous.get("/login")
    assert login_page.status_code == 200
    assert "登录后：工作台" in login_page.text
    login("admin", "admin123")
    demo_page = client.get("/demo")
    assert demo_page.status_code == 200
    assert "从这里开始" in demo_page.text
    assert "新建项目" in demo_page.text
    client.get("/logout")



# ---------------------------------------------------------------------------
# v4：新建项目录入项目编码 + 上传分批累计 + 图纸删除
# ---------------------------------------------------------------------------
def test_new_project_page_has_code_field():
    login("zaojia", "123456")
    page = client.get("/project/new")
    assert page.status_code == 200
    assert "项目编码" in page.text
    assert 'name="code"' in page.text
    assert "留空自动生成" in page.text
    client.get("/logout")


def test_new_project_with_entered_code():
    login("zaojia", "123456")
    before = project_count()
    code = "TEST-CODE-0001"
    pid = create_project_via_http("录入编码项目", code=code)
    assert project_count() == before + 1
    conn = db_conn()
    row = conn.execute("SELECT code, stage FROM projects WHERE id=?", (pid,)).fetchone()
    assert row["code"] == code
    audit_detail = conn.execute(
        "SELECT detail FROM audit WHERE project_id=? AND action='新建项目' ORDER BY id DESC LIMIT 1",
        (pid,),
    ).fetchone()["detail"]
    conn.close()
    assert code in audit_detail
    page = client.get(f"/project/{pid}")
    assert page.status_code == 200
    assert code in page.text
    assert "下一步：打开图纸解析" in page.text
    client.get("/logout")


def test_new_project_duplicate_code_rejected():
    login("zaojia", "123456")
    code = "TEST-CODE-0002"
    create_project_via_http("重复编码项目A", code=code)
    before = project_count()
    response = client.post(
        "/project/new",
        data={"name": "重复编码项目B", "code": code, "ptype": "台区改造"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "项目编码已存在" in response.text
    assert project_count() == before
    client.get("/logout")


def test_new_project_blank_code_autogenerated():
    login("zaojia", "123456")
    pid = create_project_via_http("留空编码项目", code="   ")
    conn = db_conn()
    code = conn.execute("SELECT code FROM projects WHERE id=?", (pid,)).fetchone()["code"]
    conn.close()
    assert re.match(r"^PRJ-\d{8}-\d{2}$", code), code
    client.get("/logout")


def test_new_project_invalid_code_rejected():
    login("zaojia", "123456")
    before = project_count()
    cases = [
        ("AB", "项目编码长度"),
        ("A" * 41, "项目编码长度"),
        ("编码中文", "项目编码只能包含"),
        ("BAD CODE", "项目编码只能包含"),
    ]
    for code, expected in cases:
        response = client.post(
            "/project/new",
            data={"name": f"非法编码测试{len(code)}", "code": code, "ptype": "台区改造"},
            follow_redirects=False,
        )
        assert response.status_code == 200, code
        assert expected in response.text, code
    assert project_count() == before
    client.get("/logout")


def test_new_project_code_is_trimmed():
    login("zaojia", "123456")
    pid = create_project_via_http("编码去空格项目", code="  TEST-CODE-0003  ")
    conn = db_conn()
    saved = conn.execute("SELECT code FROM projects WHERE id=?", (pid,)).fetchone()["code"]
    conn.close()
    assert saved == "TEST-CODE-0003"
    client.get("/logout")


def test_drawings_upload_then_delete():
    login("zaojia", "123456")
    pid = create_project_via_http("图纸删除测试项目", code="TEST-CODE-0004")
    before = drawing_count(pid)
    data = xlsx_bytes()
    upload = client.post(
        f"/project/{pid}/drawings/upload",
        files=[("files", ("待删除材料清册.xlsx", data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        data={"category": "材料清册"},
        follow_redirects=False,
    )
    assert upload.status_code == 303, upload.text
    assert drawing_count(pid) == before + 1
    conn = db_conn()
    row = conn.execute(
        "SELECT id, name FROM drawings WHERE project_id=? ORDER BY id DESC LIMIT 1", (pid,)
    ).fetchone()
    conn.close()
    did = int(row["id"])
    page = client.get(f"/project/{pid}/drawings")
    assert page.status_code == 200
    assert f"/drawings/{did}/delete" in page.text
    resp = client.post(f"/project/{pid}/drawings/{did}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert drawing_count(pid) == before
    page_after = client.get(f"/project/{pid}/drawings")
    assert f"/drawings/{did}/delete" not in page_after.text
    conn = db_conn()
    audit_actions = [r["action"] for r in conn.execute(
        "SELECT action FROM audit WHERE project_id=? ORDER BY id DESC LIMIT 5", (pid,)
    )]
    conn.close()
    assert "删除图纸" in audit_actions
    client.get("/logout")


def test_drawings_delete_forbidden():
    # 复核员不可删除
    login("zaojia", "123456")
    pid = create_project_via_http("图纸删除权限项目", code="TEST-CODE-0005")
    client.post(
        f"/project/{pid}/drawings/upload",
        files=[("files", ("权限测试清册.xlsx", xlsx_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        data={"category": "材料清册"},
        follow_redirects=False,
    )
    conn = db_conn()
    did = conn.execute("SELECT id FROM drawings WHERE project_id=? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()["id"]
    conn.close()
    before = drawing_count(pid)
    client.get("/logout")

    login("fuhe", "123456")
    forbidden = client.post(f"/project/{pid}/drawings/{did}/delete", follow_redirects=False)
    assert forbidden.status_code == 403
    assert drawing_count(pid) == before
    client.get("/logout")

    # 图纸阶段已完成的项目（项目A）也不可删除
    login("zaojia", "123456")
    conn = db_conn()
    did_a = conn.execute("SELECT id FROM drawings WHERE project_id=1 ORDER BY id LIMIT 1").fetchone()["id"]
    conn.close()
    before_a = drawing_count(1)
    blocked = client.post(f"/project/1/drawings/{did_a}/delete", follow_redirects=False)
    assert blocked.status_code in (303, 403)  # 阶段不可编辑：重定向提示或 403
    assert drawing_count(1) == before_a
    client.get("/logout")


def test_upload_ui_supports_accumulated_selection_and_delete():
    login("zaojia", "123456")
    pid = create_project_via_http("上传交互测试项目", code="TEST-CODE-0006")
    client.post(
        f"/project/{pid}/drawings/upload",
        files=[("files", ("交互测试清册.xlsx", xlsx_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        data={"category": "材料清册"},
        follow_redirects=False,
    )
    page = client.get(f"/project/{pid}/drawings")
    assert page.status_code == 200
    assert "data-file-clear" in page.text
    assert "会累计添加" in page.text
    assert "/delete" in page.text
    static = client.get("/static/app.js")
    assert static.status_code == 200
    assert "DataTransfer" in static.text
    assert "data-file-clear" in static.text
    client.get("/logout")


# ---------------------------------------------------------------------------
# v5：典设目录“图纸文件”列（本机典设库接入）
# ---------------------------------------------------------------------------
def test_typdesign_catalog_file_path_column():
    login("zaojia", "123456")
    csv_text = (
        "卷册,篇,类别,模块编号,模块名称,模块单位,图纸文件\n"
        "第三册,第二篇 台架变,台架变,CSG-GD-10B-TB-GT-JD,台架变地网图,处,"
        "03 第三册 台架变智能台区标准设计/第二篇 Ⅰ型(10+12)台架智能台区变标准设计.dwg\n"
    ).encode("utf-8")
    response = client.post(
        "/project/3/typdesign/import",
        files={"file": ("catalog_path.csv", csv_text, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    client.post("/project/3/typdesign/match", follow_redirects=False)
    page = client.get("/project/3/typdesign")
    assert page.status_code == 200
    assert "库内图纸" in page.text
    assert "台架智能台区变标准设计.dwg" in page.text
    export = client.get("/project/3/typdesign/export.xlsx")
    assert export.status_code == 200
    workbook = load_workbook(BytesIO(export.content))
    sheet = workbook[workbook.sheetnames[0]]
    header_row = None
    header = []
    for row_index, row in enumerate(sheet.iter_rows(min_row=1, max_row=6, values_only=True), 1):
        values = [str(value or "") for value in row]
        if "图纸文件" in values and "模块编号" in values:
            header_row, header = row_index, values
            break
    assert header_row is not None, "导出表头未找到“图纸文件/模块编号”"
    path_column = header.index("图纸文件")
    data_rows = list(sheet.iter_rows(min_row=header_row + 1, values_only=True))
    assert any(str(row[path_column] or "").endswith(".dwg") for row in data_rows if len(row) > path_column)
    client.get("/logout")


# ---------------------------------------------------------------------------
# v6：典型设计匹配改为自动执行（不再作为单独环节）
# ---------------------------------------------------------------------------
def test_typdesign_import_runs_match_automatically():
    login("zaojia", "123456")
    pid = create_project_via_http("典设自动匹配项目", code="TEST-CODE-AUTO1")
    csv_text = (
        "卷册,篇,类别,模块编号,模块名称,模块单位,图纸文件\n"
        "第三册,第二篇 台架变,台架变,CSG-GD-10B-TB-GT-JD,台架变地网图,处,"
        "03 第三册 台架变智能台区标准设计/第二篇 Ⅰ型(10+12)台架智能台区变标准设计.dwg\n"
    ).encode("utf-8")
    response = client.post(
        f"/project/{pid}/typdesign/import",
        files={"file": ("catalog_auto.csv", csv_text, "text/csv")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "自动执行典设匹配" in unquote(response.headers["location"])
    conn = db_conn()
    count = conn.execute("SELECT COUNT(*) AS n FROM typdesign_matches WHERE project_id=?", (pid,)).fetchone()["n"]
    conn.close()
    assert count >= 1  # 导入目录后自动匹配，无需手动点“执行匹配”
    client.get("/logout")


def test_drawings_upload_runs_typdesign_match_automatically():
    login("zaojia", "123456")
    pid = create_project_via_http("上传自动匹配项目", code="TEST-CODE-AUTO2")
    csv_text = (
        "卷册,篇,类别,模块编号,模块名称,模块单位,图纸文件\n"
        "第七册,第二篇 低压线路本体,低压架空,CSG-GD-0.4K-HD,横担架线,套,"
        "07 第七册 低压线路标准设计/02 第二篇 低压线路本体/03 低压架空模块/横担架线标准设计.dwg\n"
    ).encode("utf-8")
    client.post(
        f"/project/{pid}/typdesign/import",
        files={"file": ("catalog_auto2.csv", csv_text, "text/csv")},
        follow_redirects=False,
    )
    response = client.post(
        f"/project/{pid}/drawings/upload",
        files=[("files", ("自动匹配清册.xlsx", xlsx_bytes(),
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        data={"category": "材料清册"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "自动执行典设匹配" in unquote(response.headers["location"])
    client.get("/logout")


def test_typdesign_not_in_primary_nav():
    login("zaojia", "123456")
    page = client.get("/project/3/drawings")
    assert page.status_code == 200
    assert 'class="nav-aux' not in page.text  # 主导航不再单列“典型设计”
    assert "/project/3/typdesign" in page.text  # 仍可从图纸解析页进入明细/确认
    client.get("/logout")


# ---------------------------------------------------------------------------
# v7：上传可选择文件夹（保留目录层级）
# ---------------------------------------------------------------------------
def test_upload_folder_keeps_relative_path():
    login("zaojia", "123456")
    pid = create_project_via_http("文件夹上传项目", code="TEST-CODE-DIR1")
    rel_paths = json.dumps(["施工图/10kV/线路平面图.xlsx", "施工图/说明/说明书.pdf"])
    response = client.post(
        f"/project/{pid}/drawings/upload",
        files=[
            ("files", ("线路平面图.xlsx", xlsx_bytes(),
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ("files", ("说明书.pdf", b"%PDF-1.4 demo", "application/pdf")),
        ],
        data={"category": "自动识别", "rel_paths": rel_paths},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    conn = db_conn()
    rows = conn.execute(
        "SELECT name, file_path FROM drawings WHERE project_id=? ORDER BY id", (pid,)
    ).fetchall()
    conn.close()
    assert rows, "未登记上传文件"
    paths = [row["file_path"] for row in rows]
    assert any("施工图/10kV" in path for path in paths), paths
    assert any("施工图/说明" in path for path in paths), paths
    client.get("/logout")


def test_upload_folder_ui_has_folder_picker():
    login("zaojia", "123456")
    # 用新建项目（图纸解析进行中，可上传）检查上传区，避免依赖其它测试改变项目C阶段
    pid = create_project_via_http("文件夹入口项目", code="TEST-CODE-DIR2")
    page = client.get(f"/project/{pid}/drawings")
    assert page.status_code == 200
    assert "webkitdirectory" in page.text
    assert "或选择文件夹" in page.text
    assert 'name="rel_paths"' in page.text
    static = client.get("/static/app.js")
    assert static.status_code == 200
    assert "webkitRelativePath" in static.text
    client.get("/logout")


# ---------------------------------------------------------------------------
# v8：作业指导书工序库页面
# ---------------------------------------------------------------------------
def test_instructions_page_and_nav():
    login("zaojia", "123456")
    page = client.get("/instructions")
    assert page.status_code == 200
    assert "作业指导书" in page.text
    assert "工序条目" in page.text
    # 关键词检索
    found = client.get("/instructions?q=%E7%94%B5%E7%BC%86")
    assert found.status_code == 200
    assert "电缆" in found.text
    client.get("/logout")


def test_quantities_page_shows_related_instructions():
    login("zaojia", "123456")
    page = client.get("/project/1/quantities")
    assert page.status_code == 200
    assert "相关施工工序" in page.text
    client.get("/logout")
