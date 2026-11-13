"""File parsers for the settlement AI workbench MVP.

Each parser returns a dict:
  status: parsed | needs_plugin | unsupported | error
  summary: short human-readable summary
  data: structured payload (may be empty)
  text: optional extracted text (truncated where appropriate)
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from docx import Document
from openpyxl import load_workbook
from PIL import Image

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover
    fitz = None

try:
    import xlrd
except Exception:  # pragma: no cover
    xlrd = None

try:
    import ezdxf
except Exception:  # pragma: no cover
    ezdxf = None

try:
    from lxml import etree
except Exception:  # pragma: no cover
    etree = None


def _sample(text: str, n: int = 1200) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:n]


def _find_oda() -> str | None:
    for p in Path(r"C:\Program Files\ODA").glob("*/ODAFileConverter.exe"):
        return str(p)
    q = shutil.which("ODAFileConverter")
    return q


def _find_soffice() -> str | None:
    for p in (
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
    ):
        if p.exists():
            return str(p)
    return shutil.which("soffice")


def parse_docx(path: str) -> dict:
    d = Document(path)
    paras = [p.text.strip() for p in d.paragraphs if p.text.strip()]
    tables = []
    for t in d.tables[:20]:
        rows = []
        for row in t.rows[:40]:
            rows.append([c.text.strip().replace("\n", " / ") for c in row.cells])
        tables.append(rows)
    text = "\n".join(paras)
    # best-effort metadata
    code = ""
    m = re.search(r"(?:项目编码|工程编号)[：:\s]*([0-9A-Za-z\-]{6,})", text)
    if m:
        code = m.group(1)
    name = ""
    for p in paras[:12]:
        if "工程" in p and 4 <= len(p) <= 80 and "设计" not in p[:2]:
            name = p
            break
    km = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*km", text)
    kva = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*kVA", text, re.I)
    data = {
        "project_name": name,
        "project_code": code,
        "km_values": km[:10],
        "kva_values": kva[:10],
        "tables": tables,
    }
    return {
        "status": "parsed",
        "summary": f"DOCX 解析：{len(paras)} 段、{len(tables)} 张表；项目编码 {code or '未识别'}",
        "data": data,
        "text": _sample(text),
    }


def _sheet_rows(ws, max_rows=200):
    rows = []
    for row in ws.iter_rows(min_row=1, max_row=min(max_rows, ws.max_row), max_col=min(12, ws.max_column), values_only=True):
        vals = ["" if v is None else str(v).strip() for v in row]
        if any(vals):
            rows.append(vals)
    return rows


def parse_xlsx(path: str) -> dict:
    wb = load_workbook(path, read_only=True, data_only=True)
    sheets = {}
    materials, boq, budget = [], [], []
    for ws in wb.worksheets:
        rows = _sheet_rows(ws)
        sheets[ws.title] = {"rows": ws.max_row, "cols": ws.max_column}
        header = " ".join(rows[0]) if rows else ""
        body = rows[1:] if rows else []
        if "甲供" in ws.title or "乙供" in ws.title:
            for r in body:
                if len(r) >= 5 and r[2]:
                    materials.append({
                        "supply": "甲供" if "甲供" in ws.title else "乙供",
                        "code": r[1],
                        "name": r[2],
                        "spec": r[3],
                        "unit": r[4],
                        "qty": r[5] if len(r) > 5 else "",
                    })
        elif "工程量" in ws.title:
            for r in body:
                if len(r) >= 4 and r[1]:
                    boq.append({"code": r[0], "name": r[1], "unit": r[2], "qty": r[3]})
        elif "预算" in ws.title or "费用明细" in ws.title or "总算" in ws.title:
            for r in body:
                if len(r) >= 8 and str(r[0]).strip() in ("1", "一") and any(r[3:8]):
                    budget.append(r)
    wb.close()
    data = {"sheets": sheets, "materials": materials[:500], "boq": boq[:500], "budget_rows": budget[:20]}
    parts = [f"工作表 {len(sheets)} 个"]
    if materials:
        parts.append(f"材料 {len(materials)} 条（甲供/乙供）")
    if boq:
        parts.append(f"工程量 {len(boq)} 条")
    if budget:
        parts.append(f"预算汇总 {len(budget)} 行")
    return {"status": "parsed", "summary": "XLSX 解析：" + "，".join(parts), "data": data, "text": ""}


def parse_xls(path: str) -> dict:
    if xlrd is None:
        return {"status": "error", "summary": "缺少 xlrd", "data": {}, "text": ""}
    book = xlrd.open_workbook(path)
    sheets = {s.name: {"rows": s.nrows, "cols": s.ncols} for s in book.sheets()}
    names = book.sheet_names()
    return {"status": "parsed", "summary": f"XLS 解析：{len(names)} 个工作表", "data": {"sheets": sheets}, "text": ""}


def parse_pdf(path: str) -> dict:
    if fitz is None:
        return {"status": "error", "summary": "缺少 PyMuPDF", "data": {}, "text": ""}
    doc = fitz.open(path)
    pages = []
    for i in range(min(10, len(doc))):
        pages.append(doc[i].get_text())
    doc.close()
    text = "\n".join(pages)
    return {"status": "parsed", "summary": f"PDF 解析：{len(pages)} 页、{len(text)} 字符", "data": {"pages": len(pages)}, "text": _sample(text)}


def parse_xml(path: str) -> dict:
    if etree is None:
        return {"status": "error", "summary": "缺少 lxml", "data": {}, "text": ""}
    tree = etree.parse(path)
    root = tree.getroot()
    leaves = []
    for el in root.iter():
        if len(el) == 0 and (el.text or "").strip():
            leaves.append(f"{el.tag}: {el.text.strip()}")
            if len(leaves) >= 30:
                break
    return {"status": "parsed", "summary": f"XML 解析：根节点 {root.tag}，{len(root)} 个子节点", "data": {"root": root.tag, "children": len(root)}, "text": _sample("\n".join(leaves))}


def parse_zip(path: str) -> dict:
    entries = []
    with zipfile.ZipFile(path) as z:
        for info in z.infolist()[:100]:
            name = info.filename
            try:
                name = name.encode("cp437").decode("gbk")
            except Exception:
                pass
            entries.append({"name": name, "size": info.file_size})
    return {"status": "parsed", "summary": f"ZIP 解析：{len(entries)} 个条目", "data": {"entries": entries}, "text": ""}


def parse_dwg(path: str) -> dict:
    oda = _find_oda()
    if not oda:
        return {"status": "needs_plugin", "summary": "DWG 需要 ODA File Converter 插件（未安装）", "data": {}, "text": ""}
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src"
        out = Path(td) / "out"
        src.mkdir(); out.mkdir()
        shutil.copy2(path, src / Path(path).name)
        try:
            subprocess.run([oda, str(src), str(out), "ACAD2018", "DXF", "0", "1", "*.dwg"], capture_output=True, text=True, timeout=120)
        except Exception as e:
            return {"status": "error", "summary": f"ODA 转换失败：{e}", "data": {}, "text": ""}
        dxfs = list(out.glob("*.dxf"))
        if not dxfs:
            return {"status": "error", "summary": "ODA 未生成 DXF", "data": {}, "text": ""}
        if ezdxf is None:
            return {"status": "parsed", "summary": f"已转 DXF：{dxfs[0].name}（缺少 ezdxf，未提取文字）", "data": {}, "text": ""}
        doc = ezdxf.readfile(str(dxfs[0]))
        texts = []
        for e in doc.modelspace():
            if e.dxftype() == "TEXT":
                texts.append(e.dxf.text)
            elif e.dxftype() == "MTEXT":
                texts.append(e.text)
            elif e.dxftype() == "INSERT":
                texts.extend(a.dxf.text for a in e.attribs)
        uniq = []
        for t in texts:
            t = (t or "").strip()
            if t and t not in uniq:
                uniq.append(t)
        return {"status": "parsed", "summary": f"DWG 经 ODA 转 DXF 解析：{len(uniq)} 条文字", "data": {"dxf": dxfs[0].name, "texts": uniq[:200]}, "text": _sample("\n".join(uniq))}


def parse_doc(path: str) -> dict:
    soffice = _find_soffice()
    if not soffice:
        return {"status": "needs_plugin", "summary": "DOC 需要 LibreOffice 或格式转换插件（未安装）", "data": {}, "text": ""}
    with tempfile.TemporaryDirectory() as td:
        try:
            subprocess.run([soffice, "--headless", "--convert-to", "txt:Text", "--outdir", td, path], capture_output=True, text=True, timeout=120)
        except Exception as e:
            return {"status": "error", "summary": f"LibreOffice 转换失败：{e}", "data": {}, "text": ""}
        txts = list(Path(td).glob("*.txt"))
        if not txts:
            return {"status": "error", "summary": "LibreOffice 未生成文本", "data": {}, "text": ""}
        text = txts[0].read_text(encoding="utf-8", errors="replace")
        return {"status": "parsed", "summary": f"DOC 经 LibreOffice 转文本：{len(text)} 字符", "data": {}, "text": _sample(text)}


def parse_bdy3(path: str) -> dict:
    return {
        "status": "needs_plugin",
        "summary": "BDY3 为预算软件原生格式，需厂商插件；可改用同目录 XLSX/XML 预算导出",
        "data": {},
        "text": "",
    }


def parse_image(path: str) -> dict:
    im = Image.open(path)
    info = {"width": im.width, "height": im.height, "format": im.format}
    exif = {}
    try:
        exif = {k: str(v) for k, v in im.getexif().items()}
    except Exception:
        pass
    return {"status": "parsed", "summary": f"图片：{im.width}x{im.height} {im.format}", "data": {"info": info, "exif": exif}, "text": ""}


def parse_file(path: str, filename: str | None = None) -> dict:
    filename = filename or os.path.basename(path)
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".docx":
            return parse_docx(path)
        if ext == ".xlsx":
            return parse_xlsx(path)
        if ext == ".xls":
            return parse_xls(path)
        if ext == ".pdf":
            return parse_pdf(path)
        if ext == ".xml":
            return parse_xml(path)
        if ext == ".zip":
            return parse_zip(path)
        if ext == ".dwg":
            return parse_dwg(path)
        if ext == ".doc":
            return parse_doc(path)
        if ext == ".bdy3":
            return parse_bdy3(path)
        if ext in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
            return parse_image(path)
        return {"status": "unsupported", "summary": f"暂不支持 {ext or '未知'} 格式", "data": {}, "text": ""}
    except Exception as e:
        return {"status": "error", "summary": f"解析失败：{e}", "data": {}, "text": ""}
