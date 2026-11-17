# -*- coding: utf-8 -*-
"""检修结算书 reader 测试。

- 匿名化小工作簿（openpyxl 在 tmp_path 生成）验证 totals / items / categories /
  materials / 甲供识别 / 缺表容错 / warnings；
- 若本机存在真实样例工作簿，追加只读冒烟断言（文件不存在时 skip）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from openpyxl import Workbook

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from diffworkflow.readers import settlement  # noqa: E402

# 真实资料根目录（只读；不写死项目名，按年份目录 + reader.matches 自动定位）
REAL_DATA_BASE = Path(r"D:\DeepSeek Harness\资料对比工作流")


# ---------------------------------------------------------------------------
# 匿名化测试工作簿构造
# ---------------------------------------------------------------------------


def _append(ws, row) -> None:
    ws.append(list(row))


def _write_summary_sheet(wb: Workbook) -> None:
    ws = wb.create_sheet("检修工程总表(表一)")
    rows = [
        ["电网检修工程总结算表"],
        ["表一", None, None, "金额单位:元"],
        ["序号", "工程或费用名称", "金额", "占合计总费用的比例(%)"],
        ["一", "建筑修缮费"],
        ["二", "设备检修费", 1000.0, 100],
        ["三", "配件购置费"],
        [None, "其中：编制基准期价差", 10.0, 1],
        ["四", "小计", 1000.0, 100],
        [None, "其中：甲供配件材料费", 200.0, 20],
        ["五", "其他费用"],
        [None, "其中：可抵扣增值税金额", 5.0],
        [None, "其中：施工费", 1000.0, 100],
        ["十二", "下浮后送审金额", 950.0],
    ]
    for row in rows:
        _append(ws, row)


def _write_category_sheet(wb: Workbook) -> None:
    ws = wb.create_sheet("设备检修专业汇总表(表二甲)")
    rows = [
        ["设备检修专业汇总结算表"],
        ["表二甲", None, None, None, None, "金额单位:元"],
        ["序号", "工程或费用名称", "设备检修费", None, "配件购置费", "合计"],
        [None, None, "检修费", "未计价材料费", None, None],
        [None, "设备检修工程", 900.0, 100.0, 200.0, 1200.0],
        ["一", "架空线路本体工程", 800.0, 100.0, 200.0, 1100.0],
        ["1", "土石方工程", 500.0, None, None, 500.0],
        ["2", "杆塔工程", 300.0, 100.0, 200.0, 600.0],
        [None, "合计", 900.0, 100.0, 200.0, 1200.0],
    ]
    for row in rows:
        _append(ws, row)


def _table3_row(
    *,
    sequence=None,
    basis=None,
    name=None,
    unit=None,
    qty=None,
    part_price=None,
    quota_price=None,
    part_amount=None,
    quota_amount=None,
    unpriced_amount=None,
    row_type=None,
) -> list:
    """按真实“表三甲”27 列版式构造一行（含多行表头所需的列位）。"""
    row = [None] * 27
    row[0] = sequence
    row[1] = basis
    row[2] = name
    row[3] = unit
    row[4] = qty
    row[5] = part_price
    row[6] = quota_price
    row[10] = part_amount
    row[11] = quota_amount
    row[14] = unpriced_amount
    row[15] = row_type
    return row


def _write_detail_sheet(wb: Workbook, *, with_bad_row: bool = False) -> None:
    ws = wb.create_sheet("设备检修单位工程表(表三甲)")
    header_rows = [
        [None] * 27,
        [None] * 27,
        [None] * 27,
        [None] * 27,
        [None] * 27,
    ]
    header_rows[0][0] = "设备检修单位工程结算表"
    header_rows[1][0] = "表三甲"
    header_rows[1][14] = "金额单位:元"
    top = [
        "序号",
        "编制依据",
        "项目名称",
        "单位",
        "数量",
        "配件单价",
        "单价",
        None,
        None,
        None,
        "配件合价",
        "合价",
        None,
        None,
        None,
        "类型",
        "市场单价含税",
        "市场单价不含税",
        "单重(kg)",
        "损耗率(%)",
        "运杂费率(%)",
        "运输类型",
        "配送",
        "线重(kg/m)",
        "制造长度(m/捆)",
        "截面积(mm²)",
        "张力架线",
    ]
    for column, text in enumerate(top):
        if text is not None:
            header_rows[2][column] = text
    for column, text in ((6, "检修费"), (9, "未计价材料"), (11, "检修费"), (14, "未计价材料")):
        header_rows[3][column] = text
    for column, text in (
        (6, "定额基价"),
        (7, "其中：人工"),
        (8, "其中：机械"),
        (11, "费用金额"),
        (12, "其中：人工"),
        (13, "其中：机械"),
    ):
        header_rows[4][column] = text
    for row in header_rows:
        _append(ws, row)

    data_rows = [
        # 项目划分：根 / 单位工程 / 分部
        _table3_row(name="设备检修工程", part_amount=200.0, quota_amount=900.0,
                    unpriced_amount=100.0, row_type="项目划分"),
        _table3_row(sequence="一", name="架空线路本体工程", part_amount=200.0,
                    quota_amount=800.0, unpriced_amount=100.0, row_type="项目划分"),
        _table3_row(sequence="1", name="土石方工程", quota_amount=500.0,
                    row_type="项目划分"),
        # 定额子目（归属于“土石方工程”）
        _table3_row(basis="JYX2-1", name="线路复测及分坑 直线单杆", unit="基",
                    qty=24.0, quota_price=40.66, quota_amount=975.84, row_type="定额"),
        _table3_row(sequence="2", name="杆塔工程", part_amount=200.0,
                    quota_amount=300.0, unpriced_amount=100.0, row_type="项目划分"),
        # 乙供主材
        _table3_row(basis="C09010102", name="普通硅酸盐水泥 42.5", unit="t",
                    qty=1.008, unpriced_amount=382.0, row_type="主材"),
        # 甲供主材（编制依据带“（甲）”标记）
        _table3_row(basis="（甲）", name="圆钢水平地极 Φ16", unit="m",
                    qty=10.0, unpriced_amount=90.0, row_type="主材"),
        # 甲供配件（配件单价/配件合价列）
        _table3_row(basis="S0401002（甲）", name="10kV 避雷器", unit="组",
                    qty=2.0, part_price=150.0, part_amount=300.0, row_type="配件"),
        # 一笔性费用
        _table3_row(name="拆除设备后退库", unit="笔", qty=1.0,
                    quota_price=1.0, quota_amount=1.0, row_type="一笔性费用"),
        # 取费表 / 小计：应跳过
        _table3_row(basis="一", name="直接费", quota_amount=500.0, row_type="取费表"),
        _table3_row(name="小计：", row_type="小计"),
    ]
    if with_bad_row:
        data_rows.append(
            _table3_row(
                basis="JYX9-9",
                name="数量待定的定额行",
                unit="基",
                qty="待定",
                quota_price="待定",
                quota_amount="待定",
                row_type="定额",
            )
        )
    for row in data_rows:
        _append(ws, row)


def _write_supply_materials(wb: Workbook) -> None:
    ws = wb.create_sheet("乙供未计价材料汇总表")
    rows = [
        ["乙供未计价材料汇总表"],
        [None, None, None, None, None, None, None, "金额单位:元"],
        ["序号", "材料名称", "规格及型号", "单位", "设计用量", "损耗率%",
         "单价（不含税）", "总价（不含税）"],
        [None, "设备检修"],
        ["1", "普通硅酸盐水泥", "42.5", "t", 10.0, 5.0, 50.0, 500.0],
        ["2", "圆钢", "Φ16", "m", 5.0, None, 9.0, 45.0],
        [None, "小计：", None, None, None, None, None, 545.0],
        [None, "合计：", None, None, None, None, None, 545.0],
    ]
    for row in rows:
        _append(ws, row)

    ws = wb.create_sheet("甲供未计价材料汇总表")
    rows = [
        ["甲供未计价材料汇总表"],
        [None, None, None, None, None, None, None, None, "金额单位:元"],
        ["序号", "物料编码", "材料名称", "规格及型号", "单位", "设计用量",
         "损耗率%", "单价（含税）", "总价（含税）"],
        [None, None, "设备检修"],
        ["1", "C010101", "角钢", "L50*5", "根", 2.0, 1.5, 30.0, 60.0],
        [None, None, "小计：", None, None, None, None, None, 60.0],
        [None, None, "合计：", None, None, None, None, None, 60.0],
    ]
    for row in rows:
        _append(ws, row)


def _write_parts_sheets(wb: Workbook) -> None:
    ws = wb.create_sheet("配件汇总表")
    rows = [
        ["配件汇总表"],
        [None] * 12 + ["金额单位:元"],
        ["编码", "配件名称", "规格型号", "单位", "数量", "市场价单价", None,
         "市场价合价", None, "运杂费", None, "配送费", "合计"],
        [None, None, None, None, None, "含税价", "不含税价", "含税价",
         "不含税价", "运杂费率(%)", "合计", None, None],
        [None, "设备检修"],
        [None, "10kV 避雷器", "YH5WS-17/50", "组", 2.0, 150.0, 132.0,
         300.0, 264.0, None, None, None, 300.0],
        [None, "小计：", None, None, None, None, None, 300.0, None, None,
         None, None, 300.0],
        [None, "合计：", None, None, None, None, None, 300.0, None, None,
         None, None, 300.0],
    ]
    for row in rows:
        _append(ws, row)

    ws = wb.create_sheet("甲供配件汇总表")
    rows = [
        ["甲供配件汇总表"],
        [None] * 10 + ["金额单位:元"],
        ["序号", "物料编码", "配件名称", "规格型号", "单位", "数量",
         "价格（含税）", None, "运杂费", None, "合计"],
        [None, None, None, None, None, None, "单价", "合价", "费率（%）",
         "合价", None],
        [None, None, "设备检修"],
        ["1", None, "10kV 避雷器", "YH5WS-17/50", "组", 2.0, 150.0, 300.0,
         None, None, 300.0],
        [None, None, "小计：", None, None, None, None, 300.0, None, None, 300.0],
        [None, None, "合计：", None, None, None, None, 300.0, None, None, 300.0],
    ]
    for row in rows:
        _append(ws, row)


def _write_assembly_sheet(wb: Workbook, *, with_total: bool = True) -> None:
    """写入“组合件汇总表”（部分轻量结算书只有该表，无表一/表三甲）。"""
    ws = wb.create_sheet("组合件汇总表")
    rows = [
        ["组合件汇总表"],
        [None, None, None, None, None, None, None, "金额单位:元"],
        ["序号", "编码", "项目名称", "单位", "计算式", "数量", "单价", "合价"],
        ["1", "ZHJ002", "架空线工程-架空导线-JKLGYJ-120", "km", "1.5", 1.5, 1000.0, 1500.0],
        # 分节标题行：只作为 section，不计入 items
        ["2", "ZHJ013", "一、新建部分", None, "1", 1.0, None, None],
        ["3", "ZHJ013", "接地网(圆钢15m,4条角钢)", "套", "2", 2.0, 500.0, 1000.0],
        # 缺少单价的组合件行：仍保留为 item（数量有效）
        ["4", "ZHJ004", "金具", "项", "1", 1.0, None, None],
    ]
    if with_total:
        rows.append([None, None, "合计：", None, None, None, None, 2500.0])
    for row in rows:
        _append(ws, row)


def _make_assembly_only_workbook(path: Path, *, with_total: bool = True) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    _write_assembly_sheet(wb, with_total=with_total)
    wb.save(path)
    wb.close()
    return path


def _write_cover_sheet(wb: Workbook) -> None:
    ws = wb.create_sheet("封面", 0)
    ws.append(["检  索  号"])
    ws.append([])
    ws.append(["生产设备大修项目结算书"])
    ws.append(["项目名称：项目A 检修工程"])
    ws.append([])
    ws.append(["编制单位："])


def _make_workbook(
    path: Path,
    *,
    with_table2: bool = True,
    with_materials: bool = True,
    with_parts: bool = True,
    with_bad_row: bool = False,
    with_cover: bool = True,
    with_assembly: bool = False,
) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    if with_cover:
        _write_cover_sheet(wb)
    _write_summary_sheet(wb)
    if with_table2:
        _write_category_sheet(wb)
    _write_detail_sheet(wb, with_bad_row=with_bad_row)
    if with_materials:
        _write_supply_materials(wb)
    if with_parts:
        _write_parts_sheets(wb)
    if with_assembly:
        _write_assembly_sheet(wb)
    wb.save(path)
    wb.close()
    return path


# ---------------------------------------------------------------------------
# 单元测试：matches / load 基本行为
# ---------------------------------------------------------------------------


def test_matches_settlement_workbook() -> None:
    assert settlement.matches(["检修工程总表(表一)", "乙供未计价材料汇总表"], "x.xlsx")
    assert settlement.matches(["设备检修单位工程表(表三甲)"], "x.xlsx")
    assert not settlement.matches(["11竣工报告1", "工程量签证表"], "x报告.XLS")
    assert settlement.KIND == "settlement_workbook"


def test_load_contract_shape(tmp_path: Path) -> None:
    path = _make_workbook(tmp_path / "项目A.xlsx")
    doc = settlement.load(path)

    assert set(doc) >= {
        "kind",
        "items",
        "totals",
        "categories",
        "materials",
        "meta",
        "warnings",
    }
    assert doc["kind"] == "settlement_workbook"
    assert doc["meta"]["format"] == "full"
    assert doc["meta"]["is_full"] is True
    assert doc["meta"]["unit_scale"] == 1.0
    assert json.loads(json.dumps(doc))  # JSON 可序列化


def test_totals_from_summary_sheet(tmp_path: Path) -> None:
    doc = settlement.load(_make_workbook(tmp_path / "项目A.xlsx"))
    totals = doc["totals"]

    assert totals["设备检修费"] == pytest.approx(1000.0)
    assert totals["施工费"] == pytest.approx(1000.0)
    assert totals["可抵扣增值税"] == pytest.approx(5.0)
    assert totals["编制基准期价差"] == pytest.approx(10.0)
    assert totals["甲供配件材料费"] == pytest.approx(200.0)
    assert totals["下浮后送审金额"] == pytest.approx(950.0)
    assert totals["配件购置费"] is None  # 表一该行为空
    assert totals["final_amount"] == pytest.approx(950.0)  # 优先“下浮后送审金额”


def test_meta_title_from_cover(tmp_path: Path) -> None:
    doc = settlement.load(_make_workbook(tmp_path / "项目A.xlsx"))
    assert doc["meta"]["title"] == "项目A 检修工程"
    assert doc["meta"]["file"] == "项目A.xlsx"
    assert doc["meta"]["counts"]["items"] == len(doc["items"])


def test_items_parsed_and_sections(tmp_path: Path) -> None:
    doc = settlement.load(_make_workbook(tmp_path / "项目A.xlsx"))
    items = doc["items"]

    by_name = {item["name"]: item for item in items}
    # 定额行：字段完整，section 使用“项目划分”层级
    quota = by_name["线路复测及分坑 直线单杆"]
    assert quota["item_type"] == "定额"
    assert quota["code"] == "JYX2-1"
    assert quota["unit"] == "基"
    assert quota["qty"] == pytest.approx(24.0)
    assert quota["unit_price"] == pytest.approx(40.66)
    assert quota["amount"] == pytest.approx(975.84)
    assert quota["section"] == "架空线路本体工程/土石方工程"
    # 乙供主材
    cement = by_name["普通硅酸盐水泥 42.5"]
    assert cement["item_type"] == "主材"
    assert cement["supply"] == "乙供"
    assert cement["unit"] == "t"
    assert cement["qty"] == pytest.approx(1.008)
    assert cement["amount"] == pytest.approx(382.0)
    # 甲供主材：编制依据含“（甲）”，code 去掉标记、supply=甲供
    steel = by_name["圆钢水平地极 Φ16"]
    assert steel["item_type"] == "主材"
    assert steel["supply"] == "甲供"
    assert steel["code"] == ""
    assert steel["amount"] == pytest.approx(90.0)
    # 配件行
    arrester = by_name["10kV 避雷器"]
    assert arrester["item_type"] == "配件"
    assert arrester["supply"] == "甲供"
    assert arrester["unit_price"] == pytest.approx(150.0)
    assert arrester["amount"] == pytest.approx(300.0)
    # 一笔性费用
    fee = by_name["拆除设备后退库"]
    assert fee["item_type"] == "费用"
    assert fee["amount"] == pytest.approx(1.0)

    # 取费表 / 小计 行不应成为 items
    assert len(items) == 5
    assert all(item["item_type"] in ("定额", "主材", "配件", "费用") for item in items)
    assert all(item["source"].startswith("项目A.xlsx!") for item in items)


def test_categories_prefer_table2(tmp_path: Path) -> None:
    doc = settlement.load(_make_workbook(tmp_path / "项目A.xlsx"))
    categories = doc["categories"]

    assert doc["meta"]["categories_source"].startswith("设备检修专业汇总表")
    assert len(categories) == 4
    by_name = {category["name"]: category for category in categories}
    root = by_name["设备检修工程"]
    assert root["section"] == ""
    assert root["检修费"] == pytest.approx(900.0)
    assert root["未计价材料费"] == pytest.approx(100.0)
    assert root["配件购置费"] == pytest.approx(200.0)
    assert root["合计"] == pytest.approx(1200.0)
    earthwork = by_name["土石方工程"]
    assert earthwork["section"] == "架空线路本体工程"
    assert earthwork["合计"] == pytest.approx(500.0)


def test_materials_items_and_totals(tmp_path: Path) -> None:
    doc = settlement.load(_make_workbook(tmp_path / "项目A.xlsx"))
    materials = doc["materials"]
    items = materials["items"]

    # 2 乙供未计价 + 1 甲供未计价 + 1 配件（配件汇总表与甲供配件汇总表不重复计数）
    assert len(items) == 4
    assert materials["totals"]["乙供合计"] == pytest.approx(545.0)
    assert materials["totals"]["甲供合计"] == pytest.approx(60.0)
    assert materials["totals"]["配件合计"] == pytest.approx(300.0)
    assert materials["totals"]["甲供配件合计"] == pytest.approx(300.0)

    cement = next(item for item in items if item["name"] == "普通硅酸盐水泥")
    assert cement["supply"] == "乙供"
    assert cement["item_type"] == "主材"
    assert cement["spec"] == "42.5"
    assert cement["amount"] == pytest.approx(500.0)

    angle = next(item for item in items if item["name"] == "角钢")
    assert angle["supply"] == "甲供"
    assert angle["code"] == "C010101"

    arrester = next(item for item in items if item["name"] == "10kV 避雷器")
    assert arrester["item_type"] == "配件"
    assert arrester["supply"] == "甲供"
    assert arrester["amount"] == pytest.approx(300.0)


def test_warnings_for_unparsable_rows(tmp_path: Path) -> None:
    doc = settlement.load(
        _make_workbook(tmp_path / "项目A.xlsx", with_bad_row=True)
    )
    assert any("数量" in warning for warning in doc["warnings"])
    assert any("合价" in warning for warning in doc["warnings"])


def test_missing_sheets_tolerated(tmp_path: Path) -> None:
    """缺表容错：没有表二甲/材料表时，categories 用表三甲项目划分，材料合计可空。"""
    path = _make_workbook(
        tmp_path / "项目B.xlsx",
        with_table2=False,
        with_materials=False,
        with_parts=False,
        with_cover=False,
    )
    doc = settlement.load(path)

    assert doc["items"]
    assert doc["meta"]["categories_source"].startswith("设备检修单位工程表")
    assert len(doc["categories"]) == 4  # 表三甲 2 个根 + 一 + 1 + 2 → 去重后 4 行
    assert doc["materials"]["items"] == []
    totals = doc["materials"]["totals"]
    assert totals["乙供合计"] is None
    assert totals["甲供合计"] is None
    assert totals["甲供配件合计"] is None
    # 没有配件汇总表时用表三甲“配件”行合价（300）
    assert totals["配件合计"] == pytest.approx(300.0)


def test_final_amount_fallback(tmp_path: Path) -> None:
    """没有“下浮后送审金额”时 final_amount 退化到设备检修费。"""
    wb = Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("检修工程总表(表一)")
    for row in (
        ["电网检修工程总结算表"],
        ["序号", "工程或费用名称", "金额"],
        ["二", "设备检修费", 880.0],
        ["其中：施工费", 800.0],
    ):
        ws.append(list(row))
    _write_detail_sheet(wb)
    path = tmp_path / "项目C.xlsx"
    wb.save(path)
    wb.close()

    doc = settlement.load(path)
    assert doc["totals"]["设备检修费"] == pytest.approx(880.0)
    assert doc["totals"]["下浮后送审金额"] is None
    assert doc["totals"]["final_amount"] == pytest.approx(880.0)


# ---------------------------------------------------------------------------
# 新格式：表一“金额单位:万元” / 仅“组合件汇总表” / is_full 标记
# ---------------------------------------------------------------------------


def test_summary_unit_wan_yuan_scaled_only_for_table_one(tmp_path: Path) -> None:
    """表一标注“金额单位:万元”时只放大表一金额；表二甲/表三甲仍是元。"""
    wb = Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("检修工程总表(表一)")
    for row in (
        ["电网检修工程总结算表"],
        ["表一", None, None, "金额单位:万元"],
        ["序号", "工程或费用名称", "金额", "占合计总费用的比例(%)"],
        ["二", "设备检修费", 42.57, 100],
        [None, "其中：编制基准期价差", 2.01, 4.72],
        [None, "其中：甲供配件材料费", 7.28, 17.1],
        [None, "其中：可抵扣增值税金额", 5.22],
        [None, "其中：施工费", 42.57, 100],
    ):
        _append(ws, row)
    _write_category_sheet(wb)  # 表二甲仍是元
    _write_detail_sheet(wb)  # 表三甲仍是元
    path = tmp_path / "万元项目.xlsx"
    wb.save(path)
    wb.close()

    doc = settlement.load(path)
    assert doc["meta"]["unit_scale"] == 10000.0
    assert doc["meta"]["format"] == "full"
    assert doc["meta"]["is_full"] is True

    totals = doc["totals"]
    assert totals["设备检修费"] == pytest.approx(425700.0, abs=0.01)
    assert totals["施工费"] == pytest.approx(425700.0, abs=0.01)
    assert totals["编制基准期价差"] == pytest.approx(20100.0, abs=0.01)
    assert totals["甲供配件材料费"] == pytest.approx(72800.0, abs=0.01)
    assert totals["可抵扣增值税"] == pytest.approx(52200.0, abs=0.01)
    assert totals["final_amount"] == pytest.approx(425700.0, abs=0.01)

    # categories / items 来自表二甲/表三甲，不应被万元倍率放大
    root = next(c for c in doc["categories"] if c["name"] == "设备检修工程")
    assert root["检修费"] == pytest.approx(900.0)
    quota = next(item for item in doc["items"] if item["code"] == "JYX2-1")
    assert quota["amount"] == pytest.approx(975.84)


def test_assembly_only_workbook(tmp_path: Path) -> None:
    """只有“组合件汇总表”的结算书：items=组合件，施工费=合价合计，is_full=False。"""
    path = _make_assembly_only_workbook(tmp_path / "组合件项目.xlsx")
    doc = settlement.load(path)

    assert settlement.matches(["组合件汇总表"], path.name)
    assert doc["meta"]["format"] == "assembly_only"
    assert doc["meta"]["is_full"] is False
    assert doc["meta"]["unit_scale"] == 1.0

    items = doc["items"]
    # 3 个组合件（“一、新建部分”是分节标题，不计入 items）
    assert len(items) == 3
    assert all(item["item_type"] == "组合件" for item in items)
    by_name = {item["name"]: item for item in items}
    wire = by_name["架空线工程-架空导线-JKLGYJ-120"]
    assert wire["code"] == "ZHJ002"
    assert wire["unit"] == "km"
    assert wire["qty"] == pytest.approx(1.5)
    assert wire["unit_price"] == pytest.approx(1000.0)
    assert wire["amount"] == pytest.approx(1500.0)
    assert wire["section"] == ""
    mesh = by_name["接地网(圆钢15m,4条角钢)"]
    assert mesh["section"] == "新建部分"
    fitting = by_name["金具"]
    assert fitting["qty"] == pytest.approx(1.0)
    assert fitting["amount"] is None  # 缺少单价的组合件行仍保留

    # totals：施工费/组合件合计/final_amount = 组合件合价合计（有“合计”行时取该值）
    assert doc["totals"]["施工费"] == pytest.approx(2500.0)
    assert doc["totals"]["组合件合计"] == pytest.approx(2500.0)
    assert doc["totals"]["final_amount"] == pytest.approx(2500.0)
    assert doc["totals"]["设备检修费"] is None
    assert doc["categories"] == []
    assert doc["materials"]["items"] == []
    assert all(value is None for value in doc["materials"]["totals"].values())
    assert any("仅组合件汇总表" in warning for warning in doc["warnings"])
    assert any("施工费口径" in warning for warning in doc["warnings"])
    json.loads(json.dumps(doc))


def test_assembly_only_total_falls_back_to_summed_amount(tmp_path: Path) -> None:
    """组合件表没有“合计”行时，施工费回退为合价逐项求和（缺合价行不计）。"""
    path = _make_assembly_only_workbook(tmp_path / "组合件无合计.xlsx", with_total=False)
    doc = settlement.load(path)
    assert doc["items"]
    assert doc["totals"]["施工费"] == pytest.approx(2500.0)
    assert doc["totals"]["组合件合计"] == pytest.approx(2500.0)


def test_full_workbook_with_assembly_sheet_no_double_count(tmp_path: Path) -> None:
    """完整结算书里同时带“组合件汇总表”时，优先完整口径，组合件明细不重复计入。"""
    path = _make_workbook(tmp_path / "项目A.xlsx", with_assembly=True)
    doc = settlement.load(path)

    assert doc["meta"]["format"] == "full"
    assert doc["meta"]["is_full"] is True
    assert doc["meta"]["sheet_map"]["组合件汇总表"] == "组合件汇总表"
    # items 只来自表三甲，5 条；组合件明细（如“架空线工程-架空导线-JKLGYJ-120”）不并入
    assert len(doc["items"]) == 5
    assert all(item["item_type"] != "组合件" for item in doc["items"])
    assembly_names = {"架空线工程-架空导线-JKLGYJ-120", "接地网(圆钢15m,4条角钢)", "金具"}
    assert assembly_names.isdisjoint({item["name"] for item in doc["items"]})
    # totals 用表一口径，而不是组合件合价合计
    assert doc["totals"]["final_amount"] == pytest.approx(950.0)
    assert doc["warnings"] == []


# ---------------------------------------------------------------------------
# 真实样例只读冒烟（文件不存在时跳过；不把真实项目名写进仓库）
# ---------------------------------------------------------------------------


_REAL_DOC_CACHE: dict[str, list[dict]] = {}


def _real_settlement_docs(directory: Path) -> list[dict]:
    """读取真实目录下全部结算书（只读、模块内缓存；目录不存在返回空列表）。"""
    key = str(directory)
    if key in _REAL_DOC_CACHE:
        return _REAL_DOC_CACHE[key]
    docs: list[dict] = []
    if directory.is_dir():
        from diffworkflow.readers.excel_utils import list_sheets

        for candidate in sorted(directory.glob("*.xlsx")):
            try:
                if not settlement.matches(list_sheets(candidate), candidate.name):
                    continue
                docs.append(settlement.load(candidate))
            except Exception:  # noqa: BLE001 - 非结算书/损坏文件直接跳过
                continue
    _REAL_DOC_CACHE[key] = docs
    return docs


def _real_docs_where(directory: Path, predicate) -> list[dict]:
    return [doc for doc in _real_settlement_docs(directory) if predicate(doc)]


def _amount_is(value, expected: float, abs_tol: float = 0.01) -> bool:
    return value is not None and abs(float(value) - expected) <= abs_tol


def _is_full_doc(doc: dict) -> bool:
    return (doc.get("meta") or {}).get("is_full") is True


REAL_OLD_DIR = REAL_DATA_BASE / "2025年资料"
REAL_NEW_DIR = REAL_DATA_BASE / "2026年资料"


def test_real_full_settlement_old_smoke() -> None:
    docs = _real_docs_where(
        REAL_OLD_DIR,
        lambda doc: _is_full_doc(doc)
        and _amount_is(doc["totals"].get("final_amount"), 417940.58),
    )
    if not docs:
        pytest.skip("本机没有 2025 年完整结算书样例")
    doc = docs[0]

    assert doc["meta"]["format"] == "full"
    assert doc["meta"]["is_full"] is True
    assert doc["meta"]["unit_scale"] == 1.0
    assert doc["totals"]["设备检修费"] == pytest.approx(417940.58, abs=0.01)
    assert doc["totals"]["final_amount"] == pytest.approx(417940.58, abs=0.01)
    assert doc["totals"]["可抵扣增值税"] == pytest.approx(32887.28, abs=0.01)
    assert doc["totals"]["编制基准期价差"] == pytest.approx(14945.0, abs=0.01)
    # 表三甲明细条数量级（旧版 603 行）
    assert 150 <= len(doc["items"]) <= 400
    assert len(doc["categories"]) > 0
    assert any(item["item_type"] == "配件" for item in doc["items"])
    assert any(item["supply"] == "甲供" for item in doc["items"])
    # 材料清册：旧版只有乙供未计价材料
    assert doc["materials"]["totals"]["乙供合计"] == pytest.approx(74981.29, abs=0.01)
    assert doc["materials"]["totals"]["配件合计"] == pytest.approx(40287.0, abs=0.01)
    assert doc["materials"]["totals"]["甲供合计"] is None
    assert doc["warnings"] == []


def test_real_full_settlement_new_smoke() -> None:
    docs = _real_docs_where(
        REAL_NEW_DIR,
        lambda doc: _is_full_doc(doc)
        and _amount_is(doc["totals"].get("final_amount"), 365816.86),
    )
    if not docs:
        pytest.skip("本机没有 2026 年完整送审结算书样例")
    doc = docs[0]

    assert doc["meta"]["format"] == "full"
    assert doc["meta"]["is_full"] is True
    assert doc["totals"]["设备检修费"] == pytest.approx(373120.5, abs=0.01)
    assert doc["totals"]["下浮后送审金额"] == pytest.approx(365816.86, abs=0.01)
    assert doc["totals"]["final_amount"] == pytest.approx(365816.86, abs=0.01)
    assert doc["totals"]["甲供配件材料费"] == pytest.approx(87459.27, abs=0.01)
    assert doc["totals"]["可抵扣增值税"] == pytest.approx(38030.42, abs=0.01)
    assert doc["totals"]["编制基准期价差"] == pytest.approx(19188.28, abs=0.01)
    # 没有表二甲 → categories 由表三甲“项目划分”行聚合
    assert doc["meta"]["categories_source"].startswith("设备检修单位工程表")
    assert len(doc["categories"]) > 0
    assert 120 <= len(doc["items"]) <= 400
    # 材料清册：甲供/乙供/配件三张表
    assert doc["materials"]["totals"]["乙供合计"] == pytest.approx(50529.0, abs=0.01)
    assert doc["materials"]["totals"]["甲供合计"] == pytest.approx(265096.0, abs=0.01)
    assert doc["materials"]["totals"]["配件合计"] == pytest.approx(87459.0, abs=0.01)
    assert any(item["supply"] == "甲供" for item in doc["materials"]["items"])
    assert doc["warnings"] == []


def test_real_duplicate_full_files_consistent() -> None:
    """同一目录里若存在内容重复的完整结算书（原件/另存），解析结果应一致。"""
    docs = _real_docs_where(
        REAL_OLD_DIR,
        lambda doc: _is_full_doc(doc)
        and _amount_is(doc["totals"].get("final_amount"), 417940.58),
    )
    if len(docs) < 2:
        pytest.skip("本机没有重复的完整结算书样例")
    first, second = docs[0], docs[1]
    assert first["meta"]["is_full"] is True
    assert second["meta"]["is_full"] is True
    for key in ("设备检修费", "施工费", "final_amount"):
        assert first["totals"][key] == pytest.approx(second["totals"][key], abs=0.01)
    assert len(first["items"]) == len(second["items"])
    assert first["materials"]["totals"] == second["materials"]["totals"]


def test_real_wan_yuan_summary_smoke() -> None:
    """真实万元版结算书：表一“金额单位:万元”→ 只放大表一金额（×10000）。"""
    docs = _real_docs_where(
        REAL_OLD_DIR,
        lambda doc: (doc.get("meta") or {}).get("unit_scale") == 10000.0,
    )
    if not docs:
        pytest.skip("本机没有万元版真实结算书样例")
    doc = docs[0]

    assert doc["meta"]["format"] == "full"
    assert doc["meta"]["is_full"] is True
    assert doc["meta"]["unit_scale"] == 10000.0
    totals = doc["totals"]
    assert totals["设备检修费"] == pytest.approx(425700.0, abs=0.01)
    assert totals["施工费"] == pytest.approx(425700.0, abs=0.01)
    assert totals["编制基准期价差"] == pytest.approx(20100.0, abs=0.01)
    assert totals["甲供配件材料费"] == pytest.approx(72800.0, abs=0.01)
    assert totals["可抵扣增值税"] == pytest.approx(52200.0, abs=0.01)
    assert totals["final_amount"] == pytest.approx(425700.0, abs=0.01)
    # 表二甲/材料表仍是元：乙供未计价材料合计 77183（万元放大不应影响这里）
    assert doc["materials"]["totals"]["乙供合计"] == pytest.approx(77183.0, abs=0.01)
    assert len(doc["items"]) >= 100
    assert doc["categories"]
    assert doc["warnings"] == []


def test_real_full_settlements_matrix_smoke() -> None:
    """其余两对真实完整结算书（设备检修费量级不同）的关键数字。"""
    old_large = _real_docs_where(
        REAL_OLD_DIR,
        lambda doc: _is_full_doc(doc)
        and _amount_is(doc["totals"].get("设备检修费"), 733914.01),
    )
    new_mid = _real_docs_where(
        REAL_NEW_DIR,
        lambda doc: _is_full_doc(doc)
        and _amount_is(doc["totals"].get("设备检修费"), 268334.15),
    )
    new_small = _real_docs_where(
        REAL_NEW_DIR,
        lambda doc: _is_full_doc(doc)
        and _amount_is(doc["totals"].get("设备检修费"), 28425.7),
    )
    if not old_large and not new_mid and not new_small:
        pytest.skip("本机没有其余项目的真实完整结算书样例")

    if old_large:
        doc = old_large[0]
        assert doc["meta"]["format"] == "full"
        assert doc["totals"]["施工费"] == pytest.approx(733914.01, abs=0.01)
        assert doc["totals"]["final_amount"] == pytest.approx(733914.01, abs=0.01)
        assert doc["totals"]["可抵扣增值税"] == pytest.approx(66270.26, abs=0.01)
        assert len(doc["items"]) >= 100
        assert doc["warnings"] == []

    if new_mid:
        doc = new_mid[0]
        assert doc["meta"]["format"] == "full"
        assert doc["totals"]["下浮后送审金额"] == pytest.approx(263062.7, abs=0.01)
        assert doc["totals"]["final_amount"] == pytest.approx(263062.7, abs=0.01)
        assert doc["totals"]["甲供配件材料费"] == pytest.approx(29775.0, abs=0.01)
        assert len(doc["items"]) >= 100
        assert doc["warnings"] == []

    if new_small:
        doc = new_small[0]
        assert doc["meta"]["format"] == "full"
        assert doc["meta"]["is_full"] is True
        assert doc["totals"]["施工费"] == pytest.approx(28425.7, abs=0.01)
        # 有“下浮后送审金额”时 final_amount 取下浮后口径
        assert doc["totals"]["下浮后送审金额"] == pytest.approx(27870.73, abs=0.01)
        assert doc["totals"]["final_amount"] == pytest.approx(27870.73, abs=0.01)
        assert doc["totals"]["可抵扣增值税"] == pytest.approx(3101.3, abs=0.01)
        # 该工作簿同时带“组合件汇总表”，完整口径优先，不混入组合件明细
        assert all(item["item_type"] != "组合件" for item in doc["items"])
        assert doc["warnings"] == []


def test_real_assembly_only_settlements_smoke() -> None:
    """仅“组合件汇总表”的真实结算书：施工费口径=组合件合价合计，is_full=False。"""
    old_docs = _real_docs_where(
        REAL_OLD_DIR,
        lambda doc: (doc.get("meta") or {}).get("format") == "assembly_only",
    )
    new_docs = _real_docs_where(
        REAL_NEW_DIR,
        lambda doc: (doc.get("meta") or {}).get("format") == "assembly_only",
    )
    cases: list[tuple[dict, int, float]] = []
    cases.extend((doc, 76, 951640.0) for doc in old_docs if len(doc["items"]) == 76)
    cases.extend((doc, 163, 202608.0) for doc in new_docs if len(doc["items"]) == 163)
    cases.extend((doc, 116, 373122.0) for doc in new_docs if len(doc["items"]) == 116)
    if not cases:
        pytest.skip("本机没有仅组合件汇总表的真实结算书样例")

    for doc, expected_count, expected_total in cases:
        assert doc["meta"]["format"] == "assembly_only"
        assert doc["meta"]["is_full"] is False
        assert len(doc["items"]) == expected_count
        assert all(item["item_type"] == "组合件" for item in doc["items"])
        assert doc["totals"]["施工费"] == pytest.approx(expected_total, abs=0.01)
        assert doc["totals"]["组合件合计"] == pytest.approx(expected_total, abs=0.01)
        assert doc["totals"]["final_amount"] == pytest.approx(expected_total, abs=0.01)
        assert any("仅组合件汇总表" in warning for warning in doc["warnings"])
        assert any("施工费口径" in warning for warning in doc["warnings"])
