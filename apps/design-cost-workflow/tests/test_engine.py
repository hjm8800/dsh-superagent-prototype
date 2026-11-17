# -*- coding: utf-8 -*-
"""engine / pricing / seed 核心逻辑测试。

全部使用 pytest 的 tmp_path 建库，不写仓库内 data/ 目录，不依赖网络。
运行：cd apps/design-cost-workflow ; & $py -m pytest -q tests/test_engine.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import engine  # noqa: E402
import pricing  # noqa: E402
import rates  # noqa: E402
import seed  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    """临时目录建库并写入演示数据。"""
    db_path = tmp_path / "data" / "workflow.db"
    seed.init_db(str(db_path))
    conn = seed.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def _pid(conn, code: str) -> int:
    row = conn.execute("SELECT id FROM projects WHERE code=?", (code,)).fetchone()
    assert row is not None, f"项目 {code} 不存在"
    return int(row["id"])


def _rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _one(conn, sql: str, params: tuple = ()) -> dict:
    row = conn.execute(sql, params).fetchone()
    assert row is not None, f"查询无结果：{sql}"
    return dict(row)


def _users(conn) -> dict[str, dict]:
    return {row["username"]: row for row in _rows(conn, "SELECT * FROM users")}


def _new_project(conn, code: str = "TEST-1") -> int:
    cursor = conn.execute(
        "INSERT INTO projects (code, name, ptype, stage, location, t0, note) "
        "VALUES (?,?,?,?,?,?,?)",
        (code, f"测试项目·{code}", "业扩配套", "图纸解析", "某供电所", "2026-11-16 09:00", "测试项目"),
    )
    conn.commit()
    return int(cursor.lastrowid)


def _add_recognized_drawings(conn, project_id: int, count: int = 3) -> None:
    for index in range(count):
        conn.execute(
            "INSERT INTO drawings (project_id, code, name, category, fmt, sheet_count, status, summary) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (project_id, f"T-{index + 1:02d}", f"测试图纸{index + 1}", "平面图", "DWG", 1,
             "已识别", "识别：10kV路径0.057km、杆塔2基、配变500kVA、低压路径0.7875km"),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# 演示数据：用户 / 项目 / 设计预算
# ---------------------------------------------------------------------------

def test_seed_users_projects_and_design_budget(db):
    users = _users(db)
    assert set(users) == {"admin", "zaojia", "fuhe"}
    assert users["admin"]["role"] == "admin" and users["admin"]["name"] == "管理员"
    assert users["zaojia"]["role"] == "zaojia" and users["zaojia"]["name"] == "造价员"
    assert users["fuhe"]["role"] == "fuhe" and users["fuhe"]["name"] == "复核员"
    assert seed.check_pw("admin123", users["admin"]["password"])
    assert seed.check_pw("123456", users["zaojia"]["password"])
    assert not seed.check_pw("wrong", users["admin"]["password"])

    projects = _rows(db, "SELECT * FROM projects ORDER BY id")
    assert len(projects) == 3
    assert [project["code"] for project in projects] == ["DEMO-A", "DEMO-B", "DEMO-C"]
    assert projects[0]["name"] == "项目A·某台区改造工程"
    assert projects[0]["ptype"] == "台区改造" and projects[0]["location"] == "某供电所"
    assert [project["stage"] for project in projects] == ["计价定稿", "算量复核", "图纸解析"]

    for project in projects:
        assert project["design_equipment"] > 0
        assert project["design_install"] > 0
        assert project["design_other"] > 0
        assert project["design_reserve"] > 0
        total = (project["design_equipment"] + project["design_install"]
                 + project["design_other"] + project["design_reserve"])
        assert abs(project["design_total"] - total) < 1e-6, "设计预算四项之和必须等于静态投资"
        assert abs(project["design_reserve"] - round(
            (project["design_equipment"] + project["design_install"]
             + project["design_other"]) * 0.03)) < 1e-6

    member_count = _one(db, "SELECT COUNT(*) AS n FROM members")["n"]
    assert member_count == 9  # 3 用户 × 3 项目


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / "nested" / "workflow.db"
    seed.init_db(str(db_path))
    seed.init_db(str(db_path))  # 重复初始化不应重复写入演示数据
    conn = seed.connect(db_path)
    try:
        assert _one(conn, "SELECT COUNT(*) AS n FROM projects")["n"] == 3
        assert _one(conn, "SELECT COUNT(*) AS n FROM users")["n"] == 3
        assert _one(conn, "SELECT COUNT(*) AS n FROM drawings")["n"] == 23  # 9 + 7 + 7
    finally:
        conn.close()


def test_drawings_and_stage_seed(db):
    drawings_a = _rows(db, "SELECT * FROM drawings d JOIN projects p ON p.id=d.project_id "
                           "WHERE p.code='DEMO-A' ORDER BY d.id")
    assert len(drawings_a) == 9
    assert all(row["status"] == "已识别" for row in drawings_a)
    names = " ".join(row["name"] for row in drawings_a)
    for keyword in ("施工图设计说明书", "10kV线路平面图", "台架变安装图", "0.4kV线路平面图",
                    "杆塔明细表", "主要设备材料清册", "施工图预算", "电缆路径图", "勘探及现场照片"):
        assert keyword in names

    stages_a = engine.get_stages(db, _pid(db, "DEMO-A"))
    assert [stage["key"] for stage in stages_a] == ["drawings", "model", "quantities", "pricing"]
    assert [stage["name"] for stage in stages_a] == ["图纸解析", "建模", "算量", "计价"]
    assert all(stage["status"] == "已完成" for stage in stages_a)
    assert [stage["version"] for stage in stages_a] == [1, 1, 1, 2]
    assert stages_a[3]["note"] == "计价复核通过，可送审"
    assert all(stage["reviewer"] == "复核员" for stage in stages_a)

    stages_b = engine.get_stages(db, _pid(db, "DEMO-B"))
    assert [stage["status"] for stage in stages_b] == ["已完成", "已完成", "待复核", "未开始"]
    assert stages_b[2]["owner"] == "造价员" and stages_b[2]["submitted_at"]

    stages_c = engine.get_stages(db, _pid(db, "DEMO-C"))
    assert [stage["status"] for stage in stages_c] == ["进行中", "未开始", "未开始", "未开始"]
    assert stages_c[0]["progress"] == 40

    progress_a = engine.project_progress(db, _pid(db, "DEMO-A"))
    assert progress_a["done"] == 4 and progress_a["percent"] == 100
    progress_b = engine.project_progress(db, _pid(db, "DEMO-B"))
    assert progress_b["done"] == 2 and progress_b["current"] == "quantities"
    assert engine.stage_editable(db, _pid(db, "DEMO-C"), "drawings") is True
    assert engine.stage_editable(db, _pid(db, "DEMO-C"), "model") is False
    assert engine.stage_editable(db, _pid(db, "DEMO-A"), "pricing") is False


# ---------------------------------------------------------------------------
# 算量
# ---------------------------------------------------------------------------

def test_compute_quantities_project_a(db):
    project_id = _pid(db, "DEMO-A")
    rows = engine.compute_quantities(db, project_id, _users(db)["zaojia"])
    assert len(rows) > 30

    rows_10kv = [row for row in rows if row["name"] == "10kV架空线路架设"]
    assert len(rows_10kv) == 1 and abs(rows_10kv[0]["qty"] - 0.057) < 1e-9
    assert rows_10kv[0]["unit"] == "km" and rows_10kv[0]["quota_code"] == "YD3-0301"
    assert rows_10kv[0]["formula"] and rows_10kv[0]["basis"]
    assert "10kV路径0.057km" in rows_10kv[0]["basis"]

    rows_lv = [row for row in rows if row["name"] == "低压线路架设"]
    assert len(rows_lv) == 1 and abs(rows_lv[0]["qty"] - 0.7875) < 1e-9
    assert rows_lv[0]["quota_code"] == "YD3-0302"

    # 导线主材：10kV 0.176（清册量）；低压 1.648 + 1.597（清册量，共 3.245）
    material_10kv = [row for row in rows if row["category"] == rates.CATEGORY_MATERIAL
                     and "10kV架空绝缘导线" == row["name"]]
    assert len(material_10kv) == 1 and abs(material_10kv[0]["qty"] - 0.176) < 1e-9
    lv_materials = [row for row in rows if row["category"] == rates.CATEGORY_MATERIAL
                    and row["name"] == "低压绝缘导线"]
    assert len(lv_materials) == 2
    assert abs(sum(row["qty"] for row in lv_materials) - 3.245) < 1e-9
    assert {row["spec"] for row in lv_materials} == {"BLVV-240", "BLVV-120"}

    # 电杆组立 16 基，杆坑土方 19.2m³，拉线坑 9.6m³
    pole_rows = [row for row in rows if row["name"] == "电杆组立"]
    assert abs(sum(row["qty"] for row in pole_rows) - 16) < 1e-9
    pit_rows = [row for row in rows if row["name"] == "杆坑土方开挖"]
    assert abs(sum(row["qty"] for row in pit_rows) - 19.2) < 1e-9
    assert all(row["category"] == rates.CATEGORY_BUILD for row in pit_rows)
    stay_pit = [row for row in rows if row["name"] == "拉线坑土方开挖"]
    assert abs(sum(row["qty"] for row in stay_pit) - 9.6) < 1e-9
    assert "16 × 1.2" in " ".join(row["formula"] for row in pit_rows) or \
           "14 × 1.2" in " ".join(row["formula"] for row in pit_rows)

    # 拉线、金具、设备安装、土建、拆除
    stay_rows = [row for row in rows if row["name"] == "拉线制作安装"]
    assert abs(sum(row["qty"] for row in stay_rows) - 12) < 1e-9
    assert any(row["name"] == "台架式变压器安装" and row["quota_code"] == "YD2-0101" for row in rows)
    # 规则 8 回归：配电箱必须套 YD2-0201，不能被“变压器”关键词误判
    box_rows = [row for row in rows if row["spec"].startswith("XLW-10/500-4")]
    assert len(box_rows) == 1
    assert box_rows[0]["name"] == "低压配电箱安装" and box_rows[0]["quota_code"] == "YD2-0201"
    assert any(row["name"] == "电力电缆敷设" and abs(row["qty"] - 0.0742) < 1e-9 for row in rows)
    assert any(row["name"] == "台架变基础" and row["category"] == rates.CATEGORY_BUILD for row in rows)
    demolition = [row for row in rows if row["category"] == rates.CATEGORY_DEMOLITION]
    assert {row["name"] for row in demolition} == {"拆除低压导线", "拆除水泥杆"}
    assert all(row["quota_code"] in ("YD5-0101", "YD5-0102") for row in demolition)

    # 审计日志有记录
    audit = _rows(db, "SELECT * FROM audit WHERE project_id=? ORDER BY id DESC", (project_id,))
    assert audit and audit[0]["action"] == "重算工程量"


def test_compute_quantities_project_b_and_c(db):
    users = _users(db)
    rows_b = engine.compute_quantities(db, _pid(db, "DEMO-B"), users["zaojia"])
    material_b = [row for row in rows_b if row["category"] == rates.CATEGORY_MATERIAL
                  and "导线" in row["name"]]
    assert len(material_b) == 2
    assert abs(sum(row["qty"] for row in material_b if "10kV" in row["name"]) - 6.585) < 1e-9
    assert abs(sum(row["qty"] for row in material_b if "低压" in row["name"]) - 3.201) < 1e-9
    assert abs(sum(row["qty"] for row in rows_b if row["name"] == "电杆组立") - 50) < 1e-9
    assert abs(sum(row["qty"] for row in rows_b if row["name"] == "拉线制作安装") - 24) < 1e-9
    assert any(row["name"] == "台架式变压器安装" and row["quota_code"] == "YD2-0102" for row in rows_b)

    rows_c = engine.compute_quantities(db, _pid(db, "DEMO-C"), users["zaojia"])
    material_c = [row for row in rows_c if row["category"] == rates.CATEGORY_MATERIAL
                  and "导线" in row["name"]]
    assert len(material_c) == 2
    assert abs(sum(row["qty"] for row in material_c if "10kV" in row["name"]) - 0.238) < 1e-9
    assert abs(sum(row["qty"] for row in material_c if "低压" in row["name"]) - 0.0618) < 1e-9
    assert abs(sum(row["qty"] for row in rows_c if row["name"] == "电杆组立") - 4) < 1e-9
    assert not any(row["category"] == rates.CATEGORY_DEMOLITION for row in rows_c)


def test_quantities_deleted_and_recomputed(db):
    project_id = _pid(db, "DEMO-A")
    before = engine.compute_quantities(db, project_id, _users(db)["zaojia"])
    after = engine.compute_quantities(db, project_id, _users(db)["zaojia"])
    assert len(before) == len(after)
    count = _one(db, "SELECT COUNT(*) AS n FROM quantities WHERE project_id=?", (project_id,))["n"]
    assert count == len(before)  # 旧行被删除，不重复累积


# ---------------------------------------------------------------------------
# 模型校验与材料量对比
# ---------------------------------------------------------------------------

def test_validate_model_no_high_issues(db):
    for code in ("DEMO-A", "DEMO-B", "DEMO-C"):
        issues = engine.validate_model(db, _pid(db, code))
        assert all(issue["level"] in ("高", "中", "低") for issue in issues)
        assert not [issue for issue in issues if issue["level"] == "高"], f"{code} 不应有高级问题：{issues}"
        levels = [issue["level"] for issue in issues]
        assert levels == sorted(levels, key=lambda level: engine.LEVEL_ORDER[level])

    issues_a = engine.validate_model(db, _pid(db, "DEMO-A"))
    assert any("档距" in issue["problem"] for issue in issues_a)


def test_material_compare_rules(db):
    users = _users(db)
    engine.compute_quantities(db, _pid(db, "DEMO-A"), users["zaojia"])
    compare_a = engine.material_compare(db, _pid(db, "DEMO-A"))
    assert len(compare_a) == 8
    low_voltage = next(row for row in compare_a if row["name"] == "低压绝缘导线")
    assert abs(low_voltage["model_qty"] - 3.2445) < 1e-9, "对比模型量应按亘长×相数×1.03 计算"
    assert abs(low_voltage["rate"] - 3.0) < 1e-6 and low_voltage["level"] == "正常"
    assert "预留" in low_voltage["reason"]
    cable = next(row for row in compare_a if row["name"] == "电力电缆")
    assert abs(cable["rate"] - 6.0) < 1e-6 and cable["level"] == "正常"
    # 模型量口径与契约 5.4 一致：0.176 / 3.2445 / 0.0742
    conductor_10kv = next(row for row in compare_a if row["name"] == "10kV架空绝缘导线")
    assert abs(conductor_10kv["model_qty"] - 0.176) < 1e-9
    assert abs(conductor_10kv["rate"]) < 1e-9
    assert all(row["status"] == "待确认" for row in compare_a)

    engine.compute_quantities(db, _pid(db, "DEMO-B"), users["zaojia"])
    compare_b = engine.material_compare(db, _pid(db, "DEMO-B"))
    stay = next(row for row in compare_b if row["name"] == "拉线")
    assert stay["level"] == "异常" and "不一致" in stay["reason"]
    assert abs(stay["diff"] - 6.0) < 1e-9
    transformer = next(row for row in compare_b if row["name"] == "配电变压器")
    assert transformer["level"] == "正常" and abs(transformer["model_qty"] - 1.0) < 1e-9
    # 契约 5.4：B 10kV 6.62 vs 6.585、低压 3.108 vs 3.201
    b_10kv = next(row for row in compare_b if row["name"] == "10kV架空绝缘导线")
    assert abs(b_10kv["model_qty"] - 6.585) < 1e-9 and b_10kv["level"] == "正常"
    b_lv = next(row for row in compare_b if row["name"] == "低压绝缘导线")
    assert abs(b_lv["model_qty"] - 3.201) < 1e-9
    assert abs(b_lv["rate"] - 3.0) < 0.05 and b_lv["level"] == "正常"

    compare_c = engine.material_compare(db, _pid(db, "DEMO-C"))
    assert {row["name"] for row in compare_c} == {
        "10kV架空绝缘导线", "低压绝缘导线", "水泥杆", "配电智能网关"}
    c_10kv = next(row for row in compare_c if row["name"] == "10kV架空绝缘导线")
    c_lv = next(row for row in compare_c if row["name"] == "低压绝缘导线")
    assert abs(c_10kv["model_qty"] - 0.238) < 1e-9 and c_10kv["level"] == "正常"
    assert abs(c_lv["model_qty"] - 0.0618) < 1e-9 and abs(c_lv["rate"] - 3.0) < 1e-6


def test_material_compare_regenerates_when_empty(db):
    project_id = _pid(db, "DEMO-A")
    db.execute("DELETE FROM compare_materials WHERE project_id=?", (project_id,))
    db.commit()
    compare = engine.material_compare(db, project_id)
    assert len(compare) == 8
    assert any(row["level"] == "正常" for row in compare)


# ---------------------------------------------------------------------------
# 阶段状态机
# ---------------------------------------------------------------------------

def test_stage_state_machine_c_project(db):
    users = _users(db)
    project_id = _pid(db, "DEMO-C")
    zaojia, fuhe = users["zaojia"], users["fuhe"]

    assert engine.submit_stage(db, project_id, "drawings", zaojia)[0] is True
    stage = _one(db, "SELECT * FROM stages WHERE project_id=? AND key='drawings'", (project_id,))
    assert stage["status"] == "待复核" and stage["owner"] == "造价员" and stage["submitted_at"]
    # 重复提交失败
    ok, message = engine.submit_stage(db, project_id, "drawings", zaojia)
    assert ok is False and "等待复核" in message

    # 复核退回 → 回到进行中
    ok, message = engine.review_stage(db, project_id, "drawings", fuhe, "退回", "识别结论缺少杆塔明细")
    assert ok is True and "退回" in message
    stage = _one(db, "SELECT * FROM stages WHERE project_id=? AND key='drawings'", (project_id,))
    assert stage["status"] == "进行中" and stage["version"] == 0
    review = _one(db, "SELECT * FROM reviews WHERE project_id=? ORDER BY id DESC LIMIT 1", (project_id,))
    assert review["result"] == "退回" and review["reviewer"] == "复核员"

    # 再次提交 → 复核通过 → 已完成，建模阶段自动开始
    assert engine.submit_stage(db, project_id, "drawings", zaojia)[0] is True
    ok, message = engine.review_stage(db, project_id, "drawings", fuhe, "通过", "已补充杆塔明细，通过")
    assert ok is True and "建模" in message
    stages = {stage["key"]: stage for stage in engine.get_stages(db, project_id)}
    assert stages["drawings"]["status"] == "已完成"
    assert stages["drawings"]["reviewer"] == "复核员"
    assert stages["drawings"]["version"] == 1
    assert stages["drawings"]["progress"] == 100
    assert stages["model"]["status"] == "进行中"
    project = _one(db, "SELECT * FROM projects WHERE id=?", (project_id,))
    assert project["stage"] == "建模"

    # 已完成的阶段不能再提交
    ok, message = engine.submit_stage(db, project_id, "drawings", zaojia)
    assert ok is False and "已完成" in message

    audit_count = _one(db, "SELECT COUNT(*) AS n FROM audit WHERE project_id=?", (project_id,))["n"]
    assert audit_count >= 3


def test_stage_roles_and_preconditions(db):
    users = _users(db)
    project_id = _new_project(db, "TEST-PRECOND")
    engine.init_project_stages(db, project_id)
    zaojia, fuhe = users["zaojia"], users["fuhe"]

    # 角色兜底：复核员不能提交、造价员不能复核
    ok, message = engine.submit_stage(db, project_id, "drawings", fuhe)
    assert ok is False and "提交权限" in message
    ok, message = engine.review_stage(db, project_id, "drawings", zaojia, "通过", "")
    assert ok is False and "复核权限" in message

    # 图纸不足 3 条，提交失败且不落库
    ok, message = engine.submit_stage(db, project_id, "drawings", zaojia)
    assert ok is False and "至少需要 3 条" in message
    stage = _one(db, "SELECT * FROM stages WHERE project_id=? AND key='drawings'", (project_id,))
    assert stage["status"] == "进行中"

    _add_recognized_drawings(db, project_id, 3)
    assert engine.submit_stage(db, project_id, "drawings", zaojia)[0] is True
    assert engine.review_stage(db, project_id, "drawings", fuhe, "通过", "")[0] is True

    # 建模：无线路 → 失败；补线路后无设备 → 失败
    ok, message = engine.submit_stage(db, project_id, "model", zaojia)
    assert ok is False and "至少需要 1 条线路" in message
    db.execute(
        "INSERT INTO model_lines (project_id, name, voltage, start_point, end_point, length_km, "
        "phases, conductor, poles, pole_type, span_m, stays) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (project_id, "10kV线路", "10kV", "T接点", "台架变1#杆", 0.057, 3,
         "JKLGYJ-70/10", 2, "Φ150×8m", 28.5, 0),
    )
    db.commit()
    ok, message = engine.submit_stage(db, project_id, "model", zaojia)
    assert ok is False and "至少需要 1 条设备" in message

    db.execute(
        "INSERT INTO model_equipment (project_id, category, name, spec, unit, qty, unit_price, supply) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (project_id, "设备", "配电变压器", "SB20-M-500kVA", "台", 1, 68000, "甲供"),
    )
    db.execute(
        "INSERT INTO model_civil (project_id, name, spec, unit, qty, note) VALUES (?,?,?,?,?,?)",
        (project_id, "台架变基础", "C20混凝土", "m³", 2.4, "测试"),
    )
    db.commit()
    assert all(issue["level"] != "高" for issue in engine.validate_model(db, project_id))
    assert engine.submit_stage(db, project_id, "model", zaojia)[0] is True
    assert engine.review_stage(db, project_id, "model", fuhe, "通过", "")[0] is True

    # 算量：无工程量 → 失败；算量后通过
    ok, message = engine.submit_stage(db, project_id, "quantities", zaojia)
    assert ok is False and "算量结果为空" in message
    engine.compute_quantities(db, project_id, zaojia)
    assert engine.submit_stage(db, project_id, "quantities", zaojia)[0] is True
    assert engine.review_stage(db, project_id, "quantities", fuhe, "通过", "")[0] is True

    # 计价：无计价结果 → 失败；计价后通过
    ok, message = engine.submit_stage(db, project_id, "pricing", zaojia)
    assert ok is False and "计价结果为空" in message
    pricing.compute_pricing(db, project_id, zaojia)
    assert engine.submit_stage(db, project_id, "pricing", zaojia)[0] is True
    assert engine.review_stage(db, project_id, "pricing", fuhe, "通过", "")[0] is True

    progress = engine.project_progress(db, project_id)
    assert progress["done"] == 4 and progress["percent"] == 100


def test_admin_can_review_own_submission(db):
    users = _users(db)
    project_id = _new_project(db, "TEST-ADMIN")
    engine.init_project_stages(db, project_id)
    _add_recognized_drawings(db, project_id, 3)
    assert engine.submit_stage(db, project_id, "drawings", users["admin"])[0] is True
    ok, message = engine.review_stage(db, project_id, "drawings", users["admin"], "通过", "管理员自审")
    assert ok is True and "通过" in message
    stage = _one(db, "SELECT * FROM stages WHERE project_id=? AND key='drawings'", (project_id,))
    assert stage["status"] == "已完成"


# ---------------------------------------------------------------------------
# 计价
# ---------------------------------------------------------------------------

def test_pricing_project_a_self_consistency(db):
    project_id = _pid(db, "DEMO-A")
    result = pricing.compute_pricing(db, project_id, _users(db)["zaojia"])
    assert result["items"] > 20
    assert result["static_total"] > 0
    component_sum = (result["equipment"] + result["install"] + result["build"]
                     + result["demolition"] + result["other"] + result["reserve"])
    assert abs(result["static_total"] - component_sum) < 0.01
    assert abs(result["equipment"] - 111671.30) < 0.01  # 110500 × 1.0106

    fees = _rows(db, "SELECT * FROM pricing_fees WHERE project_id=? ORDER BY seq", (project_id,))
    assert len(fees) > 40
    assert {fee["category"] for fee in fees} >= {
        "安装工程费", "建筑工程费", "拆除工程费", "设备购置费", "其他费用", "基本预备费", "静态投资"}
    measure = _one(db, "SELECT * FROM pricing_fees WHERE project_id=? AND category='安装工程费' "
                       "AND item='措施费小计'", (project_id,))
    assert abs(measure["rate"] - 27.90) < 1e-9
    vat = _one(db, "SELECT * FROM pricing_fees WHERE project_id=? AND category='安装工程费' "
                   "AND item='增值税'", (project_id,))
    assert abs(vat["rate"] - 9.0) < 1e-9 and vat["amount"] > 0
    # 其他费用包含真实费率项
    other_items = {row["item"] for row in fees if row["category"] == "其他费用"}
    for item in ("项目管理经费", "招标费", "工程监理费", "项目前期费" if False else "项目前期工作费",
                 "勘察费", "基本设计费", "其他设计费", "设计文件评审费", "余物清理费"):
        assert item in other_items, f"其他费用缺少 {item}"

    # 计价行与工程量行挂钩
    items = _rows(db, "SELECT * FROM pricing_items WHERE project_id=?", (project_id,))
    assert all(item["quantity_id"] for item in items)
    main_materials = _rows(db, "SELECT * FROM pricing_materials WHERE project_id=?", (project_id,))
    assert len(main_materials) == 14
    assert any(material["name"] == "低压绝缘导线" and material["qty"] == 1.648
               for material in main_materials)

    # 设计预算 vs 模型预算：5 行对比
    compare = pricing.compare_budget(db, project_id)
    assert len(compare) == 5
    assert [row["item"] for row in compare] == [
        "设备购置费", "安装工程费(含建筑)", "其他费用", "基本预备费", "静态投资"]
    static_row = compare[-1]
    project = _one(db, "SELECT * FROM projects WHERE id=?", (project_id,))
    assert abs(static_row["design_amount"] - project["design_total"]) < 1e-6
    assert abs(static_row["model_amount"] - result["static_total"]) < 1e-6

    summary = pricing.pricing_summary(db, project_id)
    assert summary["static_total"] > 0
    assert summary["items"] and summary["materials"] and summary["fees"]
    assert summary["design"]["total"] == project["design_total"]
    assert abs(summary["install"] - result["install"]) < 0.01


def test_pricing_b_c_and_design_factor_consistency(db):
    users = _users(db)
    for code in ("DEMO-B", "DEMO-C"):
        project_id = _pid(db, code)
        factors = rates.DESIGN_FACTORS[code]
        result = pricing.compute_pricing(db, project_id, users["zaojia"])
        assert result["items"] > 10 and result["static_total"] > 0
        component_sum = (result["equipment"] + result["install"] + result["build"]
                         + result["demolition"] + result["other"] + result["reserve"])
        assert abs(result["static_total"] - component_sum) < 0.01
        project = _one(db, "SELECT * FROM projects WHERE id=?", (project_id,))
        assert abs(project["design_equipment"] - round(result["equipment"] * factors["equipment"])) < 1
        assert abs(project["design_install"]
                   - round((result["install"] + result["build"]) * factors["install"])) < 1
        assert abs(project["design_other"] - round(result["other"] * factors["other"])) < 1


def test_pricing_summary_before_compute_is_empty(db):
    for code in ("DEMO-B", "DEMO-C"):
        top = pricing.pricing_summary(db, _pid(db, code))
        assert top["items"] == []
        assert top["static_total"] == 0
        assert top["install"] == 0 and top["equipment"] == 0
        assert pricing.compare_budget(db, _pid(db, code)) == []
        assert top["design"]["total"] > 0  # 设计预算（附件5）已由 seed 生成


def test_update_item_quota(db):
    project_id = _pid(db, "DEMO-A")
    pricing.compute_pricing(db, project_id, _users(db)["zaojia"])
    item = _one(db, "SELECT * FROM pricing_items WHERE project_id=? AND name='10kV架空线路架设'",
                (project_id,))
    old_quota = item["quota_code"]
    assert old_quota == "YD3-0301"
    install_before = _one(db, "SELECT amount FROM pricing_fees WHERE project_id=? "
                              "AND category='安装工程费' AND item='合计'",
                          (project_id,))["amount"]
    ok, message = pricing.update_item_quota(db, project_id, item["id"], "YD3-0201",
                                            _users(db)["zaojia"])
    assert ok is True and "YD3-0201" in message
    updated = _one(db, "SELECT * FROM pricing_items WHERE id=?", (item["id"],))
    assert updated["quota_code"] == "YD3-0201"
    assert abs(updated["labor_unit"] - 130.0) < 1e-9
    assert abs(updated["labor"] - round(0.057 * 130.0, 2)) < 1e-9
    install_after = _one(db, "SELECT amount FROM pricing_fees WHERE project_id=? "
                             "AND category='安装工程费' AND item='合计'",
                         (project_id,))["amount"]
    assert install_after != install_before
    quantity = _one(db, "SELECT * FROM quantities WHERE id=?", (item["quantity_id"],))
    assert quantity["quota_code"] == "YD3-0201"

    ok, message = pricing.update_item_quota(db, project_id, item["id"], "NO-SUCH-CODE")
    assert ok is False and "不存在" in message
    ok, message = pricing.update_item_quota(db, project_id, 999999, "YD3-0201")
    assert ok is False


def test_build_export_rows(db):
    project_id = _pid(db, "DEMO-A")
    pricing.compute_pricing(db, project_id, _users(db)["zaojia"])
    data = pricing.build_export_rows(db, project_id)
    assert set(data) >= {"project", "summary", "items", "materials", "other_fees", "compare", "totals"}
    assert data["project"]["code"] == "DEMO-A"
    assert data["summary"] and data["items"] and data["materials"] and data["other_fees"]
    assert data["totals"]["static_total"] > 0
    material = data["materials"][0]
    assert "unit_name" in material and "quota_code" in material


def test_quota_library():
    library = pricing.quota_library()
    assert len(library) == len(rates.QUOTA_LIBRARY) == 23
    codes = {quota["code"] for quota in library}
    assert {"YD3-0301", "YD2-0101", "YD1-0201", "YD5-0102"} <= codes
    for quota in library:
        assert quota["category"] in ("安装", "建筑", "拆除")
        assert isinstance(quota["labor"], float)


# ---------------------------------------------------------------------------
# 统计、图纸生成模型、空项目安全
# ---------------------------------------------------------------------------

def test_project_stats(db):
    stats_a = engine.project_stats(db, _pid(db, "DEMO-A"))
    assert stats_a["lines"] == 2
    assert stats_a["poles"] == 16
    assert stats_a["equipment"] == 3
    assert stats_a["quantities"] > 30
    assert abs(stats_a["material_amount"] - 69667.0) < 1e-6
    stats_b = engine.project_stats(db, _pid(db, "DEMO-B"))
    assert stats_b["poles"] == 50


def test_autofill_model_from_drawings(db):
    users = _users(db)
    project_id = _new_project(db, "TEST-AUTOFILL")
    engine.init_project_stages(db, project_id)
    _add_recognized_drawings(db, project_id, 3)
    added = engine.autofill_model(db, project_id, users["zaojia"])
    assert added >= 5
    lines = _rows(db, "SELECT * FROM model_lines WHERE project_id=? ORDER BY id", (project_id,))
    assert len(lines) == 2
    assert abs(lines[0]["length_km"] - 0.057) < 1e-9
    assert abs(lines[1]["length_km"] - 0.7875) < 1e-9
    equipment = _rows(db, "SELECT * FROM model_equipment WHERE project_id=?", (project_id,))
    assert len(equipment) >= 3
    assert any(row["name"] == "配电变压器" for row in equipment)
    civil = _rows(db, "SELECT * FROM model_civil WHERE project_id=?", (project_id,))
    assert any(row["name"] == "台架变基础" for row in civil)
    assert all(issue["level"] != "高" for issue in engine.validate_model(db, project_id))
    # 再次调用不重复生成
    assert engine.autofill_model(db, project_id, users["zaojia"]) == 0
    # 已有完整模型的项目返回 0
    assert engine.autofill_model(db, _pid(db, "DEMO-A"), users["zaojia"]) == 0


def test_empty_project_is_safe(db):
    project_id = _new_project(db, "TEST-EMPTY")
    engine.init_project_stages(db, project_id)
    assert pricing.pricing_summary(db, project_id)["items"] == []
    assert pricing.compare_budget(db, project_id) == []
    assert engine.project_stats(db, project_id)["lines"] == 0
    assert isinstance(engine.validate_model(db, project_id), list)
    result = pricing.compute_pricing(db, project_id, _users(db)["zaojia"])
    assert result["items"] == 0 and result["static_total"] == 0
    summary = pricing.pricing_summary(db, project_id)
    assert summary["items"] == [] and summary["static_total"] == 0
    assert len(pricing.build_export_rows(db, project_id)["summary"]) >= 1
