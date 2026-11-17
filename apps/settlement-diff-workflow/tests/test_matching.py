# -*- coding: utf-8 -*-
"""matching 模块单元测试。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diffworkflow.matching import (  # noqa: E402
    MATCH_EXACT,
    MATCH_FUZZY,
    MATCH_NAME_UNIT,
    MATCH_NEW,
    MATCH_REMOVED,
    group_items,
    item_key,
    match_items,
    units_compatible,
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


class TestGroupItems:
    def test_merge_same_key(self):
        items = [
            item("电杆坑土方", "普通土", "m3", qty=10, price=19.36),
            item("电杆坑土方", "普通土", "m3", qty=5, price=19.36),
        ]
        groups = group_items(items, "settlement")
        assert len(groups) == 1
        assert groups[0]["qty"] == 15
        assert groups[0]["amount"] == 15 * 19.36

    def test_amount_computed(self):
        groups = group_items([item("导线架设", "", "km", qty=2, amount=20000)], "settlement")
        assert groups[0]["amount"] == 20000
        # 无显式单价时不强行推导（避免把费用类条目误判成单价变化）
        assert groups[0]["unit_price"] is None

    def test_settlement_code_key(self):
        left = item("任意名称A", "规格A", "台", qty=1, code="JYX6-1", item_type="定额")
        right = item("任意名称B", "规格B", "台", qty=1, code="JYX6-1", item_type="定额")
        assert item_key(left, "settlement") == item_key(right, "settlement")


class TestUnitsCompatible:
    def test_same_family(self):
        assert units_compatible("m", "km")
        assert units_compatible("", "台")
        assert not units_compatible("台", "m")


class TestMatchItems:
    def test_exact_match(self):
        old = [item("电杆组立", "Φ190×12", "根", qty=10, price=350)]
        new = [item("电杆组立", "Φ190×12", "根", qty=11, price=350)]
        pairs = match_items(old, new, "settlement")
        assert len(pairs) == 1
        assert pairs[0]["match_type"] == MATCH_EXACT
        assert pairs[0]["old"]["qty"] == 10 and pairs[0]["new"]["qty"] == 11

    def test_spec_change_matched(self):
        old = [item("导线架设", "JKLGYJ-240", "km", qty=1)]
        new = [item("导线架设", "JKLGYJ-185", "km", qty=1)]
        pairs = match_items(old, new, "settlement")
        assert len(pairs) == 1
        assert pairs[0]["match_type"] == MATCH_NAME_UNIT

    def test_fuzzy_match(self):
        old = [item("10kV架空线路架设", "", "km", qty=1)]
        new = [item("10kV架空线路架设工程", "", "km", qty=1.1)]
        pairs = match_items(old, new, "settlement")
        assert len(pairs) == 1
        assert pairs[0]["match_type"] in {MATCH_FUZZY, MATCH_NAME_UNIT}

    def test_added_removed(self):
        old = [item("电杆组立", "", "根", qty=3)]
        new = [item("拉线安装", "", "条", qty=8)]
        pairs = match_items(old, new, "material")
        types = {pair["match_type"] for pair in pairs}
        assert MATCH_REMOVED in types and MATCH_NEW in types
        assert len(pairs) == 2

    def test_duplicate_rows_aggregate(self):
        old = [
            item("圆钢接地极", "Φ16", "m", qty=100),
            item("圆钢接地极", "Φ16", "m", qty=50),
        ]
        new = [item("圆钢接地极", "Φ16", "m", qty=180)]
        pairs = match_items(old, new, "material")
        assert len(pairs) == 1
        assert pairs[0]["old"]["qty"] == 150
        assert pairs[0]["new"]["qty"] == 180

    def test_renamed_match(self):
        old = [item("混凝土杆 普通混凝土电杆 Φ190*15", "", "根", qty=4)]
        new = [item("预应力混凝土电杆 Φ190×15M", "", "根", qty=4)]
        pairs = match_items(old, new, "material")
        assert len(pairs) == 1
        assert pairs[0]["match_type"] in {MATCH_FUZZY, "疑似改名", MATCH_NAME_UNIT}
