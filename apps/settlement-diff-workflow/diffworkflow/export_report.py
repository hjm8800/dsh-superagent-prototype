"""文字分析报告导出：Markdown 与 docx。

报告面向业务负责人：先结论后论据，关键数字用千分位/百分比，附数据说明。
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from .diff import RISK_HIGH, RISK_MID, STATUS_NEW, STATUS_REMOVED, STATUS_SAME
from .normalize import format_delta, format_money, format_number, format_ratio

CATEGORY_LABELS = {"settlement": "审核结算", "material": "材料清册", "boq": "竣工资料工程量"}


def _escape_cell(value: Any, limit: int = 160) -> str:
    text = str(value if value is not None else "").replace("|", "／").replace("\n", " ").strip()
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text or "—"


def _table(headers: list[str], rows: list[list[Any]], limits: list[int] | None = None) -> str:
    limits = limits or [160] * len(headers)
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    for row in rows:
        cells = [_escape_cell(value, limits[index] if index < len(limits) else 160)
                 for index, value in enumerate(row)]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _fmt_amount(value: float | None) -> str:
    return format_money(value) if value is not None else "—"


def _fmt_ratio(value: float | None) -> str:
    return format_ratio(value) if value is not None else "—"


def _diff_rows(diffs: list[dict], limit: int = 15) -> list[list[Any]]:
    rows = []
    for diff in diffs[:limit]:
        old = diff.get("old") or {}
        new = diff.get("new") or {}
        detail = "；".join(diff.get("reasons") or []) or diff.get("note", "")
        rows.append([
            diff.get("status", ""),
            diff.get("risk", "") or "—",
            f"{diff.get('name', '')} {diff.get('spec', '')}".strip(),
            diff.get("unit", ""),
            _fmt_amount(old.get("qty")) if old.get("qty") is not None else "—",
            _fmt_amount(new.get("qty")) if new.get("qty") is not None else "—",
            _fmt_amount(diff.get("amount_delta")) if diff.get("amount_delta") is not None else "—",
            _fmt_ratio(diff.get("amount_ratio")),
            detail,
        ])
    return rows


DIFF_HEADERS = ["状态", "风险", "名称", "单位", "原数量", "新数量", "金额差（元）", "金额变化率", "说明"]
DIFF_LIMITS = [8, 6, 40, 8, 12, 12, 16, 12, 80]


def _construction_fee_section(comparison: dict) -> list[str]:
    analysis = comparison.get("analysis") or {}
    fee = analysis.get("construction_fee") or {}
    lines: list[str] = ["## 三、施工费差异成因", ""]
    if fee.get("old_fee") is None and fee.get("new_fee") is None:
        lines.append("未从资料中提取到施工费总额，无法拆解成因。")
        lines.append("")
        return lines
    fee_label = "组合件合价合计口径" if fee.get("format") == "assembly_only" else "表一口径"
    lines.append(
        f"施工费（{fee_label}）由 {_fmt_amount(fee.get('old_fee'))} 元变为 {_fmt_amount(fee.get('new_fee'))} 元"
        f"（{format_delta(fee.get('fee_delta'))} 元，{_fmt_ratio(fee.get('fee_ratio'))}）。"
    )
    lines.append("")
    direct = fee.get("direct") or {}
    yigong = fee.get("yigong_material") or {}
    agong = fee.get("agong_material") or {}
    parts = fee.get("parts") or {}
    rows = [
        ["施工费（表一口径）", _fmt_amount(fee.get("old_fee")), _fmt_amount(fee.get("new_fee")),
         _fmt_amount(fee.get("fee_delta")), _fmt_ratio(fee.get("fee_ratio"))],
        ["定额直接费合计", _fmt_amount(fee.get("direct_old")), _fmt_amount(fee.get("direct_new")),
         _fmt_amount(direct.get("net")), "—"],
        ["  其中：量差效应", "—", "—", _fmt_amount(direct.get("qty_effect")), "—"],
        ["  其中：价差效应", "—", "—", _fmt_amount(direct.get("price_effect")), "—"],
        ["  其中：新增项", "—", "—", _fmt_amount(direct.get("added")), "—"],
        ["  其中：删除项", "—", "—", _fmt_amount(-(direct.get("removed") or 0)) if direct.get("removed") else "—", "—"],
        ["  其中：单位/规格/其他", "—", "—", _fmt_amount(direct.get("other")), "—"],
        ["乙供未计价材料（计入施工费）", "—", "—", _fmt_amount(yigong.get("net")), "—"],
        ["  其中：量差/价差", "—", "—",
         _fmt_amount((yigong.get("qty_effect") or 0) + (yigong.get("price_effect") or 0)), "—"],
        ["  其中：新增-删除", "—", "—",
         _fmt_amount((yigong.get("added") or 0) - (yigong.get("removed") or 0)), "—"],
        ["取费与汇总口径残差", "—", "—", _fmt_amount(fee.get("residual_vs_effects")), "—"],
        ["（口径参考）甲供材料变化", "—", "—", _fmt_amount(agong.get("net")), "—"],
        ["（口径参考）配件变化", "—", "—", _fmt_amount(parts.get("net")), "—"],
    ]
    lines.append(_table(["分解项", "原版（元）", "新版（元）", "变化（元）", "变化率"], rows,
                        [22, 16, 16, 16, 10]))
    lines.append("")
    lines.append("口径说明：量差/价差按匹配条目逐项拆分（Δ金额≈Δ数量×原单价 + 新数量×Δ单价）；"
                 "残差 = 一笔性费用（费用类条目）+ 取费构成（措施费/规费/利润/价差/税金等）+ 损耗 + 条目匹配差异，"
                 "不能全部归因于取费。单项差异按名称+规格匹配，名称/规格改写可能放大单项；"
                 "分部数据含层级（父子项不可直接相加），判断金额影响时以分部/类别净额与汇总表为准。")
    lines.append("")

    components = fee.get("components") or []
    if components:
        rows = [[c.get("name"), _fmt_amount(c.get("old")), _fmt_amount(c.get("new")), _fmt_amount(c.get("delta"))]
                for c in components]
        lines.append("费用构成变化：")
        lines.append("")
        lines.append(_table(["费用项", "原版（元）", "新版（元）", "变化（元）"], rows, [22, 16, 16, 16]))
        lines.append("")

    sections = fee.get("sections") or []
    if sections:
        rows = [[s.get("name") or "（未命名分部）", _fmt_amount(s.get("old")), _fmt_amount(s.get("new")),
                 _fmt_amount(s.get("delta")), _fmt_ratio(s.get("ratio"))] for s in sections]
        lines.append("分部贡献（按金额变化排序）：")
        lines.append("")
        lines.append(_table(["分部", "原版（元）", "新版（元）", "变化（元）", "变化率"], rows,
                            [24, 16, 16, 16, 10]))
        lines.append("")

    causes = fee.get("item_causes") or []
    if causes:
        rows = [[c.get("name"), c.get("effect_kind"), _fmt_amount(c.get("old_qty")) if c.get("old_qty") is not None else "—",
                 _fmt_amount(c.get("new_qty")) if c.get("new_qty") is not None else "—",
                 _fmt_ratio(c.get("qty_ratio")), _fmt_amount(c.get("amount_delta"))] for c in causes[:12]]
        lines.append("项目级 TOP 成因（量差主导/价差主导/新增/删除）：")
        lines.append("")
        lines.append(_table(["名称", "成因类型", "原数量", "新数量", "数量变化率", "金额差（元）"], rows,
                            [36, 12, 12, 12, 10, 16]))
        lines.append("")

    if fee.get("yigong_delta") is not None:
        lines.append(
            f"供料口径提示：乙供未计价材料 {_fmt_amount(fee.get('old_yigong'))} 元 → "
            f"{_fmt_amount(fee.get('new_yigong'))} 元（{format_delta(fee.get('yigong_delta'))} 元）。"
            "乙供材料规模变化会直接影响施工费（含少量改列甲供）；旧版甲供材料规模也可能同步变化，"
            "判断施工费降幅时须同时看甲供清单与含配件完整口径。"
        )
        lines.append("")

    top_added = fee.get("top_added") or []
    top_removed = fee.get("top_removed") or []
    if top_added or top_removed:
        rows = []
        for label, items in (("新增", top_added), ("删除", top_removed)):
            for item in items[:4]:
                rows.append([label, item.get("name"), item.get("effect_kind"),
                             _fmt_amount(item.get("amount_delta"))])
        lines.append("新增 / 删除金额最大的条目：")
        lines.append("")
        lines.append(_table(["类型", "名称", "归类", "金额差（元）"], rows, [8, 40, 14, 16]))
        lines.append("")
    if fee.get("format") == "assembly_only":
        lines.append("注意：本版本结算书仅有“组合件汇总表”（无表一/表二甲/表三甲），施工费按组合件合价合计比较，"
                     "取费构成与甲供/乙供材料无法拆分，建议补齐完整结算书后再做正式对比。")
        lines.append("")
    return lines


def _quantity_section(comparison: dict) -> list[str]:
    analysis = comparison.get("analysis") or {}
    quantity = analysis.get("quantity") or {}
    lines: list[str] = ["## 四、工程量差异", ""]
    lines.append(
        f"结算明细中数量变化 {quantity.get('qty_changed_count', 0)} 项"
        f"（增加 {quantity.get('qty_increase_count', 0)}、减少 {quantity.get('qty_decrease_count', 0)}），"
        f"新增 {quantity.get('added_count', 0)} 项、删除 {quantity.get('removed_count', 0)} 项；"
        f"单位变化 {quantity.get('unit_change_count', 0)} 项、规格变化 {quantity.get('spec_change_count', 0)} 项。"
    )
    lines.append("")
    lines.append(
        "注：单项差异按“名称+规格+单位”匹配，名称/规格改写可能使单项数量或金额差异被放大；"
        "判断工程量影响时以分部/类别净额为准，并人工核对名称相似/疑似改名条目。"
    )
    lines.append("")
    top_qty = quantity.get("top_qty_changes") or []
    if top_qty:
        rows = []
        for item in top_qty[:12]:
            old_qty = _fmt_amount(item.get("old_qty")) if item.get("old_qty") is not None else "—"
            new_qty = _fmt_amount(item.get("new_qty")) if item.get("new_qty") is not None else "—"
            rows.append([item.get("name"), item.get("unit"), old_qty, new_qty,
                         _fmt_amount(item.get("qty_delta")), _fmt_ratio(item.get("qty_ratio")),
                         _fmt_amount(item.get("amount_delta"))])
        lines.append("数量变化 TOP：")
        lines.append("")
        lines.append(_table(["名称", "单位", "原数量", "新数量", "数量差", "变化率", "金额差（元）"], rows,
                            [34, 8, 12, 12, 12, 10, 16]))
        lines.append("")

    added = quantity.get("top_added") or []
    removed = quantity.get("top_removed") or []
    if added or removed:
        rows = []
        for label, items, qty_key in (("新增", added, "new_qty"), ("删除", removed, "old_qty")):
            for item in items[:5]:
                rows.append([label, item.get("name"), item.get("unit"),
                             _fmt_amount(item.get(qty_key)) if item.get(qty_key) is not None else "—",
                             _fmt_amount(item.get("amount_delta"))])
        lines.append("新增 / 删除条目 TOP：")
        lines.append("")
        lines.append(_table(["类型", "名称", "单位", "数量", "金额差（元）"], rows, [8, 36, 8, 12, 16]))
        lines.append("")

    unit_changes = quantity.get("unit_changes") or []
    spec_changes = quantity.get("spec_changes") or []
    if unit_changes or spec_changes:
        rows = []
        for kind, items in (("单位变化", unit_changes), ("规格变化", spec_changes)):
            for item in items[:8]:
                rows.append([kind, item.get("name"), item.get("spec"), item.get("unit"),
                             _fmt_amount(item.get("old_qty")) if item.get("old_qty") is not None else "—",
                             _fmt_amount(item.get("new_qty")) if item.get("new_qty") is not None else "—"])
        lines.append("单位 / 规格变化（需人工确认换算与依据）：")
        lines.append("")
        lines.append(_table(["类型", "名称", "规格", "单位", "原数量", "新数量"], rows, [10, 30, 18, 8, 12, 12]))
        lines.append("")

    grounding = quantity.get("grounding") or {}
    old_grounding = grounding.get("old") or {}
    new_grounding = grounding.get("new") or {}
    if old_grounding or new_grounding:
        rows = []
        for name in sorted(set(old_grounding) | set(new_grounding)):
            old_value = old_grounding.get(name, 0.0)
            new_value = new_grounding.get(name, 0.0)
            rows.append([name, format_number(old_value, 2), format_number(new_value, 2), format_delta(new_value - old_value)])
        lines.append("竣工资料接地工程量（可与结算量勾稽）：")
        lines.append("")
        lines.append(_table(["材料", "原版", "新版", "差值"], rows, [24, 14, 14, 14]))
        lines.append("")
    return lines


def _settlement_section(comparison: dict) -> list[str]:
    lines: list[str] = []
    analysis = comparison.get("analysis") or {}
    analysis_totals = analysis.get("totals") or {}
    totals = analysis_totals.get("settlement") or {}
    material_totals = analysis_totals.get("material") or {}
    settlement = comparison["documents"]["settlement"]
    lines.append("## 五、审核结算差异")
    lines.append("")
    fee_rows = [
        ["设备检修费（表一口径）", _fmt_amount(totals.get("old_equipment")), _fmt_amount(totals.get("new_equipment")),
         _fmt_amount((totals.get("new_equipment") or 0) - (totals.get("old_equipment") or 0))
         if totals.get("old_equipment") is not None and totals.get("new_equipment") is not None else "—"],
        ["下浮后送审金额", "—", _fmt_amount(totals.get("new_discount_final")), "—"],
        ["配件购置费（含配件完整口径）", _fmt_amount(material_totals.get("old_parts")),
         _fmt_amount(material_totals.get("new_parts")), "—"],
        ["设备检修费+配件购置费", _fmt_amount(totals.get("old_complete")), _fmt_amount(totals.get("new_complete")),
         _fmt_amount(totals.get("complete_delta"))],
        ["可抵扣增值税", _fmt_amount(totals.get("old_vat")), _fmt_amount(totals.get("new_vat")), "—"],
        ["编制基准期价差", _fmt_amount(totals.get("old_price_diff")), _fmt_amount(totals.get("new_price_diff")), "—"],
    ]
    lines.append(_table(["费用项", "原版（元）", "新版（元）", "变化（元）"], fee_rows, [18, 18, 18, 18]))
    lines.append("")
    top = [diff for diff in settlement.get("diffs") or []
           if diff.get("status") != STATUS_SAME and diff.get("amount_delta") is not None]
    top.sort(key=lambda diff: abs(diff["amount_delta"]), reverse=True)
    if top:
        lines.append("金额影响最大的差异项：")
        lines.append("")
        lines.append(_table(DIFF_HEADERS, _diff_rows(top, limit=12), DIFF_LIMITS))
        lines.append("")
        lines.append(
            "注：单项差异按“名称+规格+单位”匹配汇总，名称或规格写法改写可能使单项差异被放大；"
            "判断金额影响时以分部/类别净额与汇总表为准。"
        )
        lines.append("")
    status_counts = settlement.get("summary", {}).get("status_counts") or {}
    lines.append(
        f"本期结算明细共 {settlement.get('summary', {}).get('count', 0)} 项，"
        f"其中新增 {status_counts.get(STATUS_NEW, 0)} 项、删除 {status_counts.get(STATUS_REMOVED, 0)} 项，"
        f"变化项 {settlement.get('summary', {}).get('changed_count', 0)} 项。"
    )
    lines.append("")
    return lines


def _material_section(comparison: dict) -> list[str]:
    lines: list[str] = []
    analysis = comparison.get("analysis") or {}
    totals = (analysis.get("totals") or {}).get("material") or {}
    material = comparison["documents"]["material"]
    lines.append("## 六、材料清册差异")
    lines.append("")
    rows = [
        ["乙供未计价材料", _fmt_amount(totals.get("old_yigong")), _fmt_amount(totals.get("new_yigong"))],
        ["甲供未计价材料", _fmt_amount(totals.get("old_ag")), _fmt_amount(totals.get("new_ag"))],
        ["配件购置费", _fmt_amount(totals.get("old_parts")), _fmt_amount(totals.get("new_parts"))],
    ]
    lines.append(_table(["材料口径", "原版（元）", "新版（元）"], rows, [20, 16, 16]))
    lines.append("")
    top = [diff for diff in material.get("diffs") or []
           if diff.get("status") != STATUS_SAME and diff.get("amount_delta") is not None]
    top.sort(key=lambda diff: abs(diff["amount_delta"]), reverse=True)
    if top:
        lines.append("乙供材料金额影响最大的差异项：")
        lines.append("")
        lines.append(_table(DIFF_HEADERS, _diff_rows(top, limit=12), DIFF_LIMITS))
        lines.append("")
        lines.append(
            "注：材料条目按“名称+规格+单位”匹配，名称/规格改写会造成“删除+新增”或单项差异放大；"
            "类别净额以材料汇总表合计与甲供/乙供汇总为准。"
        )
        lines.append("")
    if totals.get("old_ag") is None and totals.get("new_ag") is not None:
        lines.append(
            f"原版资料未提供甲供未计价材料清单，新版甲供材料合计 {_fmt_amount(totals.get('new_ag'))} 元，"
            "甲供材料无法做原/新逐项对比，需补充原版甲供清单。"
        )
        lines.append("")
    supplementary = material.get("supplementary") or {}
    parts_summary = (supplementary.get("配件口径") or {}).get("summary") or {}
    if parts_summary.get("amount_old") is not None and parts_summary.get("amount_new") is not None:
        delta = parts_summary["amount_new"] - parts_summary["amount_old"]
        lines.append(
            f"配件口径（表三甲配件行）：{_fmt_amount(parts_summary['amount_old'])} 元 → "
            f"{_fmt_amount(parts_summary['amount_new'])} 元（{format_delta(delta)} 元，"
            f"{_fmt_ratio(delta / parts_summary['amount_old'] if parts_summary['amount_old'] else None)}），"
            f"共 {parts_summary.get('count', 0)} 项、变化 {parts_summary.get('changed_count', 0)} 项。"
        )
        lines.append("")
    return lines


def _boq_section(comparison: dict) -> list[str]:
    lines: list[str] = []
    boq = comparison["documents"]["boq"]
    analysis = comparison.get("analysis") or {}
    lines.append("## 七、竣工资料工程量差异")
    lines.append("")
    changes = boq.get("text_changes") or []
    changed = [change for change in changes if change["status"] != STATUS_SAME]
    lines.append(
        f"竣工报告“主要工程量”文字描述共提取 {len(changes)} 段，其中新增 "
        f"{sum(1 for change in changes if change['status'] == STATUS_NEW)} 段、修改 "
        f"{sum(1 for change in changes if change['status'] == '修改')} 段、未变化 "
        f"{sum(1 for change in changes if change['status'] == STATUS_SAME)} 段。"
    )
    lines.append("")
    if changed:
        text_rows = []
        for change in changed[:10]:
            text_rows.append([
                change.get("status"),
                f"{change.get('similarity', 0):.0%}",
                change.get("old_label") or change.get("old_id"),
                change.get("new_label") or change.get("new_id"),
                change.get("summary"),
            ])
        lines.append(_table(["状态", "相似度", "原版位置", "新版位置", "摘要"], text_rows,
                           [8, 8, 30, 30, 40]))
        lines.append("")

    grounding = (boq.get("table_changes") or {}).get("接地工程") or {}
    old_grounding = grounding.get("old") or {}
    new_grounding = grounding.get("new") or {}
    if old_grounding or new_grounding:
        rows = []
        for name in sorted(set(old_grounding) | set(new_grounding)):
            old_value = old_grounding.get(name, 0.0)
            new_value = new_grounding.get(name, 0.0)
            if abs(new_value - old_value) < 1e-9:
                continue
            rows.append([name, format_number(old_value, 2), format_number(new_value, 2),
                         format_delta(new_value - old_value)])
        if rows:
            lines.append("接地工程量对比：")
            lines.append("")
            lines.append(_table(["材料", "原版数量", "新版数量", "差值"], rows, [24, 14, 14, 14]))
            lines.append("")

    findings = analysis.get("findings") or []
    boq_findings = [finding for finding in findings if "竣工" in finding or "资料" in finding or "空记录" in finding or "甲供" in finding]
    if boq_findings:
        lines.append("资料完整性发现：")
        lines.append("")
        for index, finding in enumerate(boq_findings, start=1):
            lines.append(f"{index}. {finding}")
        lines.append("")
    return lines


def _risk_section(comparison: dict) -> list[str]:
    lines: list[str] = []
    analysis = comparison.get("analysis") or {}
    lines.append("## 八、重点风险与建议")
    lines.append("")
    risks = analysis.get("risks") or []
    risk_rows = []
    for risk in risks[:20]:
        risk_rows.append([
            risk.get("level", ""),
            risk.get("category", ""),
            risk.get("title", ""),
            risk.get("detail", ""),
            _fmt_amount(risk.get("amount_delta")) if risk.get("amount_delta") is not None else "—",
        ])
    if risk_rows:
        lines.append(_table(["等级", "类别", "事项", "说明", "金额影响（元）"], risk_rows,
                           [6, 12, 30, 70, 16]))
        lines.append("")
    lines.append("处理建议：")
    lines.append("")
    for index, suggestion in enumerate(_suggestions(comparison), start=1):
        lines.append(f"{index}. {suggestion}")
    lines.append("")
    return lines


def _suggestions(comparison: dict) -> list[str]:
    from .export_excel import _suggestions as excel_suggestions

    return excel_suggestions(comparison)


def build_markdown(comparison: dict) -> str:
    """生成 Markdown 文字分析报告。"""
    project = comparison.get("project") or {}
    analysis = comparison.get("analysis") or {}
    documents = comparison["documents"]
    summary = comparison.get("summary") or {}

    lines: list[str] = []
    lines.append(f"# {project.get('name', '')} 原/新资料逐项对比分析报告")
    lines.append("")
    lines.append(f"- 生成时间：{project.get('generated_at', '')}")
    lines.append(f"- 原版资料：{project.get('old_dir', '')}")
    lines.append(f"- 新版资料：{project.get('new_dir', '')}")
    lines.append("- 对比范围：竣工资料工程量描述、材料清册、审核结算")
    lines.append("")
    lines.append("## 一、资料与口径")
    lines.append("")
    files_old = []
    files_new = []
    for category, label in CATEGORY_LABELS.items():
        for item in documents[category]["old"].get("files") or []:
            name = Path(str(item.get("file", ""))).name
            if name and name not in files_old:
                files_old.append(name)
        for item in documents[category]["new"].get("files") or []:
            name = Path(str(item.get("file", ""))).name
            if name and name not in files_new:
                files_new.append(name)
    lines.append(f"原版资料（2025 年）共 {len(files_old)} 个文件：")
    lines.append("")
    for name in files_old:
        lines.append(f"- {name}")
    lines.append("")
    lines.append(f"新版资料（2026 年）共 {len(files_new)} 个文件：")
    lines.append("")
    for name in files_new:
        lines.append(f"- {name}")
    lines.append("")
    lines.append("审核结算金额口径：原版采用表一“设备检修费/施工费”；新版优先采用“下浮后送审金额”，无则采用设备检修费。")
    lines.append("")
    fee_meta = (analysis.get("construction_fee") or {})
    if fee_meta.get("format") == "assembly_only":
        lines.append(
            "> **资料范围提示**：本项目结算资料仅有“组合件汇总表”（无表一/表二甲/表三甲/材料表），"
            "施工费按组合件合价合计口径比较，取费构成与甲供/乙供材料无法拆分，资料完整性不足，"
            "正式核减前需补齐完整结算书。"
        )
        lines.append("")

    lines.append("## 二、总体结论")
    lines.append("")
    for index, conclusion in enumerate(analysis.get("conclusions") or [], start=1):
        lines.append(f"{index}. {conclusion}")
    if not analysis.get("conclusions"):
        lines.append("未提取到可对比数据。")
    lines.append("")
    risk_counts = summary.get("risk_counts") or {}
    lines.append(
        f"差异条目合计 {summary.get('count', 0)} 项（变化 {summary.get('changed_count', 0)} 项），"
        f"风险等级：高 {risk_counts.get(RISK_HIGH, 0)} 项、中 {risk_counts.get(RISK_MID, 0)} 项、"
        f"低 {risk_counts.get('低', 0)} 项。"
    )
    lines.append("")

    lines.extend(_construction_fee_section(comparison))
    lines.extend(_quantity_section(comparison))
    lines.extend(_settlement_section(comparison))
    lines.extend(_material_section(comparison))
    lines.extend(_boq_section(comparison))
    lines.extend(_risk_section(comparison))

    lines.append("## 附：说明")
    lines.append("")
    lines.append("- 本报告由“竣工资料与结算对比工作流”按规则自动生成，数据来自上述工作簿的结构化提取。")
    lines.append("- 匹配规则：定额按编号+单位、材料按名称+规格+单位匹配；同名多行先合并；名称相似/疑似改名会标注，需人工确认。")
    lines.append("- 风险规则：高=金额差≥5000 元或（变化率≥10% 且金额差≥500 元）；中=金额差≥1000 元或（变化率≥3% 且金额差≥100 元）；新增/删除/规格/单位变化至少为中风险。")
    lines.append("- 本报告为内部复核底稿，正式结算与签认前须由业务人员复核确认。")
    lines.append("")
    return "\n".join(lines)


def build_docx(comparison: dict) -> bytes:
    """生成 Word 版文字分析报告。"""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Pt

    project = comparison.get("project") or {}
    analysis = comparison.get("analysis") or {}
    summary = comparison.get("summary") or {}
    documents = comparison["documents"]

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(f"{project.get('name', '')} 原/新资料逐项对比分析报告")
    run.bold = True
    run.font.size = Pt(16)
    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    meta.add_run(f"生成时间：{project.get('generated_at', '')}").font.size = Pt(9)
    doc.add_paragraph(
        f"原版资料目录：{project.get('old_dir', '')}\n新版资料目录：{project.get('new_dir', '')}"
    ).runs[0].font.size = Pt(9)

    doc.add_heading("一、总体结论", level=1)
    for index, conclusion in enumerate(analysis.get("conclusions") or [], start=1):
        doc.add_paragraph(f"{index}. {conclusion}")
    risk_counts = summary.get("risk_counts") or {}
    doc.add_paragraph(
        f"差异条目合计 {summary.get('count', 0)} 项（变化 {summary.get('changed_count', 0)} 项），"
        f"风险等级：高 {risk_counts.get(RISK_HIGH, 0)} 项、中 {risk_counts.get(RISK_MID, 0)} 项、"
        f"低 {risk_counts.get('低', 0)} 项。"
    )

    analysis_totals = analysis.get("totals") or {}
    settlement_totals = analysis_totals.get("settlement") or {}
    material_totals = analysis_totals.get("material") or {}

    fee = analysis.get("construction_fee") or {}
    doc.add_heading("二、施工费差异成因", level=1)
    if fee.get("old_fee") is not None or fee.get("new_fee") is not None:
        doc.add_paragraph(
            f"施工费（表一口径）由 {_fmt_amount(fee.get('old_fee'))} 元变为 "
            f"{_fmt_amount(fee.get('new_fee'))} 元"
            f"（{format_delta(fee.get('fee_delta'))} 元，{_fmt_ratio(fee.get('fee_ratio'))}）。"
        )
        direct = fee.get("direct") or {}
        yigong = fee.get("yigong_material") or {}
        doc.add_paragraph(
            f"拆分：定额直接费 {_fmt_amount(direct.get('net'))} 元、乙供材料 {_fmt_amount(yigong.get('net'))} 元、"
            f"取费与汇总口径残差 {_fmt_amount(fee.get('residual_vs_effects'))} 元。"
            f"定额直接费中：量差 {_fmt_amount(direct.get('qty_effect'))} 元、价差 {_fmt_amount(direct.get('price_effect'))} 元、"
            f"新增 {_fmt_amount(direct.get('added'))} 元、删除 "
            f"{_fmt_amount(-(direct.get('removed') or 0)) if direct.get('removed') else '—'} 元；"
            f"乙供材料中：量差 {_fmt_amount(yigong.get('qty_effect'))} 元、价差 {_fmt_amount(yigong.get('price_effect'))} 元。"
        )
        causes = fee.get("item_causes") or []
        if causes:
            doc.add_paragraph("施工费金额影响最大的项目（前 10 项）：")
            rows = [[c.get("name"), c.get("effect_kind"),
                     _fmt_amount(c.get("old_qty")) if c.get("old_qty") is not None else "—",
                     _fmt_amount(c.get("new_qty")) if c.get("new_qty") is not None else "—",
                     _fmt_ratio(c.get("qty_ratio")), _fmt_amount(c.get("amount_delta"))] for c in causes[:10]]
            _add_docx_table(doc, ["名称", "成因类型", "原数量", "新数量", "数量变化率", "金额差（元）"], rows)
        if fee.get("yigong_delta") is not None:
            doc.add_paragraph(
                f"供料口径：乙供未计价材料 {_fmt_amount(fee.get('old_yigong'))} 元 → "
                f"{_fmt_amount(fee.get('new_yigong'))} 元（{format_delta(fee.get('yigong_delta'))} 元）；"
                "乙供转甲供会直接减少施工费，需结合甲供清单与含配件口径判断可比性。"
            )
        if fee.get("format") == "assembly_only":
            doc.add_paragraph("注意：本版本结算书仅有组合件汇总表，无表一/表二甲/表三甲，取费构成无法拆分。")

    quantity = analysis.get("quantity") or {}
    doc.add_heading("三、工程量差异", level=1)
    doc.add_paragraph(
        f"数量变化 {quantity.get('qty_changed_count', 0)} 项（增加 {quantity.get('qty_increase_count', 0)}、"
        f"减少 {quantity.get('qty_decrease_count', 0)}），新增 {quantity.get('added_count', 0)} 项、"
        f"删除 {quantity.get('removed_count', 0)} 项；单位变化 {quantity.get('unit_change_count', 0)} 项、"
        f"规格变化 {quantity.get('spec_change_count', 0)} 项。"
    )
    top_qty = quantity.get("top_qty_changes") or []
    if top_qty:
        rows = [[item.get("name"), item.get("unit"),
                 _fmt_amount(item.get("old_qty")) if item.get("old_qty") is not None else "—",
                 _fmt_amount(item.get("new_qty")) if item.get("new_qty") is not None else "—",
                 _fmt_amount(item.get("qty_delta")), _fmt_ratio(item.get("qty_ratio")),
                 _fmt_amount(item.get("amount_delta"))] for item in top_qty[:10]]
        _add_docx_table(doc, ["名称", "单位", "原数量", "新数量", "数量差", "变化率", "金额差（元）"], rows)

    doc.add_heading("四、审核结算差异", level=1)
    doc.add_paragraph(
        f"原版 {_fmt_amount(settlement_totals.get('old_final'))} 元 → "
        f"新版 {_fmt_amount(settlement_totals.get('new_final'))} 元，"
        f"变化 {_fmt_amount(settlement_totals.get('delta'))} 元"
        f"（{_fmt_ratio(settlement_totals.get('ratio'))}）。"
    )
    settlement = documents["settlement"]
    top = [diff for diff in settlement.get("diffs") or []
           if diff.get("status") != STATUS_SAME and diff.get("amount_delta") is not None]
    top.sort(key=lambda diff: abs(diff["amount_delta"]), reverse=True)
    if top:
        doc.add_paragraph("金额影响最大的差异项（前 10 项）：")
        _add_docx_table(doc, DIFF_HEADERS, _diff_rows(top, limit=10))

    doc.add_heading("五、材料清册差异", level=1)
    doc.add_paragraph(
        f"乙供未计价材料：{_fmt_amount(material_totals.get('old_yigong'))} 元 → "
        f"{_fmt_amount(material_totals.get('new_yigong'))} 元；"
        f"甲供未计价材料：{_fmt_amount(material_totals.get('old_ag'))} 元 → "
        f"{_fmt_amount(material_totals.get('new_ag'))} 元。"
    )
    material = documents["material"]
    top_material = [diff for diff in material.get("diffs") or []
                    if diff.get("status") != STATUS_SAME and diff.get("amount_delta") is not None]
    top_material.sort(key=lambda diff: abs(diff["amount_delta"]), reverse=True)
    if top_material:
        doc.add_paragraph("乙供材料金额影响最大的差异项（前 10 项）：")
        _add_docx_table(doc, DIFF_HEADERS, _diff_rows(top_material, limit=10))

    doc.add_heading("六、竣工资料工程量差异", level=1)
    boq = documents["boq"]
    changes = boq.get("text_changes") or []
    doc.add_paragraph(
        f"竣工报告主要工程量文字描述共提取 {len(changes)} 段，其中新增 "
        f"{sum(1 for change in changes if change['status'] == STATUS_NEW)} 段、修改 "
        f"{sum(1 for change in changes if change['status'] == '修改')} 段、未变化 "
        f"{sum(1 for change in changes if change['status'] == STATUS_SAME)} 段。"
    )
    changed = [change for change in changes if change["status"] != STATUS_SAME]
    if changed:
        rows = [[change.get("status"), f"{change.get('similarity', 0):.0%}",
                 change.get("old_label") or change.get("old_id"),
                 change.get("new_label") or change.get("new_id"), change.get("summary")]
                for change in changed[:10]]
        _add_docx_table(doc, ["状态", "相似度", "原版位置", "新版位置", "摘要"], rows)
    findings = analysis.get("findings") or []
    if findings:
        doc.add_paragraph("资料完整性发现：")
        for index, finding in enumerate(findings, start=1):
            doc.add_paragraph(f"{index}. {finding}")

    doc.add_heading("七、重点风险与建议", level=1)
    risks = analysis.get("risks") or []
    if risks:
        rows = [[risk.get("level", ""), risk.get("category", ""), risk.get("title", ""),
                 risk.get("detail", ""),
                 _fmt_amount(risk.get("amount_delta")) if risk.get("amount_delta") is not None else "—"]
                for risk in risks[:15]]
        _add_docx_table(doc, ["等级", "类别", "事项", "说明", "金额影响（元）"], rows)
    doc.add_paragraph("处理建议：")
    for index, suggestion in enumerate(_suggestions(comparison), start=1):
        doc.add_paragraph(f"{index}. {suggestion}")

    doc.add_heading("附：说明", level=1)
    doc.add_paragraph("本报告由“竣工资料与结算对比工作流”按规则自动生成，数据来自原/新资料工作簿的结构化提取。")
    doc.add_paragraph("匹配规则：定额按编号+单位、材料按名称+规格+单位匹配；名称相似/疑似改名会标注，需人工确认。")
    doc.add_paragraph("风险规则：高=金额差≥5000 元或（变化率≥10% 且金额差≥500 元）；中=金额差≥1000 元或（变化率≥3% 且金额差≥100 元）。")
    doc.add_paragraph("本报告为内部复核底稿，正式结算与签认前须由业务人员复核确认。")

    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _add_docx_table(doc, headers: list[str], rows: list[list[Any]]):
    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    header_cells = table.rows[0].cells
    for index, header in enumerate(headers):
        header_cells[index].text = str(header)
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = _escape_cell(value, limit=80)


def build_console_summary(comparison: dict) -> str:
    """控制台摘要（给 CLI / agent 用）。"""
    project = comparison.get("project") or {}
    analysis = comparison.get("analysis") or {}
    summary = comparison.get("summary") or {}
    totals = analysis.get("totals") or {}
    settlement_totals = totals.get("settlement") or {}
    material_totals = totals.get("material") or {}
    lines = [f"项目：{project.get('name', '')}",
             f"原版：{project.get('old_dir', '')}",
             f"新版：{project.get('new_dir', '')}"]
    if settlement_totals.get("old_equipment") is not None and settlement_totals.get("new_equipment") is not None:
        lines.append(
            "审核结算（表一口径·设备检修费）："
            f"{_fmt_amount(settlement_totals.get('old_equipment'))} → "
            f"{_fmt_amount(settlement_totals.get('new_equipment'))} 元"
            f"（{format_delta(settlement_totals.get('equipment_delta'))}，"
            f"{_fmt_ratio(settlement_totals.get('equipment_ratio'))}）"
        )
        if settlement_totals.get("new_discount_final") is not None:
            lines.append(
                "按下浮后送审口径："
                f"{_fmt_amount(settlement_totals.get('new_discount_final'))} 元"
            )
        if settlement_totals.get("old_complete") is not None and settlement_totals.get("new_complete") is not None:
            lines.append(
                "含配件完整口径："
                f"{_fmt_amount(settlement_totals.get('old_complete'))} → "
                f"{_fmt_amount(settlement_totals.get('new_complete'))} 元"
                f"（{format_delta(settlement_totals.get('complete_delta'))}，"
                f"{_fmt_ratio(settlement_totals.get('complete_ratio'))}）"
            )
    fee = analysis.get("construction_fee") or {}
    if fee.get("fee_delta") is not None:
        direct = fee.get("direct") or {}
        yigong = fee.get("yigong_material") or {}
        lines.append(
            "施工费差异成因："
            f"定额直接费 {format_delta(direct.get('net'))}（量差 {format_delta(direct.get('qty_effect'))}、"
            f"价差 {format_delta(direct.get('price_effect'))}）、"
            f"乙供材料 {format_delta(yigong.get('net'))}、"
            f"取费与汇总口径残差 {format_delta(fee.get('residual_vs_effects'))} 元"
        )
    quantity = analysis.get("quantity") or {}
    if quantity.get("qty_changed_count") or quantity.get("added_count") or quantity.get("removed_count"):
        lines.append(
            f"工程量：数量变化 {quantity.get('qty_changed_count', 0)} 项"
            f"（增 {quantity.get('qty_increase_count', 0)}/减 {quantity.get('qty_decrease_count', 0)}），"
            f"新增 {quantity.get('added_count', 0)} 项、删除 {quantity.get('removed_count', 0)} 项"
        )
    if material_totals.get("old_yigong") is not None or material_totals.get("new_yigong") is not None:
        lines.append(
            "材料清册（乙供）："
            f"{_fmt_amount(material_totals.get('old_yigong'))} → {_fmt_amount(material_totals.get('new_yigong'))} 元"
        )
    if material_totals.get("new_ag") is not None:
        lines.append(f"材料清册（甲供）：— → {_fmt_amount(material_totals.get('new_ag'))} 元（原版无清单）")
    risk_counts = summary.get("risk_counts") or {}
    lines.append(
        f"差异 {summary.get('count', 0)} 项（变化 {summary.get('changed_count', 0)} 项）；"
        f"高风险 {risk_counts.get(RISK_HIGH, 0)} 项、中风险 {risk_counts.get(RISK_MID, 0)} 项"
    )
    return "\n".join(lines)


def write_markdown(comparison: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_markdown(comparison), encoding="utf-8")
    return path


def write_docx(comparison: dict, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_docx(comparison))
    return path
