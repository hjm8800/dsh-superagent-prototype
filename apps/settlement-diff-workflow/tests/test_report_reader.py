# -*- coding: utf-8 -*-
"""``diffworkflow.readers.report`` 的单元测试。

- 用 openpyxl 在 ``tmp_path`` 生成匿名化的 .xlsx 竣工报告工作簿（结构模拟真实表），
  断言 texts / tables / items / warnings / totals / meta；
- 对真实 .XLS 竣工报告做只读冒烟测试（文件不存在时 skip，不复制、不提交真实资料）。

真实资料路径通过环境变量 ``REPORT_SMOKE_BASE`` 覆盖，默认扫描
``D:\\DeepSeek Harness\\资料对比工作流`` 下的 ``2025年资料`` / ``2026年资料``。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:  # 兼容从仓库任意目录运行 pytest
    sys.path.insert(0, str(APP_ROOT))

from diffworkflow.readers import report  # noqa: E402

# 匿名化长文本（> 80 字），模拟“主要工程量”文字描述
LONG_TEXT_1 = (
    "主要工程量：一、新建部分：1、安装匿名变压器2台，架设绝缘导线1.5km；"
    "2、组立电杆12基，制作拉线6组，安装避雷器6组；3、敷设电缆0.3km，"
    "安装开关3台，新建台架1套，安装标示牌若干。拆除部分：拆除旧导线2km，"
    "拆除旧电杆8基，拆除旧金具若干。"
)
LONG_TEXT_2 = (
    "十、新建部分（匿名支线）：1、安装跌落式开关1组，安装隔离开关3台，安装避雷器3只；"
    "2、架设避雷线0.4km，安装直线杆支架6套、转角杆支架3套、避雷线金具3套、接地引线3组。"
)
LONG_TEXT_3 = (
    "一、安装部分：1、更换绝缘导线路径长1.5km；2、更换电杆12基；3、更换金具若干；"
    "4、拆除旧线路2km；5、新建开关站1座；6、完成接地工程7组；7、完成安健环标识牌50块。"
)
VISA_CONTENT = (
    "安装工程量：1、树立匿名警示牌共28块（每块1.6米高×1.2米宽，1mm铝板）；"
    "2、开挖土方及外运10km共28处；3、浇筑零星混凝土28处；4、安装铁加工件50kg；"
    "5、完成其他零星工程若干。"
)


# --------------------------------------------------------------------------- #
# 匿名化测试工作簿构建
# --------------------------------------------------------------------------- #
def _append(ws, values):
    ws.append(list(values))


def _build_report_workbook(path: Path) -> None:
    """模拟真实竣工报告结构（全部匿名数据）。"""
    wb = Workbook()

    # 1) 竣工报告：长文本、重复单元格、短文本（不应提取）
    ws = wb.active
    ws.title = "竣工报告"
    ws.cell(row=1, column=1, value="工 程 竣 工 报 告")
    ws.cell(row=2, column=1, value=LONG_TEXT_1)          # -> R2C1
    ws.cell(row=2, column=11, value=LONG_TEXT_1)         # 与 R2C1 完全相同 -> 去重
    ws.cell(row=2, column=13, value=LONG_TEXT_2)         # -> R2C13
    ws.cell(row=11, column=3, value=LONG_TEXT_3)         # -> R11C3
    ws.cell(row=12, column=1, value="主要工程量：无")      # 短文本，不提取

    # 2) 隐蔽-接地汇总（台架接地）：2 个安装地点 + 1 条空记录
    ws = wb.create_sheet("隐蔽-接地汇总")
    _append(ws, ["隐蔽工程施工记录之二（台架接地）"])
    _append(ws, [])
    _append(ws, ["项目名称", None, "项目A"])
    _append(
        ws,
        ["序号", "安装地点", "圆钢水平地极（米）", "镀锌角钢（条）", "圆钢引出线（米）",
         "连接板（块）", "埋深（米）", "电阻实测值（Ω）", "备注"],
    )
    _append(ws, [None, None, "Φ16", "∠50*5*2500", "Φ16", "—40×4，L=300", "/", "/"])
    _append(ws, [1, "站点甲", 12, 4, 5.5, 1, 0.8, 3.5])
    _append(ws, [2, "站点乙", 12, 4, 5.5, 1, 0.8, 4.2])
    _append(ws, [3])
    _append(ws, ["施工人员签证"])

    # 3) 隐蔽-接地汇总 (2)（杆塔设备接地）：1 个安装地点
    ws = wb.create_sheet("隐蔽-接地汇总 (2)")
    _append(ws, ["隐蔽工程施工记录之二（杆塔设备接地）"])
    _append(ws, [])
    _append(ws, ["项目名称", None, "项目A"])
    _append(
        ws,
        ["序号", "安装地点", "圆钢水平地极（米）", "镀锌角钢（条）", "圆钢引出线（米）",
         "连接板（块）", "埋深（米）", "电阻实测值（Ω）", "备注"],
    )
    _append(ws, [None, None, "Φ16", "∠50*5*2500", "Φ16", "—40×4，L=300", "/", "/"])
    _append(ws, [1, "站点丙", 12, 4, 5.5, 1, 1.0, 3.9])
    _append(ws, ["施工人员签证"])

    # 4) 3.台变地网：两个安装地点并排（面板各 8 列） + 底部电阻实测值表
    ws = wb.create_sheet("3.台变地网")
    ws.cell(row=1, column=1, value="隐蔽工程施工记录之一（台架接地网部分）")
    ws.cell(row=3, column=1, value="项目名称")
    ws.cell(row=3, column=3, value="项目A")
    ws.cell(row=3, column=6, value="安装地点")
    ws.cell(row=3, column=7, value="站点甲")
    ws.cell(row=3, column=9, value="项目名称")
    ws.cell(row=3, column=11, value="项目A")
    ws.cell(row=3, column=14, value="安装地点")
    ws.cell(row=3, column=15, value="站点乙")
    header_cols = (
        ["材料", "类别", "角桩规格", "角桩根数", "角桩长度（米）", "圆钢规格", "圆钢长度（米）"],
        ["材料", "类别", "角桩规格", "角桩根数", "角桩长度（米）", "圆钢规格", "圆钢长度（米）"],
    )
    for panel_index, start_col in enumerate((1, 9)):
        for offset, text in enumerate(header_cols[panel_index]):
            ws.cell(row=4, column=start_col + offset, value=text)
            ws.cell(row=5, column=start_col + offset, value=("高压" if offset == 1 else None))
            ws.cell(row=6, column=start_col + offset, value=("低压" if offset == 1 else None))
    ws.cell(row=6, column=3, value="∠50*5")
    ws.cell(row=6, column=4, value=4)
    ws.cell(row=6, column=5, value=2.5)
    ws.cell(row=6, column=6, value="Φ16")
    ws.cell(row=6, column=7, value=20)
    ws.cell(row=6, column=11, value="∠50*5")
    ws.cell(row=6, column=12, value=4)
    ws.cell(row=6, column=13, value=2.5)
    ws.cell(row=6, column=14, value="Φ16")
    ws.cell(row=6, column=15, value=30)
    ws.cell(row=7, column=1, value="附图：")
    ws.cell(row=31, column=2, value="序号")
    ws.cell(row=31, column=3, value="安装地点")
    ws.cell(row=31, column=4, value="电阻实测值（Ω）")
    ws.cell(row=32, column=2, value=1)
    ws.cell(row=32, column=3, value="站点甲")
    ws.cell(row=32, column=4, value=3.1)
    ws.cell(row=33, column=2, value=2)
    ws.cell(row=33, column=3, value="站点乙")
    ws.cell(row=33, column=4, value=4.4)

    # 5) 隐蔽-拉盘：备注里含拉盘数量
    ws = wb.create_sheet("隐蔽-拉盘")
    _append(ws, ["隐蔽工程施工记录之二（拉盘及其它部分）"])
    _append(ws, [])
    _append(ws, ["项目名称", None, "项目A"])
    _append(ws, ["序号", "电杆编号", "电杆规格（米）", "拉盘规格", "拉盘埋深（米）", "备注"])
    _append(ws, [1, "P1", "10", "0.6*0.8", 2, "2组拉线，2个拉盘"])
    _append(ws, [2, "P2", "12", "0.6*0.8", 2, "1组拉线，1个拉盘"])
    _append(ws, ["施工人员签证"])

    # 6) 隐蔽-底盘
    ws = wb.create_sheet("隐蔽-底盘")
    _append(ws, ["隐蔽工程施工记录之二（底盘及其它部分）"])
    _append(ws, [])
    _append(ws, ["项目名称", None, "项目A"])
    _append(ws, ["序号", "电杆编号", "电杆规格（米）", "底盘规格", "底盘埋深（米）", "备注"])
    _append(ws, [1, "P3", "φ190×12", "0.5*0.5", 2])
    _append(ws, ["施工人员签证"])

    # 7) 安健环工程量见证表：2 项材料 + 1 条空记录
    ws = wb.create_sheet("安健环工程量见证表")
    _append(ws, ["安健环工程量见证表"])
    _append(ws, ["工程名称：", None, "项目A"])
    _append(ws, [])
    _append(ws, ["序号", "材料名称", "规格型号", "材质", "单位", "数量"])
    _append(ws, [1, "匿名标示牌(含安装架)", "320×200", "1mm铝板", "块", 6])
    _append(ws, [2, "匿名警示牌(含安装架)", "400×320", "1mm铝板", "块", 12])
    _append(ws, [3])
    _append(ws, ["施工单位："])

    # 8) 清理旧线路设施工程量登记表
    ws = wb.create_sheet("清理旧线路设施工程量登记表")
    _append(ws, ["清理旧线路设施工程量登记表"])
    _append(ws, ["工程名称：", "项目A"])
    _append(ws, [])
    _append(ws, ["序号", None, "项目名称", "规格、等级", "单位", "数量", "备注"])
    _append(ws, [1, None, "匿名变压器", "S11-M-100/10", "台", 2, "保护性拆除"])
    _append(ws, [2, None, "匿名开关", None, "台", 3, "破坏性拆除"])
    _append(ws, [])
    _append(ws, [None, "测量负责人："])

    # 9) 工程量签证表：长文本在结构化表内 -> 进 tables，不重复进 texts
    ws = wb.create_sheet("工程量签证表")
    _append(ws, ["工程量签证表"])
    _append(ws, ["工 程 名 称", "项目A"])
    _append(ws, [])
    _append(ws, ["序号", "工作地点", "签证内容"])
    _append(ws, [1, "匿名地点一", VISA_CONTENT])
    _append(ws, ["承包单位意见"])

    # 10) 质量控制记录：模板短文本，不应产生 tables/items
    ws = wb.create_sheet("5、设备接地装置质量控制记录 -2")
    _append(ws, ["接地装置质量控制记录表"])
    _append(ws, [])
    _append(ws, ["序号", None, "检查项目", "质量标准 / 检验结果"])
    _append(ws, [1, None, "接地网", "钢材的品种、规格、质量必须符合设计要求"])
    _append(ws, [2, None, "埋深", ">0.8 m"])
    _append(ws, ["施工单位检查人："])

    wb.save(path)


def _build_empty_grounding_workbook(path: Path) -> None:
    """只有序号、没有安装地点的接地汇总（模拟原资料缺项）。"""
    wb = Workbook()
    ws = wb.active
    ws.title = "隐蔽-接地汇总"
    _append(ws, ["隐蔽工程施工记录之二（台架接地）"])
    _append(ws, [])
    _append(ws, ["项目名称", None, "项目B"])
    _append(
        ws,
        ["序号", "安装地点", "圆钢水平地极（米）", "镀锌角钢（条）", "圆钢引出线（米）",
         "连接板（块）", "埋深（米）", "电阻实测值（Ω）", "备注"],
    )
    _append(ws, [None, None, "Φ16", "∠50*5*2500", "Φ16", "—40×4，L=300", "/", "/"])
    for seq in (1, 2, 3):
        _append(ws, [seq])
    _append(ws, ["施工人员签证"])
    wb.save(path)


# --------------------------------------------------------------------------- #
# 单元测试：匿名化 xlsx
# --------------------------------------------------------------------------- #
def test_matches():
    assert report.matches(["12竣工报告2", "工程量签证表"], "任意.xls") is True
    assert report.matches(["隐蔽-接地汇总"], "") is True
    assert report.matches(["安健环工程量见证表"], "") is True
    assert report.matches(["清理旧线路设施工程量登记表"], "") is True
    assert report.matches([], "项目A-2025年竣工报告.xls") is True
    # 结算书工作簿不应被识别为竣工报告
    assert (
        report.matches(
            ["检修工程总表(表一)", "设备检修专业单位工程表(表三甲)", "乙供未计价材料汇总表"],
            "项目A-26年送审.xlsx",
        )
        is False
    )
    assert report.matches(None, "项目A.xlsx") is False


def test_load_anonymous_report(tmp_path):
    path = tmp_path / "项目A-2025年竣工报告.xlsx"
    _build_report_workbook(path)

    doc = report.load(path)

    # 基本结构 + JSON 可序列化（契约要求模块间只传 dict/list）
    assert doc["kind"] == "report_workbook"
    assert set(doc) >= {"kind", "texts", "tables", "items", "totals", "meta", "warnings"}
    json.dumps(doc, ensure_ascii=False)

    # ---- texts：长文本提取、去重、结构化表内的长文本排除 ----
    texts = doc["texts"]
    assert len(texts) == 3
    assert [text["id"] for text in texts] == [
        "竣工报告!R2C1",
        "竣工报告!R2C13",
        "竣工报告!R11C3",
    ]
    assert texts[0]["label"] == "竣工报告·主要工程量"
    assert "R2C11" in texts[0].get("note", "")  # 同文本重复单元格的去重说明
    assert texts[1]["label"] == "竣工报告·新建部分"
    assert VISA_CONTENT not in [text["text"] for text in texts]  # 签证内容已进 tables

    # ---- tables ----
    tables = doc["tables"]
    assert len(tables) == 8
    categories = sorted(table["category"] for table in tables)
    assert categories == sorted(
        ["接地工程", "接地工程", "地网", "拉盘", "底盘", "安健环", "拆旧", "签证"]
    )
    for table in tables:
        assert set(table) >= {"id", "title", "columns", "rows"}
        assert table["columns"], f"{table['id']} 缺少表头"
        assert all(len(row) == len(table["columns"]) for row in table["rows"])

    grounding = next(
        table for table in tables if table["id"] == "隐蔽-接地汇总"
    )
    assert grounding["columns"][1] == "安装地点"
    assert grounding["columns"][2] == "圆钢水平地极（米）"
    assert grounding["rows"][0] == [1, "站点甲", 12, 4, 5.5, 1, 0.8, 3.5, ""]
    assert len(grounding["rows"]) == 2

    ground_grid = next(table for table in tables if table["category"] == "地网")
    assert len(ground_grid["rows"]) == 2
    # 多安装地点并排结构拆成行 + 底部电阻表合并
    assert ground_grid["rows"][0] == ["站点甲", "低压", "∠50*5", 4, 2.5, "Φ16", 20, 3.1]
    assert ground_grid["rows"][1] == ["站点乙", "低压", "∠50*5", 4, 2.5, "Φ16", 30, 4.4]

    visa = next(table for table in tables if table["category"] == "签证")
    assert len(visa["rows"]) == 1
    assert visa["rows"][0][2] == VISA_CONTENT

    # ---- items：接地按安装地点×材料展开；安健环；拆旧；拉盘/底盘计数 ----
    items = doc["items"]
    type_counts: dict[str, int] = {}
    for item in items:
        type_counts[item["item_type"]] = type_counts.get(item["item_type"], 0) + 1
    assert type_counts == {"接地": 12, "安健环": 2, "拆旧": 2, "工程量": 2}

    ground_items = [item for item in items if item["item_type"] == "接地"]
    assert {item["name"] for item in ground_items} == {
        "圆钢水平地极", "镀锌角钢", "圆钢引出线", "连接板",
    }
    assert all(item["section"] == "接地工程" for item in ground_items)
    assert all(item["supply"] == "" for item in ground_items)
    assert all(item["source"].startswith("项目A-2025年竣工报告.xlsx!") for item in ground_items)
    first_site = [
        item for item in ground_items if item["note"].startswith("安装地点：站点甲")
    ]
    assert len(first_site) == 4
    assert {item["unit"] for item in first_site} == {"米", "条", "块"}

    health_items = [item for item in items if item["item_type"] == "安健环"]
    assert [item["name"] for item in health_items] == ["匿名标示牌(含安装架)", "匿名警示牌(含安装架)"]
    assert health_items[0]["qty"] == 6
    assert health_items[0]["spec"] == "320×200"
    assert health_items[0]["note"] == "材质：1mm铝板"

    clearing_items = [item for item in items if item["item_type"] == "拆旧"]
    assert [item["name"] for item in clearing_items] == ["匿名变压器", "匿名开关"]
    assert clearing_items[0]["spec"] == "S11-M-100/10"
    assert clearing_items[0]["qty"] == 2
    assert clearing_items[1]["spec"] == ""

    rod = next(item for item in items if item["item_type"] == "工程量" and item["name"] == "拉盘")
    assert rod["qty"] == 3  # 备注 "2个拉盘" + "1个拉盘"
    assert rod["spec"] == "0.6*0.8"
    assert rod["unit"] == "个"
    plate = next(item for item in items if item["item_type"] == "工程量" and item["name"] == "底盘")
    assert plate["qty"] == 1
    assert plate["unit"] == "块"

    # ---- totals / warnings / meta ----
    totals = doc["totals"]
    assert totals["接地工程"]["count"] == 12
    assert totals["安健环"]["count"] == 2
    assert totals["拆旧"]["count"] == 2
    assert totals["texts_count"] == 3

    warnings = doc["warnings"]
    assert any("接地汇总" in text and "仅有编号" in text for text in warnings)
    assert any("安健环" in text and "仅有编号" in text for text in warnings)

    meta = doc["meta"]
    assert Path(meta["file"]).name == "项目A-2025年竣工报告.xlsx"
    assert meta["title"] == "项目A"
    assert meta["texts_count"] == 3
    assert meta["table_count"] == 8
    assert meta["item_count"] == 18
    assert meta["missing_sections"] == []
    assert len(meta["sheets"]) == 10


def test_load_empty_grounding_records(tmp_path):
    path = tmp_path / "项目B-2025年竣工报告.xlsx"
    _build_empty_grounding_workbook(path)

    doc = report.load(path)

    assert doc["texts"] == []
    assert doc["items"] == []
    assert len(doc["tables"]) == 1
    assert doc["tables"][0]["category"] == "接地工程"
    assert doc["tables"][0]["rows"] == []

    warnings = "\n".join(doc["warnings"])
    assert "记录为空" in warnings
    assert "原资料缺项" in warnings
    assert "未提取到竣工报告主要工程量长文本" in warnings
    assert len(doc["meta"]["missing_sections"]) == 4


# --------------------------------------------------------------------------- #
# 真实 .XLS 只读冒烟（文件不存在时跳过）
# --------------------------------------------------------------------------- #
_SMOKE_BASE = Path(
    os.environ.get("REPORT_SMOKE_BASE", r"D:\DeepSeek Harness\资料对比工作流")
)
_SMOKE_CACHE: dict[int, dict] = {}


def _find_real_report(year: int) -> Path | None:
    """按年份目录 + 文件名特征找真实竣工报告（不写死真实项目名）。"""
    folder = _SMOKE_BASE / f"{year}年资料"
    if not folder.is_dir():
        return None
    keyword = f"{year}年竣工报告"
    candidates = [
        item
        for item in folder.iterdir()
        if item.is_file()
        and item.name.lower().endswith((".xls", ".xlsx"))
        and keyword in item.name
    ]
    return sorted(candidates)[0] if candidates else None


def _load_real_report(year: int) -> dict:
    if year not in _SMOKE_CACHE:
        path = _find_real_report(year)
        if path is None:
            pytest.skip(f"真实 {year} 年竣工报告不存在，跳过只读冒烟测试")
        _SMOKE_CACHE[year] = report.load(path)
    return _SMOKE_CACHE[year]


def test_real_report_2025_smoke():
    doc = _load_real_report(2025)

    assert doc["kind"] == "report_workbook"
    # 2025 原资料只有竣工报告长文本 2 段（R2C1 / R11C3）
    assert len(doc["texts"]) == 2
    assert {text["id"].split("!")[1] for text in doc["texts"]} == {"R2C1", "R11C3"}
    # 签证内容属于结构化表，不重复进入 texts
    assert all("签证" not in text["id"] for text in doc["texts"])

    # 2025 接地汇总记录为空：不产出 items，warnings 报原资料缺项
    assert doc["items"] == []
    assert any(table["category"] == "接地工程" and table["rows"] == [] for table in doc["tables"])
    warnings = "\n".join(doc["warnings"])
    assert "记录为空" in warnings
    assert "原资料缺项" in warnings
    assert "安健环" in warnings  # 2025 无安健环表

    # 多面板地网表仍能提取（2025 有设备地网记录）
    ground_grids = [table for table in doc["tables"] if table["category"] == "地网"]
    assert len(ground_grids) == 1
    assert len(ground_grids[0]["rows"]) == 4


def test_real_report_2026_smoke():
    doc = _load_real_report(2026)

    # 2026 竣工报告长文本 >= 4 段（R2C1 与 R2C11 同文本去重后 4 段）
    assert len(doc["texts"]) >= 4
    assert {text["id"].split("!")[1] for text in doc["texts"]} >= {
        "R2C1", "R2C13", "R2C21", "R11C3",
    }

    item_counts: dict[str, int] = {}
    for item in doc["items"]:
        item_counts[item["item_type"]] = item_counts.get(item["item_type"], 0) + 1
    # 真实文件：接地汇总 7 个安装地点 × 4 种材料
    assert item_counts.get("接地") == 28
    # 真实文件安健环数据行为 R5–R14 共 10 项（任务描述的 12 与真实资料不一致，按真实读取断言）
    assert item_counts.get("安健环") == 10
    assert item_counts.get("拆旧") == 16

    assert doc["totals"]["接地工程"]["count"] == 28
    assert doc["totals"]["安健环"]["count"] == 10
    assert doc["totals"]["拆旧"]["count"] == 16

    # 拉盘/底盘计数类 items
    extra = {item["name"]: item for item in doc["items"] if item["item_type"] == "工程量"}
    assert extra["拉盘"]["qty"] == 4
    assert extra["拉盘"]["unit"] == "个"
    assert extra["底盘"]["qty"] == 12
    assert extra["底盘"]["unit"] == "块"

    categories = [table["category"] for table in doc["tables"]]
    assert categories.count("接地工程") == 2
    assert categories.count("地网") == 3
    assert "安健环" in categories
    assert "拆旧" in categories

    warnings = "\n".join(doc["warnings"])
    assert "仅有编号" in warnings
    # 2026 资料齐全，不应出现“未发现安健环/清理旧线路”的缺项 warning
    assert "未发现安健环" not in warnings
    assert "未发现清理旧线路" not in warnings
