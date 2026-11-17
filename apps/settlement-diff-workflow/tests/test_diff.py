# -*- coding: utf-8 -*-
"""diff 模块单元测试。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diffworkflow.diff import (  # noqa: E402
    RISK_HIGH,
    RISK_LOW,
    RISK_MID,
    STATUS_AMOUNT,
    STATUS_NEW,
    STATUS_PRICE,
    STATUS_QTY_DOWN,
    STATUS_QTY_UP,
    STATUS_REMOVED,
    STATUS_SAME,
    STATUS_SPEC,
    STATUS_UNIT,
    compare_items,
    compare_texts,
    merge_summaries,
    summarize,
    top_diffs,
)


def item(name, spec="", unit="", qty=None, price=None, amount=None, code="", item_type="", supply=""):
    return {
        "name": name,
        "spec": spec,
        "unit": unit,
        "qty": qty,
        "unit_price": price,
        "amount": amount,
        "code": code,
        "item_type": item_type,
        "supply": supply,
        "section": "",
        "source": "",
    }


def diff_of(old_items, new_items, category="settlement"):
    diffs = compare_items(old_items, new_items, category)
    assert len(diffs) == 1, diffs
    return diffs[0]


class TestStatus:
    def test_same(self):
        old = [item("电杆组立", "Φ190×12", "根", qty=10, price=350)]
        new = [item("电杆组立", "Φ190×12", "根", qty=10, price=350)]
        diff = diff_of(old, new)
        assert diff["status"] == STATUS_SAME
        assert diff["risk"] == ""

    def test_qty_increase_high(self):
        old = [item("导线架设", "", "km", qty=1.0, price=10000)]
        new = [item("导线架设", "", "km", qty=1.3, price=10000)]
        diff = diff_of(old, new)
        assert diff["status"] == STATUS_QTY_UP
        assert diff["risk"] == RISK_HIGH
        assert abs(diff["qty_delta"] - 0.3) < 1e-9

    def test_qty_decrease_small(self):
        old = [item("电杆组立", "", "根", qty=100, price=100)]
        new = [item("电杆组立", "", "根", qty=99, price=100)]
        diff = diff_of(old, new)
        assert diff["status"] == STATUS_QTY_DOWN
        assert diff["risk"] in {RISK_LOW, RISK_MID}

    def test_price_change(self):
        old = [item("避雷器", "YH5WS-17/50", "组", qty=10, price=1000)]
        new = [item("避雷器", "YH5WS-17/50", "组", qty=10, price=1300)]
        diff = diff_of(old, new)
        assert diff["status"] == STATUS_PRICE
        assert diff["risk"] == RISK_HIGH  # 金额差 3000? -> mid; 30% & >=500 -> high

    def test_spec_change(self):
        old = [item("电缆", "ZA-YJV-1×240", "m", qty=90, price=300)]
        new = [item("电缆", "ZA-YJV-1×185", "m", qty=90, price=300)]
        diff = diff_of(old, new)
        assert diff["status"] == STATUS_SPEC
        assert diff["risk"] in {RISK_MID, RISK_HIGH}

    def test_unit_change_same_quantity(self):
        old = [item("导线", "", "km", qty=1.2, price=100)]
        new = [item("导线", "", "m", qty=1200, price=0.1)]
        diff = diff_of(old, new)
        assert diff["status"] == STATUS_UNIT
        assert diff["amount_delta"] is not None
        assert abs(diff["amount_delta"]) < 1e-6

    def test_amount_change_without_qty_price(self):
        old = [item("其他费用", "", "项", qty=1, amount=10000)]
        new = [item("其他费用", "", "项", qty=1, amount=13000)]
        diff = diff_of(old, new)
        assert diff["status"] == STATUS_AMOUNT
        assert diff["status"] in {STATUS_AMOUNT, STATUS_PRICE}

    def test_added(self):
        diffs = compare_items([], [item("拉线安装", "", "条", qty=8)])
        assert len(diffs) == 1 and diffs[0]["status"] == STATUS_NEW
        assert diffs[0]["risk"] in {RISK_MID, RISK_HIGH, RISK_LOW}

    def test_removed(self):
        diffs = compare_items([item("临时围栏", "", "m", qty=40, price=25)], [])
        assert len(diffs) == 1 and diffs[0]["status"] == STATUS_REMOVED
        assert diffs[0]["old"] is not None and diffs[0]["new"] is None


class TestSummary:
    def test_counts_and_amounts(self):
        old = [
            item("A", "", "km", qty=1, price=1000),
            item("B", "", "台", qty=2, price=500),
        ]
        new = [
            item("A", "", "km", qty=1.5, price=1000),
            item("C", "", "台", qty=1, price=800),
        ]
        diffs = compare_items(old, new, "settlement")
        summary = summarize(diffs)
        assert summary["count"] == 3
        assert summary["status_counts"].get(STATUS_QTY_UP) == 1
        assert summary["status_counts"].get(STATUS_REMOVED) == 1
        assert summary["status_counts"].get(STATUS_NEW) == 1
        assert summary["amount_old"] == 1000 + 1000
        assert summary["amount_new"] == 1500 + 800

    def test_top_diffs(self):
        old = [item("A", "", "km", qty=1, price=1000), item("B", "", "km", qty=1, price=1000)]
        new = [item("A", "", "km", qty=1.1, price=1000), item("B", "", "km", qty=2, price=1000)]
        diffs = compare_items(old, new, "settlement")
        top = top_diffs(diffs, limit=1)
        assert top and top[0]["name"] == "B"


class TestCompareTexts:
    def test_same_modified_added(self):
        old = [{"id": "a", "label": "报告1", "text": "新建线路1.2km，电杆12基。"},
               {"id": "b", "label": "报告2", "text": "拆除旧导线2.0km。"}]
        new = [{"id": "a2", "label": "报告1", "text": "新建线路1.2km，电杆12基。"},
               {"id": "b2", "label": "报告2", "text": "拆除旧导线2.5km。"},
               {"id": "c", "label": "附页", "text": "新增安健环工程量。"}]
        changes = compare_texts(old, new)
        assert len(changes) == 3
        statuses = {change["status"] for change in changes}
        assert STATUS_SAME in statuses
        assert "修改" in statuses
        assert STATUS_NEW in statuses


class TestMergeSummaries:
    def test_merge(self):
        merged = merge_summaries({
            "settlement": {"count": 2, "changed_count": 1, "status_counts": {"数量增加": 1},
                           "risk_counts": {"高": 1, "中": 0, "低": 0},
                           "amount_old": 100.0, "amount_new": 120.0, "high_risk_amount": 20.0},
            "material": {"count": 1, "changed_count": 1, "status_counts": {"删除": 1},
                         "risk_counts": {"高": 0, "中": 1, "低": 0},
                         "amount_old": 50.0, "amount_new": 0.0, "high_risk_amount": 0.0},
            "boq": {"count": 1, "changed_count": 0, "status_counts": {"无变化": 1},
                    "risk_counts": {}, "amount_old": None, "amount_new": None, "high_risk_amount": 0.0},
        })
        assert merged["count"] == 4
        assert merged["changed_count"] == 2
        assert merged["amount_old"] == 150.0
        assert merged["amount_new"] == 120.0
        assert merged["high_risk_count"] == 1
        assert merged["risk_counts"]["中"] == 1
