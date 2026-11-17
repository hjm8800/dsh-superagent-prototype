"""批量对比：按项目文件名分组，逐项目出对比结果并生成施工费/工程量差异汇总。

适用目录结构：`原版目录` 与 `新版目录` 下混放多个项目的资料文件，文件名含项目名。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import export_excel, export_report
from .build import (
    _clean_project_token,
    build_comparison,
    comparison_to_json,
    list_workbook_files,
)
from .normalize import format_delta, format_money, format_ratio


def _safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name or "").strip(" _.")
    return name or "项目"


def short_name(project_key: str) -> str:
    """从项目全名提取短名：'110kV某变电站10kV仙湖II线大修（供电能力提升）' → '仙湖II线大修'。"""
    key = project_key or ""
    match = re.search(r"(?<!\d)10kV\s*([^（(]+?)(大修|改造|工程)", key)
    if match:
        return (match.group(1) + match.group(2)).strip(" -_")
    match = re.search(r"(?<!\d)10kV\s*([^（(]+)", key)
    if match:
        return match.group(1).strip(" -_")
    return key[:30]


def group_projects(old_dir: str | Path, new_dir: str | Path) -> list[dict]:
    """按文件名（去掉年份/旧新/资料类别）把老、新文件归属到同一项目。"""
    old_dir, new_dir = Path(old_dir), Path(new_dir)
    groups: dict[str, dict[str, Any]] = {}

    def register(side: str, path: Path) -> None:
        key = _clean_project_token(path.stem)
        if not key:
            return
        group = groups.setdefault(key, {"key": key, "old": [], "new": []})
        group[side].append(path)

    for path in list_workbook_files(old_dir):
        register("old", path)
    for path in list_workbook_files(new_dir):
        register("new", path)
    # 两侧项目名略有差异时（例如一侧缺括号内容），按“互为前缀”合并
    keys = sorted(groups, key=len)
    merged: dict[str, dict[str, Any]] = {}
    for key in keys:
        group = groups[key]
        if not group["old"] or not group["new"]:
            continue
        target = None
        for existing_key, existing in merged.items():
            if key.startswith(existing_key) or existing_key.startswith(key):
                target = existing
                break
        if target is None:
            merged[key] = group
        else:
            target["old"].extend(group["old"])
            target["new"].extend(group["new"])
    for key, group in groups.items():
        if group["old"] and group["new"]:
            continue
        if any(key.startswith(existing) or existing.startswith(key) for existing in merged):
            continue
        merged[key] = group
    return [merged[key] for key in sorted(merged)]


def _write_outputs(comparison: dict, folder: Path, write_docx: bool = True) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    name = _safe_filename(comparison["project"]["name"])
    paths = {
        "excel": folder / f"{name}-数据对比表.xlsx",
        "markdown": folder / f"{name}-对比分析报告.md",
        "json": folder / f"{name}-summary.json",
    }
    export_excel.write(comparison, paths["excel"])
    export_report.write_markdown(comparison, paths["markdown"])
    if write_docx:
        paths["docx"] = folder / f"{name}-对比分析报告.docx"
        export_report.write_docx(comparison, paths["docx"])
    paths["json"].write_text(comparison_to_json(comparison), encoding="utf-8")
    return paths


def _summary_markdown(results: list[dict]) -> str:
    lines = ["# 施工费与工程量差异成因 · 多项目汇总", ""]
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 项目数量：{len(results)}")
    lines.append("")
    lines.append("## 一、施工费差异总览（表一口径）")
    lines.append("")
    rows = [
        "| 项目 | 施工费原版（元） | 施工费新版（元） | 变化（元） | 变化率 | 定额直接费 | 其中量差 | 其中价差 | 乙供材料 | 取费与残差 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        fee = (result["comparison"].get("analysis") or {}).get("construction_fee") or {}
        direct = fee.get("direct") or {}
        yigong = fee.get("yigong_material") or {}
        project = result["comparison"]["project"]["name"]
        rows.append(
            f"| {project} | {format_money(fee.get('old_fee'))} | {format_money(fee.get('new_fee'))} | "
            f"{format_delta(fee.get('fee_delta'))} | {format_ratio(fee.get('fee_ratio'))} | "
            f"{format_delta(direct.get('net'))} | {format_delta(direct.get('qty_effect'))} | "
            f"{format_delta(direct.get('price_effect'))} | {format_delta(yigong.get('net'))} | "
            f"{format_delta(fee.get('residual_vs_effects'))} |"
        )
    lines.extend(rows)
    lines.append("")

    lines.append("## 二、各项目施工费主要成因")
    lines.append("")
    for result in results:
        comparison = result["comparison"]
        project = comparison["project"]["name"]
        fee = (comparison.get("analysis") or {}).get("construction_fee") or {}
        lines.append(f"### {project}")
        lines.append("")
        if fee.get("format") == "assembly_only":
            lines.append("- 资料限制：仅有组合件汇总表，施工费按组合件合价合计比较，取费构成无法拆分。")
        if fee.get("fee_delta") is not None:
            direct = fee.get("direct") or {}
            yigong = fee.get("yigong_material") or {}
            lines.append(
                f"- 施工费（表一口径）{format_money(fee.get('old_fee'))} → {format_money(fee.get('new_fee'))} 元"
                f"（{format_delta(fee.get('fee_delta'))} 元，{format_ratio(fee.get('fee_ratio'))}）；"
                f"定额直接费 {format_delta(direct.get('net'))}（量差 {format_delta(direct.get('qty_effect'))}、"
                f"价差 {format_delta(direct.get('price_effect'))}）、乙供材料 {format_delta(yigong.get('net'))}、"
                f"取费与口径残差 {format_delta(fee.get('residual_vs_effects'))}。"
            )
        causes = fee.get("item_causes") or []
        for index, cause in enumerate(causes[:4], start=1):
            lines.append(
                f"- 成因 {index}：{cause.get('name', '')}，{cause.get('effect_kind', '')}，"
                f"金额差 {format_delta(cause.get('amount_delta'))} 元。"
            )
        sections = fee.get("sections") or []
        if sections:
            lines.append(
                "- 分部合计变化（含配件口径、含父子层级，不可直接相加）："
                + "、".join(f"{section.get('name')} {format_delta(section.get('delta'))} 元" for section in sections[:3])
                + "。"
            )
        quantity = (comparison.get("analysis") or {}).get("quantity") or {}
        if quantity.get("qty_changed_count"):
            lines.append(
                f"- 工程量：数量变化 {quantity.get('qty_changed_count')} 项、新增 {quantity.get('added_count')} 项、"
                f"删除 {quantity.get('removed_count')} 项。"
            )
        lines.append("")

    lines.append("## 三、资料与口径提示")
    lines.append("")
    for result in results:
        comparison = result["comparison"]
        findings = (comparison.get("analysis") or {}).get("findings") or []
        if not findings:
            continue
        lines.append(f"- **{comparison['project']['name']}**：" + "；".join(findings[:4]))
    lines.append("")
    lines.append("说明：本汇总由规则比对生成，量差/价差按匹配条目逐项拆分，正式签认前需业务复核。")
    lines.append("")
    return "\n".join(lines)


def run_batch(old_dir: str | Path, new_dir: str | Path, out_dir: str | Path,
              project_filter: str | None = None, write_docx: bool = True) -> list[dict]:
    """批量运行；返回每项目 {comparison, paths, folder}。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    groups = group_projects(old_dir, new_dir)
    if project_filter:
        groups = [group for group in groups if project_filter in group["key"]]
    if not groups:
        raise RuntimeError("未在目录中找到可配对的项目（请检查文件名是否包含项目名）")
    results: list[dict] = []
    for index, group in enumerate(groups, start=1):
        project_name = group["key"]
        folder = out_dir / f"{index:02d}-{_safe_filename(short_name(project_name))}"
        comparison = build_comparison(
            old_dir, new_dir, project_name,
            old_files=group["old"], new_files=group["new"],
        )
        paths = _write_outputs(comparison, folder, write_docx=write_docx)
        results.append({"project": project_name, "folder": folder, "comparison": comparison, "paths": paths})
        print(f"[{index}/{len(groups)}] {project_name} → {folder}")
        print(export_report.build_console_summary(comparison))
        print("-" * 72)

    summary_path = out_dir / "施工费与工程量差异成因-汇总.md"
    summary_path.write_text(_summary_markdown(results), encoding="utf-8")
    summary_json = out_dir / "batch-summary.json"
    summary_json.write_text(json.dumps([
        {
            "project": result["project"],
            "folder": str(result["folder"]),
            "fee": (result["comparison"].get("analysis") or {}).get("construction_fee") or {},
            "quantity": (result["comparison"].get("analysis") or {}).get("quantity") or {},
        }
        for result in results
    ], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"汇总：{summary_path}")
    return results
