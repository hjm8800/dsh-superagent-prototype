"""竣工资料与结算逐项对比工作流 · 命令行入口。

用法：
  python cli.py analyze --old <原版资料目录> --new <新版资料目录> [--project 名称] [--out 输出目录]
  python cli.py demo [--out 输出目录]          # 用 examples/demo 的匿名演示资料跑一遍
  python cli.py make-demo [--out 目录]          # 生成/刷新匿名演示资料
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable

from diffworkflow import export_excel, export_report
from diffworkflow.build import build_comparison, comparison_to_json

APP_DIR = Path(__file__).resolve().parent


def _safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name or "").strip(" _.")
    return name or "未命名项目"


def _write_outputs(comparison: dict, out_dir: str | Path | None, write_docx: bool = True) -> dict:
    name = _safe_filename(comparison["project"]["name"])
    out = Path(out_dir) if out_dir else Path.cwd() / "对比结果"
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "excel": out / f"{name}-数据对比表.xlsx",
        "markdown": out / f"{name}-对比分析报告.md",
        "json": out / f"{name}-summary.json",
    }
    export_excel.write(comparison, paths["excel"])
    export_report.write_markdown(comparison, paths["markdown"])
    if write_docx:
        paths["docx"] = out / f"{name}-对比分析报告.docx"
        export_report.write_docx(comparison, paths["docx"])
    paths["json"].write_text(comparison_to_json(comparison), encoding="utf-8")
    return paths


def _print_result(comparison: dict, paths: dict) -> None:
    print("=" * 72)
    print(export_report.build_console_summary(comparison))
    print("-" * 72)
    print("输出文件：")
    for label, path in paths.items():
        print(f"  [{label}] {path}")
    print("=" * 72)


def analyze(old_dir: str, new_dir: str, out_dir: str | None = None, project: str | None = None,
            write_docx: bool = True) -> dict:
    comparison = build_comparison(old_dir, new_dir, project)
    paths = _write_outputs(comparison, out_dir, write_docx=write_docx)
    _print_result(comparison, paths)
    return {"comparison": comparison, "paths": paths}


def _ensure_demo_data(demo_root: Path) -> tuple[Path, Path]:
    from diffworkflow import demo_data

    old_dir = demo_root / "原版"
    new_dir = demo_root / "新版"
    if not old_dir.exists() or not new_dir.exists():
        print(f"演示资料不存在，正在生成：{demo_root}")
        demo_data.generate(demo_root)
    return old_dir, new_dir


def run_demo(out_dir: str | None = None, demo_root: str | None = None) -> dict:
    root = Path(demo_root) if demo_root else APP_DIR / "examples" / "demo"
    old_dir, new_dir = _ensure_demo_data(root)
    comparison = build_comparison(old_dir, new_dir)
    paths = _write_outputs(comparison, out_dir if out_dir else APP_DIR / "demo-output", write_docx=True)
    _print_result(comparison, paths)
    return {"comparison": comparison, "paths": paths}


def make_demo(out_dir: str | None = None) -> dict:
    from diffworkflow import demo_data

    root = Path(out_dir) if out_dir else APP_DIR / "examples" / "demo"
    result = demo_data.generate(root)
    print(f"演示资料已生成：{root}")
    for label, path in result.items():
        print(f"  [{label}] {path}")
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="settlement-diff-workflow",
        description="竣工资料工程量描述、材料清册、审核结算的原版/新版逐项对比工作流",
    )
    sub = parser.add_subparsers(dest="command")

    analyze_parser = sub.add_parser("analyze", help="对比一个项目的原版/新版资料目录")
    analyze_parser.add_argument("--old", required=True, help="原版资料目录")
    analyze_parser.add_argument("--new", required=True, help="新版资料目录")
    analyze_parser.add_argument("--project", default=None, help="项目名称（缺省从文件名推断）")
    analyze_parser.add_argument("--out", default=None, help="输出目录（缺省 ./对比结果）")
    analyze_parser.add_argument("--no-docx", action="store_true", help="不生成 Word 报告")

    demo_parser = sub.add_parser("demo", help="用匿名演示资料跑完整流程")
    demo_parser.add_argument("--out", default=None, help="输出目录（缺省 apps/settlement-diff-workflow/demo-output）")
    demo_parser.add_argument("--demo-root", default=None, help="演示资料目录（缺省 examples/demo）")

    batch_parser = sub.add_parser("batch", help="按文件名自动分组多个项目并批量对比")
    batch_parser.add_argument("--old", required=True, help="原版资料目录（可混放多个项目文件）")
    batch_parser.add_argument("--new", required=True, help="新版资料目录（可混放多个项目文件）")
    batch_parser.add_argument("--out", default=None, help="输出目录（缺省 ./对比结果）")
    batch_parser.add_argument("--project", default=None, help="只跑包含该关键字的项目")
    batch_parser.add_argument("--no-docx", action="store_true", help="不生成 Word 报告")

    make_parser = sub.add_parser("make-demo", help="生成/刷新匿名演示资料")
    make_parser.add_argument("--out", default=None, help="演示资料目录（缺省 examples/demo）")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "analyze":
        analyze(args.old, args.new, out_dir=args.out, project=args.project, write_docx=not args.no_docx)
        return 0
    if args.command == "demo":
        run_demo(out_dir=args.out, demo_root=args.demo_root)
        return 0
    if args.command == "batch":
        from diffworkflow.batch import run_batch

        run_batch(args.old, args.new, args.out or str(Path.cwd() / "对比结果"),
                  project_filter=args.project, write_docx=not args.no_docx)
        return 0
    if args.command == "make-demo":
        make_demo(out_dir=args.out)
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
