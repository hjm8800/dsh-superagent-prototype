import os
import tempfile

os.environ["PLATFORM_DATA_DIR"] = tempfile.mkdtemp(prefix="settlement-test-")
os.environ["PLATFORM_DEMO"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app import app  # noqa: E402

client = TestClient(app)


def login(username="ziliao", password="123456"):
    r = client.post("/login", data={"username": username, "password": password}, follow_redirects=False)
    assert r.status_code == 303
    return client


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_login_and_dashboard():
    login()
    r = client.get("/")
    assert r.status_code == 200
    assert "项目A" in r.text
    assert "工作台" in r.text


def test_project_tabs():
    login("admin", "admin123")
    for tab in ("overview", "docs", "materials", "boq", "budget", "compare", "ai", "reviews"):
        r = client.get(f"/projects/1?tab={tab}")
        assert r.status_code == 200, tab
        assert "项目A" in r.text


def test_compare_update_and_export():
    login("admin", "admin123")
    r = client.post("/projects/1/compare/1", data={"settle_qty": "0.170"}, follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/projects/1?tab=compare")
    assert "0.170" in r.text
    r = client.get("/export/1/compare")
    assert r.status_code == 200
    assert "10kV导线" in r.text


def test_ai_generate_and_review():
    login("ziliao")
    r = client.post("/projects/1/ai", data={"scene": "三算对比"}, follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/projects/1?tab=ai")
    assert "三算对比" in r.text
    assert "规则初稿" in r.text
    # find latest output id via page link is not exposed; review via known seeded id 1
    r = client.post("/ai/1/review", data={"result": "通过"}, follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/projects/1?tab=reviews")
    assert "复核记录" in r.text


def test_upload_docx(tmp_path):
    from docx import Document

    login("ziliao")
    p = tmp_path / "demo.docx"
    d = Document()
    d.add_heading("施工图设计说明书", 0)
    d.add_paragraph("项目编码：DEMO-TEST，10kV线路0.057km，配变500kVA。")
    d.save(p)
    with open(p, "rb") as fh:
        r = client.post(
            "/projects/1/documents",
            data={"name": "测试设计说明书", "category": "设计说明书"},
            files={"file": ("demo.docx", fh, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            follow_redirects=False,
        )
    assert r.status_code == 303
    r = client.get("/projects/1?tab=docs")
    assert "测试设计说明书" in r.text
    assert "parsed" in r.text

def test_create_project_and_edit():
    login("admin", "admin123")
    code = "DEMO-TEST-CREATE"
    r = client.post(
        "/projects",
        data={"code": code, "name": "项目D·某新建台区", "ptype": "台区改造", "stage": "资料解析", "t0": "2026-12-31", "owner": "资料员", "location": "某供电所", "note": "测试"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    pid = int(loc.split("/projects/")[1].split("?")[0])
    r = client.get(f"/projects/{pid}?tab=docs")
    assert r.status_code == 200
    assert code in r.text
    r = client.post(
        f"/projects/{pid}/edit",
        data={"code": code, "name": "项目D·某新建台区（改）", "ptype": "台区改造", "stage": "资料移交", "t0": "2026-12-31", "owner": "资料员", "location": "某供电所", "note": "改"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = client.get(f"/projects/{pid}?tab=overview")
    assert "项目D·某新建台区（改）" in r.text


def _make_xlsx(path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "甲供材料"
    ws.append(["序号", "物资编码", "物资名称", "规格型号", "单位", "数量"])
    ws.append([1, "TEST-001", "测试电缆", "YJV-1×240", "千米", 0.12])
    ws2 = wb.create_sheet("工程量清单")
    ws2.append(["序号", "工程量名称", "单位", "数量"])
    ws2.append([1, "10kV线路架设", "千米", 0.2])
    ws3 = wb.create_sheet("施工图预算费用明细表")
    ws3.append(["序号", "工程名称", "工程编号", "预算总额", "建筑工程费", "设备购置费", "安装工程费", "其他费用"])
    ws3.append([1, "测试工程", "DEMO-TEST", 123456, 0, 10000, 100000, 13456])
    wb.save(path)


def test_upload_xlsx_import_and_excel_export(tmp_path):
    login("admin", "admin123")
    # create a fresh project
    code = "DEMO-TEST-IMPORT"
    r = client.post("/projects", data={"code": code, "name": "项目E·导入测试", "ptype": "业扩配套", "stage": "资料解析", "t0": "", "owner": "资料员", "location": "某供电所", "note": ""}, follow_redirects=False)
    pid = int(r.headers["location"].split("/projects/")[1].split("?")[0])
    p = tmp_path / "materials.xlsx"
    _make_xlsx(p)
    with open(p, "rb") as fh:
        r = client.post(
            f"/projects/{pid}/documents",
            data={"name": "测试材料清册", "category": "材料清册"},
            files={"file": ("materials.xlsx", fh, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            follow_redirects=False,
        )
    assert r.status_code == 303
    r = client.get(f"/projects/{pid}?tab=materials")
    assert "测试电缆" in r.text
    assert "TEST-001" in r.text
    r = client.get(f"/projects/{pid}?tab=boq")
    assert "10kV线路架设" in r.text
    r = client.get(f"/projects/{pid}?tab=budget")
    assert "123,456" in r.text or "123456" in r.text
    r = client.get(f"/export/{pid}/xlsx/materials")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]

