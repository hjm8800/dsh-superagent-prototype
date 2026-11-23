#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""build_work_instruction_index.py — 从本机“作业指导书”资料生成工序知识索引（JSON）。

用途：把作业指导书/样板点施工指导作业书中的施工工序整理成结构化条目
（工序编号、名称、类别、版本、施工工艺/质量/安全/材料机具/引用标准要点），
供工作流原型在建模、算量、计价阶段引用“相关工序”。

用法（需完整 python 路径；本机已有 PyMuPDF / python-docx / olefile）：
  python scripts/build_work_instruction_index.py ^
      --source "D:\\知识库\\作业指导书" ^
      --workdir "D:\\dsh-temp\\zizhu-extract" ^
      --out "data/instructions.json"

说明：
- 压缩包（.rar/.zip/.7z）用系统自带的 bsdtar（tar）解包，无需额外安装工具；
- PDF 用 PyMuPDF 提取文字（扫描版会标记“需人工核对”）；DOCX 用 python-docx；DOC 用启发式提取；
- 提取结果为要点摘录，**原文与版权内容保留在本机**，索引文件默认写入应用 data/ 目录（已 gitignore），
  不随仓库分发；如需共享，请另行确认授权范围。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

CODE_RE = re.compile(r"\b([A-Z]{2,10})-?(\d{1,3})\b")

CATEGORY_RULES = [
    ("基础与土建", ("基础", "土建", "混凝土", "砌筑", "楼地面", "门窗", "预埋件", "地基", "施工测量", "屋面", "抹灰", "照明")),
    ("杆塔与拉线", ("杆塔", "电杆", "拉线", "支架", "铁塔", "抱箍")),
    ("架线与导线", ("架线", "导线", "绝缘子", "金具", "弧垂", "紧线", "跨越")),
    ("电缆", ("电缆", "电缆沟", "电缆井", "电缆管", "中间接头", "终端头", "桥架", "防火封堵")),
    ("变压器与台区", ("变压器", "台架变", "箱变", "配变", "计量", "户表")),
    ("开关站与配电房", ("开关站", "开关柜", "环网", "配电房", "配电室", "箱式变电站", "预装式", "组合式")),
    ("接地与防雷", ("接地", "防雷", "避雷", "地网")),
    ("配电自动化与通信", ("自动化", "终端", "通信", "光缆", "配电自动化", "FTU", "DTU", "TTU", "监测")),
    ("试验与调试", ("试验", "调试", "检验", "试验")),
]

TAG_KEYWORDS = [
    "水泥杆", "钢管杆", "拉线", "基础", "底盘", "拉盘", "卡盘", "混凝土", "钢筋", "模板",
    "导线", "绝缘子", "金具", "横担", "电缆", "终端", "中间接头", "电缆沟", "电缆井", "桥架",
    "变压器", "台架变", "箱变", "配变", "开关柜", "环网柜", "接地", "接地网", "避雷器",
    "自动化终端", "FTU", "DTU", "TTU", "光缆", "户表", "电能表", "采集器", "箱体", "围栏",
    "土石方", "回填", "掏挖", "爆破", "排水", "防雷", "标志", "牌号",
]

SECTION_BUCKETS = {
    "steps": ("施工工艺", "工艺标准", "作业步骤", "施工步骤", "技术措施", "操作步骤", "施工方法", "工艺要求"),
    "quality": ("质量要求", "质量标准", "质量控制", "验收要求", "允许偏差", "检验", "质量检验"),
    "safety": ("安全措施", "安全要求", "注意事项", "危险点", "安全注意事项", "文明施工", "环境保护"),
    "materials": ("材料", "机具", "工器具", "机械设备", "劳动力", "工料机", "主要材料"),
    "standards": ("引用标准", "执行标准", "依据标准", "规程", "规范", "标准", "规程规范"),
}


def clean_line(text: str) -> str:
    value = re.sub(r"\s+", " ", (text or "").strip())
    value = value.replace(" ", " ")
    return value


def extract_archives(source: Path, workdir: Path) -> None:
    archives = [p for p in source.iterdir() if p.suffix.lower() in (".rar", ".zip", ".7z")]
    if not archives:
        return
    workdir.mkdir(parents=True, exist_ok=True)
    tar = shutil.which("tar")
    if not tar:
        raise SystemExit("未找到系统 tar（bsdtar），无法解包 rar/zip/7z")
    for archive in archives:
        print(f"[解包] {archive.name}")
        result = subprocess.run([tar, "-xf", str(archive), "-C", str(workdir)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  解包告警：{result.stderr.strip()[:160]}")


def pdf_text(path: Path, max_pages: int = 80, max_chars: int = 60000) -> tuple[str, bool]:
    try:
        import fitz
    except Exception:
        return "", False
    try:
        document = fitz.open(str(path))
    except Exception:
        return "", False
    parts: list[str] = []
    pages = document.page_count
    for index in range(min(pages, max_pages)):
        try:
            parts.append(document[index].get_text())
        except Exception:
            continue
    document.close()
    text = "\n".join(parts)[:max_chars]
    return text, len(text.strip()) >= 200


def docx_text(path: Path, max_chars: int = 60000) -> tuple[str, bool]:
    try:
        import docx
    except Exception:
        return "", False
    try:
        document = docx.Document(str(path))
    except Exception:
        return "", False
    parts = [p.text for p in document.paragraphs]
    for table in document.tables[:40]:
        for row in table.rows[:40]:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    text = "\n".join(parts)[:max_chars]
    return text, len(text.strip()) >= 200


def doc_text(path: Path, max_chars: int = 60000) -> tuple[str, bool]:
    """旧版 .doc：用启发式提取可打印的 UTF-16LE 文本片段（质量有限，标记需核对）。"""
    try:
        import olefile
    except Exception:
        return "", False
    try:
        ole = olefile.OleFileIO(str(path))
        stream = ole.openstream("WordDocument").read()
        ole.close()
    except Exception:
        return "", False
    decoded = stream.decode("utf-16-le", "ignore")
    chunks = re.findall(r"[\u4e00-\u9fffA-Za-z0-9，。、；：（）%×./\-—+_()\s]{30,}", decoded)
    text = "\n".join(c.strip() for c in chunks if len(c.strip()) > 30)[:max_chars]
    return text, len(text.strip()) >= 200


def detect_meta(rel_path: str, stem: str) -> dict:
    code = ""
    match = CODE_RE.search(stem)
    if match:
        code = f"{match.group(1)}-{match.group(2)}"
    title = stem
    if code:
        title = title.replace(code, " ", 1)
    title = re.sub(r"^[0-9]+[.、]\s*", "", title)
    title = re.sub(r"[（(].*?[)）]", " ", title)
    title = clean_line(title.replace("_", " ")) or stem
    version = ""
    if "2018" in rel_path:
        version = "2018年版"
    elif "2021" in rel_path:
        version = "2021年试用版"
    elif "2015" in rel_path:
        version = "2015年单行版"
    elif "2012" in rel_path:
        version = "2012年版"
    elif "样板点" in rel_path:
        version = "2012年样板点"
    category = "其它"
    for name, keywords in CATEGORY_RULES:
        if any(keyword in title for keyword in keywords):
            category = name
            break
    return {"code": code, "title": title, "version": version, "category": category}


def split_sections(text: str) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {key: [] for key in SECTION_BUCKETS}
    current = None
    for raw_line in (text or "").splitlines():
        line = clean_line(raw_line)
        if not line or len(line) < 4:
            continue
        if len(line) <= 24:
            matched = None
            for key, keywords in SECTION_BUCKETS.items():
                if any(keyword in line for keyword in keywords):
                    matched = key
                    break
            if matched:
                current = matched
                continue
        if current:
            if len(buckets[current]) < 8 and 6 <= len(line) <= 160:
                buckets[current].append(line)
    return buckets


def build_index(source: Path, workdir: Path, out_path: Path, limit_chars: int) -> dict:
    extract_archives(source, workdir)
    docs: list[Path] = []
    for pattern in ("*.pdf", "*.docx", "*.doc"):
        docs.extend(workdir.rglob(pattern))
    docs = sorted(set(docs))
    print(f"文档总数：{len(docs)}")
    entries: list[dict] = []
    stats = {"text": 0, "needs_review": 0, "failed": 0}
    for path in docs:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            text, ok = pdf_text(path)
        elif suffix == ".docx":
            text, ok = docx_text(path)
        else:
            text, ok = doc_text(path)
        if not text.strip():
            stats["failed"] += 1
            continue
        stats["text" if ok else "needs_review"] += 1
        rel = str(path.relative_to(workdir))
        meta = detect_meta(rel, path.stem)
        buckets = split_sections(text)
        tags = [word for word in TAG_KEYWORDS if word in text or word in meta["title"]]
        entries.append({
            "code": meta["code"],
            "title": meta["title"],
            "category": meta["category"],
            "version": meta["version"],
            "source_file": rel,
            "chars": len(text),
            "needs_review": not ok,
            "tags": tags[:18],
            "steps": buckets["steps"][:6],
            "quality": buckets["quality"][:5],
            "safety": buckets["safety"][:5],
            "materials": buckets["materials"][:5],
            "standards": buckets["standards"][:4],
        })
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": str(source),
        "note": "工序要点摘录自本机作业指导书资料，仅用于内部施工工序参考；原文版权归原发布方，索引文件不入仓库。",
        "stats": {"documents": len(docs), **stats, "entries": len(entries)},
        "entries": entries,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"索引条目：{len(entries)}（文字可提取 {stats['text']} / 需人工核对 {stats['needs_review']} / 解析失败 {stats['failed']}）")
    print(f"输出：{out_path}")
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成作业指导书工序索引（本地）")
    parser.add_argument("--source", required=True, help="作业指导书资料目录（含 rar/zip/7z）")
    parser.add_argument("--workdir", default="", help="解包与中间文件目录（默认 <source>_extract）")
    parser.add_argument("--out", default=str(APP_DIR / "data" / "instructions.json"), help="索引 JSON 输出路径")
    args = parser.parse_args(argv)
    source = Path(args.source)
    if not source.is_dir():
        raise SystemExit(f"目录不存在：{source}")
    workdir = Path(args.workdir) if args.workdir else source.with_name(source.name + "_extract")
    out_path = Path(args.out)
    build_index(source, workdir, out_path, limit_chars=60000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
