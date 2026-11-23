# -*- coding: utf-8 -*-
"""rates.py — 费率、示例定额库、主材价格与设计清册基线（纯数据模块）。

费率依据 2022 版《20kV及以下配电网工程建设预算编制与计算规定》
（演示取江西 Ⅰ 类地区、县城或镇）；示例定额库与主材价格为演示数据，
不代表真实市场价，页面与文档中均已标注“演示用”。

本模块不含数据库访问，engine.py / pricing.py / seed.py 仅从这里取数。
"""

# ---------------------------------------------------------------------------
# 费用类别与计价常量
# ---------------------------------------------------------------------------

CATEGORY_INSTALL = "安装"
CATEGORY_BUILD = "建筑"
CATEGORY_DEMOLITION = "拆除"
CATEGORY_MATERIAL = "主材"
CATEGORIES = (CATEGORY_INSTALL, CATEGORY_BUILD, CATEGORY_DEMOLITION, CATEGORY_MATERIAL)

#: 措施费（基数 = 人工费 + 机械费），费率单位：%
MEASURE_FEE_ITEMS = {
    "install": [
        ("冬雨季施工增加费", 3.25),
        ("夜间施工增加费", 0.56),
        ("施工工具用具使用费", 2.03),
        ("特殊地区施工增加费", 0.0),
        ("安全文明施工费", 22.06),
    ],
    "build": [
        ("冬雨季施工增加费", 1.81),
        ("夜间施工增加费", 0.31),
        ("施工工具用具使用费", 0.93),
        ("特殊地区施工增加费", 0.0),
        ("安全文明施工费", 12.88),
    ],
}
#: 措施费率合计（用于页面/文档提示）：安装 27.90%，建筑 15.93%
MEASURE_FEE_TOTAL = {
    key: round(sum(rate for _, rate in items), 2) for key, items in MEASURE_FEE_ITEMS.items()
}

#: 规费：人工费 × 系数 ×（社保费率 + 公积金费率），费率单位：%
REGULATORY_FEE = {
    "install": {"labor_factor": 1.10, "social_rate": 26.5, "housing_rate": 12.0},
    "build": {"labor_factor": 1.08, "social_rate": 26.5, "housing_rate": 12.0},
}

#: 企业管理费 / 利润（基数），费率单位：%
ENTERPRISE_FEE_RATE = {"install": 19.91, "build": 10.22}
PROFIT_RATE = {"install": 12.40, "build": 6.56}

#: 增值税率（%）、设备运杂费率（%）、基本预备费率（%）
VAT_RATE = 9.0
EQUIPMENT_TRANSPORT_FEE_RATE = 1.06
BASIC_RESERVE_RATE = 3.0
#: 编制基准期价差：演示取 0（真实项目需按造价管理机构发布文件调整）
PRICE_DIFF_RATE = 0.0

#: 其他费用（基数中“建安”= 建筑工程费 + 安装工程费 + 拆除工程费）
#: base: jianan=建安 | jianan_equipment=建安+设备购置费 | build_distance=建筑+线路亘长
#: base: basic_design=基本设计费
OTHER_FEE_ITEMS = [
    {"item": "项目管理经费", "base": "jianan", "rate": 1.22,
     "formula": "建安工程费×1.22%"},
    {"item": "招标费", "base": "jianan_equipment", "rate": 0.32,
     "formula": "(建安工程费+设备购置费)×0.32%"},
    {"item": "工程监理费", "base": "jianan", "rate": 2.64,
     "formula": "建安工程费×2.64%"},
    {"item": "项目前期工作费", "base": "jianan", "rate": 2.29,
     "formula": "建安工程费×2.29%"},
    {"item": "勘察费", "base": "build_distance", "rate": 4.5,
     "formula": "建筑工程费×4.5% + (10kV线路亘长+低压线路亘长)×1000元/km"},
    {"item": "基本设计费", "base": "jianan_equipment", "rate": 5.5,
     "formula": "(建安工程费+设备购置费)×5.5%（演示费率）"},
    {"item": "其他设计费", "base": "basic_design", "rate": 18.0,
     "formula": "基本设计费×18%"},
    {"item": "设计文件评审费", "base": "basic_design", "rate": 4.8,
     "formula": "基本设计费×4.8%"},
    {"item": "施工过程造价咨询及竣工结算审核费", "base": "jianan", "rate": 0.51,
     "formula": "建安工程费×0.51%"},
    {"item": "工程质量检测费", "base": "jianan", "rate": 0.15,
     "formula": "建安工程费×0.15%"},
    {"item": "技术经济标准编制费", "base": "jianan", "rate": 0.10,
     "formula": "建安工程费×0.10%"},
    {"item": "生产准备费", "base": "jianan", "rate": 0.5,
     "formula": "建安工程费×0.5%"},
    {"item": "项目后评价费", "base": "jianan", "rate": 0.5,
     "formula": "建安工程费×0.5%"},
]
#: 余物清理费：仅当有拆除工程量时 = 拆除工程直接费×35%（线路拆除不能利用）
RESIDUAL_CLEAN_FEE_RATE = 35.0
#: 勘察费的线路亘长单价（元/km）
SURVEY_PRICE_PER_KM = 1000.0

# ---------------------------------------------------------------------------
# 示例定额库（演示用；code, name, unit, labor, material, machine 元/单位）
# ---------------------------------------------------------------------------

QUOTA_LIBRARY = [
    {"code": "YD3-0101", "name": "电杆组立 Φ150×8m 水泥杆", "unit": "根",
     "labor": 310.0, "material": 45.0, "machine": 85.0, "category": CATEGORY_INSTALL},
    {"code": "YD3-0103", "name": "电杆组立 Φ190×12m 水泥杆", "unit": "根",
     "labor": 480.0, "material": 60.0, "machine": 130.0, "category": CATEGORY_INSTALL},
    {"code": "YD3-0201", "name": "拉线制作安装", "unit": "条",
     "labor": 130.0, "material": 25.0, "machine": 12.0, "category": CATEGORY_INSTALL},
    {"code": "YD3-0301", "name": "10kV 架空绝缘导线架设（亘长）", "unit": "km",
     "labor": 760.0, "material": 90.0, "machine": 240.0, "category": CATEGORY_INSTALL},
    {"code": "YD3-0302", "name": "低压架空绝缘导线架设（亘长）", "unit": "km",
     "labor": 520.0, "material": 60.0, "machine": 130.0, "category": CATEGORY_INSTALL},
    {"code": "YD3-0401", "name": "10kV 避雷器安装", "unit": "只",
     "labor": 55.0, "material": 15.0, "machine": 8.0, "category": CATEGORY_INSTALL},
    {"code": "YD3-0501", "name": "电力电缆敷设（1kV 240mm²）", "unit": "km",
     "labor": 900.0, "material": 80.0, "machine": 160.0, "category": CATEGORY_INSTALL},
    {"code": "YD3-0502", "name": "电力电缆终端头制作（1kV 240mm²）", "unit": "个",
     "labor": 180.0, "material": 70.0, "machine": 15.0, "category": CATEGORY_INSTALL},
    {"code": "YD3-0601", "name": "10kV 线路金具安装", "unit": "套",
     "labor": 25.0, "material": 5.0, "machine": 3.0, "category": CATEGORY_INSTALL},
    {"code": "YD3-0602", "name": "低压线路金具安装", "unit": "套",
     "labor": 18.0, "material": 4.0, "machine": 2.0, "category": CATEGORY_INSTALL},
    {"code": "YD2-0101", "name": "台架式变压器安装（500kVA）", "unit": "套",
     "labor": 2200.0, "material": 260.0, "machine": 520.0, "category": CATEGORY_INSTALL},
    {"code": "YD2-0102", "name": "台架式变压器安装（200kVA）", "unit": "套",
     "labor": 1800.0, "material": 220.0, "machine": 430.0, "category": CATEGORY_INSTALL},
    {"code": "YD2-0201", "name": "低压配电箱安装", "unit": "台",
     "labor": 720.0, "material": 90.0, "machine": 80.0, "category": CATEGORY_INSTALL},
    {"code": "YD2-0301", "name": "配电智能网关安装", "unit": "套",
     "labor": 110.0, "material": 15.0, "machine": 5.0, "category": CATEGORY_INSTALL},
    {"code": "YD2-0401", "name": "接地装置安装（2 根接地极）", "unit": "组",
     "labor": 260.0, "material": 90.0, "machine": 30.0, "category": CATEGORY_INSTALL},
    {"code": "YD2-0501", "name": "安全围栏安装", "unit": "套",
     "labor": 180.0, "material": 40.0, "machine": 10.0, "category": CATEGORY_INSTALL},
    {"code": "YD2-0601", "name": "台架变金具及支架安装", "unit": "套",
     "labor": 90.0, "material": 20.0, "machine": 8.0, "category": CATEGORY_INSTALL},
    {"code": "YD1-0101", "name": "杆坑土方开挖（普通土）", "unit": "m³",
     "labor": 42.0, "material": 0.0, "machine": 9.0, "category": CATEGORY_BUILD},
    {"code": "YD1-0102", "name": "拉线坑土方开挖", "unit": "m³",
     "labor": 38.0, "material": 0.0, "machine": 8.0, "category": CATEGORY_BUILD},
    {"code": "YD1-0201", "name": "混凝土基础（C20）", "unit": "m³",
     "labor": 360.0, "material": 380.0, "machine": 55.0, "category": CATEGORY_BUILD},
    {"code": "YD1-0301", "name": "电缆直埋沟开挖及回填", "unit": "m",
     "labor": 18.0, "material": 2.0, "machine": 4.0, "category": CATEGORY_BUILD},
    {"code": "YD5-0101", "name": "拆除低压导线", "unit": "km",
     "labor": 160.0, "material": 10.0, "machine": 20.0, "category": CATEGORY_DEMOLITION},
    {"code": "YD5-0102", "name": "拆除水泥杆", "unit": "根",
     "labor": 240.0, "material": 15.0, "machine": 60.0, "category": CATEGORY_DEMOLITION},
]
QUOTA_BY_CODE = {q["code"]: q for q in QUOTA_LIBRARY}

# ---------------------------------------------------------------------------
# 主材默认单价（元/单位；演示用，页面可改）
# ---------------------------------------------------------------------------

MATERIAL_PRICES = {
    "水泥杆 Φ150×8m": 800.0,
    "水泥杆 Φ190×12m": 1450.0,
    "拉线": 380.0,
    "10kV避雷器": 380.0,
    "10kV金具": 450.0,
    "低压金具": 120.0,
    "台架变金具及支架": 3500.0,
    "接地装置材料": 900.0,
    "安全围栏": 1200.0,
    "电缆终端头": 450.0,
    "电力电缆 ZA-YJV-1×240": 180000.0,
    "10kV导线 JKLGYJ-70/10": 12500.0,
    "BLVV-240": 9000.0,
    "BLVV-120": 7000.0,
    "JKLYJ-120": 9000.0,
    "JKLYJ-240": 14000.0,
}

#: 主材默认匹配表：按关键词匹配清单名称/型号，取不到时给默认价
DEFAULT_MATERIALS = [
    {"name": "水泥杆", "spec": "Φ150×8m", "unit": "根", "price": 800.0,
     "keywords": ["水泥杆", "Φ150×8m", "150×8"]},
    {"name": "水泥杆", "spec": "Φ190×12m", "unit": "根", "price": 1450.0,
     "keywords": ["水泥杆", "Φ190×12m", "190×12"]},
    {"name": "拉线", "spec": "", "unit": "条", "price": 380.0,
     "keywords": ["拉线"]},
    {"name": "10kV避雷器", "spec": "", "unit": "只", "price": 380.0,
     "keywords": ["避雷器"]},
    {"name": "10kV金具", "spec": "", "unit": "套", "price": 450.0,
     "keywords": ["10kV金具", "10kV 金具"]},
    {"name": "低压金具", "spec": "", "unit": "套", "price": 120.0,
     "keywords": ["低压金具"]},
    {"name": "台架变金具及支架", "spec": "", "unit": "套", "price": 3500.0,
     "keywords": ["台架变金具", "支架"]},
    {"name": "接地装置材料", "spec": "", "unit": "组", "price": 900.0,
     "keywords": ["接地装置"]},
    {"name": "安全围栏", "spec": "", "unit": "套", "price": 1200.0,
     "keywords": ["围栏"]},
    {"name": "电缆终端头", "spec": "", "unit": "个", "price": 450.0,
     "keywords": ["终端头"]},
    {"name": "电力电缆", "spec": "ZA-YJV-1×240", "unit": "千米", "price": 180000.0,
     "keywords": ["ZA-YJV", "电力电缆"]},
    {"name": "10kV架空绝缘导线", "spec": "JKLGYJ-70/10", "unit": "千米", "price": 12500.0,
     "keywords": ["JKLGYJ-70/10"]},
    {"name": "低压绝缘导线", "spec": "BLVV-240", "unit": "千米", "price": 9000.0,
     "keywords": ["BLVV-240"]},
    {"name": "低压绝缘导线", "spec": "BLVV-120", "unit": "千米", "price": 7000.0,
     "keywords": ["BLVV-120"]},
    {"name": "低压绝缘导线", "spec": "JKLYJ-120", "unit": "千米", "price": 9000.0,
     "keywords": ["JKLYJ-120"]},
    {"name": "低压绝缘导线", "spec": "JKLYJ-240", "unit": "千米", "price": 14000.0,
     "keywords": ["JKLYJ-240"]},
]


def _normalize(text: str) -> str:
    """去掉空白并统一全角括号，便于型号匹配。"""
    return (text or "").replace("（", "(").replace("）", ")").replace(" ", "")


def is_conductor_material(name: str) -> bool:
    """判断某主材是否为架空绝缘导线。"""
    return "导线" in (name or "")


def conductor_voltage(name: str, spec: str = "") -> str | None:
    """从主材/线路名称与型号推断电压等级：'10kV' 或 '1kV'。"""
    text = _normalize(f"{name or ''} {spec or ''}")
    if "10kV" in text or "10KV" in text:
        return "10kV"
    if "1kV" in text or "0.4kV" in text or "低压" in text:
        return "1kV"
    if "JKLGYJ" in text:
        return "10kV"
    if "JKLYJ" in text or "BLVV" in text or "BLV" in text:
        return "1kV"
    return None


def pole_quota(pole_type: str) -> str:
    """按杆型返回电杆组立定额子目：190×12 → YD3-0103，其余 → YD3-0101。"""
    text = _normalize(pole_type)
    if "190×12" in text or "190x12" in text.lower():
        return "YD3-0103"
    return "YD3-0101"


def conductor_quota(voltage: str) -> str:
    """按电压等级返回导线架设定额子目。"""
    return "YD3-0301" if "10kV" in (voltage or "") else "YD3-0302"


def lookup_material_price(name: str, spec: str = "") -> float:
    """按主材名称/型号匹配默认单价（rates.MATERIAL_PRICES），取不到返回 0。"""
    text = _normalize(f"{name or ''} {spec or ''}")
    best_price = 0.0
    best_score = 0
    for mat in DEFAULT_MATERIALS:
        score = 0
        for kw in mat["keywords"]:
            if _normalize(kw) and _normalize(kw) in text:
                score += 2
        if _normalize(mat["spec"]) and _normalize(mat["spec"]) in text:
            score += 3
        if _normalize(mat["name"]) == _normalize(name):
            score += 2
        if score > best_score:
            best_score = score
            best_price = mat["price"]
    return best_price


# ---------------------------------------------------------------------------
# 设计预算（附件5）生成系数：先算模型预算，再按系数生成设计预算
# ---------------------------------------------------------------------------

DESIGN_FACTORS = {
    "DEMO-A": {"equipment": 0.995, "install": 1.012, "other": 0.990},
    "DEMO-B": {"equipment": 1.008, "install": 0.988, "other": 1.020},
    "DEMO-C": {"equipment": 0.998, "install": 1.005, "other": 1.000},
}

# ---------------------------------------------------------------------------
# 设计清册量基线（compare_materials 的“设计量”来源；按项目编码区分）
#   source 取值：
#     ("conductor", "10kV"|"1kV")  模型导线主材量 = 亘长×相数×1.03
#     ("poles", None)              电杆数量
#     ("stays", None)              拉线数量
#     ("equipment", 关键词)         设备数量（model_equipment.category=设备）
#     ("main", 关键词)              主材数量（model_equipment.category=主材）
#     ("demolition", 关键词)        拆除数量（model_demolition）
#   reserve_pct：清册未含预留损耗时，模型侧允许的差异率（%）
#   abnormal_reason：判定为“异常”时的归因说明
# ---------------------------------------------------------------------------

DESIGN_BASELINES = {
    "DEMO-A": [
        {"name": "10kV架空绝缘导线", "spec": "JKLGYJ-70/10", "unit": "km",
         "design_qty": 0.176, "source": ("conductor", "10kV")},
        {"name": "低压绝缘导线", "spec": "BLVV-240/BLVV-120", "unit": "km",
         "design_qty": 3.15, "source": ("conductor", "1kV"), "reserve_pct": 3.0,
         "reason": "清册未含预留损耗，模型按3%预留，量差正常"},
        {"name": "水泥杆", "spec": "Φ150×8m", "unit": "根",
         "design_qty": 16, "source": ("poles", None)},
        {"name": "电力电缆", "spec": "ZA-YJV-1×240", "unit": "km",
         "design_qty": 0.070, "source": ("main", "电力电缆"), "reserve_pct": 6.0,
         "reason": "电缆预留及波形系数按6%，量差正常"},
        {"name": "拉线", "spec": "", "unit": "条",
         "design_qty": 12, "source": ("stays", None)},
        {"name": "10kV避雷器", "spec": "YH10CX-17/50", "unit": "只",
         "design_qty": 2, "source": ("main", "避雷器")},
        {"name": "拆除低压导线", "spec": "", "unit": "km",
         "design_qty": 2.5, "source": ("demolition", "低压导线")},
        {"name": "拆除水泥杆", "spec": "Φ150×8m", "unit": "根",
         "design_qty": 3, "source": ("demolition", "水泥杆")},
    ],
    "DEMO-B": [
        {"name": "10kV架空绝缘导线", "spec": "JKLGYJ-70/10", "unit": "km",
         "design_qty": 6.62, "source": ("conductor", "10kV")},
        {"name": "低压绝缘导线", "spec": "JKLYJ-120(1kV)", "unit": "km",
         "design_qty": 3.108, "source": ("conductor", "1kV"), "reserve_pct": 3.0,
         "reason": "清册未含预留损耗，模型按3%预留，量差正常"},
        {"name": "水泥杆", "spec": "Φ190×12m/Φ150×8m", "unit": "根",
         "design_qty": 50, "source": ("poles", None)},
        {"name": "拉线", "spec": "", "unit": "条",
         "design_qty": 18, "source": ("stays", None),
         "abnormal_reason": "清册与杆塔明细表不一致，需设计确认"},
        {"name": "10kV避雷器", "spec": "YH10CX-17/50", "unit": "只",
         "design_qty": 36, "source": ("main", "避雷器")},
        {"name": "配电变压器", "spec": "SB20-M-200kVA", "unit": "台",
         "design_qty": 1, "source": ("equipment", "配电变压器")},
    ],
    "DEMO-C": [
        {"name": "10kV架空绝缘导线", "spec": "JKLGYJ-70/10", "unit": "km",
         "design_qty": 0.238, "source": ("conductor", "10kV")},
        {"name": "低压绝缘导线", "spec": "JKLYJ-240(1kV)", "unit": "km",
         "design_qty": 0.060, "source": ("conductor", "1kV"), "reserve_pct": 3.0,
         "reason": "清册未含预留损耗，模型按3%预留，量差正常"},
        {"name": "水泥杆", "spec": "Φ150×8m", "unit": "根",
         "design_qty": 4, "source": ("poles", None)},
        {"name": "配电智能网关", "spec": "", "unit": "套",
         "design_qty": 1, "source": ("equipment", "网关")},
    ],
}
