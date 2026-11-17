# -*- coding: utf-8 -*-
"""build 集成测试：用匿名演示资料跑完整对比流程。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diffworkflow import demo_data  # noqa: E402
from diffworkflow.build import build_comparison  # noqa: E402


@pytest.fixture(scope="module")
def comparison(tmp_path_factory):
    root = tmp_path_factory.mktemp("demo")
    demo_data.generate(root)
    return build_comparison(root / "原版", root / "新版")


class TestDemoComparison:
    def test_project_and_documents(self, comparison):
        assert comparison["project"]["name"] == "项目A"
        documents = comparison["documents"]
        for category in ("settlement", "material", "boq"):
            assert documents[category]["old"]["items"] is not None
            assert documents[category]["new"]["items"] is not None
            assert isinstance(documents[category]["diffs"], list)

    def test_settlement_totals(self, comparison):
        totals = (comparison["analysis"]["totals"] or {}).get("settlement") or {}
        assert totals["old_equipment"] == pytest.approx(486200.0)
        assert totals["new_equipment"] == pytest.approx(438500.0)
        assert totals["new_discount_final"] == pytest.approx(429730.0)
        assert totals["old_complete"] == pytest.approx(486200.0 + 86767.0, rel=0.2)
        assert totals["equipment_delta"] == pytest.approx(-47700.0)

    def test_settlement_diffs_have_status_and_risk(self, comparison):
        diffs = comparison["documents"]["settlement"]["diffs"]
        assert diffs
        statuses = {diff["status"] for diff in diffs}
        assert "无变化" in statuses or "数量增加" in statuses or "数量减少" in statuses
        assert any(diff["risk"] in {"高", "中", "低"} for diff in diffs)
        # 取费构成行不应进入逐项差异
        assert all(diff.get("item_type") != "费用" for diff in diffs)

    def test_material_primary_is_yigong(self, comparison):
        material = comparison["documents"]["material"]
        assert material["old"]["items"]
        assert all((item.get("supply") or "") != "甲供" for item in material["old"]["items"])
        supplementary = material["supplementary"]
        assert supplementary["甲供清单"]["old_count"] == 0
        assert supplementary["甲供清单"]["new_count"] > 0
        assert supplementary["配件口径"]["old_count"] > 0

    def test_boq_texts_and_grounding(self, comparison):
        boq = comparison["documents"]["boq"]
        assert len(boq["old"]["texts"]) == 2
        assert len(boq["new"]["texts"]) == 4
        statuses = {change["status"] for change in boq["text_changes"]}
        assert "无变化" in statuses
        assert "修改" in statuses
        assert "新增" in statuses
        grounding = boq["table_changes"]["接地工程"]
        assert grounding["old"] == {}
        assert grounding["new"].get("圆钢水平地极", 0) > 0

    def test_analysis_outputs(self, comparison):
        analysis = comparison["analysis"]
        assert len(analysis["conclusions"]) >= 5
        assert analysis["findings"]
        assert any("甲供" in finding for finding in analysis["findings"])
        assert any("竣工" in finding for finding in analysis["findings"])
        assert analysis["risks"]
        assert comparison["summary"]["count"] > 0
