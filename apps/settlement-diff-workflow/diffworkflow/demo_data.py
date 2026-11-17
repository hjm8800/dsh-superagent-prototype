"""匿名化演示数据生成器。

生成 `examples/demo/原版` 与 `examples/demo/新版` 两个目录，结构模拟真实资料：
- 结算书 xlsx：表一 / 表二甲（仅原版）/ 表三甲 / 甲供、乙供未计价材料 / 配件汇总；
- 竣工报告 xlsx：主要工程量长文本 / 工程量签证表 / 隐蔽接地汇总 / 安健环 / 清理旧线路 / 拉盘底盘。

所有项目名、单位、金额均为虚构演示数据，不含任何真实项目信息。
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

DEMO_PROJECT = "项目A·某10kV线路大修（供电能力提升）"

# 表三甲列位（0-based，与真实结算书一致；共 27 列）
COL_SEQ = 0
COL_CODE = 1
COL_NAME = 2
COL_UNIT = 3
COL_QTY = 4
COL_PARTS_PRICE = 5
COL_BASE_PRICE = 6
COL_LABOR_PRICE = 7
COL_MACHINE_PRICE = 8
COL_MATERIAL_PRICE = 9
COL_PARTS_AMOUNT = 10
COL_AMOUNT = 11
COL_LABOR_AMOUNT = 12
COL_MACHINE_AMOUNT = 13
COL_MATERIAL_AMOUNT = 14
COL_KIND = 15
COL_MARKET_INCL = 16
COL_MARKET_EXCL = 17


def _t3_row(**kwargs) -> list:
    row = [None] * 27
    mapping = {
        "seq": COL_SEQ, "code": COL_CODE, "name": COL_NAME, "unit": COL_UNIT, "qty": COL_QTY,
        "parts_price": COL_PARTS_PRICE, "base_price": COL_BASE_PRICE, "labor_price": COL_LABOR_PRICE,
        "machine_price": COL_MACHINE_PRICE, "material_price": COL_MATERIAL_PRICE,
        "parts_amount": COL_PARTS_AMOUNT, "amount": COL_AMOUNT, "labor_amount": COL_LABOR_AMOUNT,
        "machine_amount": COL_MACHINE_AMOUNT, "material_amount": COL_MATERIAL_AMOUNT,
        "kind": COL_KIND, "market_incl": COL_MARKET_INCL, "market_excl": COL_MARKET_EXCL,
    }
    for key, column in mapping.items():
        if key in kwargs:
            row[column] = kwargs[key]
    return row


def _section(seq: str, name: str, amount: float | None = None, material_amount: float | None = None,
             parts_amount: float | None = None) -> list:
    return _t3_row(seq=seq, name=name, amount=amount, material_amount=material_amount,
                   parts_amount=parts_amount, kind="项目划分")


def _quota(code: str, name: str, unit: str, qty: float, base_price: float, labor_price: float,
           machine_price: float, amount: float, labor_amount: float, machine_amount: float) -> list:
    return _t3_row(code=code, name=name, unit=unit, qty=qty, base_price=base_price,
                   labor_price=labor_price, machine_price=machine_price, amount=amount,
                   labor_amount=labor_amount, machine_amount=machine_amount, kind="定额")


def _material(code: str, name: str, unit: str, qty: float, price: float, amount: float,
              market_incl: float | None = None, market_excl: float | None = None) -> list:
    return _t3_row(code=code, name=name, unit=unit, qty=qty, material_price=price,
                   material_amount=amount, kind="主材",
                   market_incl=market_incl or price, market_excl=market_excl or price)


def _part(name: str, unit: str, qty: float, price: float, amount: float) -> list:
    return _t3_row(name=name, unit=unit, qty=qty, parts_price=price, parts_amount=amount, kind="配件")


def _quota_count_row(name: str, amount: float) -> list:
    """取费表行（reader 应跳过）。"""
    row = _t3_row(name=name, kind="取费表", amount=amount)
    return row


def _sheet(wb: Workbook, title: str, rows: list[list]) -> None:
    ws = wb.create_sheet(title)
    for row_index, row in enumerate(rows, start=1):
        for column_index, value in enumerate(row, start=1):
            if value is not None:
                ws.cell(row=row_index, column=column_index, value=value)


def _save(wb: Workbook, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


# ---------------------------------------------------------------------------
# 审核结算（结算书）
# ---------------------------------------------------------------------------

TABLE_ONE_HEADER = [
    ["电网检修工程总结算表"],
    ["表一", None, None, "金额单位:元"],
    ["序号", "工程或费用名称", "金额", "占合计总费用的比例(%)"],
]


def _table_one_rows(equipment: float, vat: float, price_diff: float, discount_final: float | None = None,
                    ag_parts: float | None = None) -> list[list]:
    rows = [list(row) for row in TABLE_ONE_HEADER]
    rows.append(["一", "建筑修缮费", None, None])
    rows.append(["二", "设备检修费", equipment, 100])
    rows.append(["三", "配件购置费", None, None])
    rows.append([None, "其中：编制基准期价差", price_diff, round(price_diff / equipment * 100, 2)])
    rows.append(["四", "小计", equipment, 100])
    if ag_parts is not None:
        rows.append([None, "其中：甲供配件材料费", ag_parts, round(ag_parts / equipment * 100, 2)])
    else:
        rows.append([None, "其中：甲供配件材料费", None, None])
    rows.append(["五", "其他费用", None, None])
    rows.append(["六", "基本预备费", None, None])
    rows.append(["七", "工程总费用合计", equipment, 100])
    rows.append([None, "其中：可抵扣增值税金额", vat, None])
    rows.append([None, "其中：施工费", equipment, 100])
    if discount_final is not None:
        rows.append(["十二", "下浮后送审金额", discount_final, None])
    return rows


def _table_two_rows() -> list[list]:
    return [
        ["设备检修专业汇总结算表"],
        ["表二甲", None, None, None, None, "金额单位:元"],
        ["序号", "工程或费用名称", "设备检修费", None, "配件购置费", "合计"],
        [None, None, "检修费", "未计价材料费", None, None],
        [None, "设备检修工程", 380000, 55000, 51200, 486200],
        [None, "架空输电线路设备检修工程", 380000, 55000, 51200, 486200],
        ["一", "架空线路本体工程", 340000, 55000, 51200, 446200],
        ["1", "土石方工程", 30000, 0, 0, 30000],
        ["2", "基础工程", 20000, 1200, 0, 21200],
        ["3", "杆塔工程", 70000, 18000, 0, 88000],
        ["4", "架线工程", 95000, 35800, 0, 130800],
        ["5", "杆上变配电装置", 125000, 0, 51200, 176200],
        ["三", "拆除项目", 40000, 0, 0, 40000],
        [None, "合计", 380000, 55000, 51200, 486200],
    ]


def _table_three_header() -> list[list]:
    header = [None] * 27
    header[0] = "序号"
    header[1] = "编制依据"
    header[2] = "项目名称"
    header[3] = "单位"
    header[4] = "数量"
    header[5] = "配件单价"
    header[6] = "单价"
    header[10] = "配件合价"
    header[11] = "合价"
    header[15] = "类型"
    header[16] = "市场单价含税"
    header[17] = "市场单价不含税"
    header[18] = "单重(kg)"
    header[19] = "损耗率(%)"
    header[20] = "运杂费率(%)"
    sub1 = [None] * 27
    sub1[6] = "检修费"
    sub1[9] = "未计价材料"
    sub1[11] = "检修费"
    sub1[14] = "未计价材料"
    sub2 = [None] * 27
    sub2[6] = "定额基价"
    sub2[7] = "其中：人工"
    sub2[8] = "其中：机械"
    sub2[11] = "费用金额"
    sub2[12] = "其中：人工"
    sub2[13] = "其中：机械"
    return [
        ["设备检修单位工程结算表"],
        ["表三甲", None, None, None, None, None, None, None, None, None, None, None, None, None, None,
         None, None, None, None, None, None, None, None, None, None, None, "金额单位:元"],
        header,
        sub1,
        sub2,
    ]


def _table_three_old_rows() -> list[list]:
    rows = _table_three_header()
    rows.append(_section("", "设备检修工程", 380000, 55000, 51200))
    rows.append(_section("", "架空输电线路设备检修工程", 380000, 55000, 51200))
    rows.append(_section("一", "架空线路本体工程", 340000, 55000, 51200))
    rows.append(_section("1", "土石方工程", 30000))
    rows.append(_quota("JYX2-9", "电杆坑、塔坑、拉线坑人工挖方（或爆破）及回填 普通土 坑深（m） 3.0以内",
                       "m³", 50, 19.36, 17.8, 1.56, 968, 890, 78))
    rows.append(_quota("JYX2-17", "电杆坑、塔坑、拉线坑人工挖方（或爆破）及回填 坚土 坑深（m） 3.0以内",
                       "m³", 30, 24.03, 21.81, 2.22, 720.9, 654.3, 66.6))
    rows.append(_section("2", "基础工程", 20000, 1200))
    rows.append(_quota("JYX3-63", "混凝土搅拌及浇制 每个基础混凝土量（m³） 5以内",
                       "m³", 3.2, 274.56, 190.78, 16.6, 878.59, 610.5, 53.12))
    rows.append(_material("", "混凝土 C25", "m³", 3.2, 384, 1228.8))
    rows.append(_section("3", "杆塔工程", 70000, 18000))
    rows.append(_quota("JYX4-1", "电杆组立", "根", 12, 350, 200, 120, 4200, 2400, 1440))
    rows.append(_material("T02020302（甲）", "混凝土杆 普通混凝土电杆 Φ190*15", "根", 8, 2110.62, 16884.96))
    rows.append(_material("", "底盘 DP6", "块", 6, 175.22, 1051.32))
    rows.append(_section("4", "架线工程", 95000, 35800))
    rows.append(_quota("JYX5-1", "导线架设", "km", 1.2, 9500, 5000, 3800, 11400, 6000, 4560))
    rows.append(_material("T02020302（甲）", "10kV架空绝缘导线 JKLGYJ-240/30", "km", 1.2, 23799, 28558.8))
    rows.append(_material("T02020302（甲）", "10kV架空绝缘导线 JKLGYJ-70/10", "km", 0.8, 8655, 6924))
    rows.append(_section("5", "杆上变配电装置", 125000, 0, 51200))
    rows.append(_quota("JYX6-1", "台架变安装", "台", 2, 28000, 15000, 9000, 56000, 30000, 18000))
    rows.append(_material("T02020302（甲）", "配电变压器 S13-M-200kVA", "台", 2, 95000, 190000))
    rows.append(_part("避雷器 YH5WS-17/50", "组", 6, 759, 4554))
    rows.append(_section("三", "拆除项目", 40000))
    rows.append(_quota("CYX1-1", "拆除导线", "km", 1.2, 8000, 5000, 2400, 9600, 6000, 2880))
    rows.append(_quota("CYX2-1", "拆除电杆", "根", 6, 350, 200, 120, 2100, 1200, 720))
    rows.append(_quota("CYX9-1", "拆除临时围栏", "m", 40, 25, 15, 5, 1000, 600, 200))
    rows.append(_quota_count_row("直接费", 300000))
    rows.append(_quota_count_row("增值税", 38000))
    rows.append(_quota_count_row("合计", 486200))
    return rows


def _table_three_new_rows() -> list[list]:
    rows = _table_three_header()
    rows.append(_section("", "设备检修工程", 350000, 57000, 20433))
    rows.append(_section("", "架空输电线路设备检修工程", 350000, 57000, 20433))
    rows.append(_section("一", "架空线路本体工程", 320000, 57000, 20433))
    rows.append(_section("1", "土石方工程", 34000))
    rows.append(_quota("JYX2-9", "电杆坑、塔坑、拉线坑人工挖方（或爆破）及回填 普通土 坑深（m） 3.0以内",
                       "m³", 62, 19.36, 17.8, 1.56, 1200.32, 1103.6, 96.72))
    rows.append(_quota("JYX2-17", "电杆坑、塔坑、拉线坑人工挖方（或爆破）及回填 坚土 坑深（m） 3.0以内",
                       "m³", 26, 24.03, 21.81, 2.22, 624.78, 567.06, 57.72))
    rows.append(_section("2", "基础工程", 18000, 1100))
    rows.append(_quota("JYX3-63", "混凝土搅拌及浇制 每个基础混凝土量（m³） 5以内",
                       "m³", 2.8, 274.56, 190.78, 16.6, 768.77, 534.18, 46.48))
    rows.append(_material("", "混凝土 C25", "m³", 2.8, 384, 1075.2))
    rows.append(_section("3", "杆塔工程", 66000, 15000))
    rows.append(_quota("JYX4-1", "电杆组立", "根", 11, 350, 200, 120, 3850, 2200, 1320))
    rows.append(_quota("JYX4-9", "拉线安装", "条", 8, 85, 50, 20, 680, 400, 160))
    rows.append(_material("T02020302（甲）", "混凝土杆 普通混凝土电杆 Φ190*15", "根", 7, 2110.62, 14774.34))
    rows.append(_material("T02020302（甲）", "底盘 DP6", "块", 6, 175.22, 1051.32))
    rows.append(_section("4", "架线工程", 90000, 35000))
    rows.append(_quota("JYX5-1", "导线架设", "km", 1.06, 9500, 5000, 3800, 10070, 5300, 4028))
    rows.append(_material("T02020302（甲）", "10kV架空绝缘导线 JKLGYJ-240/30", "km", 1.06, 23799, 25226.94))
    rows.append(_material("T02020302（甲）", "10kV架空绝缘导线 JKLGYJ-70/10", "km", 0.86, 8655, 7443.3))
    rows.append(_section("5", "杆上变配电装置", 80000, 0, 20433))
    rows.append(_quota("JYX6-1", "台架变安装", "台", 1, 28000, 15000, 9000, 28000, 15000, 9000))
    rows.append(_material("T02020302（甲）", "配电变压器 S13-M-200kVA", "台", 1, 95000, 95000))
    rows.append(_part("避雷器 YH5WS-17/50", "组", 3, 759, 2277))
    rows.append(_part("隔离开关 HGW9-10/630A", "组", 6, 1170, 7020))
    rows.append(_part("跌落式熔断器 200A", "组", 4, 765, 3060))
    rows.append(_part("10kV户外隔离开关 630A", "台", 3, 415, 1245))
    rows.append(_part("10kV线路型避雷器 YH5WS-17/50", "组", 9, 759, 6831))
    rows.append(_section("三", "拆除项目", 30000))
    rows.append(_quota("CYX1-1", "拆除导线", "km", 1.2, 8000, 5000, 2400, 9600, 6000, 2880))
    rows.append(_quota("CYX2-1", "拆除电杆", "根", 6, 350, 200, 120, 2100, 1200, 720))
    rows.append(_quota("CYX5-1", "拆除变压器", "台", 1, 1800, 900, 500, 1800, 900, 500))
    rows.append(_quota_count_row("直接费", 280000))
    rows.append(_quota_count_row("增值税", 35150))
    rows.append(_quota_count_row("合计", 438500))
    return rows


# 未计价材料： (材料名称, 规格及型号, 单位, 设计用量, 损耗率%, 单价, 总价)
OLD_YIGONG_MATERIALS = [
    ("混凝土 C25", "", "m³", 3.2, 1.5, 384, 1228.8),
    ("底盘 DP6", "", "块", 6, 0.5, 175.22, 1051.32),
    ("镀锌圆钢 Φ16", "", "m", 120, 0.5, 9.5, 1140),
    ("角钢桩垂直地极", "L50*5,L=2.5m，热镀锌", "根", 20, 0.5, 55.75, 1115),
    ("连接板", "-5*50*270", "块", 10, 0.5, 4.761, 47.61),
    ("螺栓", "M16×45(全丝)", "套", 20, 3, 1.743, 34.86),
    ("反光贴纸", "", "m²", 9.8, 5, 65, 637),
    ("商品混凝土", "碎石最大粒径 20mm C25", "m³", 22, 1.5, 384, 8574),
    ("接地圆钢 Φ16", "", "m", 60, 6, 9.5, 570),
    ("钢筋", "综合", "t", 2.4, 2, 4200, 10080),
    ("模板", "复合木模板", "m²", 40, 1.5, 45, 1800),
    ("低压电缆", "ZA-YJV-1×240", "m", 90, 1.8, 265, 23850),
]

NEW_YIGONG_MATERIALS = [
    ("混凝土 C25", "", "m³", 2.8, 1.5, 384, 1075.2),
    ("底盘 DP6", "", "块", 6, 0.5, 175.22, 1051.32),
    ("螺栓", "M16×45(全丝)", "套", 20, 3, 1.743, 34.86),
    ("反光贴纸", "", "m²", 17.5, 5, 65, 1137.5),
    ("钢筋", "综合", "t", 2.2, 2, 4200, 9240),
    ("模板", "复合木模板", "m²", 45, 1.5, 45, 2025),
    ("低压电缆", "ZA-YJV-1×240", "m", 90, 1.8, 265, 23850),
    ("商品混凝土", "碎石最大粒径 20mm C25", "m³", 25, 1.5, 384, 9600),
    ("镀锌圆钢 Φ16", "", "m", 140, 0.5, 9.5, 1330),
    ("角钢桩垂直地极", "L50*5,L=2.5m，热镀锌", "根", 24, 0.5, 55.75, 1338),
]

NEW_AGONG_MATERIALS = [
    ("", "卡顶抱箍 Φ200", "套", 24, 1.5, 78.53, 1913),
    ("", "角钢横担 HD-75/09-230", "根", 48, 0.5, 48, 2316),
    ("", "双头螺栓 MS18x310", "套", 180, 3, 4.4, 816),
    ("", "10kV瓷绝缘横担 SQ-210Z", "只", 48, 2, 42.74, 2093),
    ("", "预应力混凝土电杆 Φ190×15M", "根", 8, 0.5, 2384, 19163),
    ("", "10kV架空绝缘导线 JKLGYJ-240/30", "km", 1.06, 1.8, 23800, 25680),
    ("", "10kV架空绝缘导线 JKLGYJ-70/10", "km", 0.86, 1.8, 8655, 7580),
    ("", "配电变压器 S13-M-200kVA", "台", 1, 0, 95000, 95000),
    ("", "避雷器 YH5WS-17/50", "组", 3, 1.5, 759, 2311),
    ("", "底盘 DP6", "块", 6, 0.5, 175.22, 1056),
]

NEW_PARTS = [
    ("", "10kV线路型避雷器", "YH5WS-17/50", "组", 9, 759, 6831),
    ("", "隔离开关", "HGW9-10/630A", "组", 6, 1170, 7020),
    ("", "跌落式熔断器", "200A", "组", 4, 765, 3060),
    ("", "10kV户外隔离开关", "630A", "台", 3, 415, 1245),
    ("", "10kV交流无间隙金属氧化物避雷器", "YH5WS-17/50", "只", 3, 128, 384),
    ("", "低压刀闸", "1000A，三极", "组", 1, 1719.137, 1719),
]


def _material_sheet_rows(items: list[tuple], agong: bool = False) -> list[list]:
    if agong:
        rows = [["甲供未计价材料汇总表"], [None, None, None, None, None, None, None, None, "金额单位:元"],
                ["序号", "物料编码", "材料名称", "规格及型号", "单位", "设计用量", "损耗率%",
                 "单价（含税）", "总价（含税）"]]
    else:
        rows = [["乙供未计价材料汇总表"], [None, None, None, None, None, None, None, "金额单位:元"],
                ["序号", "材料名称", "规格及型号", "单位", "设计用量", "损耗率%",
                 "单价（不含税）", "总价（不含税）"]]
    rows.append([None] * (9 if agong else 8))
    for index, item in enumerate(items, start=1):
        if agong:
            material_name, spec, unit, qty, loss, price, amount = item
            rows.append([index, "", material_name, spec, unit, qty, loss, price, amount])
        else:
            material_name, spec, unit, qty, loss, price, amount = item
            rows.append([index, material_name, spec, unit, qty, loss, price, amount])
    total = round(sum(item[-1] for item in items), 2)
    rows.append([None] * (8 if agong else 7) + ["小计：", None] if not agong else [None] * 8 + ["小计："])
    # 用统一格式写小计/合计
    rows = rows[: 4 + len(items)]
    width = 9 if agong else 8
    subtotal = [None] * width
    subtotal[-2] = "小计："
    subtotal[-1] = total
    rows.append(subtotal)
    total_row = [None] * width
    total_row[-2] = "合计："
    total_row[-1] = total
    rows.append(total_row)
    return rows


def _parts_sheet_rows() -> list[list]:
    rows = [["配件汇总表"],
            [None] * 12 + ["金额单位:元"],
            ["编码", "配件名称", "规格型号", "单位", "数量", "市场价单价", None, "市场价合价", None,
             "运杂费", None, "配送费", "合计"],
            [None, None, None, None, None, "含税价", "不含税价", "含税价", "不含税价", "运杂费率(%)", "合计", None, None]]
    rows.append([None] * 13)
    for code, name, spec, unit, qty, price, amount in NEW_PARTS:
        row = [None] * 13
        row[0] = code
        row[1] = name
        row[2] = spec
        row[3] = unit
        row[4] = qty
        row[5] = price
        row[6] = round(price / 1.13, 3)
        row[7] = amount
        row[8] = round(amount / 1.13, 3)
        row[12] = amount
        rows.append(row)
    total = round(sum(item[6] for item in NEW_PARTS), 2)
    subtotal = [None] * 13
    subtotal[11] = "小计："
    subtotal[12] = total
    rows.append(subtotal)
    total_row = [None] * 13
    total_row[11] = "合计："
    total_row[12] = total
    rows.append(total_row)
    return rows


def _agong_parts_sheet_rows() -> list[list]:
    """甲供配件汇总表（配件版式，区别于未计价材料汇总表）。"""
    rows = [
        ["甲供配件汇总表"],
        [None] * 10 + ["金额单位:元"],
        ["序号", "物料编码", "配件名称", "规格型号", "单位", "数量", "价格（含税）", None, "运杂费", None, "合计"],
        [None, None, None, None, None, None, "单价", "合价", "费率（%）", "合价", None],
        [None] * 11,
    ]
    total = 0.0
    for index, (code, name, spec, unit, qty, price, amount) in enumerate(NEW_PARTS, start=1):
        row = [None] * 11
        row[0] = index
        row[1] = code
        row[2] = name
        row[3] = spec
        row[4] = unit
        row[5] = qty
        row[6] = price
        row[7] = amount
        row[10] = amount
        rows.append(row)
        total += amount
    subtotal = [None] * 11
    subtotal[9] = "小计："
    subtotal[10] = round(total, 2)
    rows.append(subtotal)
    total_row = [None] * 11
    total_row[9] = "合计："
    total_row[10] = round(total, 2)
    rows.append(total_row)
    return rows


def _write_settlement_workbook(path: Path, old: bool) -> None:
    wb = Workbook()
    wb.remove(wb.active)
    if old:
        _sheet(wb, "检修工程总表(表一)", _table_one_rows(486200, 40120, 16800))
        _sheet(wb, "设备检修专业汇总表(表二甲)", _table_two_rows())
        _sheet(wb, "设备检修单位工程表(表三甲)", _table_three_old_rows())
        _sheet(wb, "乙供未计价材料汇总表", _material_sheet_rows(OLD_YIGONG_MATERIALS))
    else:
        ag_total = round(sum(item[-1] for item in NEW_AGONG_MATERIALS), 2)
        parts_total = round(sum(item[6] for item in NEW_PARTS), 2)
        _sheet(wb, "检修工程总表(表一)",
               _table_one_rows(438500, 36150, 20400, discount_final=429730, ag_parts=parts_total))
        _sheet(wb, "设备检修单位工程表(表三甲)", _table_three_new_rows())
        _sheet(wb, "乙供未计价材料汇总表", _material_sheet_rows(NEW_YIGONG_MATERIALS))
        _sheet(wb, "甲供未计价材料汇总表", _material_sheet_rows(NEW_AGONG_MATERIALS, agong=True))
        _sheet(wb, "配件汇总表", _parts_sheet_rows())
        _sheet(wb, "甲供配件汇总表", _agong_parts_sheet_rows())
        _sheet(wb, "材料汇总表", _material_sheet_rows(NEW_YIGONG_MATERIALS))
        _sheet(wb, "施工费明细表", [
            ["施工费明细表"], [None, None, None, "金额单位:元"],
            ["序号", "名称", "计算过程", "合价", "计算说明"],
            ["一", "最终施工费(元)", "381000.00+57500.00", 438500, "本体施工费+乙供物料费"],
            ["1", "施工费(元)", "381000.00+57500.00", 438500, "本体施工费+乙供物料费"],
        ])
    _save(wb, path)


# ---------------------------------------------------------------------------
# 竣工报告
# ---------------------------------------------------------------------------

MAIN_TEXT_OLD = (
    "主要工程量：一、新建部分：1、10kV架空线路架设：架设 JKLGYJ-240/30 绝缘导线 1.20km、"
    "JKLGYJ-70/10 绝缘导线 0.80km；组立 Φ190×15m 电杆 12 基；安装拉线 8 组；"
    "安装 S13-M-200kVA 台架变 2 台；安装避雷器 6 组；架设低压绝缘导线 BLVV-120 0.42km。"
    "2、土建部分：普通土开挖 50m³、坚土开挖 30m³，C25 混凝土 3.2m³，底盘 6 块。"
    "二、拆除部分：拆除旧导线 1.20km、旧电杆 6 基。"
)

SECOND_TEXT_OLD = (
    "1、土石方工程：普通土开挖 50m³、坚土开挖 30m³；2、基础工程：C25 混凝土 3.2m³，底盘 6 块；"
    "3、杆塔工程：Φ190×15m 电杆 8 根；4、架线工程：导线架设 1.20km；"
    "5、杆上变配电装置：台架变 2 台、避雷器 6 组；6、拆除工程：拆除导线 1.20km、拆除电杆 6 基。"
)

SECOND_TEXT_NEW = (
    "1、土石方工程：普通土开挖 62m³、坚土开挖 26m³；2、基础工程：C25 混凝土 2.8m³，底盘 6 块；"
    "3、杆塔工程：Φ190×15m 电杆 7 根；4、架线工程：导线架设 1.06km；"
    "5、杆上变配电装置：台架变 1 台、避雷器 3 组；6、拆除工程：拆除导线 1.20km、拆除电杆 6 基、"
    "拆除变压器 1 台；7、新增拉线安装 8 组。"
)

APPENDIX_TEXT_1 = (
    "附页：一、安装部分：更换 10kV 绝缘导线 JKLGYJ-240/30 1.06km，更换耐张串 NLD-4 共 24 串，"
    "更换 15m 电杆 4 根，安装拉线 8 组。二、土建部分：新建拉线坑 8 个，C25 混凝土 1.2m³。"
    "三、安健环：标示牌 96 块、相线牌 66 块。"
)

APPENDIX_TEXT_2 = (
    "附页二：拆旧部分：保护性拆除变压器 4 台、隔离开关 96 台、避雷器 123 只、10 米电杆 4 基、"
    "12 米电杆 5 基；破坏性拆除台架金具 13 套、带电三角环 36 处。"
)

VISA_TABLE_OLD_ROWS = [
    ["工程量签证表"],
    ["工 程 名 称", DEMO_PROJECT],
    ["建 设 单 位", "某供电局"],
    ["监 理 单 位", "某监理公司"],
    ["设 计 单 位", "某设计院"],
    ["施 工 单 位", "某施工单位"],
    ["序号", "工作地点", "签证内容"],
    [1, "10kV 甲线 #1-#12 杆",
     "安装工程量：1、组立 Φ190×15m 电杆 12 基；2、架设 JKLGYJ-240/30 绝缘导线 1.20km；"
     "3、安装拉线 8 组；4、安装台架变 2 台。"],
    [2, "10kV 甲线 台区",
     "拆除工程量：1、拆除旧导线 1.20km；2、拆除旧电杆 6 基；3、拆除台架金具 2 套。"],
]

GROUNDING_HEADER = [
    ["隐蔽工程施工记录之二（台架接地）"],
    [None],
    ["项目名称", None, DEMO_PROJECT],
    ["序 号", "安装地点", "圆钢水平地极（米）", "镀锌角钢（条）", "圆钢引出线（米）",
     "连接板（块）", "埋深（米）", "电阻实测值（Ω）", "备       注"],
    [None, None, "Φ16", "∠50*5*2500", "Φ16", "—40×4，L=300", "/", "/"],
]

GROUNDING_ROWS = [
    [1, "台区1", 35, 6, 6.88, 2, 0.8, 4.8],
    [2, "台区2", 35, 6, 6.88, 2, 0.8, 3.5],
    [3, "台区3", 35, 6, 6.88, 2, 0.8, 4.1],
    [4, "台区4", 35, 6, 6.88, 2, 0.8, 4.2],
]


def _grounding_sheet(rows: list[list], subtitle: str) -> list[list]:
    sheet = [list(row) for row in GROUNDING_HEADER]
    sheet[0] = [subtitle]
    sheet.extend(rows)
    return sheet


GROUNDING_POLE_ROWS = [
    [1, "支线7", 40, 8, 2, 2, 0.8, 3.4],
    [2, "支线52", 40, 8, 2, 2, 0.8, 4.1],
    [3, "支线50", 40, 8, 2, 2, 0.8, 3.6],
]

ANJIANHUAN_ROWS = [
    ["安健环工程量见证表"],
    ["工程名称:", DEMO_PROJECT],
    ["本工程安装“安健环”工程量如下，请予以见证："],
    ["序号", "材料名称", "规格型号", "材质", "单位", "数量"],
    [1, "隔离刀闸标示牌(含安装架)", "320×200", "1mm铝板，带反光", "块", 6],
    [2, "避雷器标示牌(含安装架)", "320×200", "1mm铝板，带反光", "块", 6],
    [3, "跌落式熔断开关标示牌(含安装架)", "320×200", "1mm铝板，带反光", "块", 6],
    [4, "变压器标志牌(含安装架)", "320×200", "1mm铝板，带反光", "块", 6],
    [5, "禁止在电力变压器周围2米以内停放机动车辆或堆放杂物(含安装架)", "400×320", "1mm铝板，带反光", "块", 12],
    [6, "“禁止抛物，高压危险”(含安装架)", "400×320", "1mm铝板，带反光", "块", 6],
    [7, "“禁止攀登，高压危险”(含安装架)", "400×320", "1mm铝板，带反光", "块", 6],
    [8, "“止步，高压危险”(含安装架)", "400×320", "1mm铝板，带反光", "块", 12],
    [9, "10kV相线牌", "", "", "块", 18],
    [10, "0.4kV相线牌", "", "", "块", 48],
    [11, "杆号牌", "", "1mm铝板", "块", 12],
    [12, "警示牌", "400×320", "1mm铝板", "块", 6],
]

QINGLI_ROWS = [
    ["清理旧线路设施工程量登记表"],
    [None],
    [None, "工程名称：", DEMO_PROJECT, None, "编    号：", "DEMO-2026-001"],
    [None],
    ["序号", None, "项目名称", "规格、等级", "单位", "数量", "备注"],
    [1, None, "变压器", "S13-M-100/10", "台", 4, "保护性拆除"],
    [2, None, "变压器", "S11-M-315/10", "台", 1, "保护性拆除"],
    [3, None, "台架金具", "", "套", 13, "破坏性拆除"],
    [4, None, "带电三角环", "", "处", 36, "破坏性拆除"],
    [5, None, "隔离开关", "", "台", 96, "破坏性拆除"],
    [6, None, "避雷器", "", "只", 123, "破坏性拆除"],
    [7, None, "跌落式熔断器", "", "组", 2, "破坏性拆除"],
    [8, None, "横担绝缘子", "", "支", 3, "破坏性拆除"],
    [9, None, "柱式绝缘子", "", "只", 11, "破坏性拆除"],
    [10, None, "针式绝缘子", "", "只", 9, "破坏性拆除"],
    [11, None, "10米电杆", "", "基", 4, "破坏性拆除"],
    [12, None, "12米电杆", "", "基", 5, "破坏性拆除"],
    [13, None, "15米拼接杆", "", "基", 1, "破坏性拆除"],
    [14, None, "单回路支线水泥杆金具", "", "套", 3, "破坏性拆除"],
    [15, None, "拆除引下线", "", "处", 141, "破坏性拆除"],
    [16, None, "拆除横担", "", "套", 8, "破坏性拆除"],
]

LAPAN_ROWS = [
    ["隐蔽工程施工记录之二（拉盘及其它部分）"],
    [None],
    ["项目名称", None, DEMO_PROJECT],
    ["序 号", "电杆编号", "电杆规格 （米）", None, "拉盘规格", "拉盘埋深 （米）", None, "备       注"],
    [1, "", "10", None, "0.6*0.8", 2, None, "2组拉线，2个拉盘"],
    [2, "", "12", None, "0.6*0.8", 2, None, "1组拉线，1个拉盘"],
    [3, "", "12", None, "0.6*0.8", 2, None, "1组拉线，1个拉盘"],
]

DIPAN_ROWS = [
    ["隐蔽工程施工记录之二（底盘及其它部分）"],
    [None],
    ["项目名称", None, DEMO_PROJECT],
    ["序 号", "电杆编号", "电杆规格 （米）", None, "底盘规格", "底盘埋深 （米）", None, "备       注"],
    [1, "台区1主杆", "φ190×12", None, "0.5*0.5", 2, None, ""],
    [2, "台区1副杆", "φ190×10", None, "0.5*0.5", 2, None, ""],
    [3, "台区2主杆", "φ190×12", None, "0.5*0.5", 2, None, ""],
    [4, "台区2副杆", "φ190×10", None, "0.5*0.5", 2, None, ""],
    [5, "台区3主杆", "φ190×12", None, "0.5*0.5", 2, None, ""],
    [6, "台区3副杆", "φ190×10", None, "0.5*0.5", 2, None, ""],
    [7, "台区4主杆", "φ190×12", None, "0.5*0.5", 2, None, ""],
    [8, "台区4副杆", "φ190×10", None, "0.5*0.5", 2, None, ""],
]


def _write_report_workbook(path: Path, new: bool) -> None:
    wb = Workbook()
    wb.remove(wb.active)

    if new:
        report_rows = [
            ["工 程 竣 工 报 告"],
            [MAIN_TEXT_OLD, None, None, None, None, None, None, None, None, None, MAIN_TEXT_OLD, None,
             APPENDIX_TEXT_1, None, None, None, None, None, None, None, None, APPENDIX_TEXT_2],
            [None, None, None, "工 程 名 称", None, DEMO_PROJECT],
        ]
        second = [None] * 11
        second[2] = SECOND_TEXT_NEW
        report_rows.append(second)
        grounding = _grounding_sheet(GROUNDING_ROWS, "隐蔽工程施工记录之二（台架接地）")
        pole_grounding = _grounding_sheet(GROUNDING_POLE_ROWS, "隐蔽工程施工记录之二（杆塔设备接地）")
    else:
        report_rows = [
            ["工 程 竣 工 报 告"],
            [MAIN_TEXT_OLD, None, None, None, None, None, None, None, None, None, None],
            [None, None, None, "工 程 名 称", None, DEMO_PROJECT],
        ]
        second = [None] * 3
        second[2] = SECOND_TEXT_OLD
        report_rows.append(second)
        grounding = [list(row) for row in GROUNDING_HEADER]
        grounding[0] = ["隐蔽工程施工记录之二（台架接地）"]
        for index in range(1, 16):
            grounding.append([index])
        pole_grounding = None

    _sheet(wb, "竣工报告" if new else "竣工报告", report_rows)
    _sheet(wb, "工程量签证表", VISA_TABLE_OLD_ROWS)
    _sheet(wb, "隐蔽-接地汇总", grounding)
    if pole_grounding is not None:
        _sheet(wb, "隐蔽-接地汇总 (2)", pole_grounding)
        _sheet(wb, "安健环工程量见证表", ANJIANHUAN_ROWS)
        _sheet(wb, "清理旧线路设施工程量登记表", QINGLI_ROWS)
        _sheet(wb, "隐蔽-拉盘 ", LAPAN_ROWS)
        _sheet(wb, "隐蔽-底盘 ", DIPAN_ROWS)
    _save(wb, path)


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def generate(root: str | Path) -> dict:
    """生成演示资料，返回 {label: path}。"""
    root = Path(root)
    old_dir = root / "原版"
    new_dir = root / "新版"
    old_dir.mkdir(parents=True, exist_ok=True)
    new_dir.mkdir(parents=True, exist_ok=True)

    files = {
        "原版结算书": old_dir / "项目A-2025年结算书.xlsx",
        "原版竣工报告": old_dir / "项目A-2025年竣工报告.xlsx",
        "新版送审结算书": new_dir / "项目A-2026年送审.xlsx",
        "新版竣工报告": new_dir / "项目A-2026年竣工报告.xlsx",
    }
    _write_settlement_workbook(files["原版结算书"], old=True)
    _write_settlement_workbook(files["新版送审结算书"], old=False)
    _write_report_workbook(files["原版竣工报告"], new=False)
    _write_report_workbook(files["新版竣工报告"], new=True)
    return files


if __name__ == "__main__":
    target = Path(__file__).resolve().parent.parent / "examples" / "demo"
    result = generate(target)
    print(f"演示资料已生成：{target}")
    for label, path in result.items():
        print(f"  [{label}] {path}")
