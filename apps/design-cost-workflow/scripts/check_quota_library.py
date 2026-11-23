#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""check_quota_library.py — 盘点本机 2022 版配网定额资料（PDF）是否可自动解析。

用于“典型设计/计价”原型接入真实定额前的可行性检查：
- 列出目录下 PDF 的页数、大小；
- 抽样检查是否存在可提取文字（文字版可解析出定额表；扫描版需 OCR/电子版）；
- 输出 JSON（可选）与中文摘要，便于在方案/README 中如实说明限制。

用法（在 apps/design-cost-workflow 下）：
  python scripts/check_quota_library.py --dir "D:\\知识库\\电力定额\\20kV配电网2022" [--json out.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def inspect_pdf(path: Path, max_pages: int = 6) -> dict:
    """返回 PDF 页数、抽样页文字长度（判断文字版/扫描版）。"""
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # noqa: BLE001
        return {"error": f"缺少 PyMuPDF：{exc}"}
    try:
        document = fitz.open(str(path))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"无法打开：{exc}"}
    pages = document.page_count
    sample_pages = min(pages, max_pages)
    chars = []
    for index in range(sample_pages):
        try:
            chars.append(len(document[index].get_text().strip()))
        except Exception:  # noqa: BLE001
            chars.append(0)
    document.close()
    average = (sum(chars) / len(chars)) if chars else 0
    text_based = average >= 50
    return {
        "pages": pages,
        "size_mb": round(path.stat().st_size / 1024 / 1024, 1),
        "sample_text_chars": chars,
        "avg_sample_chars": round(average, 1),
        "text_based": text_based,
        "kind": "文字版（可提取）" if text_based else "扫描版（需 OCR / 电子版）",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="盘点 2022 版配网定额资料可解析性")
    parser.add_argument("--dir", required=True, help="定额资料目录")
    parser.add_argument("--json", default="", help="可选：输出 JSON 路径")
    args = parser.parse_args(argv)

    folder = Path(args.dir)
    if not folder.is_dir():
        raise SystemExit(f"目录不存在：{folder}")
    pdfs = sorted(folder.glob("*.pdf"))
    if not pdfs:
        raise SystemExit(f"目录下未找到 PDF：{folder}")
    report = {"directory": str(folder), "pdfs": [], "summary": {}}
    text_count = 0
    scanned_count = 0
    for pdf in pdfs:
        info = inspect_pdf(pdf)
        info["file"] = pdf.name
        report["pdfs"].append(info)
        if info.get("text_based"):
            text_count += 1
        elif "error" not in info:
            scanned_count += 1
    report["summary"] = {
        "total": len(pdfs),
        "text_based": text_count,
        "scanned": scanned_count,
        "note": "文字版可尝试解析定额表；扫描版需 OCR 或厂商/电子版定额数据后才能导入原型。",
    }
    for item in report["pdfs"]:
        print(f"- {item['file']}：{item.get('pages', '?')} 页，{item.get('size_mb', '?')} MB，"
              f"{item.get('kind', item.get('error', ''))}（抽样平均 {item.get('avg_sample_chars', 0)} 字/页）")
    print(f"合计 {report['summary']['total']} 份：文字版 {text_count}，扫描版 {scanned_count}。")
    print(report["summary"]["note"])
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"JSON 已写入：{args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
