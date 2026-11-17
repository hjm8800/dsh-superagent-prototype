# -*- coding: utf-8 -*-
"""批量对比与分组测试（使用匿名示例文件名）。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diffworkflow import batch, demo_data  # noqa: E402
from diffworkflow.build import _clean_project_token, build_comparison, list_workbook_files  # noqa: E402


class TestProjectNaming:
    def test_clean_tokens(self):
        assert _clean_project_token("110kV某变电站10kV某某线大修（供电能力提升）-旧结算") == \
            "110kV某变电站10kV某某线大修（供电能力提升）"
        assert _clean_project_token("110kV某变电站10kV某某线大修（供电能力提升）-2026年竣工报告") == \
            "110kV某变电站10kV某某线大修（供电能力提升）"
        assert _clean_project_token("110kV某变电站10kV某某线大修（供电能力提升）-新送审") == \
            "110kV某变电站10kV某某线大修（供电能力提升）"

    def test_short_name(self):
        assert batch.short_name("110kV某变电站10kV某某线大修（供电能力提升）") == "某某线大修"
        assert batch.short_name("110kV某变电站10kV某甲线改造（供电能力提升）") == "某甲线改造"


class TestBatch:
    def test_group_and_run(self, tmp_path):
        demo_root = tmp_path / "demo"
        demo_data.generate(demo_root)
        groups = batch.group_projects(demo_root / "原版", demo_root / "新版")
        assert len(groups) == 1
        assert groups[0]["old"] and groups[0]["new"]

        out_dir = tmp_path / "out"
        results = batch.run_batch(demo_root / "原版", demo_root / "新版", out_dir)
        assert len(results) == 1
        folder = results[0]["folder"]
        assert folder.exists()
        names = {path.suffix.lower() for path in folder.iterdir()}
        assert ".xlsx" in names and ".md" in names and ".docx" in names and ".json" in names
        summary = out_dir / "施工费与工程量差异成因-汇总.md"
        assert summary.exists()
        text = summary.read_text(encoding="utf-8")
        assert "施工费差异总览" in text and "项目A" in text
        assert (out_dir / "batch-summary.json").exists()

    def test_dedupe_copy(self, tmp_path):
        demo_root = tmp_path / "demo"
        demo_data.generate(demo_root)
        old_dir = demo_root / "原版"
        files = list_workbook_files(old_dir)
        assert files
        # 复制一个同名内容不同文件名的副本，应只保留一份
        source = files[0]
        (old_dir / f"副本-{source.name}").write_bytes(source.read_bytes())
        assert len(list_workbook_files(old_dir)) == len(files)

    def test_batch_comparison_has_fee_and_quantity(self, tmp_path):
        demo_root = tmp_path / "demo"
        demo_data.generate(demo_root)
        comparison = build_comparison(demo_root / "原版", demo_root / "新版")
        analysis = comparison["analysis"]
        fee = analysis["construction_fee"]
        assert fee["old_fee"] is not None
        assert "direct" in fee and "yigong_material" in fee
        assert "top_qty_changes" in analysis["quantity"]
        assert any("施工费差异成因" in conclusion for conclusion in analysis["conclusions"])
        assert any("工程量差异" in conclusion for conclusion in analysis["conclusions"])
