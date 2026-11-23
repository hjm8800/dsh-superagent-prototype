#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""e2e_check.py — 「设计图纸→建模→算量→计价」原型 HTTP 端到端检查（项目C）。

用法（在仓库根或 apps/design-cost-workflow 下运行；Windows 如 python 为应用商店别名，请改用完整解释器路径）：
  python ^
      apps/design-cost-workflow/scripts/e2e_check.py [--base-url http://127.0.0.1:8091]

前置条件：应用已运行（WORKFLOW_DEMO=1），项目C 处于干净 seed 状态（图纸解析/进行中）。
脚本以真实登录会话驱动：造价员(zaojia) 提交各阶段 → 复核员(fuhe) 复核通过，
完整走通 图纸解析 → 建模 → 算量 → 计价 → 导出 → 对比，并只读查库校验最终状态。
第二轮（v2）追加“典型设计（典设）匹配”验证：GET /project/3/typdesign →
POST match（复核员 403 越权校验）→ POST confirm 一项 → GET export.xlsx →
/project/3/compare 送审条件与风险提示 → 只读查库校验匹配/确认计数与 A/B 演示种子。
第三轮（v3）追加“工作流开始入口/新建项目”验证：GET /project/new 200 →
POST /project/new（唯一名称）→ 跳转新项目图纸解析页 → 项目页含“下一步” →
只读查库校验新项目 stage=图纸解析、四阶段初始化、成员与审计 → 工作台“开始工作流”引导。

注意：本脚本会修改演示数据库（项目C 四阶段状态 + 典设匹配确认状态 + 新增一个演示项目），
属预期行为；运行前请确认 C 项目为干净状态。输出：PASS/FAIL 明细表 + 汇总；全部通过返回 0。
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

try:  # 中文输出在 GBK 控制台/重定向时都尽量正常
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

import httpx

APP = Path(__file__).resolve().parents[1]      # apps/design-cost-workflow
ROOT = APP.parents[1]                           # 仓库根
DEFAULT_DB = APP / "data" / "workflow.db"
PID = 3                                          # 项目C
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(condition), detail))
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}" + (f"  —— {detail}" if detail else ""))
    return bool(condition)


def login(username: str, password: str) -> httpx.Client:
    client = httpx.Client(base_url=BASE, follow_redirects=True, timeout=30)
    response = client.post("/login", data={"username": username, "password": password})
    check(f"登录 {username}", response.status_code == 200 and "项目A" in response.text,
          f"HTTP {response.status_code}，已建立会话")
    return client


def post(client: httpx.Client, url: str, data=None) -> httpx.Response:
    return client.post(url, data=data or {})


def db_query(sql: str, params=()):
    """只读查询演示库（WAL/占用时重试一次）。"""
    import time

    for attempt in range(3):
        try:
            con = sqlite3.connect(DEFAULT_DB.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
            con.row_factory = sqlite3.Row
            rows = [dict(r) for r in con.execute(sql, params)]
            con.close()
            return rows
        except sqlite3.Error:
            if attempt == 2:
                raise
            time.sleep(0.4)
    return []


def stage_status(key: str) -> str:
    rows = db_query("SELECT status FROM stages WHERE project_id=? AND key=?", (PID, key))
    return rows[0]["status"] if rows else "（无数据）"


def check_stage(name: str, expected: dict) -> None:
    for key, want in expected.items():
        got = stage_status(key)
        check(name, got == want, f"{key} 阶段状态={got}（期望 {want}）")


def main(argv=None) -> int:
    global BASE, DEFAULT_DB
    parser = argparse.ArgumentParser(description="设计图纸→建模→算量→计价 原型 E2E HTTP 检查")
    parser.add_argument("--base-url", default="http://127.0.0.1:8091")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="演示数据库路径（只读校验用）")
    parser.add_argument("--typdesign-only", action="store_true",
                        help="只执行 v2 典型设计匹配检查（用于四阶段流程已走完后的复验）")
    args = parser.parse_args(argv)
    BASE = args.base_url.rstrip("/")
    DEFAULT_DB = args.db
    if not DEFAULT_DB.exists():
        print(f"[错误] 演示数据库不存在：{DEFAULT_DB}")
        return 2

    print("=" * 80)
    print("设计图纸 → 建模 → 算量 → 计价 · 端到端检查（项目C）")
    print(f"  应用地址：{BASE}")
    print(f"  演示数据库：{DEFAULT_DB}")
    print("=" * 80)

    with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as anon:
        response = anon.get("/health")
        check("健康检查 /health", response.status_code == 200 and response.json().get("ok") is True,
              f"HTTP {response.status_code}")
        response = anon.get(f"/project/{PID}")
        check("未登录访问项目页被重定向到登录页",
              "/login" in str(response.url) or "登录" in response.text,
              f"最终 URL：{response.url}")

    # ---------- 1. 图纸解析阶段：提交 → 复核 ----------
    zaojia = login("zaojia", "123456")
    fuhe = login("fuhe", "123456")
    try:
        if args.typdesign_only:
            print("[模式] 仅执行 v2 典型设计匹配检查（跳过四阶段流程，用于流程已走完后的复验）")
        else:
            initial = stage_status("drawings")
            check("项目C 初始图纸阶段为“进行中”", initial == "进行中", f"当前={initial}")

            response = post(zaojia, f"/project/{PID}/stage/drawings/submit")
            check("提交图纸阶段复核", response.status_code == 200, f"HTTP {response.status_code}")
            check_stage("图纸阶段进入待复核", {"drawings": "待复核"})

            response = post(fuhe, f"/project/{PID}/stage/drawings/review",
                            {"result": "通过", "comment": "E2E：图纸识别结果完整，复核通过"})
            check("复核通过图纸阶段", response.status_code == 200, f"HTTP {response.status_code}")
            check_stage("图纸完成、建模启动", {"drawings": "已完成", "model": "进行中"})

            # 复核门禁：非“待复核”阶段不能复核
            response = post(fuhe, f"/project/{PID}/stage/drawings/review", {"result": "通过", "comment": "重复复核"})
            stage_now = stage_status("drawings")
            check("已完成阶段不可重复复核（门禁生效）", stage_now == "已完成", f"状态仍为 {stage_now}")

            # ---------- 2. 建模阶段：生成初稿 → 校验 → 提交 → 复核 ----------
            response = post(zaojia, f"/project/{PID}/model/autofill")
            lines = db_query("SELECT COUNT(*) AS n FROM model_lines WHERE project_id=?", (PID,))
            check("由图纸生成建模初稿", response.status_code == 200 and lines and lines[0]["n"] > 0,
                  f"线路 {lines[0]['n'] if lines else 0} 条")

            response = post(zaojia, f"/project/{PID}/model/validate")
            check("模型校验执行", response.status_code == 200 and "校验完成" in response.text,
                  "页面含“校验完成”提示")

            response = post(zaojia, f"/project/{PID}/stage/model/submit")
            check("提交建模阶段复核", response.status_code == 200, f"HTTP {response.status_code}")
            check_stage("建模阶段进入待复核", {"model": "待复核"})

            response = post(fuhe, f"/project/{PID}/stage/model/review",
                            {"result": "通过", "comment": "E2E：模型与图纸一致，复核通过"})
            check("复核通过建模阶段", response.status_code == 200, f"HTTP {response.status_code}")
            check_stage("建模完成、算量启动", {"model": "已完成", "quantities": "进行中"})

            # ---------- 3. 算量阶段：重算 → 提交 → 复核 ----------
            response = post(zaojia, f"/project/{PID}/quantities/compute")
            qty = db_query("SELECT COUNT(*) AS n FROM quantities WHERE project_id=?", (PID,))
            compare = db_query("SELECT COUNT(*) AS n FROM compare_materials WHERE project_id=?", (PID,))
            check("按规则重算工程量", response.status_code == 200 and qty and qty[0]["n"] > 0,
                  f"清单 {qty[0]['n'] if qty else 0} 行，材料对比 {compare[0]['n'] if compare else 0} 行")

            response = post(zaojia, f"/project/{PID}/stage/quantities/submit")
            check("提交算量阶段复核", response.status_code == 200, f"HTTP {response.status_code}")
            check_stage("算量阶段进入待复核", {"quantities": "待复核"})

            response = post(fuhe, f"/project/{PID}/stage/quantities/review",
                            {"result": "通过", "comment": "E2E：工程量计算式与依据完整，复核通过"})
            check("复核通过算量阶段", response.status_code == 200, f"HTTP {response.status_code}")
            check_stage("算量完成、计价启动", {"quantities": "已完成", "pricing": "进行中"})

            # ---------- 4. 计价阶段：计价 → 提交 → 复核 ----------
            response = post(zaojia, f"/project/{PID}/pricing/compute")
            fees = db_query("SELECT COUNT(*) AS n FROM pricing_fees WHERE project_id=?", (PID,))
            check("按示例定额库与 2022 版费率计价", response.status_code == 200 and fees and fees[0]["n"] > 0,
                  f"费用汇总 {fees[0]['n'] if fees else 0} 行")

            response = post(zaojia, f"/project/{PID}/stage/pricing/submit")
            check("提交计价阶段复核", response.status_code == 200, f"HTTP {response.status_code}")
            check_stage("计价阶段进入待复核", {"pricing": "待复核"})

            response = post(fuhe, f"/project/{PID}/stage/pricing/review",
                            {"result": "通过", "comment": "E2E：费率与费用构成复核通过"})
            check("复核通过计价阶段", response.status_code == 200, f"HTTP {response.status_code}")
            check_stage("四阶段全部完成", {"drawings": "已完成", "model": "已完成",
                                           "quantities": "已完成", "pricing": "已完成"})

            # ---------- 5. 页面与导出 ----------
            response = zaojia.get(f"/project/{PID}/pricing")
            static_rows = db_query(
                "SELECT amount FROM pricing_fees WHERE project_id=? AND category='静态投资' ORDER BY id DESC LIMIT 1",
                (PID,))
            static_total = float(static_rows[0]["amount"]) if static_rows else 0.0
            check("计价页返回 200 且含费用汇总", response.status_code == 200 and "费用汇总" in response.text,
                  f"HTTP {response.status_code}，静态投资 {static_total:,.2f} 元")
            check("静态投资 > 0", static_total > 0, f"{static_total:,.2f} 元")

            for label, url in (("工程量清单", f"/project/{PID}/quantities/export.xlsx"),
                               ("施工图预算", f"/project/{PID}/pricing/export.xlsx")):
                response = zaojia.get(url)
                ctype = response.headers.get("content-type", "")
                ok = response.status_code == 200 and "spreadsheet" in ctype and len(response.content) > 3000
                detail = f"HTTP {response.status_code}，{len(response.content):,} bytes，{ctype}"
                if ok:
                    try:
                        from openpyxl import load_workbook
                        wb = load_workbook(io.BytesIO(response.content), read_only=True)
                        ok = bool(wb.sheetnames) and all(
                            (wb[name].max_row or 0) > 2 for name in wb.sheetnames)
                        detail += f"，工作表：{'/'.join(wb.sheetnames)}"
                    except Exception as exc:  # noqa: BLE001
                        ok = False
                        detail += f"，xlsx 解析失败：{exc}"
                check(f"导出{label} xlsx 可下载且内容有效", ok, detail)

            response = zaojia.get(f"/project/{PID}/compare")
            check("对比页返回 200 且含材料量/预算对比",
                  response.status_code == 200 and ("材料量对比" in response.text or "预算对比" in response.text),
                  f"HTTP {response.status_code}")
            check("对比页含差异说明", "差异" in response.text, "页面含“差异”")

            # ---------- 6. 只读数据库最终校验 ----------
            stages = {r["key"]: r["status"] for r in db_query(
                "SELECT key, status FROM stages WHERE project_id=?", (PID,))}
            check("数据库四阶段状态均为“已完成”",
                  all(stages.get(k) == "已完成" for k in ("drawings", "model", "quantities", "pricing")),
                  "、".join(f"{k}={v}" for k, v in stages.items()))
            budget = db_query("SELECT COUNT(*) AS n FROM compare_budget WHERE project_id=?", (PID,))
            check("预算对比表已生成", budget and budget[0]["n"] > 0,
                  f"对比行 {budget[0]['n'] if budget else 0} 行")
            audit = db_query("SELECT COUNT(*) AS n FROM audit WHERE project_id=?", (PID,))
            check("审计日志记录完整", audit and audit[0]["n"] >= 10,
                  f"审计 {audit[0]['n'] if audit else 0} 条")

        # ---------- 7. 典型设计（典设）匹配（第二轮 v2，contract2 §5/§7） ----------
        try:
            response = zaojia.get(f"/project/{PID}/typdesign")
            # 未执行匹配时页面为空态/目录态，可能还没有模块编号行；因此接受目录/表头/编号任一证据
            has_page_evidence = any(k in response.text for k in ("CSG-GD", "典设目录", "模块编号", "导入记录"))
            check("典型设计匹配页返回 200 且含目录/模块编号",
                  response.status_code == 200 and "典型设计" in response.text and has_page_evidence,
                  f"HTTP {response.status_code}")

            modules = db_query("SELECT COUNT(*) AS n FROM typdesign_modules WHERE project_id=?", (PID,))
            imports = db_query("SELECT COUNT(*) AS n FROM typdesign_imports WHERE project_id=?", (PID,))
            module_count = modules[0]["n"] if modules else 0
            import_count = imports[0]["n"] if imports else 0
            check("项目C 典设目录已导入（演示目录 40 个模块）",
                  module_count == 40 and import_count >= 1,
                  f"目录 {module_count} 个模块 / 导入记录 {import_count} 条")

            response = post(fuhe, f"/project/{PID}/typdesign/match")
            check("复核员无“执行匹配”权限（403）", response.status_code == 403,
                  f"HTTP {response.status_code}")

            response = post(zaojia, f"/project/{PID}/typdesign/match")
            matched_rows = db_query(
                "SELECT COUNT(*) AS matched, "
                "SUM(CASE WHEN confidence='高' THEN 1 ELSE 0 END) AS high "
                "FROM typdesign_matches WHERE project_id=? AND match_type<>'未匹配'", (PID,))
            matched = matched_rows[0]["matched"] if matched_rows else 0
            high = int(matched_rows[0]["high"] or 0) if matched_rows else 0
            check("执行典型设计匹配（项目C）", response.status_code == 200 and matched > 0,
                  f"已匹配 {matched} 个模块，高置信 {high} 个")
            check("匹配置信度含高置信结果", high > 0, f"高置信 {high} 个")
            check("匹配页展示匹配统计", "匹配" in response.text, "页面含匹配统计")

            evidence_rows = db_query(
                "SELECT COUNT(*) AS n FROM typdesign_matches WHERE project_id=? "
                "AND (evidence LIKE '%附件%' OR evidence LIKE '%杆塔明细表%' OR evidence LIKE '%平面图%' "
                "OR evidence LIKE '%说明书%')", (PID,))
            evidence_count = evidence_rows[0]["n"] if evidence_rows else 0
            check("匹配证据可追溯到图纸来源", evidence_count > 0,
                  f"{evidence_count} 条匹配带图纸来源证据")

            pending = db_query(
                "SELECT id, code FROM typdesign_matches WHERE project_id=? AND status='待确认' "
                "ORDER BY id LIMIT 1", (PID,))
            target = pending[0] if pending else (
                db_query("SELECT id, code FROM typdesign_matches WHERE project_id=? "
                         "AND match_type<>'未匹配' ORDER BY id LIMIT 1", (PID,)) or [{"id": 0, "code": "（无匹配项）"}])[0]
            target_id = int(target["id"])
            target_code = target["code"]
            response = post(zaojia, f"/project/{PID}/typdesign/{target_id}/confirm",
                            {"qty": "1", "note": "E2E 自动确认"})
            confirmed = db_query("SELECT status, qty FROM typdesign_matches WHERE id=?", (target_id,))
            status_ok = bool(confirmed) and confirmed[0]["status"] == "已确认"
            check(f"人工确认典设模块（{target_code}）", response.status_code == 200 and status_ok,
                  f"状态={confirmed[0]['status'] if confirmed else '（无数据）'}，"
                  f"qty={confirmed[0]['qty'] if confirmed else '—'}")
            check("确认后页面含“已确认”", "已确认" in response.text, "页面含“已确认”")

            response = zaojia.get(f"/project/{PID}/typdesign/export.xlsx")
            ctype = response.headers.get("content-type", "")
            export_ok = (response.status_code == 200 and "spreadsheet" in ctype
                         and len(response.content) > 2000)
            sheet_detail = ""
            if export_ok:
                try:
                    from openpyxl import load_workbook
                    wb = load_workbook(io.BytesIO(response.content), read_only=True)
                    sheet = wb["典型设计匹配清单"] if "典型设计匹配清单" in wb.sheetnames \
                        else wb[wb.sheetnames[0]]
                    rows_in_sheet = list(sheet.iter_rows(values_only=True))
                    header_idx = next(
                        (i for i, row in enumerate(rows_in_sheet)
                         if any(str(cell).strip() == "模块编号" for cell in row if cell)), None)
                    module_rows = 0
                    if header_idx is not None:
                        code_col = next(
                            i for i, cell in enumerate(rows_in_sheet[header_idx])
                            if str(cell or "").strip() == "模块编号")
                        module_rows = sum(
                            1 for row in rows_in_sheet[header_idx + 1:]
                            if row[code_col] and str(row[code_col]).strip().startswith("CSG"))
                    export_ok = (header_idx is not None and module_rows == module_count
                                 and "典型设计" in "".join(wb.sheetnames))
                    sheet_detail = (f"，工作表：{'/'.join(wb.sheetnames)}，"
                                    f"表头第 {header_idx + 1 if header_idx is not None else 0} 行，"
                                    f"模块数据 {module_rows} 行，总行数 {len(rows_in_sheet)}")
                except Exception as exc:  # noqa: BLE001
                    export_ok = False
                    sheet_detail = f"，xlsx 解析失败：{exc}"
            check("导出典型设计匹配清单 xlsx 可下载且行数正确", export_ok,
                  f"HTTP {response.status_code}，{len(response.content):,} bytes{sheet_detail}")

            response = zaojia.get(f"/project/{PID}/compare")
            check("对比页含典型设计送审条件",
                  response.status_code == 200 and "典型设计" in response.text,
                  f"HTTP {response.status_code}")
            check("对比页提示典型设计确认/待确认风险",
                  ("待确认" in response.text) or ("已确认" in response.text),
                  "页面含典型设计确认状态")

            confirmed_c = db_query(
                "SELECT COUNT(*) AS n FROM typdesign_matches WHERE project_id=? AND status='已确认'",
                (PID,))
            check("典设确认计数写入数据库", bool(confirmed_c) and confirmed_c[0]["n"] >= 1,
                  f"项目C 已确认 {confirmed_c[0]['n'] if confirmed_c else 0} 个模块")

            confirmed_a = db_query(
                "SELECT COUNT(*) AS n FROM typdesign_matches WHERE project_id=1 AND status='已确认'")
            pending_b = db_query(
                "SELECT COUNT(*) AS n FROM typdesign_matches WHERE project_id=2 AND status='待确认'")
            check("项目A 典设匹配完成态（已确认 ≥18）",
                  bool(confirmed_a) and confirmed_a[0]["n"] >= 18,
                  f"已确认 {confirmed_a[0]['n'] if confirmed_a else 0} 个")
            check("项目B 典设匹配演示态（待确认 ≥12）",
                  bool(pending_b) and pending_b[0]["n"] >= 12,
                  f"待确认 {pending_b[0]['n'] if pending_b else 0} 个")
        except Exception as exc:  # noqa: BLE001 - v2 未就绪时给出 FAIL 而不是中断
            check("典型设计匹配（v2）E2E 执行", False, f"异常：{exc}")

        # ---------- 8. 工作流开始入口与新建项目（第三轮 v3，contract3 §5） ----------
        try:
            response = zaojia.get("/project/new")
            check("新建项目表单页 GET /project/new 200",
                  response.status_code == 200 and "新建项目" in response.text and "项目名称" in response.text,
                  f"HTTP {response.status_code}")

            new_name = f"E2E新建项目-{datetime.now().strftime('%H%M%S')}"
            response = post(zaojia, "/project/new", {
                "name": new_name, "ptype": "业扩配套", "location": "某供电所",
                "note": "E2E 自动创建（v3 新建项目校验）",
            })
            final_url = str(response.url)
            url_match = re.search(r"/project/(\d+)/drawings", final_url)
            new_pid = int(url_match.group(1)) if url_match else 0
            check("POST /project/new 创建项目并跳转图纸解析页",
                  response.status_code == 200 and bool(url_match),
                  f"HTTP {response.status_code}，最终 URL {final_url}")
            check("新项目图纸解析页含“图纸解析/提交复核”",
                  response.status_code == 200 and "图纸解析" in response.text and "提交复核" in response.text,
                  "页面含“图纸解析/提交复核”")

            response = post(fuhe, "/project/new", {"name": "越权新建项目-不应创建",
                                                    "ptype": "台区改造", "location": "某供电所", "note": ""})
            check("复核员无新建项目权限（403）", response.status_code == 403, f"HTTP {response.status_code}")

            if new_pid:
                project = db_query("SELECT name, stage FROM projects WHERE id=?", (new_pid,))
                check("新建项目已入库（stage=图纸解析）",
                      bool(project) and project[0]["name"] == new_name
                      and project[0]["stage"] == "图纸解析",
                      f"项目 {project[0]['name'] if project else '（无数据）'} / "
                      f"stage={project[0]['stage'] if project else '—'}")
                stages_new = {r["key"]: r["status"] for r in db_query(
                    "SELECT key, status FROM stages WHERE project_id=?", (new_pid,))}
                check("新建项目四阶段已初始化",
                      len(stages_new) == 4 and stages_new.get("drawings") == "进行中"
                      and all(stages_new.get(k) == "未开始" for k in ("model", "quantities", "pricing")),
                      "、".join(f"{k}={v}" for k, v in stages_new.items()))
                members = db_query("SELECT COUNT(*) AS n FROM members WHERE project_id=?", (new_pid,))
                check("新建项目已加入全部演示账号", bool(members) and members[0]["n"] >= 3,
                      f"成员 {members[0]['n'] if members else 0} 个")
                audit_new = db_query(
                    "SELECT COUNT(*) AS n FROM audit WHERE project_id=? AND action LIKE '%新建项目%'",
                    (new_pid,))
                check("新建项目写入审计日志", bool(audit_new) and audit_new[0]["n"] >= 1,
                      f"审计 {audit_new[0]['n'] if audit_new else 0} 条")
                response = zaojia.get(f"/project/{new_pid}")
                check("新建项目页含“下一步”引导条",
                      response.status_code == 200 and "下一步" in response.text,
                      f"HTTP {response.status_code}")

            response = zaojia.get("/")
            check("工作台含“开始工作流”引导区",
                  response.status_code == 200 and "开始工作流" in response.text and "新建项目" in response.text,
                  "页面含“开始工作流/新建项目”")
        except Exception as exc:  # noqa: BLE001 - v3 未就绪时给出 FAIL 而不是中断
            check("新建项目（v3）E2E 执行", False, f"异常：{exc}")

        # ---------- 9. 项目编码录入 + 上传分批/删除（第四轮 v4，contract4 §5） ----------
        try:
            from openpyxl import Workbook  # 本地生成一个小清册用于上传/删除校验

            response = zaojia.get("/project/new")
            check("新建项目表单含“项目编码”输入",
                  response.status_code == 200 and "项目编码" in response.text and 'name="code"' in response.text,
                  f"HTTP {response.status_code}")

            code_value = f"E2E-CODE-{datetime.now().strftime('%H%M%S')}"
            response = post(zaojia, "/project/new", {
                "name": f"E2E编码项目-{datetime.now().strftime('%H%M%S')}",
                "code": code_value, "ptype": "业扩配套", "location": "某供电所", "note": "v4 编码校验",
            })
            final_url = str(response.url)
            m2 = re.search(r"/project/(\d+)/drawings", final_url)
            code_pid = int(m2.group(1)) if m2 else 0
            check("录入项目编码创建成功并跳转图纸解析页",
                  response.status_code == 200 and bool(m2),
                  f"HTTP {response.status_code}，最终 URL {final_url}")
            if code_pid:
                project = db_query("SELECT code, stage FROM projects WHERE id=?", (code_pid,))
                check("项目编码按录入值保存（唯一）",
                      bool(project) and project[0]["code"] == code_value
                      and project[0]["stage"] == "图纸解析",
                      f"code={project[0]['code'] if project else '—'}")

            after_count = db_query("SELECT COUNT(*) AS n FROM projects")[0]["n"]
            response = post(zaojia, "/project/new", {
                "name": "E2E重复编码-不应创建", "code": code_value,
                "ptype": "台区改造", "location": "某供电所", "note": "",
            })
            final_count = db_query("SELECT COUNT(*) AS n FROM projects")[0]["n"]
            check("重复项目编码被拒绝且不写库",
                  response.status_code == 200 and "项目编码已存在" in response.text
                  and final_count == after_count,
                  f"HTTP {response.status_code}，项目数 {after_count}→{final_count}")

            response = post(zaojia, "/project/new", {
                "name": "E2E非法编码-不应创建", "code": "中文编码",
                "ptype": "台区改造", "location": "某供电所", "note": "",
            })
            check("非法项目编码被拒绝",
                  response.status_code == 200 and "项目编码只能包含" in response.text,
                  f"HTTP {response.status_code}")

            response = post(zaojia, "/project/new", {
                "name": f"E2E自动编码-{datetime.now().strftime('%H%M%S')}", "code": "",
                "ptype": "台区改造", "location": "某供电所", "note": "",
            })
            auto_match = re.search(r"/project/(\d+)/drawings", str(response.url))
            auto_code = ""
            if auto_match:
                auto_row = db_query("SELECT code FROM projects WHERE id=?", (int(auto_match.group(1)),))
                auto_code = auto_row[0]["code"] if auto_row else ""
            check("留空项目编码自动生成 PRJ 编码",
                  bool(re.match(r"^PRJ-\d{8}-\d{2}$", auto_code or "")), f"code={auto_code}")

            if code_pid:
                wb = Workbook()
                ws = wb.active
                ws.title = "材料清册"
                ws.append(["名称", "规格", "单位", "数量"])
                ws.append(["电缆", "YJV-1×240", "米", 10])
                buffer = io.BytesIO()
                wb.save(buffer)
                upload = zaojia.post(
                    f"/project/{code_pid}/drawings/upload",
                    files={"files": (
                        "E2E待删除清册.xlsx",
                        buffer.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )},
                    data={"category": "材料清册"},
                    follow_redirects=False,
                )
                check("上传图纸成功（用于删除校验）", upload.status_code == 303,
                      f"HTTP {upload.status_code}")
                rows = db_query(
                    "SELECT id FROM drawings WHERE project_id=? ORDER BY id DESC LIMIT 1", (code_pid,))
                did = int(rows[0]["id"]) if rows else 0
                page = zaojia.get(f"/project/{code_pid}/drawings")
                check("图纸目录含删除操作",
                      page.status_code == 200 and f"/drawings/{did}/delete" in page.text,
                      f"HTTP {page.status_code}，drawing={did}")
                if did:
                    deleted = zaojia.post(
                        f"/project/{code_pid}/drawings/{did}/delete", follow_redirects=False)
                    remain = db_query("SELECT COUNT(*) AS n FROM drawings WHERE id=?", (did,))
                    check("图纸可删除（记录移除）",
                          deleted.status_code == 303 and bool(remain) and remain[0]["n"] == 0,
                          f"HTTP {deleted.status_code}，剩余 {remain[0]['n'] if remain else '—'}")
                    audit_del = db_query(
                        "SELECT COUNT(*) AS n FROM audit WHERE project_id=? AND action='删除图纸'",
                        (code_pid,))
                    check("删除图纸写入审计",
                          bool(audit_del) and audit_del[0]["n"] >= 1, "审计含“删除图纸”")
                page = zaojia.get(f"/project/{code_pid}/drawings")
                check("上传区支持分批累计选择（清空按钮/累计提示）",
                      page.status_code == 200 and "data-file-clear" in page.text
                      and "会累计添加" in page.text,
                      "页面含 data-file-clear/会累计添加")
                js = zaojia.get("/static/app.js")
                check("前端选择器支持累计选择（DataTransfer）",
                      js.status_code == 200 and "DataTransfer" in js.text, f"HTTP {js.status_code}")
        except Exception as exc:  # noqa: BLE001 - v4 未就绪时给出 FAIL 而不是中断
            check("项目编码与上传体验（v4）E2E 执行", False, f"异常：{exc}")

        # ---------- 10. 真实典设库目录（含“图纸文件”列）导入（第五轮 v5） ----------
        try:
            from openpyxl import load_workbook

            csv_text = (
                "卷册,篇,类别,模块编号,模块名称,模块单位,图纸文件\n"
                "第三册,第二篇 台架变,台架变,CSG-GD-10B-TB-GT-JD,台架变地网图,处,"
                "03 第三册 台架变智能台区标准设计/第二篇 Ⅰ型(10+12)台架智能台区变标准设计.dwg\n"
                "第七册,第二篇 低压线路本体,低压架空,CSG-GD-0.4K-HD,横担架线,套,"
                "07 第七册 低压线路标准设计/02 第二篇 低压线路本体/03 低压架空模块/横担架线标准设计.dwg\n"
            ).encode("utf-8")
            response = zaojia.post(
                "/project/3/typdesign/import",
                files={"file": ("E2E典设目录-含图纸.csv", csv_text, "text/csv")},
                follow_redirects=False,
            )
            check("典设目录（含图纸文件列）导入成功", response.status_code == 303,
                  f"HTTP {response.status_code}")
            auto_matches = db_query(
                "SELECT COUNT(*) AS n FROM typdesign_matches WHERE project_id=3")
            check("导入典设目录后自动执行匹配（无需手动点击）",
                  bool(auto_matches) and auto_matches[0]["n"] >= 1,
                  f"匹配记录 {auto_matches[0]['n'] if auto_matches else 0} 条")
            zaojia.post("/project/3/typdesign/match", follow_redirects=True)
            page = zaojia.get("/project/3/typdesign")
            check("匹配明细显示“库内图纸”列与文件路径",
                  page.status_code == 200 and "库内图纸" in page.text
                  and "台架智能台区变标准设计.dwg" in page.text,
                  f"HTTP {page.status_code}")
            export = zaojia.get("/project/3/typdesign/export.xlsx")
            workbook = load_workbook(io.BytesIO(export.content))
            sheet = workbook[workbook.sheetnames[0]]
            header, header_row = [], 0
            for row_index, row in enumerate(sheet.iter_rows(min_row=1, max_row=6, values_only=True), 1):
                values = [str(value or "") for value in row]
                if "图纸文件" in values and "模块编号" in values:
                    header, header_row = values, row_index
                    break
            data_rows = list(sheet.iter_rows(min_row=header_row + 1, values_only=True)) if header_row else []
            has_path = bool(header) and any(
                str(row[header.index("图纸文件")] or "").endswith(".dwg")
                for row in data_rows if len(row) > header.index("图纸文件"))
            check("导出匹配清单含“图纸文件”列与路径", export.status_code == 200 and has_path,
                  f"HTTP {export.status_code}，表头含图纸文件={('图纸文件' in header)}")
        except Exception as exc:  # noqa: BLE001 - v5 未就绪时给出 FAIL 而不是中断
            check("真实典设库目录（v5）E2E 执行", False, f"异常：{exc}")
    finally:
        zaojia.close()
        fuhe.close()

    # ---------- 汇总 ----------
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print("-" * 80)
    print(f"E2E 结果：{passed} PASS / {failed} FAIL / 共 {len(RESULTS)} 项")
    if failed:
        print("失败项：")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name}：{detail}")
        return 1
    print("E2E 全部通过 ✅（项目C 已走完 图纸→建模→算量→计价 全链路并完成复核与导出；"
          "典型设计匹配/确认/导出/送审条件亦通过；新建项目、项目编码录入、上传分批与图纸删除、典设库目录图纸文件列通过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
