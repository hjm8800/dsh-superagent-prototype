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
