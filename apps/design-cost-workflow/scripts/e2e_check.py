#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""e2e_check.py — 「设计图纸→建模→算量→计价」原型 HTTP 端到端检查（项目C）。

用法（在仓库根或 apps/design-cost-workflow 下运行；Windows 如 python 为应用商店别名，请改用完整解释器路径）：
  python ^
      apps/design-cost-workflow/scripts/e2e_check.py [--base-url http://127.0.0.1:8091]

前置条件：应用已运行（WORKFLOW_DEMO=1），项目C 处于干净 seed 状态（图纸解析/进行中）。
脚本以真实登录会话驱动：造价员(zaojia) 提交各阶段 → 复核员(fuhe) 复核通过，
完整走通 图纸解析 → 建模 → 算量 → 计价 → 导出 → 对比，并只读查库校验最终状态。

注意：本脚本会修改演示数据库（项目C 流程状态），属预期行为；运行前请确认 C 项目为干净状态。
输出：PASS/FAIL 明细表 + 汇总；全部通过返回 0，否则返回 1。
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sqlite3
import sys
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
    print("E2E 全部通过 ✅（项目C 已走完 图纸→建模→算量→计价 全链路并完成复核与导出）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
