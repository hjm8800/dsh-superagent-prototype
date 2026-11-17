# -*- coding: utf-8 -*-
"""export_excel / export_report 单元测试（使用合成的 Comparison，不依赖 readers）。"""
from __future__ import annotations

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpyxl import load_workbook  # noqa: E402

from diffworkflow import export_excel, export_report  # noqa: E402
from diffworkflow.diff import compare_items, compare_texts, summarize  # noqa: E402


def _item(name, spec="", unit="", qty=None, price=None, amount=None, code="", item_type="", supply=""):
    return {"name": name, "spec": spec, "unit": unit, "qty": qty, "unit_price": price,
            "amount": amount, "code": code, "item_type": item_type, "supply": supply,
            "section": "", "source": ""}


def _docset(category, side, items, totals=None, texts=None, tables=None, files=None):
    return {
        "category": category, "side": side, "files": files or [],
        "items": items, "totals": totals or {}, "categories": [],
        "texts": texts or [], "tables": tables or [], "warnings": [],
        "meta": {"files": [item.get("file") for item in (files or [])]},
    }


def make_comparison():
    old_settlement_items = [
        _item("电杆组立", "", "根", qty=12, price=350, amount=4200, item_type="定额"),
        _item("导线架设", "", "km", qty=1.2, price=9500, amount=11400, item_type="定额"),
        _item("混凝土杆 Φ190*15", "", "根", qty=8, price=2110.62, amount=16884.96,
              item_type="主材", supply="甲供"),
        _item("拆除临时围栏", "", "m", qty=40, price=25, amount=1000, item_type="定额"),
    ]
    new_settlement_items = [
        _item("电杆组立", "", "根", qty=11, price=350, amount=3850, item_type="定额"),
        _item("导线架设", "", "km", qty=1.06, price=9500, amount=10070, item_type="定额"),
        _item("混凝土杆 Φ190*15", "", "根", qty=7, price=2110.62, amount=14774.34,
              item_type="主材", supply="甲供"),
        _item("拉线安装", "", "条", qty=8, price=85, amount=680, item_type="定额"),
    ]
    settlement_diffs = compare_items(old_settlement_items, new_settlement_items, "settlement")

    old_materials = [
        _item("混凝土 C25", "C25", "m³", qty=3.2, price=384, amount=1228.8, item_type="材料", supply="乙供"),
        _item("镀锌圆钢", "Φ16", "m", qty=120, price=9.5, amount=1140, item_type="材料", supply="乙供"),
    ]
    new_materials = [
        _item("混凝土 C25", "C25", "m³", qty=2.8, price=384, amount=1075.2, item_type="材料", supply="乙供"),
        _item("镀锌圆钢", "Φ16", "m", qty=140, price=9.5, amount=1330, item_type="材料", supply="乙供"),
    ]
    material_diffs = compare_items(old_materials, new_materials, "material")

    old_boq_items = []
    new_boq_items = [
        _item("圆钢水平地极", "Φ16", "m", qty=140, item_type="接地"),
        _item("镀锌角钢", "∠50*5*2500", "条", qty=24, item_type="接地"),
    ]
    boq_diffs = compare_items(old_boq_items, new_boq_items, "boq")

    old_texts = [{"id": "old!R2C1", "label": "竣工报告·主要工程量", "text": "新建线路 1.20km，电杆 12 基，导线 0.80km。" * 3}]
    new_texts = [
        {"id": "new!R2C1", "label": "竣工报告·主要工程量", "text": "新建线路 1.20km，电杆 12 基，导线 0.80km。" * 3},
        {"id": "new!R2C13", "label": "竣工报告·附页", "text": "新增拉线 8 组，更换导线 1.06km，更换电杆 4 根。" * 2},
    ]
    text_changes = compare_texts(old_texts, new_texts)

    comparison = {
        "project": {
            "name": "项目A·某10kV线路大修（供电能力提升）",
            "old_dir": r"D:\demo\原版",
            "new_dir": r"D:\demo\新版",
            "generated_at": "2026-11-17 17:00:00",
        },
        "documents": {
            "settlement": {
                "old": _docset("settlement", "old", old_settlement_items,
                               totals={"设备检修费": 486200.0, "施工费": 486200.0, "可抵扣增值税": 40120.0,
                                       "编制基准期价差": 16800.0, "final_amount": 486200.0},
                               files=[{"file": "项目A-2025年结算书.xlsx", "kind": "settlement_workbook", "meta": {}}]),
                "new": _docset("settlement", "new", new_settlement_items,
                               totals={"设备检修费": 438500.0, "施工费": 438500.0, "可抵扣增值税": 36150.0,
                                       "编制基准期价差": 20400.0, "下浮后送审金额": 429730.0,
                                       "final_amount": 429730.0},
                               files=[{"file": "项目A-2026年送审.xlsx", "kind": "settlement_workbook", "meta": {}}]),
                "diffs": settlement_diffs,
                "summary": summarize(settlement_diffs),
                "supplementary": {},
            },
            "material": {
                "old": _docset("material", "old", old_materials,
                               totals={"乙供合计": 2368.8}, files=[{"file": "项目A-2025年结算书.xlsx", "meta": {}}]),
                "new": _docset("material", "new", new_materials,
                               totals={"乙供合计": 2405.2, "甲供合计": 95000.0},
                               files=[{"file": "项目A-2026年送审.xlsx", "meta": {}}]),
                "diffs": material_diffs,
                "summary": summarize(material_diffs),
                "supplementary": {"主材口径": {"diffs": [], "summary": summarize([]), "old_count": 0, "new_count": 0},
                                  "甲供清单": {"diffs": [], "summary": summarize([]), "old_count": 0, "new_count": 0}},
            },
            "boq": {
                "old": _docset("boq", "old", old_boq_items, texts=old_texts,
                               files=[{"file": "项目A-2025年竣工报告.xlsx", "meta": {}}]),
                "new": _docset("boq", "new", new_boq_items, texts=new_texts,
                               files=[{"file": "项目A-2026年竣工报告.xlsx", "meta": {}}]),
                "diffs": boq_diffs,
                "summary": summarize(boq_diffs),
                "text_changes": text_changes,
                "table_changes": {"接地工程": {"old": {}, "new": {"圆钢水平地极": 140.0}}},
            },
        },
        "summary": {},
        "analysis": {},
    }

    from diffworkflow.build import analyze

    comparison["analysis"] = analyze(comparison)
    from diffworkflow.diff import merge_summaries

    comparison["summary"] = merge_summaries({
        "settlement": comparison["documents"]["settlement"]["summary"],
        "material": comparison["documents"]["material"]["summary"],
        "boq": comparison["documents"]["boq"]["summary"],
    })
    return comparison


class TestExcelExport:
    def test_sheets_and_content(self):
        comparison = make_comparison()
        data = export_excel.build(comparison)
        assert isinstance(data, bytes) and len(data) > 5000
        workbook = load_workbook(io.BytesIO(data))
        names = workbook.sheetnames
        for expected in ["总览", "审核结算-汇总", "审核结算-逐项差异", "材料清册-乙供差异",
                         "竣工工程量-文字描述", "风险与建议", "说明"]:
            assert expected in names, names
        overview = workbook["总览"]
        assert "项目A" in str(overview["A1"].value)
        diff_sheet = workbook["审核结算-逐项差异"]
        # 至少包含表头 + 变化行
        assert diff_sheet.max_row >= 2
        risk_sheet = workbook["风险与建议"]
        assert risk_sheet.max_row >= 2


class TestReportExport:
    def test_markdown(self):
        comparison = make_comparison()
        markdown = export_report.build_markdown(comparison)
        assert "原/新资料逐项对比分析报告" in markdown
        assert "## 二、总体结论" in markdown
        assert "审核结算" in markdown
        assert "| 状态 |" in markdown or "状态" in markdown
        assert "项目A" in markdown

    def test_docx(self):
        comparison = make_comparison()
        data = export_report.build_docx(comparison)
        assert data[:2] == b"PK"
        from docx import Document

        document = Document(io.BytesIO(data))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        assert "原/新资料逐项对比分析报告" in text
        assert "总体结论" in text

    def test_console_summary(self):
        comparison = make_comparison()
        summary = export_report.build_console_summary(comparison)
        assert "项目A" in summary
        assert "审核结算" in summary
        assert "高风险" in summary
