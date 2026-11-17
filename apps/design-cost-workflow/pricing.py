# -*- coding: utf-8 -*-
"""pricing.py — 计价引擎：定额套用、费用汇总、设计预算对比、导出数据。

公共接口与内部契约第 7 节一致：

    quota_library / compute_pricing / pricing_summary / compare_budget
    build_export_rows
另提供 Web 层“更换定额子目”所需的 update_item_quota。

费用构成与费率严格按 2022 版《20kV及以下配电网工程建设预算编制与计算规定》
（演示取江西 Ⅰ 类地区、县城或镇）；定额库与主材价为演示数据。
"""

from __future__ import annotations

from typing import Any

import engine
import rates

#: 与 pricing_fees.category 保持一致的费用分组名称
FEE_INSTALL = "安装工程费"
FEE_BUILD = "建筑工程费"
FEE_DEMOLITION = "拆除工程费"
FEE_EQUIPMENT = "设备购置费"
FEE_OTHER = "其他费用"
FEE_RESERVE = "基本预备费"
FEE_STATIC = "静态投资"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _as_dict(row: Any) -> dict:
    if isinstance(row, dict):
        return dict(row)
    keys = getattr(row, "keys", None)
    if keys is not None:
        return {key: row[key] for key in keys()}
    return dict(row)


def _rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    return [_as_dict(row) for row in conn.execute(sql, params).fetchall()]


def _one(conn, sql: str, params: tuple = ()) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return _as_dict(row) if row is not None else None


def _round2(value: float) -> float:
    return round(float(value or 0), 2)


def _normalize(text: str) -> str:
    return (text or "").replace("（", "(").replace("）", ")").replace(" ", "").replace("　", "")


def _material_price(main_rows: list[dict], name: str, spec: str) -> tuple[float, str]:
    """匹配主材单价与供应方式：优先主材清册，取不到用 rates 默认价。"""
    text = _normalize(f"{name} {spec}")
    best: dict | None = None
    best_score = -1
    for equip in main_rows:
        equip_name = equip.get("name") or ""
        equip_spec = equip.get("spec") or ""
        equip_text = _normalize(f"{equip_name} {equip_spec}")
        score = 0
        norm_name = _normalize(name)
        norm_spec = _normalize(spec)
        if norm_name and norm_name == _normalize(equip_name):
            score += 5
        elif norm_name and norm_name in equip_text:
            score += 3
        elif norm_name and _normalize(equip_name) and _normalize(equip_name) in norm_name:
            score += 2
        if norm_spec and _normalize(equip_spec) and (
            norm_spec in equip_text or _normalize(equip_spec) in text
        ):
            score += 4
        if score > best_score:
            best_score = score
            best = equip
    if best is not None and best_score >= 3:
        price = float(best.get("unit_price") or 0)
        if price > 0:
            return price, (best.get("supply") or "").strip() or "乙供"
    return float(rates.lookup_material_price(name, spec)), ""


# ---------------------------------------------------------------------------
# 定额库
# ---------------------------------------------------------------------------

def quota_library() -> list[dict]:
    """返回示例定额库（演示用；字段 code,name,unit,labor,material,machine,category）。"""
    return [dict(quota) for quota in rates.QUOTA_LIBRARY]


# ---------------------------------------------------------------------------
# 计价数据构建（不写库）
# ---------------------------------------------------------------------------

def _build_items_and_materials(conn, project_id: int) -> tuple[list[dict], list[dict]]:
    """由工程量清单生成计价行与主材明细（不写库）。"""
    quantities = _rows(conn, "SELECT * FROM quantities WHERE project_id=? ORDER BY seq, id", (project_id,))
    model_equipment = _rows(conn, "SELECT * FROM model_equipment WHERE project_id=? ORDER BY id", (project_id,))
    main_rows = [row for row in model_equipment if row.get("category") == rates.CATEGORY_MATERIAL]

    items: list[dict] = []
    materials: list[dict] = []
    item_index_by_key: dict[tuple[str, str], int] = {}

    for quantity in quantities:
        if quantity.get("category") == rates.CATEGORY_MATERIAL:
            continue
        quota_code = (quantity.get("quota_code") or "").strip()
        quota = rates.QUOTA_BY_CODE.get(quota_code)
        qty = float(quantity.get("qty") or 0)
        labor_unit = float(quota["labor"]) if quota else 0.0
        material_unit = float(quota["material"]) if quota else 0.0
        machine_unit = float(quota["machine"]) if quota else 0.0
        item = {
            "quantity_id": quantity.get("id"),
            "unit_name": quantity.get("unit_name") or "",
            "name": quantity.get("name") or "",
            "spec": quantity.get("spec") or "",
            "unit": quantity.get("unit") or "",
            "qty": qty,
            "category": quantity.get("category") or rates.CATEGORY_INSTALL,
            "quota_code": quota_code,
            "quota_name": quota["name"] if quota else (quantity.get("name") or ""),
            "labor_unit": labor_unit,
            "material_unit": material_unit,
            "machine_unit": machine_unit,
            "labor": _round2(qty * labor_unit),
            "material": _round2(qty * material_unit),
            "machine": _round2(qty * machine_unit),
            "main_material": 0.0,
            "direct_cost": 0.0,
        }
        index = len(items)
        items.append(item)
        if quota_code:
            item_index_by_key.setdefault((item["unit_name"], quota_code), index)

    # 主材明细挂接到对应安装清单行（按“单位工程 + 定额子目”定位）
    for quantity in quantities:
        if quantity.get("category") != rates.CATEGORY_MATERIAL:
            continue
        name = quantity.get("name") or ""
        spec = quantity.get("spec") or ""
        qty = float(quantity.get("qty") or 0)
        price, supply = _material_price(main_rows, name, spec)
        amount = _round2(qty * price)
        quota_code = (quantity.get("quota_code") or "").strip()
        unit_name = quantity.get("unit_name") or ""
        target = item_index_by_key.get((unit_name, quota_code))
        if target is None:
            target = next((i for i, item in enumerate(items) if item["unit_name"] == unit_name), None)
        if target is None:
            items.append({
                "quantity_id": quantity.get("id"),
                "unit_name": unit_name,
                "name": f"{name}（主材）",
                "spec": spec,
                "unit": quantity.get("unit") or "",
                "qty": 1.0,
                "category": rates.CATEGORY_INSTALL,
                "quota_code": "",
                "quota_name": "主材（未匹配安装子目）",
                "labor_unit": 0.0, "material_unit": 0.0, "machine_unit": 0.0,
                "labor": 0.0, "material": 0.0, "machine": 0.0,
                "main_material": 0.0, "direct_cost": 0.0,
            })
            target = len(items) - 1
        items[target]["main_material"] = _round2(items[target]["main_material"] + amount)
        materials.append({
            "item_index": target,
            "name": name,
            "spec": spec,
            "unit": quantity.get("unit") or "",
            "qty": qty,
            "unit_price": _round2(price),
            "amount": amount,
            "supply": supply or "乙供",
        })

    for item in items:
        item["direct_cost"] = _round2(
            item["labor"] + item["material"] + item["machine"] + item["main_material"]
        )
    return items, materials


def _category_costs(items: list[dict], category: str) -> dict:
    selected = [item for item in items if item.get("category") == category]
    labor = _round2(sum(item["labor"] for item in selected))
    material = _round2(sum(item["material"] for item in selected))
    machine = _round2(sum(item["machine"] for item in selected))
    main_material = _round2(sum(item["main_material"] for item in selected))
    direct = _round2(labor + material + machine + main_material)
    return {
        "count": len(selected),
        "labor": labor,
        "material": material,
        "machine": machine,
        "main_material": main_material,
        "direct": direct,
    }


def _category_fee_rows(costs: dict, category_label: str, fee_key: str) -> tuple[list[dict], float]:
    """生成某一工程类别（安装/建筑/拆除）的费用明细行，返回 (rows, 含税合计)。"""
    rows: list[dict] = []
    direct = costs["direct"]
    rows.append({
        "category": category_label, "item": "直接费",
        "base_desc": "人工费+材料费+机械费+主材费",
        "formula": f"人工费 {costs['labor']:,.2f} + 材料费 {costs['material'] + costs['main_material']:,.2f} + 机械费 {costs['machine']:,.2f}",
        "rate": 0.0, "amount": direct,
    })
    base = _round2(costs["labor"] + costs["machine"])
    measure_total = 0.0
    for item_name, rate in rates.MEASURE_FEE_ITEMS[fee_key]:
        amount = _round2(base * rate / 100.0)
        measure_total += amount
        rows.append({
            "category": category_label, "item": item_name,
            "base_desc": "人工费+机械费", "formula": f"(人工费+机械费)×{rate}%",
            "rate": float(rate), "amount": amount,
        })
    measure_total = _round2(measure_total)
    rows.append({
        "category": category_label, "item": "措施费小计",
        "base_desc": "人工费+机械费",
        "formula": f"合计费率 {rates.MEASURE_FEE_TOTAL[fee_key]}%",
        "rate": rates.MEASURE_FEE_TOTAL[fee_key], "amount": measure_total,
    })
    regulatory_cfg = rates.REGULATORY_FEE[fee_key]
    regulatory = _round2(
        costs["labor"] * regulatory_cfg["labor_factor"]
        * (regulatory_cfg["social_rate"] + regulatory_cfg["housing_rate"]) / 100.0
    )
    rows.append({
        "category": category_label, "item": "规费",
        "base_desc": (
            f"人工费×{regulatory_cfg['labor_factor']}×(社保{regulatory_cfg['social_rate']}%"
            f"+公积金{regulatory_cfg['housing_rate']}%)"
        ),
        "formula": f"人工费×{regulatory_cfg['labor_factor']}×{regulatory_cfg['social_rate'] + regulatory_cfg['housing_rate']}%",
        "rate": _round2(regulatory_cfg["labor_factor"] * (regulatory_cfg["social_rate"] + regulatory_cfg["housing_rate"])),
        "amount": regulatory,
    })
    enterprise_rate = rates.ENTERPRISE_FEE_RATE[fee_key]
    enterprise = _round2(base * enterprise_rate / 100.0)
    rows.append({
        "category": category_label, "item": "企业管理费",
        "base_desc": "人工费+机械费", "formula": f"(人工费+机械费)×{enterprise_rate}%",
        "rate": float(enterprise_rate), "amount": enterprise,
    })
    profit_rate = rates.PROFIT_RATE[fee_key]
    profit = _round2(base * profit_rate / 100.0)
    rows.append({
        "category": category_label, "item": "利润",
        "base_desc": "人工费+机械费", "formula": f"(人工费+机械费)×{profit_rate}%",
        "rate": float(profit_rate), "amount": profit,
    })
    price_diff = _round2((direct + measure_total + regulatory + enterprise + profit) * rates.PRICE_DIFF_RATE / 100.0)
    rows.append({
        "category": category_label, "item": "编制基准期价差",
        "base_desc": "人工/材料/机械价差（演示取 0）",
        "formula": "按造价管理机构发布文件调整（演示取 0）",
        "rate": rates.PRICE_DIFF_RATE, "amount": price_diff,
    })
    tax_base = _round2(direct + measure_total + regulatory + enterprise + profit + price_diff)
    vat = _round2(tax_base * rates.VAT_RATE / 100.0)
    rows.append({
        "category": category_label, "item": "增值税",
        "base_desc": "直接费+间接费+利润+价差",
        "formula": f"(直接费+措施费+规费+企业管理费+利润+价差)×{rates.VAT_RATE}%",
        "rate": rates.VAT_RATE, "amount": vat,
    })
    total = _round2(tax_base + vat)
    rows.append({
        "category": category_label, "item": "合计",
        "base_desc": "含税工程费用", "formula": "直接费+措施费+规费+企业管理费+利润+价差+增值税",
        "rate": 0.0, "amount": total,
    })
    return rows, total


def _build_other_fees(install: float, build: float, demolition: float,
                      equipment: float, line_km: float, demolition_direct: float) -> tuple[list[dict], float]:
    """按 2022 版编制规定生成其他费用明细，返回 (rows, 合计)。"""
    jianan = _round2(install + build + demolition)
    rows: list[dict] = []
    amounts: dict[str, float] = {}

    def amount_of(base: str, rate: float) -> tuple[float, str]:
        if base == "jianan":
            return _round2(jianan * rate / 100.0), "建安工程费"
        if base == "jianan_equipment":
            return _round2((jianan + equipment) * rate / 100.0), "建安工程费+设备购置费"
        if base == "build_distance":
            amount = _round2(build * rate / 100.0 + line_km * rates.SURVEY_PRICE_PER_KM)
            return amount, "建筑工程费+线路亘长"
        if base == "basic_design":
            return _round2(amounts.get("基本设计费", 0.0) * rate / 100.0), "基本设计费"
        return 0.0, ""

    for spec in rates.OTHER_FEE_ITEMS:
        amount, base_desc = amount_of(spec["base"], float(spec["rate"]))
        amounts[spec["item"]] = amount
        rows.append({
            "category": FEE_OTHER, "item": spec["item"],
            "base_desc": base_desc, "formula": spec["formula"],
            "rate": float(spec["rate"]), "amount": amount,
        })
    if demolition_direct > 0:
        amount = _round2(demolition_direct * rates.RESIDUAL_CLEAN_FEE_RATE / 100.0)
        amounts["余物清理费"] = amount
        rows.append({
            "category": FEE_OTHER, "item": "余物清理费",
            "base_desc": "拆除工程直接费",
            "formula": f"拆除工程直接费×{rates.RESIDUAL_CLEAN_FEE_RATE}%（线路拆除不能利用）",
            "rate": rates.RESIDUAL_CLEAN_FEE_RATE, "amount": amount,
        })
    other_total = _round2(sum(amounts.values()))
    rows.append({
        "category": FEE_OTHER, "item": "合计",
        "base_desc": "其他费用合计", "formula": "Σ其他费用各项",
        "rate": 0.0, "amount": other_total,
    })
    return rows, other_total


def _build_fees(conn, project_id: int, items: list[dict]) -> tuple[list[dict], dict]:
    """汇总生成 pricing_fees 行与金额摘要（不写库）。"""
    model_equipment = _rows(conn, "SELECT * FROM model_equipment WHERE project_id=?", (project_id,))
    model_lines = _rows(conn, "SELECT * FROM model_lines WHERE project_id=?", (project_id,))
    line_km = _round2(sum(float(line.get("length_km") or 0) for line in model_lines))

    fees: list[dict] = []
    install_costs = _category_costs(items, rates.CATEGORY_INSTALL)
    build_costs = _category_costs(items, rates.CATEGORY_BUILD)
    demolition_costs = _category_costs(items, rates.CATEGORY_DEMOLITION)

    install_total = 0.0
    if install_costs["count"] > 0:
        rows, install_total = _category_fee_rows(install_costs, FEE_INSTALL, "install")
        fees.extend(rows)
    build_total = 0.0
    if build_costs["count"] > 0:
        rows, build_total = _category_fee_rows(build_costs, FEE_BUILD, "build")
        fees.extend(rows)
    demolition_total = 0.0
    if demolition_costs["count"] > 0:
        rows, demolition_total = _category_fee_rows(demolition_costs, FEE_DEMOLITION, "install")
        fees.extend(rows)

    equipment_rows = [row for row in model_equipment if row.get("category") == "设备"]
    equipment_original = _round2(
        sum(float(row.get("qty") or 0) * float(row.get("unit_price") or 0) for row in equipment_rows)
    )
    equipment_total = 0.0
    if equipment_original > 0:
        transport = _round2(equipment_original * rates.EQUIPMENT_TRANSPORT_FEE_RATE / 100.0)
        equipment_total = _round2(equipment_original + transport)
        fees.append({
            "category": FEE_EQUIPMENT, "item": "设备原价",
            "base_desc": "Σ设备数量×设备单价",
            "formula": f"设备数量×设备单价（{len(equipment_rows)} 项）",
            "rate": 0.0, "amount": equipment_original,
        })
        fees.append({
            "category": FEE_EQUIPMENT, "item": "运杂费",
            "base_desc": "设备原价", "formula": f"设备原价×{rates.EQUIPMENT_TRANSPORT_FEE_RATE}%",
            "rate": rates.EQUIPMENT_TRANSPORT_FEE_RATE, "amount": transport,
        })
        fees.append({
            "category": FEE_EQUIPMENT, "item": "合计",
            "base_desc": "设备购置费（含运杂费）", "formula": "设备原价+运杂费",
            "rate": 0.0, "amount": equipment_total,
        })

    other_total = 0.0
    if install_costs["count"] or build_costs["count"] or demolition_costs["count"] or equipment_original:
        rows, other_total = _build_other_fees(
            install_total, build_total, demolition_total,
            equipment_total, line_km, demolition_costs["direct"],
        )
        fees.extend(rows)

    reserve_base = _round2(install_total + build_total + demolition_total + equipment_total + other_total)
    reserve = _round2(reserve_base * rates.BASIC_RESERVE_RATE / 100.0)
    fees.append({
        "category": FEE_RESERVE, "item": "基本预备费",
        "base_desc": "建安工程费+设备购置费+其他费用",
        "formula": (
            f"(建安 {install_total + build_total + demolition_total:,.2f} + 设备 {equipment_total:,.2f} "
            f"+ 其他 {other_total:,.2f})×{rates.BASIC_RESERVE_RATE}%"
        ),
        "rate": rates.BASIC_RESERVE_RATE, "amount": reserve,
    })
    jianan = _round2(install_total + build_total + demolition_total)
    static_total = _round2(jianan + equipment_total + other_total + reserve)
    fees.append({
        "category": FEE_STATIC, "item": "静态投资",
        "base_desc": "建安工程费+设备购置费+其他费用+基本预备费",
        "formula": (
            f"{jianan:,.2f} + {equipment_total:,.2f} + {other_total:,.2f} + {reserve:,.2f}"
        ),
        "rate": 0.0, "amount": static_total,
    })
    summary = {
        "install": install_total,
        "build": build_total,
        "demolition": demolition_total,
        "equipment": equipment_total,
        "other": other_total,
        "reserve": reserve,
        "jianan": jianan,
        "static_total": static_total,
    }
    return fees, summary


def _build_pricing_data(conn, project_id: int) -> dict:
    """构建完整计价数据（不写库）：items/materials/fees/summary。"""
    items, materials = _build_items_and_materials(conn, project_id)
    fees, summary = _build_fees(conn, project_id, items)
    summary["items"] = len(items)
    return {"items": items, "materials": materials, "fees": fees, "summary": summary}


# ---------------------------------------------------------------------------
# 写库
# ---------------------------------------------------------------------------

def _clear_pricing(conn, project_id: int) -> None:
    conn.execute("DELETE FROM pricing_items WHERE project_id=?", (project_id,))
    conn.execute("DELETE FROM pricing_materials WHERE project_id=?", (project_id,))
    conn.execute("DELETE FROM pricing_fees WHERE project_id=?", (project_id,))


def _persist_pricing(conn, project_id: int, data: dict) -> None:
    _clear_pricing(conn, project_id)
    item_ids: list[int] = []
    for item in data["items"]:
        cursor = conn.execute(
            "INSERT INTO pricing_items (project_id, seq, quantity_id, unit_name, name, spec, unit, qty, "
            "category, quota_code, quota_name, labor_unit, material_unit, machine_unit, labor, material, "
            "machine, main_material, direct_cost) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, len(item_ids) + 1, item["quantity_id"], item["unit_name"], item["name"],
             item["spec"], item["unit"], item["qty"], item["category"], item["quota_code"],
             item["quota_name"], item["labor_unit"], item["material_unit"], item["machine_unit"],
             item["labor"], item["material"], item["machine"], item["main_material"],
             item["direct_cost"]),
        )
        item_ids.append(int(cursor.lastrowid))
    for material in data["materials"]:
        pricing_item_id = item_ids[material["item_index"]] if material["item_index"] < len(item_ids) else None
        conn.execute(
            "INSERT INTO pricing_materials (project_id, pricing_item_id, name, spec, unit, qty, "
            "unit_price, amount, supply) VALUES (?,?,?,?,?,?,?,?,?)",
            (project_id, pricing_item_id, material["name"], material["spec"], material["unit"],
             material["qty"], material["unit_price"], material["amount"], material["supply"]),
        )
    for seq, fee in enumerate(data["fees"], 1):
        conn.execute(
            "INSERT INTO pricing_fees (project_id, seq, category, item, base_desc, formula, rate, amount) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (project_id, seq, fee["category"], fee["item"], fee["base_desc"], fee["formula"],
             fee["rate"], fee["amount"]),
        )


def _update_compare_budget(conn, project_id: int, summary: dict) -> None:
    """更新设计预算（附件5） vs 模型预算的 5 行对比。"""
    project = _one(conn, "SELECT * FROM projects WHERE id=?", (project_id,))
    if project is None:
        return
    design_map = {
        "设备购置费": float(project.get("design_equipment") or 0),
        "安装工程费(含建筑)": float(project.get("design_install") or 0),
        "其他费用": float(project.get("design_other") or 0),
        "基本预备费": float(project.get("design_reserve") or 0),
        "静态投资": float(project.get("design_total") or 0),
    }
    model_map = {
        "设备购置费": summary["equipment"],
        "安装工程费(含建筑)": _round2(summary["install"] + summary["build"]),
        "其他费用": summary["other"],
        "基本预备费": summary["reserve"],
        "静态投资": summary["static_total"],
    }
    conn.execute("DELETE FROM compare_budget WHERE project_id=?", (project_id,))
    for seq, item in enumerate(design_map, 1):
        design_amount = _round2(design_map[item])
        model_amount = _round2(model_map[item])
        diff = _round2(model_amount - design_amount)
        rate = _round2(diff / design_amount * 100.0) if design_amount else 0.0
        conn.execute(
            "INSERT INTO compare_budget (project_id, seq, item, design_amount, model_amount, diff, rate, note) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (project_id, seq, item, design_amount, model_amount, diff, rate, "模型按示例定额库重算，与附件5设计预算对比"),
        )


def _audit(conn, project_id: int, user: dict | None, action: str, detail: str = "") -> None:
    user = user or engine.SYSTEM_USER
    conn.execute(
        "INSERT INTO audit (project_id, user_id, action, detail, created_at) VALUES (?,?,?,?,?)",
        (project_id, user.get("id"), action, detail, engine.now()),
    )


# ---------------------------------------------------------------------------
# 公共接口
# ---------------------------------------------------------------------------

def compute_pricing(conn, project_id: int, user: dict | None = None) -> dict:
    """清空并重算计价（items/materials/fees/compare_budget），返回金额摘要。"""
    user = user or engine.SYSTEM_USER
    quantity_count = _one(
        conn, "SELECT COUNT(*) AS n FROM quantities WHERE project_id=?", (project_id,)
    )["n"]
    if quantity_count == 0:
        engine.compute_quantities(conn, project_id, user)
    data = _build_pricing_data(conn, project_id)
    _persist_pricing(conn, project_id, data)
    summary = data["summary"]
    _update_compare_budget(conn, project_id, summary)
    _audit(conn, project_id, user, "计价完成",
           (f"套用定额 {summary['items']} 项，静态投资 {summary['static_total']:,.2f} 元 "
            f"（设备购置费 {summary['equipment']:,.2f} 元）"))
    conn.commit()
    return {
        "items": summary["items"],
        "total": summary["static_total"],
        "install": summary["install"],
        "build": summary["build"],
        "demolition": summary["demolition"],
        "equipment": summary["equipment"],
        "other": summary["other"],
        "reserve": summary["reserve"],
        "static_total": summary["static_total"],
    }


def _fee_amount(fees: list[dict], category: str, item: str) -> float:
    for fee in fees:
        if fee.get("category") == category and fee.get("item") == item:
            return float(fee.get("amount") or 0)
    return 0.0


def _design_dict(project: dict | None) -> dict:
    project = project or {}
    return {
        "equipment": float(project.get("design_equipment") or 0),
        "install": float(project.get("design_install") or 0),
        "other": float(project.get("design_other") or 0),
        "reserve": float(project.get("design_reserve") or 0),
        "total": float(project.get("design_total") or 0),
        "name": project.get("name") or "",
        "code": project.get("code") or "",
    }


def pricing_summary(conn, project_id: int) -> dict:
    """读取计价结果：items / materials / fees / 分组金额 / 设计预算 / 预算对比。"""
    items = _rows(conn, "SELECT * FROM pricing_items WHERE project_id=? ORDER BY seq, id", (project_id,))
    material_rows = _rows(
        conn, "SELECT * FROM pricing_materials WHERE project_id=? ORDER BY id", (project_id,)
    )
    fees = _rows(conn, "SELECT * FROM pricing_fees WHERE project_id=? ORDER BY seq, id", (project_id,))
    project = _one(conn, "SELECT * FROM projects WHERE id=?", (project_id,))
    compare = compare_budget(conn, project_id)

    materials: dict[int, list[dict]] = {}
    for material in material_rows:
        materials.setdefault(int(material.get("pricing_item_id") or 0), []).append(material)

    install = _fee_amount(fees, FEE_INSTALL, "合计")
    build = _fee_amount(fees, FEE_BUILD, "合计")
    demolition = _fee_amount(fees, FEE_DEMOLITION, "合计")
    equipment = _fee_amount(fees, FEE_EQUIPMENT, "合计")
    other = _fee_amount(fees, FEE_OTHER, "合计")
    reserve = _fee_amount(fees, FEE_RESERVE, "基本预备费")
    static_total = _fee_amount(fees, FEE_STATIC, "静态投资")
    return {
        "items": items,
        "materials": materials,
        "fees": fees,
        "install": install,
        "build": build,
        "demolition": demolition,
        "equipment": equipment,
        "other": other,
        "reserve": reserve,
        "jianan": _round2(install + build + demolition),
        "static_total": static_total,
        "design": _design_dict(project),
        "compare": compare,
    }


def compare_budget(conn, project_id: int) -> list[dict]:
    """读取设计预算 vs 模型预算对比行（未计价时为空列表）。"""
    return _rows(conn, "SELECT * FROM compare_budget WHERE project_id=? ORDER BY seq, id", (project_id,))


def build_export_rows(conn, project_id: int) -> dict:
    """汇总 Excel 导出所需行数据（费用汇总/安装工程预算表/主材表/其他费用明细）。"""
    summary = pricing_summary(conn, project_id)
    project = _one(conn, "SELECT * FROM projects WHERE id=?", (project_id,)) or {}
    items = summary["items"]
    items_by_id = {int(item["id"]): item for item in items if item.get("id") is not None}
    materials: list[dict] = []
    for item_id, rows in summary["materials"].items():
        item = items_by_id.get(int(item_id)) or {}
        for row in rows:
            enriched = dict(row)
            enriched["unit_name"] = item.get("unit_name") or ""
            enriched["quota_code"] = item.get("quota_code") or ""
            enriched["install_item"] = item.get("name") or ""
            materials.append(enriched)
    materials.sort(key=lambda row: (items_by_id.get(int(row.get("pricing_item_id") or 0), {}).get("seq", 0),
                                    row.get("id", 0)))
    fees = summary["fees"]
    other_fees = [fee for fee in fees if fee.get("category") == FEE_OTHER]
    totals = {
        "install": summary["install"],
        "build": summary["build"],
        "demolition": summary["demolition"],
        "equipment": summary["equipment"],
        "other": summary["other"],
        "reserve": summary["reserve"],
        "static_total": summary["static_total"],
    }
    return {
        "project": dict(project),
        "summary": fees,
        "fees": fees,
        "items": items,
        "pricing_items": items,
        "materials": materials,
        "other_fees": other_fees,
        "compare": summary["compare"],
        "design": summary["design"],
        "totals": totals,
    }


def update_item_quota(conn, project_id: int, item_id: int, quota_code: str,
                      user: dict | None = None) -> tuple[bool, str]:
    """更换某计价行的定额子目，重算该行金额与费用汇总（Web 层按钮使用）。"""
    user = user or engine.SYSTEM_USER
    quota_code = (quota_code or "").strip()
    quota = rates.QUOTA_BY_CODE.get(quota_code)
    if quota is None:
        return False, f"定额子目 {quota_code or '（空）'} 不存在，请从示例定额库中选择"
    item = _one(
        conn, "SELECT * FROM pricing_items WHERE id=? AND project_id=?", (item_id, project_id)
    )
    if item is None:
        return False, "计价行不存在，请先执行计价"
    qty = float(item.get("qty") or 0)
    labor = _round2(qty * float(quota["labor"]))
    material = _round2(qty * float(quota["material"]))
    machine = _round2(qty * float(quota["machine"]))
    direct_cost = _round2(labor + material + machine + float(item.get("main_material") or 0))
    conn.execute(
        "UPDATE pricing_items SET quota_code=?, quota_name=?, labor_unit=?, material_unit=?, "
        "machine_unit=?, labor=?, material=?, machine=?, direct_cost=? WHERE id=? AND project_id=?",
        (quota_code, quota["name"], float(quota["labor"]), float(quota["material"]),
         float(quota["machine"]), labor, material, machine, direct_cost, item_id, project_id),
    )
    if item.get("quantity_id"):
        conn.execute(
            "UPDATE quantities SET quota_code=?, note=? WHERE id=? AND project_id=?",
            (quota_code, f"人工更换定额子目：{quota_code}", item["quantity_id"], project_id),
        )
    # 依据现有计价行重新生成费用汇总与预算对比（不重套全部清单）
    items = _rows(conn, "SELECT * FROM pricing_items WHERE project_id=? ORDER BY seq, id", (project_id,))
    fees, summary = _build_fees(conn, project_id, items)
    conn.execute("DELETE FROM pricing_fees WHERE project_id=?", (project_id,))
    for seq, fee in enumerate(fees, 1):
        conn.execute(
            "INSERT INTO pricing_fees (project_id, seq, category, item, base_desc, formula, rate, amount) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (project_id, seq, fee["category"], fee["item"], fee["base_desc"], fee["formula"],
             fee["rate"], fee["amount"]),
        )
    _update_compare_budget(conn, project_id, summary)
    _audit(conn, project_id, user, "更换定额子目",
           f"清单行 #{item_id} {item.get('name') or ''} → {quota_code} {quota['name']}")
    conn.commit()
    return True, f"已更换定额子目：{quota_code} {quota['name']}，安装工程费合计 {summary['install']:,.2f} 元"
