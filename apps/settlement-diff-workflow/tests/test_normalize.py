# -*- coding: utf-8 -*-
"""normalize 模块单元测试。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from diffworkflow.normalize import (  # noqa: E402
    convert_qty,
    format_delta,
    format_money,
    format_number,
    format_ratio,
    normalize_name,
    normalize_spec,
    normalize_text,
    normalize_unit,
    parse_number,
    similarity,
    text_similarity,
)


class TestNormalizeText:
    def test_fullwidth_and_spaces(self):
        assert normalize_text("　ＪＫＬＧＹＪ－７０／１０  ") == "JKLGYJ-70/10"

    def test_none_and_numbers(self):
        assert normalize_text(None) == ""
        assert normalize_text(12.0) == "12"


class TestNormalizeName:
    def test_remove_sequence_and_spaces(self):
        assert normalize_name("1、 10kV架空线路架设") == "10kv架空线路架设"
        assert normalize_name("（甲）混凝土杆") == "混凝土杆"

    def test_punctuation_removed(self):
        assert normalize_name("电杆坑、塔坑、拉线坑人工挖方（或爆破）") == "电杆坑塔坑拉线坑人工挖方或爆破"


class TestNormalizeSpec:
    def test_multiplications(self):
        assert normalize_spec("Φ190×12") == "φ190*12"
        assert normalize_spec("500mL") == "500ml"


class TestNormalizeUnit:
    def test_aliases(self):
        assert normalize_unit("米") == "m"
        assert normalize_unit("千米") == "km"
        assert normalize_unit("平方米") == "m2"
        assert normalize_unit("立方米") == "m3"
        assert normalize_unit("吨") == "t"

    def test_unknown_kept(self):
        assert normalize_unit("相线") == "相线"


class TestConvertQty:
    def test_km_to_m(self):
        value, note = convert_qty(1.2, "km", "m")
        assert value == 1200.0
        assert note and "1.2" in note

    def test_m_to_km(self):
        value, _ = convert_qty(500, "m", "km")
        assert value == 0.5

    def test_incompatible(self):
        value, note = convert_qty(1, "台", "m")
        assert value is None and note is None


class TestParseNumber:
    def test_thousands(self):
        assert parse_number("1,234.56") == 1234.56

    def test_fullwidth(self):
        assert parse_number("１２３．４") == 123.4

    def test_parentheses_negative(self):
        assert parse_number("(1,000)") == -1000.0

    def test_percent_strip(self):
        assert parse_number("1.5%") == 1.5

    def test_empty(self):
        assert parse_number("—") is None
        assert parse_number("") is None


class TestFormat:
    def test_numbers(self):
        assert format_number(1234.5) == "1,234.5"
        assert format_number(1200) == "1,200"
        assert format_money(1234) == "1,234.00"
        assert format_ratio(0.1234) == "+12.3%"
        assert format_delta(-1000) == "-1,000"


class TestSimilarity:
    def test_name_similarity(self):
        assert similarity("10kV架空线路架设", "10kV架空线路架设工程") > 0.8
        assert similarity("电杆组立", "拉线安装") < 0.5

    def test_text_similarity(self):
        left = "新建10kV架空线路1.2km，电杆12基。"
        right = "新建10kV架空线路1.2km，电杆12基。"
        assert text_similarity(left, right) == 1.0
