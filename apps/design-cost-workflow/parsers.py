"""图纸 / 清册文件解析器（尽力而为，失败可降级）。

当前原型支持的输入：

- PDF   ：PyMuPDF 抽取文本，正则识别路径长度 / 杆塔数量 / 配变容量 / 导线型号等；
- DWG   ：检测本机 ODA File Converter，能转换时转 DXF 后用 ezdxf 读取文字，否则标记“需插件”；
- XLSX  ：openpyxl 读取工作表名、表头与行数，辅助识别材料清册 / 杆塔明细表；
- XLS   ：xlrd 读取（老版 Excel，博微导出常见格式之一）；
- DOCX  ：python-docx 读取段落与表格文本；
- JPG/PNG：Pillow 读取尺寸，按“现场照片/图纸图片”编目；
- 其他   ：返回“解析失败”，提示人工编目或转换格式。

对外只暴露 :func:`parse_drawing` 与 :func:`guess_meta`，返回值均为纯 dict，
不抛异常（单个文件解析失败不影响批量上传的其他文件）。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# ODA File Converter 常见安装位置（未安装时 DWG 走“需插件”降级路径）
ODA_CONVERTER = Path(r"C:\Program Files\ODA\ODAFileConverter 27.10.0\ODAFileConverter.exe")

# 图纸类别猜测规则：文件名关键词 → 类别（越靠前优先级越高）
CATEGORY_RULES: list[tuple[tuple[str, ...], str]] = [
    (("说明书", "设计说明", "总说明"), "设计说明书"),
    (("平面图", "路径图", "线路图", "路径示意"), "平面图"),
    (("安装图", "接线图", "组装图", "台架变安装"), "安装图"),
    (("明细表", "杆塔", "杆型", "材料表", "设备表"), "明细表"),
    (("清册", "材料清册", "设备材料"), "材料清册"),
    (("预算", "概算", "博微", "造价", "结算"), "预算"),
    (("照片", "相片", "现场", "勘察", "影像"), "现场照片"),
]

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
SHEET_EXTS = {".xlsx", ".xlsm", ".xls"}


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _truncate(text: str, limit: int = 500) -> str:
    """压缩空白并截断文本，避免 summary 过长。"""
    text = re.sub(r"[ \t\u3000]+", " ", (text or "")).strip()
    text = re.sub(r"\n{2,}", "\n", text)
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def guess_fmt(filename: str) -> str:
    """由文件名推断图纸格式（PDF / DWG / XLSX / JPG ...）。"""
    ext = Path(filename or "").suffix.lower().lstrip(".")
    return ext.upper() if ext else "未知"


def guess_category(filename: str) -> str:
    """由文件名推断图纸/资料类别。"""
    stem = Path(filename or "").stem
    for keywords, category in CATEGORY_RULES:
        if any(k in stem for k in keywords):
            return category
    ext = Path(filename or "").suffix.lower()
    if ext in IMAGE_EXTS:
        return "现场照片"
    if ext in SHEET_EXTS:
        return "明细表"
    return "其他"


def guess_name(filename: str) -> str:
    """图纸名称默认为文件名（去扩展名）。"""
    return Path(filename or "").stem or (filename or "未命名图纸")


def guess_meta(filename: str) -> dict:
    """上传前可用的元信息：名称 / 类别 / 格式。"""
    return {
        "name": guess_name(filename),
        "category": guess_category(filename),
        "fmt": guess_fmt(filename),
        "sheet_count": 1,
    }


def _result(status: str, summary: str, text: str = "", **extra) -> dict:
    data = {"status": status, "summary": summary, "text": text}
    data.update(extra)
    return data


# --------------------------------------------------------------------------- #
# 文本识别：从说明性文字中抽取项目画像
# --------------------------------------------------------------------------- #
NUM = r"([0-9]+(?:\.[0-9]+)?)"
_KM_RE = re.compile(
    r"(?:路径长度|线路长度|亘长|全长|长度)[^0-9]{0,12}" + NUM + r"\s*(?:km|千米|公里)",
    re.IGNORECASE,
)
_KM_ANY_RE = re.compile(NUM + r"\s*(?:km|千米|公里)", re.IGNORECASE)
_POLE_RE = re.compile(
    r"(?:杆塔|电杆|立杆|杆数|新立|组立)[^0-9]{0,10}" + NUM + r"\s*(?:基|根|座|条)", re.IGNORECASE
)
_KVA_RE = re.compile(r"(?:配变|变压器|容量)[^0-9]{0,12}" + NUM + r"\s*kVA", re.IGNORECASE)
_KVA_ANY_RE = re.compile(NUM + r"\s*kVA", re.IGNORECASE)
_VOLT_RE = re.compile(r"(10|20|0\.4|1)\s*kV", re.IGNORECASE)
_CONDUCTOR_RE = re.compile(
    r"(JKLGYJ|JKLYJ|BLVV|BVV|YJV|ZA-YJV|LGJ|JKLYJ/Q)[-A-Za-z0-9×*/.]*",
    re.IGNORECASE,
)


def _uniq(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def summarize_text(text: str) -> str:
    """从图纸说明文字中抽取“路径 / 杆塔 / 配变 / 导线”等关键信息，生成识别结论。"""
    if not text or not text.strip():
        return "未提取到文字内容（可能为扫描件或纯图形图纸），请人工核对图签信息"

    parts: list[str] = []
    km_values = _uniq(re.findall(_KM_RE, text)) or _uniq(re.findall(_KM_ANY_RE, text))
    if len(km_values) >= 2:
        parts.append(f"10kV路径{km_values[0]}km")
        parts.append(f"低压路径{km_values[1]}km")
    elif len(km_values) == 1:
        parts.append(f"路径{km_values[0]}km")

    poles = _uniq(re.findall(_POLE_RE, text))
    if poles:
        parts.append(f"杆塔{poles[0]}基")

    kva = _uniq(re.findall(_KVA_RE, text)) or _uniq(re.findall(_KVA_ANY_RE, text))
    if kva:
        parts.append(f"配变{kva[0]}kVA")

    volts = _uniq([v for v in re.findall(_VOLT_RE, text) if v])
    if volts:
        parts.append("电压等级 " + "/".join(f"{v}kV" for v in volts[:2]))

    conductors = _uniq([c.upper() for c in re.findall(_CONDUCTOR_RE, text)])
    if conductors:
        parts.append("导线型号 " + "、".join(conductors[:2]))

    if not parts:
        return f"已提取文字 {len(text.strip())} 字，未识别到路径/杆塔/配变等关键字段，建议人工核对"
    return "识别：" + "、".join(parts)


# --------------------------------------------------------------------------- #
# 各格式解析
# --------------------------------------------------------------------------- #
def _parse_pdf(path: Path) -> dict:
    """PyMuPDF 抽取前几页文字。"""
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # pragma: no cover - 环境缺包时降级
        return _result("需插件", f"未安装 PyMuPDF，无法解析 PDF（{exc}）")
    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        return _result("解析失败", f"PDF 打开失败：{exc}")

    chunks: list[str] = []
    try:
        for page_no in range(min(len(doc), 5)):
            try:
                chunks.append(doc.load_page(page_no).get_text("text") or "")
            except Exception:
                continue
    finally:
        try:
            doc.close()
        except Exception:
            pass

    text = "\n".join(chunks).strip()
    if not text:
        return _result(
            "已识别",
            "PDF 已入库；前 5 页未提取到文字（扫描版），建议人工核对图签与工程量",
            "",
        )
    summary = summarize_text(text)
    return _result("已识别", summary, _truncate(text))


def _find_converter() -> Path | None:
    """查找可用的 ODA File Converter。"""
    if ODA_CONVERTER.exists():
        return ODA_CONVERTER
    found = shutil.which("ODAFileConverter") or shutil.which("ODAFileConverter.exe")
    return Path(found) if found else None


def _parse_dwg(path: Path) -> dict:
    """DWG：有 ODA File Converter 则转 DXF 后读文字，否则标记“需插件”。"""
    converter = _find_converter()
    if converter is None:
        return _result(
            "需插件",
            "DWG 需 ODA File Converter 转换，未检测到插件；图纸已登记，可人工编目与录入",
            "",
        )

    tmpdir = Path(tempfile.mkdtemp(prefix="dwg-convert-"))
    try:
        in_dir = tmpdir / "in"
        out_dir = tmpdir / "out"
        in_dir.mkdir()
        out_dir.mkdir()
        shutil.copy2(path, in_dir / path.name)
        cmd = [
            str(converter),
            str(in_dir),
            str(out_dir),
            "ACAD2018",
            "DXF",
            "0",  # 不递归
            "1",  # 执行审核
        ]
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        dxf_files = list(out_dir.glob("*.dxf")) + list(out_dir.glob("*.DXF"))
        if proc.returncode != 0 or not dxf_files:
            detail = (proc.stderr or b"").decode("utf-8", "ignore")[:160]
            return _result(
                "解析失败",
                f"DWG 转换 DXF 失败（returncode={proc.returncode}）{detail}",
                "",
            )
        return _parse_dxf(dxf_files[0])
    except subprocess.TimeoutExpired:
        return _result("解析失败", "DWG 转换超时（>120 秒），请人工转换 DXF 后重试")
    except Exception as exc:
        return _result("解析失败", f"DWG 转换异常：{exc}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _parse_dxf(path: Path) -> dict:
    """ezdxf 读取 DXF 文字实体，抽取杆号 / 档距 / 型号等线索。"""
    try:
        import ezdxf
    except Exception:
        return _result("需插件", "已转换为 DXF，但未安装 ezdxf，无法读取图面文字")
    try:
        doc = ezdxf.readfile(str(path))
    except Exception as exc:
        return _result("解析失败", f"DXF 读取失败：{exc}")

    texts: list[str] = []
    try:
        for entity in doc.modelspace().query("TEXT MTEXT"):
            try:
                value = entity.dxf.text if entity.dxftype() == "TEXT" else entity.plain_text()
            except Exception:
                value = getattr(entity.dxf, "text", "") or ""
            value = (value or "").strip()
            if value:
                texts.append(value)
    except Exception:
        pass

    text = " ".join(texts)
    if not text.strip():
        return _result("已识别", "DXF 已读取，图面未找到文字实体，建议人工编目", "")
    pole_tags = re.findall(r"#?\s*\d{1,3}\s*(?:号杆|#杆|号)", text)
    summary = summarize_text(text)
    if pole_tags and "杆" not in summary:
        summary += f"、杆号标注{len(pole_tags)}处"
    return _result("已识别", summary, _truncate(text))


def _xlsx_sheet_info(path: Path) -> dict:
    """openpyxl 读取工作表名、表头与有效行数。"""
    from openpyxl import load_workbook

    wb = load_workbook(str(path), read_only=True, data_only=True)
    try:
        sheets = list(wb.sheetnames)
        headers: list[str] = []
        row_total = 0
        for ws in wb.worksheets:
            max_row = ws.max_row or 0
            row_total += int(max_row) if max_row else 0
            # 读前两行作为表头线索
            try:
                for row_no, row in enumerate(ws.iter_rows(min_row=1, max_row=2, values_only=True)):
                    if row_no >= 2:
                        break
                    cells = [str(c).strip() for c in row if c not in (None, "")]
                    if cells:
                        headers.extend(cells[:6])
            except Exception:
                continue
        return {"sheets": sheets, "headers": _uniq(headers)[:8], "rows": row_total}
    finally:
        try:
            wb.close()
        except Exception:
            pass


def _parse_xlsx(path: Path) -> dict:
    try:
        info = _xlsx_sheet_info(path)
    except Exception as exc:
        return _result("解析失败", f"Excel 读取失败：{exc}")

    sheets = info["sheets"]
    header_text = "、".join(info["headers"][:6])
    # 粗略估算数据行数：总行数扣除每个工作表表头行
    rows = max(int(info["rows"]) - len(sheets), 0)
    parts = [f"工作表{len(sheets)}个：{'、'.join(sheets[:5])}"]
    if header_text:
        parts.append(f"表头线索：{header_text}")
    parts.append(f"识别到数据行约 {rows} 行")
    text = f"工作表：{'、'.join(sheets)}\n表头：{header_text}"
    return _result("已识别", "；".join(parts), _truncate(text, 300), sheet_count=max(len(sheets), 1))


def _parse_xls(path: Path) -> dict:
    try:
        import xlrd
    except Exception:
        return _result("需插件", "未安装 xlrd，无法解析老版 .xls；建议另存为 .xlsx 后重试")
    try:
        book = xlrd.open_workbook(str(path), on_demand=True)
        sheets = book.sheet_names()
        rows = 0
        headers: list[str] = []
        for name in sheets[:6]:
            sheet = book.sheet_by_name(name)
            rows += max(sheet.nrows - 1, 0)
            if sheet.nrows:
                headers.extend(str(sheet.cell_value(0, c)) for c in range(min(sheet.ncols, 6)))
        book.release_resources()
    except Exception as exc:
        return _result("解析失败", f"XLS 读取失败：{exc}")

    header_text = "、".join([h for h in _uniq(headers) if h][:6])
    summary = f"工作表{len(sheets)}个：{'、'.join(sheets[:5])}；识别到数据行约 {rows} 行"
    return _result(
        "已识别",
        summary,
        _truncate(f"工作表：{'、'.join(sheets)}\n表头：{header_text}", 300),
        sheet_count=max(len(sheets), 1),
    )


def _parse_docx(path: Path) -> dict:
    try:
        from docx import Document
    except Exception:
        return _result("需插件", "未安装 python-docx，无法解析 Word 文档")
    try:
        doc = Document(str(path))
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
        table_cells: list[str] = []
        for table in doc.tables[:3]:
            for row in table.rows[:20]:
                for cell in row.cells:
                    value = (cell.text or "").strip()
                    if value:
                        table_cells.append(value)
    except Exception as exc:
        return _result("解析失败", f"DOCX 读取失败：{exc}")

    text = "\n".join(paragraphs + table_cells)
    summary = summarize_text(text) if text else "文档已入库，未提取到文字"
    return _result("已识别", summary, _truncate(text))


def _parse_image(path: Path) -> dict:
    """图片：读取像素尺寸后按现场照片/图签编目。"""
    try:
        from PIL import Image

        with Image.open(str(path)) as img:
            width, height = img.size
        return _result(
            "已识别",
            f"图片已编目：{width}×{height} 像素；建议人工核对图签、杆号与现场标识",
            f"图片尺寸 {width}×{height}",
        )
    except Exception as exc:
        return _result("已识别", f"图片已入库（尺寸读取失败：{exc}），建议人工核对图签信息")


def _parse_other(path: Path) -> dict:
    ext = path.suffix.lower() or "未知格式"
    return _result(
        "解析失败",
        f"暂不支持 {ext} 格式的自动解析，请人工编目或转换为 PDF/DWG/XLSX 后重试",
    )


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #
def parse_drawing(path: Path | str, filename: str) -> dict:
    """解析一张图纸 / 一份清册，返回 {status, summary, text, sheet_count?}。

    Never raises for expected file problems; unexpected exceptions are converted
    to ``解析失败`` so a batch upload never fails as a whole.
    """
    path = Path(path)
    filename = filename or path.name
    ext = path.suffix.lower() or Path(filename).suffix.lower()
    try:
        if not path.exists():
            return _result("解析失败", "文件不存在或上传已失效")
        if ext == ".pdf":
            return _parse_pdf(path)
        if ext == ".dwg":
            return _parse_dwg(path)
        if ext == ".dxf":
            return _parse_dxf(path)
        if ext in (".xlsx", ".xlsm"):
            return _parse_xlsx(path)
        if ext == ".xls":
            return _parse_xls(path)
        if ext == ".docx":
            return _parse_docx(path)
        if ext == ".doc":
            return _result("需插件", "老版 .doc 暂不支持自动解析，建议另存为 .docx 或 PDF 后重试")
        if ext in IMAGE_EXTS:
            return _parse_image(path)
        return _parse_other(path)
    except Exception as exc:  # 兜底：单文件失败不影响上传批次
        return _result("解析失败", f"解析异常：{exc}")
