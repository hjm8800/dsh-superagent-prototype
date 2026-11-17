# -*- coding: utf-8 -*-
"""典型设计（典设）匹配引擎测试（契约2 §6）。

全部使用 pytest 的 tmp_path 建库，不写仓库 data/，不依赖网络。
运行：cd apps/design-cost-workflow ; & $py -m pytest -q tests/test_typdesign.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import engine  # noqa: E402
import seed  # noqa: E402
import typdesign  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    """临时目录建库（v1 演示数据 + v2 典设种子）。"""
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


def _users(conn) -> dict[str, dict]:
    return {row["username"]: row for row in _rows(conn, "SELECT * FROM users")}


def _new_project(conn, code: str = "TEST-TYPD") -> int:
    cursor = conn.execute(
        "INSERT INTO projects (code, name, ptype, stage, location, t0, note) VALUES (?,?,?,?,?,?,?)",
        (code, f"测试项目·{code}", "业扩配套", "图纸解析", "某供电所", "2026-11-17 09:00", "测试项目"),
    )
    conn.commit()
    return int(cursor.lastrowid)


def _audit_actions(conn, project_id: int) -> list[str]:
    return [row["action"] for row in _rows(
        conn, "SELECT action FROM audit WHERE project_id=? ORDER BY id", (project_id,))]


# ---------------------------------------------------------------------------
# 演示种子（契约2 §4.2）
# ---------------------------------------------------------------------------

def test_seed_typdesign_demo_counts(db):
    users = _users(db)

    summary_a = typdesign.typdesign_summary(db, _pid(db, "DEMO-A"))
    assert summary_a["modules"] == 40
    assert len(summary_a["imports"]) == 1
    assert summary_a["imports"][0]["modules"] == 40
    assert summary_a["imports"][0]["filename"]
    assert summary_a["confirmed"] >= 18, "项目A 已确认匹配数应 ≥18"
    assert summary_a["matched"] >= summary_a["confirmed"]
    # 完成态：高置信已确认、中/低置信已忽略、未匹配行=未匹配 → 待确认为 0
    high_rows = [row for row in summary_a["matches"] if row["confidence"] == "高"]
    assert high_rows and all(row["status"] == "已确认" for row in high_rows)
    low_conf_rows = [row for row in summary_a["matches"] if row["confidence"] in ("中", "低")]
    assert low_conf_rows and all(row["status"] == "已忽略" for row in low_conf_rows)
    assert all("低置信未采用" in (row["note"] or "") for row in low_conf_rows)
    unmatched_rows = [row for row in summary_a["matches"] if row["match_type"] == "未匹配"]
    assert unmatched_rows and all(row["status"] == "未匹配" for row in unmatched_rows)
    assert summary_a["pending"] == 0
    assert summary_a["unmatched"] == len(unmatched_rows) > 0
    assert all(row["volume"].startswith("第") for row in summary_a["matches"])
    assert len(summary_a["matches"]) == 40
    assert summary_a["last_run"] == "2026-11-13 10:30"

    summary_b = typdesign.typdesign_summary(db, _pid(db, "DEMO-B"))
    assert summary_b["modules"] == 40
    assert len(summary_b["imports"]) == 1
    assert summary_b["pending"] == 25, "项目B 已匹配行全部待确认"
    assert summary_b["unmatched"] > 0
    assert summary_b["confirmed"] == 0
    matched_b = [row for row in summary_b["matches"] if row["match_type"] != "未匹配"]
    assert all(row["status"] == "待确认" for row in matched_b)
    unmatched_b = [row for row in summary_b["matches"] if row["match_type"] == "未匹配"]
    assert unmatched_b and all(row["status"] == "未匹配" for row in unmatched_b)
    assert summary_b["matched"] == len(matched_b) > 0

    summary_c = typdesign.typdesign_summary(db, _pid(db, "DEMO-C"))
    assert summary_c["modules"] == 40
    assert len(summary_c["imports"]) == 1
    assert summary_c["matches"] == []
    assert summary_c["matched"] == 0 and summary_c["pending"] == 0
    assert summary_c["unmatched"] == 0
    assert summary_c["rate"] == 0.0

    # 图纸 summary 已写入典设编号作为匹配证据（§4.2）
    summaries = {
        row["name"]: row["summary"] or ""
        for row in _rows(db, "SELECT * FROM drawings WHERE project_id=?", (_pid(db, "DEMO-A"),))
    }
    assert "CSG-GD-10K-S1-Z" in summaries["杆塔明细表"]
    assert "CSG-GD-10B-TB-GZ-Z-Ⅰ" in summaries["台架变安装图"]
    assert "CSG-GD-0.4K-HD" in summaries["0.4kV线路平面图"]

    # 典设种子不计入审计动态条数（原契约 §5.5：项目A 6–8 条）
    assert len(_audit_actions(db, _pid(db, "DEMO-A"))) == 8
    assert len(_audit_actions(db, _pid(db, "DEMO-B"))) == 3
    assert len(_audit_actions(db, _pid(db, "DEMO-C"))) == 1


def test_init_db_typdesign_idempotent(tmp_path):
    db_path = tmp_path / "idem" / "workflow.db"
    seed.init_db(str(db_path))
    conn = seed.connect(db_path)
    try:
        counts_before = {
            "modules": conn.execute("SELECT COUNT(*) FROM typdesign_modules").fetchone()[0],
            "imports": conn.execute("SELECT COUNT(*) FROM typdesign_imports").fetchone()[0],
            "matches": conn.execute("SELECT COUNT(*) FROM typdesign_matches").fetchone()[0],
            "drawings": conn.execute("SELECT COUNT(*) FROM drawings").fetchone()[0],
        }
        summary_before = (len(typdesign.confirmed_modules(conn, _pid(conn, "DEMO-A"))),
                          typdesign.typdesign_summary(conn, _pid(conn, "DEMO-A"))["confirmed"])
    finally:
        conn.close()

    seed.init_db(str(db_path))
    conn = seed.connect(db_path)
    try:
        counts_after = {
            "modules": conn.execute("SELECT COUNT(*) FROM typdesign_modules").fetchone()[0],
            "imports": conn.execute("SELECT COUNT(*) FROM typdesign_imports").fetchone()[0],
            "matches": conn.execute("SELECT COUNT(*) FROM typdesign_matches").fetchone()[0],
            "drawings": conn.execute("SELECT COUNT(*) FROM drawings").fetchone()[0],
        }
        summary_after = (len(typdesign.confirmed_modules(conn, _pid(conn, "DEMO-A"))),
                         typdesign.typdesign_summary(conn, _pid(conn, "DEMO-A"))["confirmed"])
        draw_summary = conn.execute(
            "SELECT summary FROM drawings WHERE project_id=? AND name='杆塔明细表'",
            (_pid(conn, "DEMO-A"),),
        ).fetchone()["summary"]
    finally:
        conn.close()

    assert counts_after == counts_before
    assert counts_after["modules"] == 120  # 3 项目 × 40 条
    assert counts_after["imports"] == 3
    assert summary_after == summary_before
    # 证据编号只追加一次
    assert draw_summary.count("CSG-GD-10K-S1-Z") == 1


def test_init_db_upgrades_existing_v1_database(tmp_path):
    """模拟已存在的 v1 数据库（无 typdesign 三张表）：init_db 建表并补种，不重复。"""
    db_path = tmp_path / "upgrade" / "workflow.db"
    seed.init_db(str(db_path))
    conn = seed.connect(db_path)
    try:
        project_a = _pid(conn, "DEMO-A")
        conn.executescript(
            "DROP TABLE typdesign_matches; DROP TABLE typdesign_modules; DROP TABLE typdesign_imports;"
        )
        conn.commit()
    finally:
        conn.close()

    seed.init_db(str(db_path))
    conn = seed.connect(db_path)
    try:
        summary = typdesign.typdesign_summary(conn, _pid(conn, "DEMO-A"))
        assert summary["modules"] == 40
        assert summary["confirmed"] >= 18
        assert len(summary["imports"]) == 1
        assert len(_audit_actions(conn, _pid(conn, "DEMO-A"))) == 8
        project_a = _pid(conn, "DEMO-A")
        again = typdesign.typdesign_summary(conn, project_a)
        assert again["modules"] == 40
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 匹配与统计
# ---------------------------------------------------------------------------

def test_match_project_project_c(db):
    project_id = _pid(db, "DEMO-C")
    # 传 sqlite3.Row 也应可用（兼容 Web 层直接拿 Row 调用）
    admin_row = db.execute("SELECT * FROM users WHERE username='admin'").fetchone()
    result = typdesign.match_project(db, project_id, admin_row)
    assert result["modules"] == 40
    assert result["matched"] > 0
    assert result["high"] > 0
    assert result["pending"] == 15, "C 执行匹配后已匹配行待确认，未匹配行不计入待确认"
    assert result["confirmed"] == 0
    assert result["unmatched"] == 40 - result["matched"]

    summary = typdesign.typdesign_summary(db, project_id)
    assert summary["matched"] == result["matched"] == summary["high"] + summary["medium"] + summary["low"]
    assert summary["pending"] == result["matched"]
    assert summary["unmatched"] == 40 - result["matched"]
    assert summary["rate"] == round(result["matched"] / 40 * 100.0, 1)
    matched_codes = {row["code"] for row in summary["matches"] if row["match_type"] != "未匹配"}
    assert {"CSG-GD-10B-TB-GZ-Z-Ⅰ", "CSG-GD-10K-S1-Z", "CSG-GD-0.4K-HD"} <= matched_codes
    evidence = " ".join(row["evidence"] for row in summary["matches"])
    assert "杆塔明细表" in evidence or "台架变安装图" in evidence or "附件3施工图" in evidence
    assert all(len(row["evidence"]) <= 200 for row in summary["matches"])
    # 未匹配行也存在，便于页面筛选；状态为“未匹配”而非“待确认”
    assert any(row["match_type"] == "未匹配" and row["status"] == "未匹配"
               for row in summary["matches"])
    assert all(row["status"] == "待确认" for row in summary["matches"]
               if row["match_type"] != "未匹配")
    assert "执行典型设计匹配" in _audit_actions(db, project_id)


def test_confirm_ignore_and_stats_consistency(db):
    users = _users(db)
    project_id = _pid(db, "DEMO-C")
    typdesign.match_project(db, project_id, users["zaojia"])
    summary = typdesign.typdesign_summary(db, project_id)
    matched = [row for row in summary["matches"] if row["match_type"] != "未匹配"]
    target, second, third = matched[0], matched[1], matched[2]

    ok, message = typdesign.confirm_match(db, project_id, target["id"], users["zaojia"],
                                          qty=2, note="演示确认应用")
    assert ok is True and "已确认" in message
    stats = typdesign.typdesign_stats(db, project_id)
    assert stats["confirmed"] == 1
    assert stats["pending"] == summary["pending"] - 1
    updated = next(row for row in typdesign.typdesign_summary(db, project_id)["matches"]
                   if row["id"] == target["id"])
    assert updated["status"] == "已确认" and updated["qty"] == 2
    assert updated["note"] == "演示确认应用"
    assert updated["code"] in {row["code"] for row in typdesign.confirmed_modules(db, project_id)}

    ok, _ = typdesign.confirm_match(db, project_id, second["id"], users["zaojia"],
                                    status="已忽略")
    assert ok is True
    summary2 = typdesign.typdesign_summary(db, project_id)
    assert summary2["ignored"] == 1
    assert summary2["pending"] == summary["pending"] - 2  # 已确认 1 + 已忽略 1
    assert summary2["unmatched"] == summary["unmatched"]  # 未匹配行不受人工操作影响
    assert typdesign.typdesign_stats(db, project_id)["pending"] == summary2["pending"]

    # 重置回待确认
    ok, _ = typdesign.confirm_match(db, project_id, third["id"], users["zaojia"],
                                    status="待确认")
    assert ok is True

    # 非法参数
    ok, message = typdesign.confirm_match(db, project_id, target["id"], users["zaojia"], status="乱写")
    assert ok is False and "状态" in message
    ok, message = typdesign.confirm_match(db, project_id, 999999, users["zaojia"])
    assert ok is False and "不存在" in message
    # 确认/忽略写审计
    actions = _audit_actions(db, project_id)
    assert "确认典型设计应用" in actions and "忽略典型设计匹配" in actions


def test_match_project_preserves_confirmed_status(db):
    project_id = _pid(db, "DEMO-A")
    before = typdesign.confirmed_modules(db, project_id)
    assert len(before) >= 18
    rows_before = {(row["id"], row["status"])
                   for row in typdesign.typdesign_summary(db, project_id)["matches"]}
    typdesign.match_project(db, project_id, _users(db)["admin"])
    after = typdesign.confirmed_modules(db, project_id)
    summary_after = typdesign.typdesign_summary(db, project_id)
    assert len(after) == len(before)
    assert {row["code"] for row in after} == {row["code"] for row in before}
    # 已确认/已忽略状态在重新匹配后沿用
    status_by_code = {row["code"]: row["status"] for row in summary_after["matches"]}
    for row in before:
        assert status_by_code[row["code"]] == "已确认"
    # 中/低置信的“已忽略”状态同样沿用；未匹配行仍为未匹配；待确认保持 0
    assert summary_after["pending"] == 0
    assert summary_after["ignored"] > 0
    assert summary_after["unmatched"] > 0


def test_name_inference_match(db):
    project_id = _new_project(db, "TEST-NAME")
    db.execute(
        "INSERT INTO drawings (project_id, code, name, category, fmt, sheet_count, status, summary) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (project_id, "T-01", "台架变安装图", "安装图", "DWG", 1, "已识别",
         "识别：台架变围网图1套，围网高度1.8m"),
    )
    for code, name, unit in (
        ("CSG-GD-10B-HWG-WF", "台架变围网图", "套"),
        ("CSG-GD-10B-TB-CK-PC", "台变操作平台图", "处"),
    ):
        db.execute(
            "INSERT INTO typdesign_modules (project_id, volume, chapter, category, code, name, unit, note) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (project_id, "第三册 台架变", "", "台架变", code, name, unit, ""),
        )
    db.commit()
    result = typdesign.match_project(db, project_id, _users(db)["admin"])
    assert result["matched"] >= 1 and result["low"] >= 1
    assert result["unmatched"] >= 1
    matches = typdesign.typdesign_summary(db, project_id)["matches"]
    net = next(row for row in matches if row["code"] == "CSG-GD-10B-HWG-WF")
    assert net["match_type"] == "名称推断" and net["confidence"] == "低"
    assert net["status"] == "待确认"
    assert "名称匹配" in net["evidence"]
    platform = next(row for row in matches if row["code"] == "CSG-GD-10B-TB-CK-PC")
    assert platform["match_type"] == "未匹配" and platform["status"] == "未匹配"


# ---------------------------------------------------------------------------
# 编号算法单测
# ---------------------------------------------------------------------------

def test_normalize_and_code_variants():
    assert typdesign.normalize_code("  csg-gd-10b-tb-gt-sb ") == "CSG-GD-10B-TB-GT-SB"
    assert typdesign.normalize_code("CSG_GD_10B_TB_GT_SB") == "CSG-GD-10B-TB-GT-SB"
    variants = typdesign.code_variants("CSG-GD-10B-TB-GT-SB-01")
    assert "CSG-GD-10B-TB-GT-SB-01" in variants
    assert "CSG-GD-10B-TB-GT-SB" in variants
    roman_variants = typdesign.code_variants("CSG-GD-10B-TB-GZ-Z-Ⅱ")
    assert "CSG-GD-10B-TB-GZ-Z" in roman_variants


def test_code_match_scores():
    module = "CSG-GD-10B-TB-GT-SB"
    assert typdesign.code_match(module, module) == 100
    assert typdesign.code_match("CSG-GD-10B-TB-GT-SB-01", module) == 90      # 图号序列
    assert typdesign.code_match("CSG-GD-10B-TB-GT-SB1", module) == 85        # 编号变体
    assert typdesign.code_match("CSG-GD-10B-TB-GZ-Z-Ⅱ", "CSG-GD-10B-TB-GZ-Z-Ⅰ") == 85  # 罗马序
    assert typdesign.code_match("CSG-GD-10B-TB", "CSG-GD-10B-TB-GT-SB") == 78  # 目录比图号细
    assert typdesign.code_match("CSG-GD-10B-TB-GT-SB", "CSG-GD-10B-TB-GT-SB×2") == 76
    assert typdesign.code_match("CSG-10B-TB-GT-SB", module) == 88            # CSG/CSG-GD 前缀差异
    # 电压标签守卫
    assert typdesign.code_match("CSG-GD-20B-TB-GZ-Z-Ⅱ", "CSG-GD-10B-TB-GZ-Z-Ⅰ") == 0
    assert typdesign.code_match("CSG-GD-0.4K-HD", "CSG-GD-10K-S1-Z") == 0
    assert typdesign.code_match("CSG-GD-10K-S1-Z", "CSG-GD-10B-TB-GT-SB") == 0
    assert typdesign.code_match("", module) == 0


def test_name_match_helper():
    assert typdesign.name_match("台架变围网图", "识别：台架变围网图1套") >= 4
    assert typdesign.name_match("台架变围网图", "无关文本") == 0
    assert typdesign.name_match("短", "短") == 0  # 名称过短不推断


# ---------------------------------------------------------------------------
# 目录文件解析
# ---------------------------------------------------------------------------

def _write_catalog_xlsx(path: Path, rows: list[tuple]) -> None:
    """生成附件6-1 风格双 sheet 工作簿（V3.1 汇总表优先）。"""
    workbook = Workbook()
    fallback = workbook.active
    fallback.title = "标准设计应用情况统计表"
    fallback.append(["标准设计应用情况统计表"])
    fallback.append(["序号", "卷册", "篇", "类别", "模块编号", "模块名称", "模块单位", "应用数量"])
    fallback.append([1, "第二册 其它", "", "其它", "CSG-GD-99-XX", "不应被优先解析的模块", "套", 0])

    sheet = workbook.create_sheet("V3.1模块汇总表（参考）")
    sheet.append(["V3.1模块汇总表（参考）"])
    sheet.append(["序号", "卷册", "篇", "类别", "模块编号", "模块名称", "模块单位"])
    for index, (volume, category, code, name, unit) in enumerate(rows, 1):
        sheet.append([index, volume, "", category, code, name, unit])
    workbook.save(path)


def test_parse_catalog_file_xlsx_two_sheets(tmp_path):
    rows = [(volume, category, code, name, unit)
            for volume, _chapter, category, code, name, unit in seed._TYP_DESIGN_CATALOG]
    path = tmp_path / "附件6-1目录.xlsx"
    _write_catalog_xlsx(path, rows)
    modules = typdesign.parse_catalog_file(str(path), "附件6-1目录.xlsx")
    assert len(modules) == 40
    assert {module["code"] for module in modules} == {row[2] for row in rows}
    assert modules[0]["volume"].startswith("第")
    assert all(module["category"] for module in modules)
    assert all(module["code"] != "CSG-GD-99-XX" for module in modules)  # 优先 V3.1 表


def test_parse_catalog_file_csv_encodings(tmp_path):
    header = "序号,卷册,类别,模块编号,模块名称,模块单位\n"
    body = (
        "1,第三册 台架变,台架变,CSG-GD-10B-TB-GT-SB,配电箱系统结线图,套\n"
        "2,第三册 台架变,台架变,CSG-GD-10B-TB-GT-JD,台架变地网图,处\n"
        "3,第三册 台架变,台架变,CSG-GD-10B-TB-GT-SB,重复行应去重,套\n"
    )
    utf8_path = tmp_path / "catalog_utf8.csv"
    utf8_path.write_bytes((header + body).encode("utf-8"))
    gbk_path = tmp_path / "catalog_gbk.csv"
    gbk_path.write_bytes((header + body).encode("gbk"))
    for path in (utf8_path, gbk_path):
        modules = typdesign.parse_catalog_file(str(path), path.name)
        assert len(modules) == 2  # 去重
        assert modules[0]["code"] == "CSG-GD-10B-TB-GT-SB"
        assert modules[0]["volume"] == "第三册 台架变"
        assert modules[0]["unit"] == "套"


def test_import_catalog_replaces_and_audits(db, tmp_path):
    users = _users(db)
    project_id = _pid(db, "DEMO-C")
    path = tmp_path / "small.csv"
    path.write_bytes(
        ("序号,卷册,类别,模块编号,模块名称,模块单位\n"
         "1,第三册 台架变,台架变,CSG-GD-10B-TB-GT-SB,配电箱系统结线图,套\n"
         "2,第三册 台架变,台架变,CSG-GD-10B-TB-GT-JD,台架变地网图,处\n"
         "3,第三册 台架变,台架变,CSG-GD-10B-HWG-WF,台架变围网图,套\n").encode("utf-8")
    )
    result = typdesign.import_catalog(db, project_id, str(path), "small.csv", users["zaojia"])
    assert result["ok"] is True and result["modules"] == 3
    summary = typdesign.typdesign_summary(db, project_id)
    assert summary["modules"] == 3
    assert len(summary["imports"]) == 2  # 演示导入 + 本次上传
    assert summary["imports"][0]["filename"] == "small.csv"
    assert summary["matches"] == []  # 目录重建后旧匹配清空
    assert "导入典型设计目录" in _audit_actions(db, project_id)

    bad_path = tmp_path / "bad.txt"
    bad_path.write_text("not a catalog", encoding="utf-8")
    result = typdesign.import_catalog(db, project_id, str(bad_path), "bad.txt", users["zaojia"])
    assert result["ok"] is False and result["modules"] == 0
    assert typdesign.typdesign_summary(db, project_id)["modules"] == 3  # 失败不清空旧目录


# ---------------------------------------------------------------------------
# 导出与工作流集成
# ---------------------------------------------------------------------------

def test_export_rows(db):
    project_id = _pid(db, "DEMO-A")
    rows = typdesign.export_rows(db, project_id)
    modules = typdesign.typdesign_summary(db, project_id)["modules"]
    assert len(rows) == modules + 1
    assert rows[0][:6] == ["序号", "卷册", "类别", "模块编号", "模块名称", "模块单位"]
    assert all(len(row) == len(typdesign.EXPORT_HEADER) for row in rows)
    body = rows[1:]
    assert all(row[3] for row in body)  # 模块编号
    assert any(row[9] == "已确认" for row in body)
    # 导出按卷册排序，第一行应为“第一册 配电站”
    assert body[0][1].startswith("第一册")


def test_project_stats_includes_typdesign(db):
    stats = engine.project_stats(db, _pid(db, "DEMO-A"))
    assert stats["typdesign_modules"] == 40
    assert stats["typdesign_confirmed"] >= 18
    assert stats["typdesign_pending"] >= 0
    assert 0 <= stats["typdesign_rate"] <= 100
    # 原有统计键保持
    assert stats["lines"] == 2 and stats["poles"] == 16
