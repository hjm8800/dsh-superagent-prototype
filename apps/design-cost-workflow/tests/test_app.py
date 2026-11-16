"""Web 层测试：FastAPI TestClient + 临时数据目录（不写仓库 data/）。

运行（在 apps/design-cost-workflow 目录下）::

    python -m pytest -q tests/test_app.py
"""
from __future__ import annotations

import os
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
    assert "已满足送审条件" in compare_page.text
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
