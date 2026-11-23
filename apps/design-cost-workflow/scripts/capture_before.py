#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""capture_before.py — 生成「Before · 现状手工流程」三张 Excel 风格证据图（项目A）。

优先只读读取 apps/design-cost-workflow/data/workflow.db 中项目A 的数据
（drawings / model_* / quantities / pricing_fees）；DB 不存在或表为空时回退到
与 .dev/contract.md 第 5/6/7 节一致的内置演示数据。应用 DB 生成后重跑本脚本即可自动改用 DB。

用法（可在仓库根目录或 apps/design-cost-workflow 下运行；Windows 如 python 为应用商店别名，请改用完整解释器路径）：
  python ^
      apps\\design-cost-workflow\\scripts\\capture_before.py [--only 01,02,03]

输出：docs/shots/design-cost/before-0{1,2,3}-*.png（Edge headless，1440px 宽）

注意：中间 HTML/PNG 放在 scripts/.capture-tmp/ 内，截图结束后自动删除；
最终 PNG 才写入 docs/shots/design-cost/。本机未安装/未授权博微造价软件，Before 为
演示数据还原（非旧版应用截图）；全部项目/单位/人员均为匿名演示数据。
"""

from __future__ import annotations

import argparse
import html
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

try:  # 中文输出在 GBK 控制台/重定向时都尽量正常
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
APP = Path(__file__).resolve().parents[1]          # apps/design-cost-workflow
ROOT = APP.parents[1]                               # 仓库根
OUT_DIR = ROOT / "docs" / "shots" / "design-cost"
SCRATCH = APP / "scripts" / ".capture-tmp"          # 工作区内临时目录（结束后删除）
WIDTH = 1440
LABEL = "Before · 现状手工流程（演示数据已匿名化）"
NOTE = ("演示数据还原（非旧版应用截图）；本机未安装/未授权博微造价软件，无法截取博微界面；"
        "演示数据已匿名化（项目A、某供电所等）。")
CATS = ["安装工程费", "建筑工程费", "拆除工程费", "设备购置费", "其他费用", "基本预备费", "静态投资"]

# ---------------------------------------------------------------------------
# 回退演示数据（contract.md 第 5/6/7 节，项目A）
# ---------------------------------------------------------------------------
PROJ = {"code": "DEMO-A", "name": "项目A·某台区改造工程", "ptype": "台区改造",
        "stage": "计价定稿", "loc": "某供电所"}
# (图纸名称, 图号, 格式, 人工抄录要点, 人工耗时/人·时)
DRAWINGS = [
    ("施工图设计说明书", "SM-A-001", "PDF", "路径长度/配变容量/导线型号/地形土质等设计说明", 2.0),
    ("10kV线路平面图", "PL10-A-001", "DWG", "10kV路径0.057km；杆塔2基、档距28.5m；导线JKLGYJ-70/10", 2.0),
    ("台架变安装图", "TA-A-001", "DWG", "配变SB20-M-500kVA×1、配电箱XLW-10/500-4×1、智能网关×1、避雷器2只", 1.5),
    ("0.4kV线路平面图", "PL04-A-001", "DWG", "低压路径0.7875km；杆14基、档距56.25m；拉线12条", 2.0),
    ("杆塔明细表", "GT-A-001", "XLSX", "杆塔16基（Φ150×8m）；拉线12条；逐基档距/杆型", 1.0),
    ("主要设备材料清册", "CL-A-001", "XLSX", "设备3台；主材14项；甲供/乙供与单价需逐项核对", 1.5),
    ("施工图预算（博微导出）", "YS-A-001", "XLSX", "设计预算（附件5）：设备/安装/其他/预备费，用于对比，不参与建模", 0.5),
    ("电缆路径图", "DL-A-001", "DWG", "电缆ZA-YJV-1×240 0.0742km；直埋沟70m；终端头2个", 1.0),
    ("勘探及现场照片", "KC-A-001", "JPG", "土质普通土、地形与进场条件；拆除量线索（低压导线2.5km、水泥杆3基）", 0.5),
]
# 人工二次录入后的模型规模 (对象, 数量/规模, 来源图纸, 人工核对要点)
MODEL = [
    ("线路（10kV）", "1 条 / 0.057 km", "10kV线路平面图、杆塔明细表", "亘长、3相、导线JKLGYJ-70/10与清册一致"),
    ("线路（低压）", "1 条 / 0.7875 km", "0.4kV线路平面图", "4相、预留损耗3%、BLVV-240/BLVV-120分段核对"),
    ("杆塔", "16 基（Φ150×8m）", "杆塔明细表", "10kV杆塔2基+低压14基；档距、拉线逐基核对"),
    ("设备", "3 台（配变500kVA/配电箱/网关）", "台架变安装图、设备材料清册", "甲供/乙供、设备单价、运杂费口径"),
    ("主材", "14 项（导线/电杆/拉线/避雷器/金具/电缆等）", "主要设备材料清册", "数量、损耗、单价逐项核对（清册与图纸常不一致）"),
    ("土建", "2 项（基础2.4m³、直埋沟70m）", "台架变安装图、电缆路径图", "混凝土标号、沟槽断面尺寸"),
    ("拆除", "2 项（低压导线2.5km、水泥杆3基）", "现状资料、现场照片", "拆除量依据、可回收量、余物清理费口径"),
]
# (单位工程, 类别, 项目名称, 规格, 单位, 数量, 计算式, 依据/说明) —— contract 6.1
QTY = [
    ("10kV线路", "安装", "10kV架空线路架设", "10kV绝缘导线(亘长)", "km", 0.057, "0.057（亘长）", "图纸识别：10kV路径0.057km；YD3-0301"),
    ("10kV线路", "安装", "电杆组立", "Φ150×8m水泥杆", "根", 2, "2（杆塔2基）", "杆塔明细表：10kV杆塔2基；YD3-0101"),
    ("10kV线路", "安装", "10kV线路金具安装", "横担/绝缘子/铁件", "套", 2, "2基×1套", "每基1套；YD3-0601"),
    ("10kV线路", "建筑", "杆坑土方开挖（普通土）", "—", "m³", 2.4, "2基×1.2", "每基1.2m³；YD1-0101"),
    ("10kV线路", "主材", "10kV架空绝缘导线", "JKLGYJ-70/10", "km", 0.176, "0.057×3×1.03", "10kV路径0.057km，3相；预留损耗3%"),
    ("10kV线路", "主材", "水泥杆", "Φ150×8m", "根", 2, "2×1", "杆塔明细表：10kV杆塔2基"),
    ("10kV线路", "主材", "10kV金具（横担/绝缘子/铁件）", "—", "套", 2, "2×1", "每基1套（乙供）"),
    ("低压线路", "安装", "低压线路架设", "1kV绝缘导线(亘长)", "km", 0.7875, "0.7875（亘长）", "图纸识别：低压路径0.7875km；YD3-0302"),
    ("低压线路", "安装", "电杆组立", "Φ150×8m水泥杆", "根", 14, "14（杆塔14基）", "杆塔明细表：低压杆塔14基；YD3-0101"),
    ("低压线路", "安装", "拉线制作安装", "含拉线棒/拉线盘/钢绞线", "条", 12, "12（拉线12条）", "平面图/杆塔明细表；YD3-0201"),
    ("低压线路", "安装", "低压线路金具安装", "四线横担/铁件", "套", 14, "14基×1套", "每基1套；YD3-0602"),
    ("低压线路", "建筑", "杆坑土方开挖（普通土）", "—", "m³", 16.8, "14基×1.2", "每基1.2m³；YD1-0101"),
    ("低压线路", "建筑", "拉线坑土方开挖", "—", "m³", 9.6, "12条×0.8", "每条0.8m³；YD1-0102"),
    ("低压线路", "主材", "低压绝缘导线", "BLVV-240", "km", 1.648, "0.7875×4×1.03=3.2445；清册拆分", "低压4相、预留损耗3%；设计清册给量"),
    ("低压线路", "主材", "低压绝缘导线", "BLVV-120", "km", 1.597, "0.7875×4×1.03=3.2445；清册拆分", "低压4相、预留损耗3%；设计清册给量"),
    ("低压线路", "主材", "水泥杆", "Φ150×8m", "根", 14, "14×1", "杆塔明细表：低压杆塔14基"),
    ("低压线路", "主材", "拉线（含拉线棒/盘/钢绞线）", "—", "条", 12, "12×1", "杆塔明细表：拉线12条（乙供）"),
    ("低压线路", "主材", "低压金具（四线横担/铁件）", "—", "套", 14, "14×1", "每基1套（乙供）"),
    ("台架变工程", "安装", "台架式变压器安装（500kVA）", "SB20-M-500kVA", "套", 1, "1（设备清册1台）", "设备材料清册；YD2-0101"),
    ("台架变工程", "安装", "低压配电箱安装", "XLW-10/500-4", "台", 1, "1（设备清册1台）", "设备材料清册；YD2-0201"),
    ("台架变工程", "安装", "配电智能网关安装", "—", "套", 1, "1（设备清册1套）", "设备材料清册；YD2-0301"),
    ("台架变工程", "安装", "10kV避雷器安装", "YH10CX-17/50", "只", 2, "2（安装图2只）", "台架变安装图；YD3-0401"),
    ("台架变工程", "安装", "接地装置安装（2根接地极）", "角钢/扁钢", "组", 2, "2（安装图2组）", "台架变安装图；YD2-0401"),
    ("台架变工程", "安装", "安全围栏安装", "—", "套", 1, "1（安装图1套）", "台架变安装图；YD2-0501"),
    ("台架变工程", "安装", "台架变金具及支架安装", "—", "套", 1, "1（安装图1套）", "台架变安装图；YD2-0601"),
    ("台架变工程", "建筑", "混凝土基础（C20）", "台架变基础", "m³", 2.4, "1座×2.4", "台架变基础尺寸；YD1-0201"),
    ("台架变工程", "主材", "10kV避雷器", "YH10CX-17/50", "只", 2, "2×1", "安装图/清册（甲供）"),
    ("台架变工程", "主材", "接地装置材料", "角钢/扁钢", "组", 2, "2×1", "安装图/清册（乙供）"),
    ("台架变工程", "主材", "安全围栏", "—", "套", 1, "1×1", "清册（乙供）"),
    ("台架变工程", "主材", "台架变金具及支架", "—", "套", 1, "1×1", "清册（甲供）"),
    ("电缆工程", "安装", "电力电缆敷设（1kV 240mm²）", "ZA-YJV-1×240", "km", 0.0742, "0.0742（电缆路径）", "电缆路径图；YD3-0501"),
    ("电缆工程", "安装", "电力电缆终端头制作（1kV 240mm²）", "1kV 240mm²", "个", 2, "2（两端各1个）", "电缆路径图；YD3-0502"),
    ("电缆工程", "建筑", "电缆直埋沟开挖及回填", "0.4m宽×0.8m深", "m", 70, "70（电缆路径）", "电缆路径图；YD1-0301"),
    ("电缆工程", "主材", "电力电缆", "ZA-YJV-1×240", "km", 0.0742, "0.0742×1", "电缆路径图/清册（甲供）"),
    ("电缆工程", "主材", "电缆终端头", "1kV 240mm²", "个", 2, "2×1", "清册（乙供）"),
    ("拆除工程", "拆除", "拆除低压导线", "—", "km", 2.5, "2.5（现状资料）", "现状资料/现场照片；YD5-0101"),
    ("拆除工程", "拆除", "拆除水泥杆", "Φ150×8m", "根", 3, "3（现状资料）", "现状资料/现场照片；YD5-0102"),
]
# 计价费用构成回退数据（contract 7.1/7.2，元）：各分组 (费用项目, 计费基数/计算式, 金额)
FEES = {
    "安装工程费": [
        ("人工费", "Σ(定额人工×工程量)", 11631.60), ("材料费", "Σ(定额材料×工程量)", 1919.32),
        ("机械费", "Σ(定额机械×工程量)", 2394.93), ("主材费（甲供+乙供）", "Σ(主材数量×单价)", 69667.00),
        ("直接工程费小计", "人工+材料+机械+主材", 85612.85),
        ("措施费", "(人工费+机械费)×27.90%", 3913.40), ("规费", "人工费×1.1×38.5%", 4925.98),
        ("企业管理费", "(人工费+机械费)×19.91%", 2792.68), ("利润", "(人工费+机械费)×12.4%", 1739.29),
        ("增值税", "(直接费+间接费+利润)×9%", 8908.58),
        ("小计：安装工程费", "直接费+间接费+利润+增值税", 107892.78)],
    "建筑工程费": [
        ("人工费", "Σ(定额人工×工程量)", 3295.20), ("材料费", "Σ(定额材料×工程量)", 1052.00),
        ("机械费", "Σ(定额机械×工程量)", 661.60), ("直接工程费小计", "人工+材料+机械", 5008.80),
        ("措施费", "(人工费+机械费)×15.93%", 630.32), ("规费", "人工费×1.08×38.5%", 1370.14),
        ("企业管理费", "(人工费+机械费)×10.22%", 404.38), ("利润", "(人工费+机械费)×6.56%", 259.57),
        ("增值税", "(直接费+间接费+利润)×9%", 690.59),
        ("小计：建筑工程费", "直接费+间接费+利润+增值税", 8363.80)],
    "拆除工程费": [
        ("人工费", "Σ(定额人工×工程量)", 1120.00), ("材料费", "Σ(定额材料×工程量)", 70.00),
        ("机械费", "Σ(定额机械×工程量)", 230.00), ("直接工程费小计", "人工+材料+机械（无主材）", 1420.00),
        ("措施费", "(人工费+机械费)×27.90%", 376.65), ("规费", "人工费×1.1×38.5%", 474.32),
        ("企业管理费", "(人工费+机械费)×19.91%", 268.79), ("利润", "(人工费+机械费)×12.4%", 167.40),
        ("增值税", "(直接费+间接费+利润)×9%", 243.64),
        ("小计：拆除工程费", "直接费+间接费+利润+增值税", 2950.80)],
    "设备购置费": [
        ("设备原价", "配变500kVA×1、配电箱×1、智能网关×1（甲供）", 110500.00),
        ("设备运杂费", "设备原价×1.06%", 1171.30),
        ("小计：设备购置费", "设备原价+运杂费", 111671.30)],
    "其他费用": [
        ("项目管理经费", "建安工程费×1.22%", 1454.33), ("招标费", "(建安+设备购置费)×0.32%", 738.81),
        ("工程监理费", "建安工程费×2.64%", 3147.07), ("项目前期工作费", "建安工程费×2.29%", 2729.85),
        ("勘察费", "建筑工程费×4.5%+亘长0.8445km×1000元/km", 1220.87),
        ("基本设计费", "(建安+设备购置费)×5.5%", 12698.33), ("其他设计费", "基本设计费×18%", 2285.70),
        ("设计文件评审费", "基本设计费×4.8%", 609.52),
        ("施工过程造价咨询及竣工结算审核费", "建安工程费×0.51%", 607.96),
        ("工程质量检测费", "建安工程费×0.15%", 178.81),
        ("技术经济标准编制费", "建安工程费×0.10%", 119.21),
        ("生产准备费", "建安工程费×0.5%", 596.04), ("项目后评价费", "建安工程费×0.5%", 596.04),
        ("余物清理费", "拆除工程直接费×35%", 497.00),
        ("小计：其他费用", "以上14项合计", 27479.54)],
    "基本预备费": [("基本预备费", "(建安+设备+其他)×3%", 7750.75)],
    "静态投资": [("静态投资合计", "建安+设备购置费+其他费用+基本预备费", 266108.97)],
}
NOTES = {"安装工程费": "设备安装/线路架设/电缆敷设，含措施费、规费、税金",
         "建筑工程费": "土方、基础、电缆直埋沟等", "拆除工程费": "拆除工程量×拆除定额（无主材）",
         "设备购置费": "甲供设备原价+运杂费1.06%", "其他费用": "14项费用（设计费/监理费/管理经费等）",
         "基本预备费": "(建安+设备+其他)×3%", "静态投资": "送审金额（静态投资合计）"}


# ---------------------------------------------------------------------------
# 格式化 / HTML 渲染
# ---------------------------------------------------------------------------
def esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def num(v) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    return str(int(x)) if abs(x - round(x)) < 1e-9 else f"{x:.4f}".rstrip("0").rstrip(".")


def money(v) -> str:
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return "—"


def wan(v) -> str:
    try:
        return f"{float(v) / 10000:,.2f}"
    except (TypeError, ValueError):
        return "—"


def rate(v) -> str:
    if v in (None, ""):
        return "—"
    if isinstance(v, str):
        return v
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{x * 100:.2f}%" if abs(x) <= 1 else f"{x:.2f}%"


CSS = """
*{box-sizing:border-box}html,body{margin:0;padding:0}
body{background:#e9edf2;font-family:"Microsoft YaHei UI","Microsoft YaHei","Segoe UI",sans-serif;color:#1f2937}
.page{padding:22px 40px 30px}
.titlebar{background:linear-gradient(90deg,#1f3864,#2b4c8c);color:#fff;border-radius:10px 10px 0 0;
  padding:11px 18px;display:flex;align-items:center;justify-content:space-between}
.titlebar .l{display:flex;align-items:center;gap:12px}
.badge{background:#f59e0b;color:#3d2b00;font-size:12px;font-weight:800;padding:3px 11px;border-radius:20px;letter-spacing:.6px}
.titlebar h1{margin:0;font-size:18px;font-weight:700;letter-spacing:.4px}
.titlebar .r{font-size:12px;color:#c7d4ee}
.infobar{background:#fff;border:1px solid #cfd7e3;border-top:0;padding:8px 18px;display:flex;
  justify-content:space-between;gap:16px;font-size:12.5px;color:#475569}
.infobar b{color:#1f3864}.infobar .r{white-space:nowrap;color:#64748b}
.sheetname{background:#eef3fb;border:1px solid #cfd7e3;border-top:0;padding:7px 18px;display:flex;
  justify-content:space-between;font-size:13px;color:#1f3864;font-weight:600}
.kpis{background:#fff;border:1px solid #cfd7e3;border-top:0;padding:10px 18px 12px;display:flex;gap:10px}
.kpi{flex:1;background:#f8fafc;border:1px solid #e2e8f0;border-left:4px solid #2563eb;border-radius:8px;padding:7px 12px 8px}
.kpi.w{border-left-color:#f59e0b}.kpi .k{font-size:11.5px;color:#64748b}
.kpi .v{font-size:18px;font-weight:800;color:#1f3864;margin-top:1px;white-space:nowrap}
.kpi .v small{font-size:12px;font-weight:600;color:#475569;margin-left:3px}
.kpi .s{font-size:11px;color:#94a3b8;margin-top:1px}
.sheetwrap{background:#fff;border:1px solid #c8ced6;border-top:0}
table.sheet{border-collapse:collapse;width:100%;table-layout:fixed}
table.sheet th,table.sheet td{border:1px solid #c8ced6;padding:5px 8px;font-size:12.5px;line-height:1.5;
  vertical-align:middle;word-break:break-all}
table.sheet thead tr.l th{background:#f1f3f5;color:#6b7280;font-weight:400;font-size:10.5px;text-align:center;padding:2px 0}
table.sheet thead tr.h th{background:#DCE8FB;color:#1f3864;font-weight:700;padding:7px 8px}
table.sheet th.n,table.sheet td.n{width:38px;background:#f1f3f5;color:#6b7280;font-weight:400;font-size:10.5px;text-align:center;padding:2px 0}
table.sheet tbody tr:nth-child(even) td:not(.n){background:#fbfcfe}
table.sheet td.r{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
table.sheet td.c{text-align:center}
table.sheet tr.g td{background:#e6eefb;font-weight:700;color:#1f3864}
table.sheet tr.t td{background:#fff7e6;font-weight:700}
table.sheet tr.s td{background:#f4f7fd;font-weight:700;color:#1f3864}
.foot{margin-top:12px;display:flex;flex-direction:column;gap:7px}
.foot div{background:#fff;border:1px solid #dbe2ec;border-left:4px solid #f59e0b;border-radius:6px;
  padding:8px 12px;font-size:12px;line-height:1.68;color:#334155}
.foot div.i{border-left-color:#2563eb}.foot div.w{border-left-color:#dc2626;background:#fff7f7}
.foot b{color:#b45309;margin-right:6px}.foot div.i b{color:#1d4ed8}.foot div.w b{color:#b91c1c}
.sig{margin-top:12px;width:100%;border-collapse:collapse}
.sig td{border:1px solid #c8ced6;padding:7px 12px;font-size:12px;color:#334155;background:#fff}
.sig td.l{background:#f1f3f5;color:#475569;font-weight:600;width:84px}
.tabs{margin-top:10px;display:flex;gap:4px;padding:0 8px;align-items:flex-end;border-bottom:1px solid #c8ced6}
.tab{background:#dfe5ee;border:1px solid #c8ced6;border-bottom:0;border-radius:6px 6px 0 0;padding:5px 18px;font-size:12px;color:#475569}
.tab.a{background:#fff;color:#1f3864;font-weight:700}
"""


def table(cols, rows) -> str:
    """cols: [(标题, 对齐 l/c/r, 宽度%)]; rows: tuple 列表，单个元素的 tuple 作为分组标题行。"""
    out = ['<table class="sheet"><colgroup><col style="width:38px">']
    out += [f'<col style="width:{c[2]}%">' for c in cols]
    out.append('</colgroup><thead><tr class="l"><th class="n"></th>')
    out += [f"<th>{chr(65 + i)}</th>" for i in range(len(cols))]
    out.append('</tr><tr class="h"><th class="n">#</th>')
    out += [f'<th class="{c[1]}">{esc(c[0])}</th>' for c in cols]
    out.append("</tr></thead><tbody>")
    n = 0
    for row in rows:
        n += 1
        if len(row) == 1:  # 分组行
            out.append(f'<tr class="g"><td class="n">{n}</td><td colspan="{len(cols)}">{esc(row[0])}</td></tr>')
            continue
        cls = {"t": "t", "s": "s"}.get(_row_cls(row), "")
        tds = "".join(f'<td class={{c}}>{esc(v)}</td>'.replace("{c}", {"l": "", "c": "c", "r": "r"}[cols[i][1]])
                      for i, v in enumerate(row))
        out.append(f'<tr class="{cls}"><td class="n">{n}</td>{tds}</tr>')
    out.append("</tbody></table>")
    return "".join(out)


def _row_cls(row) -> str:
    return ""


def page(no, sheet_title, proj_line, source, kpis, body, foots, sig, tabs) -> str:
    kpi_html = "".join(
        f'<div class="kpi{" w" if len(k) > 4 and k[4] else ""}"><div class="k">{esc(k[0])}</div>'
        f'<div class="v">{esc(k[1])}<small>{esc(k[2])}</small></div><div class="s">{esc(k[3])}</div></div>'
        for k in kpis)
    sig_html = "".join(f'<td class="l">{esc(a)}</td><td>{esc(b)}</td>' for a, b in sig)
    foot_html = "".join(f'<div class="{c}"><b>{i}</b>{esc(t)}</div>' for c, i, t in foots)
    tab_html = "".join(f'<div class="tab{" a" if j == 0 else ""}">{esc(t)}</div>' for j, t in enumerate(tabs))
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>{esc(LABEL)} · {esc(sheet_title)}</title><style>{CSS}</style></head><body><div class="page">
<div class="titlebar"><div class="l"><span class="badge">Before</span><h1>{esc(LABEL)}</h1></div>
<div class="r">Before {no} / 03 · {WIDTH}px</div></div>
<div class="infobar"><div><b>{esc(proj_line)}</b></div><div class="r">数据来源：{esc(source)}</div></div>
<div class="sheetname"><span>{esc(sheet_title)}</span><span>Before {no} / 03</span></div>
<div class="kpis">{kpi_html}</div><div class="sheetwrap">{body}</div>
<table class="sig"><tr>{sig_html}</tr></table><div class="foot">{foot_html}</div>
<div class="tabs">{tab_html}</div></div></body></html>"""


# ---------------------------------------------------------------------------
# SQLite（只读）读取
# ---------------------------------------------------------------------------
def read_db(path: Path):
    if not path or not path.exists():
        return None
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
        con.row_factory = sqlite3.Row

        def rows(sql, params=()):
            try:
                return [dict(r) for r in con.execute(sql, params)]
            except sqlite3.Error:
                return []

        projects = rows("SELECT * FROM projects ORDER BY id")
        if not projects:
            con.close()
            return None
        proj = next((p for p in projects if (p.get("code") or "").upper() == "DEMO-A"), projects[0])
        pid = int(proj["id"])
        data = {"proj": proj, "drawings": rows("SELECT * FROM drawings WHERE project_id=? ORDER BY id", (pid,)),
                "lines": rows("SELECT * FROM model_lines WHERE project_id=? ORDER BY id", (pid,)),
                "equip": rows("SELECT * FROM model_equipment WHERE project_id=? ORDER BY id", (pid,)),
                "civil": rows("SELECT * FROM model_civil WHERE project_id=? ORDER BY id", (pid,)),
                "demol": rows("SELECT * FROM model_demolition WHERE project_id=? ORDER BY id", (pid,)),
                "qty": rows("SELECT * FROM quantities WHERE project_id=? ORDER BY COALESCE(seq,999999),id", (pid,)),
                "fees": rows("SELECT * FROM pricing_fees WHERE project_id=? ORDER BY COALESCE(seq,999999),id", (pid,))}
        con.close()
        return data
    except Exception as exc:  # noqa: BLE001
        print(f"[提示] 读取数据库失败（{exc}），使用回退数据")
        return None


def hours(fmt: str) -> float:
    f = (fmt or "").upper()
    return 2.0 if "DWG" in f else 1.5 if "PDF" in f else 0.5 if ("JPG" in f or "PNG" in f) else 1.0


def load_data(db_path: Path):
    d = {"proj": PROJ, "drawings": DRAWINGS, "model": MODEL, "qty": QTY, "fees": FEES,
         "src": {"proj": 0, "drawings": 0, "model": 0, "qty": 0, "fees": 0}, "warn": []}
    raw = read_db(db_path)
    if not raw:
        d["warn"].append(f"未找到数据库（{db_path}）：使用契约一致的内置回退演示数据；"
                         f"应用 data/workflow.db 生成后重跑本脚本即自动改用 DB。")
        return d
    p = raw["proj"]
    d["proj"] = {"code": p.get("code") or PROJ["code"], "name": p.get("name") or PROJ["name"],
                 "ptype": p.get("ptype") or PROJ["ptype"], "stage": p.get("stage") or PROJ["stage"],
                 "loc": p.get("location") or PROJ["loc"]}
    d["src"]["proj"] = 1
    if raw["drawings"]:
        d["drawings"] = [(r.get("name") or "—", r.get("code") or "—", r.get("fmt") or "—",
                          r.get("summary") or r.get("status") or "已登记", hours(r.get("fmt"))) for r in raw["drawings"]]
        d["src"]["drawings"] = 1
    else:
        d["warn"].append("DB drawings 为空：图纸登记表用回退数据。")
    m = []
    lines = raw["lines"]
    if lines:
        total = sum(float(x.get("length_km") or 0) for x in lines)
        detail = "；".join(f"{x.get('name') or '线路'} {num(x.get('length_km'))}km" for x in lines)
        m.append(("线路", f"{len(lines)} 条 / 合计 {num(total)} km", "10kV/0.4kV线路平面图、杆塔明细表",
                  f"亘长、相数、导线型号与清册一致（{detail}）"))
        poles = sum(int(float(x.get("poles") or 0)) for x in lines)
        if poles:
            m.append(("杆塔", f"{poles} 基", "杆塔明细表", "杆型、档距、拉线数量逐基核对"))
    devs = [x for x in raw["equip"] if (x.get("category") or "") == "设备"]
    mats = [x for x in raw["equip"] if (x.get("category") or "") == "主材"]
    if devs:
        m.append(("设备", f"{len(devs)} 台/套", "台架变安装图、设备材料清册", "型号容量、甲供/乙供、设备单价"))
    if mats:
        m.append(("主材", f"{len(mats)} 项", "主要设备材料清册", "数量、损耗、单价逐项核对"))
    if raw["civil"]:
        m.append(("土建", f"{len(raw['civil'])} 项", "台架变安装图、电缆路径图", "混凝土标号、沟槽断面"))
    if raw["demol"]:
        m.append(("拆除", f"{len(raw['demol'])} 项", "现状资料、现场照片", "拆除量依据、可回收量"))
    if m:
        d["model"] = m
        d["src"]["model"] = 1
    if raw["qty"]:
        d["qty"] = [(r.get("unit_name") or "—", r.get("category") or "—", r.get("name") or "—",
                     r.get("spec") or "—", r.get("unit") or "—", r.get("qty") if r.get("qty") is not None else 0,
                     r.get("formula") or "—", r.get("basis") or "—") for r in raw["qty"]]
        d["src"]["qty"] = 1
    else:
        d["warn"].append("DB quantities 为空：算量表用回退数据。")
    if raw["fees"]:
        fees = {c: [] for c in CATS}
        for r in raw["fees"]:
            cat = r.get("category") or "其他费用"
            base = r.get("base_desc") or "—"
            f = r.get("formula") or ""
            rt = rate(r.get("rate"))
            calc = "；".join(x for x in [base, f, ("" if rt in ("—", "0.00%") else f"费率{rt}")] if x and x != "—")
            fees.setdefault(cat, []).append((r.get("item") or "—", calc or "—", float(r.get("amount") or 0)))
        d["fees"] = fees
        d["src"]["fees"] = 1
    else:
        d["warn"].append("DB pricing_fees 为空：计价汇总用回退数据。")
    return d


def source_text(d) -> str:
    s = d["src"]
    n = sum(s.values())
    if n == 5:
        return "workflow.db（只读读取 · 项目A）"
    if n == 0:
        return "契约演示数据（内置回退：data/workflow.db 尚未生成）"
    fb = "、".join(k for k, v in s.items() if not v)
    return f"workflow.db（只读）+ 内置回退（{fb} 无数据）"


def proj_line(d) -> str:
    p = d["proj"]
    return f"{p['name']}（{p['code']}） · {p['ptype']} · 现状阶段：{p['stage']} · {p['loc']}"


def cat_amount(d, cat) -> float:
    rows = d["fees"].get(cat) or []
    return float(rows[-1][2]) if rows else 0.0


def counts(d):
    c = {}
    for r in d["qty"]:
        c[r[1]] = c.get(r[1], 0) + 1
    return c


# ---------------------------------------------------------------------------
# 三张 Before 工件
# ---------------------------------------------------------------------------
def build_01(d) -> tuple:
    ds = d["drawings"]
    hrs = sum(x[4] for x in ds)
    rows = [(str(i), x[0], x[1], x[2], x[3], f"{x[4]:.1f}") for i, x in enumerate(ds, 1)]
    rows.append(("", "合计", "—", "—", f"{len(ds)} 份图纸：人工读图 + 关键信息抄录（重复录入 Excel/建模软件）", f"{hrs:.1f}"))
    t1 = table([("序号", "c", 5), ("图纸名称", "l", 16), ("图号", "l", 10), ("格式", "c", 6),
                ("人工抄录要点（人工读图后二次录入）", "l", 54), ("人工耗时（人·时）", "r", 9)], rows)
    mrows = [(str(i), x[0], x[1], x[2], x[3]) for i, x in enumerate(d["model"], 1)]
    mrows.append(("", "合计", f"{len(d['model'])} 类对象", "图纸 + 材料清册人工核对", "建模二次录入另需约 1 个工作日"))
    t2 = table([("序号", "c", 5), ("模型对象（人工二次录入）", "l", 16), ("数量 / 规模", "l", 18),
                ("来源图纸", "l", 22), ("人工核对要点", "l", 39)], mrows)
    kpis = [("图纸目录", str(len(ds)), "份", "PDF/DWG/XLSX/JPG 混合"),
            ("人工读图 + 关键信息抄录", f"{hrs:.1f}", "人·时", "≈1.5 个工作日（仅读图抄录）", 1),
            ("人工建模对象", str(len(d["model"])), "类", "线路/杆塔/设备/主材/土建/拆除"),
            ("主材清册条目", "14", "项", "甲供/乙供需人工逐项标注")]
    foots = [("i", "⏱", f"人工耗时：人工读图 + 关键信息抄录约 {hrs:.1f} 人·时（≈1.5 个工作日）；"
                        "若含建模二次录入（Excel/博微建模）另需约 1 个工作日，合计约 2.5 个工作日。"),
             ("w", "⚠", NOTE),
             ("i", "ℹ", "现状口径：图纸以 PDF/DWG 为主，DWG 需在 CAD 中人工量取路径长度与杆塔坐标；"
                        "设计说明书、杆塔明细表、材料清册三处数量/型号常不一致，需人工逐项核对后录入。")]
    sig = [("登记人", "造价员（手工填表）"), ("登记日期", "____年__月__日"),
           ("复核", "复核员（手工勾稽）"), ("版本", "V1 · 手工 Excel 台账")]
    html_text = page("01", "现状：图纸信息人工登记与建模（Excel 手工台账）", proj_line(d),
                     source_text(d), kpis, t1 + t2, foots, sig, ["图纸登记", "人工建模", "材料清册核对"])
    return html_text, 1750, f"图纸 {len(ds)} 份 / 人工 {hrs:.1f} 人·时"


def build_02(d) -> tuple:
    c = counts(d)
    rows = [(str(i), x[0], x[1], x[2], x[3], x[4], num(x[5]), x[6], x[7]) for i, x in enumerate(d["qty"], 1)]
    rows.append(("", "合计", "", f"{len(d['qty'])} 项工程量（" + " / ".join(f"{k}{v}项" for k, v in c.items()) + "）",
                 "", "", "", "", "口径依赖个人经验，复核需逐行勾稽"))
    body = table([("序号", "c", 4), ("单位工程", "l", 8), ("类别", "c", 5), ("项目名称", "l", 16),
                  ("规格", "l", 12), ("单位", "c", 5), ("数量", "r", 8), ("计算式（手工填写）", "l", 18),
                  ("依据 / 说明", "l", 24)], rows)
    kpis = [("工程量清单", str(len(d["qty"])), "项", "手工逐项抄列 + 套定额"),
            ("安装", str(c.get("安装", 0)), "项", "架设/组立/安装/敷设", 1),
            ("建筑", str(c.get("建筑", 0)), "项", "土方/基础/电缆沟"),
            ("主材", str(c.get("主材", 0)), "项", "导线/电杆/拉线/金具/电缆")]
    foots = [("i", "⏱", "人工算量：从图纸/清册逐项抄列计算式与依据约需 2 个工作日；"
                        "Excel 计算式与口径由人工维护，设计变更后需整表重算。"),
             ("w", "⚠", NOTE),
             ("i", "ℹ", "现状口径：导线主材=亘长×相数×1.03预留；土方按1.2m³/基、拉线坑0.8m³/条；"
                        "示例定额库为演示数据，正式使用需替换为企业定额库。")]
    sig = [("编制", "造价员（手工填表）"), ("编制日期", "____年__月__日"),
           ("复核", "复核员（手工勾稽）"), ("版本", "V1 · 手工 Excel")]
    html_text = page("02", "现状：手工算量计算表（Excel 手工计算式与依据）", proj_line(d),
                     source_text(d), kpis, body, foots, sig, ["计算式明细", "工程量汇总", "材料量对比"])
    return html_text, 2600, f"工程量 {len(d['qty'])} 项"


def build_03(d) -> tuple:
    fees = d["fees"]
    amounts = {c: cat_amount(d, c) for c in CATS}
    static = amounts.get("静态投资") or sum(v for k, v in amounts.items() if k != "静态投资")
    srows = []
    for i, c in enumerate(CATS, 1):
        amt = amounts.get(c, 0.0)
        pct = "100.00%" if c == "静态投资" else f"{(amt / static * 100 if static else 0):.2f}%"
        srows.append((str(i), c, money(amt), pct, NOTES.get(c, "")))
    t1 = table([("序号", "c", 5), ("费用名称", "l", 24), ("金额（元）", "r", 16),
                ("占静态投资", "r", 12), ("备注", "l", 43)], srows)
    drows = []
    for c in CATS:
        if not fees.get(c):
            continue
        drows.append((f"{c}（明细）",))
        for i, (item, calc, amt) in enumerate(fees[c]):
            tag = "s" if ("小计" in item or "合计" in item) else ""
            drows.append((tag, "", item, calc, money(amt)))
    drows = [(x[2], x[3], x[4]) if len(x) > 2 else x for x in drows]
    body2 = table([("费用项目", "l", 30), ("计费基数 / 计算式", "l", 52), ("金额（元）", "r", 18)], drows)
    kpis = [("静态投资（送审）", wan(static), "万元", f"{money(static)} 元", 1),
            ("安装工程费", wan(amounts.get("安装工程费")), "万元", f"{money(amounts.get('安装工程费'))} 元"),
            ("设备购置费", wan(amounts.get("设备购置费")), "万元", f"{money(amounts.get('设备购置费'))} 元"),
            ("其他费用", wan(amounts.get("其他费用")), "万元", f"{money(amounts.get('其他费用'))} 元")]
    foots = [("i", "⏱", "人工套价 + 费用汇总约需 2 个工作日；费率、基数需逐项手工核对，"
                        "定额套错、费用串项、重复计取是现状高频错误。"),
             ("w", "⚠", NOTE),
             ("i", "ℹ", "计价依据：《20kV及以下配电网工程建设预算编制与计算规定》（2022版）费用构成与费率，"
                        "演示取江西Ⅰ类地区县城/镇；示例定额库与材料价格为演示数据；金额单位：元，保留2位小数。")]
    sig = [("编制", "造价员（手工套价）"), ("编制日期", "____年__月__日"),
           ("复核", "复核员（手工勾稽）"), ("版本", "V1 · 手工 Excel")]
    html_text = page("03", "现状：手工计价汇总表（Excel 套价 · 费用构成明细）", proj_line(d),
                     source_text(d), kpis, t1 + body2, foots, sig, ["费用汇总", "套价明细", "预算对比"])
    return html_text, 3100, f"静态投资 {money(static)} 元"


ARTIFACTS = [("01", "before-01-drawing-register.png", build_01),
             ("02", "before-02-quantity-sheet.png", build_02),
             ("03", "before-03-pricing-summary.png", build_03)]


# ---------------------------------------------------------------------------
# Edge headless 截图（本机限制：输入/输出路径必须在工作区内）
# ---------------------------------------------------------------------------
def wait_png(path: Path, timeout=25.0) -> bool:
    deadline = time.time() + timeout
    last = -1
    stable = 0
    while time.time() < deadline:
        if path.exists():
            size = path.stat().st_size
            stable = stable + 1 if size > 0 and size == last else 0
            if stable >= 2:
                return True
            last = size
        time.sleep(0.15)
    return path.exists() and path.stat().st_size > 0


def trim(path: Path, pad=26) -> int:
    from PIL import Image
    with Image.open(path) as im:
        img = im.convert("RGB")
    w, h = img.size
    px = img.load()
    bg = px[4, h - 4]
    last = 0
    for y in range(h - 1, -1, -1):
        if any(sum(abs(px[x, y][k] - bg[k]) for k in range(3)) > 10 for x in range(0, w, 3)):
            last = y
            break
    if last == 0:
        raise RuntimeError("截图为空白")
    cut = last >= h - 6
    img.crop((0, 0, w, min(h, last + 1 + pad))).save(path, format="PNG", optimize=True)
    img.close()
    return h if cut else -1


def capture(html_text: str, out_png: Path, height: int) -> dict:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    stem = out_png.stem
    hp = SCRATCH / f"{stem}.html"
    hp.write_text(html_text, encoding="utf-8")
    for attempt in range(3):
        raw = SCRATCH / f"{stem}.raw.png"
        if raw.exists():
            raw.unlink()
        cmd = [EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
               f"--window-size={WIDTH},{height}", f"--screenshot={raw}",
               "--virtual-time-budget=3000", hp.as_uri()]
        subprocess.run(cmd, capture_output=True, timeout=120)
        if not wait_png(raw):
            raise RuntimeError(f"Edge 截图超时（{height}px）：{raw.name}")
        if trim(raw) < 0:
            shutil.move(str(raw), str(out_png))
            return {"width": WIDTH, "height": _height(out_png), "bytes": out_png.stat().st_size}
        height += 700
        print(f"      底部被截断，扩展视口至 {height}px 重试")
    shutil.move(str(raw), str(out_png))
    return {"width": WIDTH, "height": _height(out_png), "bytes": out_png.stat().st_size}


def _height(png: Path) -> int:
    from PIL import Image
    with Image.open(png) as im:
        return im.size[1]


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 Before · 现状手工流程三张证据图（项目A）")
    ap.add_argument("--db", type=Path, help="workflow.db 路径（默认 data/workflow.db 或 WORKFLOW_DATA_DIR）")
    ap.add_argument("--out-dir", type=Path, help="PNG 输出目录（默认 docs/shots/design-cost）")
    ap.add_argument("--only", default="", help="只生成指定编号，如 01,02,03")
    args = ap.parse_args(argv)
    if not Path(EDGE).exists():
        print(f"[错误] 未找到 Edge：{EDGE}")
        return 2
    db_path = args.db or (Path(os.environ["WORKFLOW_DATA_DIR"]).expanduser() / "workflow.db"
                          if os.environ.get("WORKFLOW_DATA_DIR") else APP / "data" / "workflow.db")
    out_dir = args.out_dir or OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    d = load_data(db_path)
    print("=" * 78)
    print("Before · 现状手工流程 证据图生成器")
    print(f"  数据库：{db_path}（{'存在，只读读取' if db_path.exists() else '不存在，使用回退数据'}）")
    print(f"  输出目录：{out_dir}")
    for w in d["warn"]:
        print(f"[提示] {w}")
    print(f"[项目] {proj_line(d)}")
    only = {x.strip().lstrip("0") or "0" for x in args.only.split(",") if x.strip()}
    todo = [(n, f, b) for n, f, b in ARTIFACTS if not only or n.lstrip("0") in only]
    failures = []
    try:
        for i, (no, name, builder) in enumerate(todo, 1):
            html_text, height, summary = builder(d)
            try:
                info = capture(html_text, out_dir / name, height)
                print(f"[{i}/{len(todo)}] {name}  {info['width']}×{info['height']}px  "
                      f"{info['bytes']:,} bytes  |  {summary}")
            except Exception as exc:  # noqa: BLE001
                failures.append((name, str(exc)))
                print(f"[{i}/{len(todo)}] {name}  失败：{exc}")
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)
    print("-" * 78)
    if failures:
        print(f"[完成] {len(todo) - len(failures)}/{len(todo)} 张成功，失败 {len(failures)} 张")
        return 1
    print(f"[完成] {len(todo)} 张 PNG 已写入：{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
