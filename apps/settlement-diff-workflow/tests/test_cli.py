# -*- coding: utf-8 -*-
"""CLI 集成测试：make-demo / demo / analyze 三个命令。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli  # noqa: E402


class TestCli:
    def test_make_demo_and_demo(self, tmp_path, capsys):
        demo_root = tmp_path / "demo"
        assert cli.main(["make-demo", "--out", str(demo_root)]) == 0
        assert (demo_root / "原版").exists()
        out_dir = tmp_path / "out"
        assert cli.main(["demo", "--out", str(out_dir), "--demo-root", str(demo_root)]) == 0
        files = list(out_dir.iterdir())
        names = {path.suffix.lower() for path in files}
        assert ".xlsx" in names and ".md" in names and ".docx" in names and ".json" in names
        captured = capsys.readouterr()
        assert "审核结算" in captured.out

    def test_analyze_requires_folders(self, tmp_path):
        demo_root = tmp_path / "demo"
        cli.main(["make-demo", "--out", str(demo_root)])
        out_dir = tmp_path / "analyze-out"
        code = cli.main([
            "analyze",
            "--old", str(demo_root / "原版"),
            "--new", str(demo_root / "新版"),
            "--project", "项目A·演示项目",
            "--out", str(out_dir),
        ])
        assert code == 0
        assert any(path.name.startswith("项目A·演示项目") for path in out_dir.iterdir())

    def test_no_command_prints_help(self, capsys):
        assert cli.main([]) == 1
