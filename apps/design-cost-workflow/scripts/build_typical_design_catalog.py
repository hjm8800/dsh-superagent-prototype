#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""build_typical_design_catalog.py — 从本机南网 V3.1 典设库生成可导入的模块目录 CSV。

用途：把“本机典设图纸库（7 册 DWG）+ 附件6-1《标准设计应用情况统计表》的 V3.1 模块汇总表”
合成为一份带“图纸文件”列的典设目录 CSV，供工作流原型的“典型设计匹配 → 导入典设目录”
使用；导入后匹配明细可显示每个模块对应的库内图纸文件。

用法（在 apps/design-cost-workflow 下执行，需本机 Python）：
  python scripts/build_typical_design_catalog.py \
      --catalog "D:\harness\...\附件6-1 标准设计应用情况统计表（广东）(V3.1).xlsx" \
      --library "D:\知识库\2、V3.1标准设计--清远本地化修编26-01版" \
      --out "D:\知识库\典设匹配分析-20261116\V3.1典设目录-含图纸文件.csv" \
      --codes-cache "D:\dsh-temp\analysis\dxf_codes.json"

说明：
- 图纸文件列采用相对典设库根目录的路径；换机器/换目录后可直接用新的库根重新生成。
- 编号匹配规则与原型 typdesign.py 一致（编号精确/图号序列/编号变体/罗马序对齐 + 名称/类别推断），
  生成结果只是“目录 + 图纸位置”的初始映射，仍需人工在页面确认。
- 默认只输出 7 册范围（第一至第七册）；--all-scope 可输出目录中第八/十册等（这些册本机库未提供图纸）。
- 若没有 codes 缓存（DWG 图面编号提取结果），会调用 ODA File Converter 逐个转换 DXF 提取编号，
  77 个 DWG 约需数分钟；缓存 JSON 结构为 {"lib::<相对路径>": {"codes": [...]}}。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import typdesign  # noqa: E402  (复用目录解析与编号归一化)

ODA_CANDIDATES = [
    r"C:\Program Files\ODA\ODAFileConverter 27.10.0\ODAFileConverter.exe",
    r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
]

VOL_KEYS = {
    "第一册": ["第一册"],
    "第二册": ["第二册"],
    "第三册": ["第三册"],
    "第四册": ["第四册"],
    "第五册": ["第五册"],
    "第六册": ["第六册"],
    "第七册": ["第七册", "第一篇低压线路本体", "第二篇低压线路智能化"],
}

VOLT_TAG = re.compile(r"^(10B|20B|10D|20D|10K|0\.4K|0\.4D)$")
CSG_RE = re.compile(r"CSG[-_ ]?[A-Z]{1,3}[-_A-Za-z0-9.ⅠⅡⅢ×]{2,60}")


# ---------------------------------------------------------------------------
# 编号归一化 / 匹配（与原型 typdesign.py 保持同一套规则）
# ---------------------------------------------------------------------------


def norm_code(s: str) -> str:
    s = (s or "").replace("Ⅰ", "I").replace("Ⅱ", "II").replace("Ⅲ", "III")
    return s.replace("×", "X").replace(" ", "").strip().upper()


def volt_tag(code: str) -> str:
    for token in norm_code(code).split("-"):
        if VOLT_TAG.match(token):
            return token
    return ""


def code_variants(code: str) -> set[str]:
    text = norm_code(code).strip("-")
    if not text:
        return set()
    tokens = [t for t in text.split("-") if t]
    out = {"-".join(tokens)}
    while len(tokens) > 1:
        last = tokens[-1]
        if re.fullmatch(r"\d{1,3}[A-Z]?", last) or re.fullmatch(r"I{1,3}|IV|V", last):
            tokens = tokens[:-1]
            out.add("-".join(tokens))
        else:
            break
    return out


def code_match(lib_code: str, cat_code: str) -> int:
    lib, cat = norm_code(lib_code), norm_code(cat_code)
    if not lib or not cat:
        return 0
    lt, ct = volt_tag(lib), volt_tag(cat)
    if lt and ct and lt != ct:
        return 0
    if lib == cat:
        return 100
    if lib.startswith(cat + "-"):
        return 90
    if cat.startswith(lib + "-") and len([t for t in lib.split("-") if t]) >= 4:
        return 78
    if cat.startswith(lib) and len(cat) > len(lib) and cat[len(lib)] in "X(":
        return 78
    if cat.startswith(lib) and len(cat) > len(lib) and cat[len(lib)] in "IV":
        return 76
    if code_variants(lib) & code_variants(cat):
        return 85
    return 0


def lcs_len(a: str, b: str) -> int:
    if not a or not b:
        return 0
    best = 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def name_affinity(name: str, relpath: str) -> float:
    stem = norm_code(os.path.splitext(os.path.basename(relpath))[0])
    text = norm_code(name)
    for word in ("标准设计", "标准", "设计", "安装总图", "安装图", "系统结线图", "加工图", "方案图", "总图"):
        text = text.replace(norm_code(word), "")
    n = lcs_len(text, stem)
    return min(8.0, n * 1.5) if n >= 4 else 0.0


def roman_bonus(cat_code: str, relpath: str) -> int:
    last = norm_code(cat_code).split("-")[-1]
    match = re.search(r"(I{1,3}|\d)$", last)
    if not match:
        return 0
    roman = match.group(1)
    want = {"I": "Ⅰ", "II": "Ⅱ", "III": "Ⅲ", "1": "Ⅰ", "2": "Ⅱ", "3": "Ⅲ"}.get(roman, roman)
    stem = norm_code(relpath)
    return 6 if (want in relpath or norm_code(want) in stem or ("型" + want) in relpath) else 0


STRUCT_RULES = [
    ("电缆顶管", "排管及工井"), ("电缆排管", "排管及工井"), ("排管", "排管及工井"),
    ("电缆沟", "电缆沟模块"), ("工井", "工井"), ("预制", "预制井模块"),
    ("馈线自动化", "馈线自动化"), ("综合监测", "综合监测"), ("建筑模块", "建筑模块"),
    ("智能化", "智能化"), ("预装式", "预装式变电站"), ("预装", "预装式变电站"),
    ("组合式变电站", "组合式变电站"), ("户外开关箱", "户外开关箱"),
    ("电缆分接箱", "电缆分接箱"), ("室内配电站", "室内配电站"),
]


def structural_target(text: str) -> str:
    for keyword, target in STRUCT_RULES:
        if keyword in text:
            return target
    return ""


def volume_of(relpath: str) -> str:
    top = relpath.split(os.sep)[0][:2]
    return {"01": "第一册", "02": "第二册", "03": "第三册", "04": "第四册",
            "05": "第五册", "06": "第六册", "07": "第七册"}.get(top, "")


def in_scope(vol: str) -> str:
    for key, aliases in VOL_KEYS.items():
        if any(alias in vol for alias in aliases):
            return key
    return ""


def best_file_for_module(module: dict, files: list[dict], codes: dict[str, list[str]]) -> tuple[str, str]:
    code = module["code"]
    cat_vol = in_scope(module["volume"])
    candidates = []
    for item in files:
        if not cat_vol or item["vol"] != cat_vol:
            continue
        for lib_code in codes.get(item["rel"], []):
            score = code_match(lib_code, code)
            if score <= 0:
                continue
            bonus = name_affinity(module["name"], item["rel"]) + roman_bonus(code, item["rel"]) \
                + (6 if structural_target((module.get("category") or "") + (module.get("name") or "")) and
                       structural_target((module.get("category") or "") + (module.get("name") or "")) in item["rel"] else 0)
            candidates.append((score + bonus, score, item["rel"], lib_code))
    if candidates:
        candidates.sort(key=lambda x: (-x[0], -x[1], -len(norm_code(x[3])), len(x[2])))
        _, score, rel, lib_code = candidates[0]
        basis = {100: "编号精确", 90: "编号+图号序列", 85: "编号变体", 78: "目录编号更细（前缀）",
                 76: "编号类型/罗马序对齐"}.get(score, "编号匹配")
        return rel, f"{basis}（{lib_code}）"
    # 章节/类别推断（多图合订本）
    fallback = ""
    if cat_vol == "第四册":
        text = (module.get("category") or "") + (module.get("name") or "")
        if "-JC-" in norm_code(code) or "基础" in text:
            fallback = "基础部分"
        elif "-BJ-" in norm_code(code) or "加工" in text:
            fallback = "部件加工"
        elif "安健环" in text:
            fallback = "安健环"
        elif "馈线自动化" in text:
            fallback = "馈线自动化"
        elif "综合监测" in text:
            fallback = "综合监测"
        elif "-JD-" in norm_code(code) or "机电" in text:
            fallback = "机电部分"
        else:
            fallback = "杆塔部分"
    elif cat_vol == "第六册":
        if "光纤" in (module.get("category") or "") + (module.get("name") or "") or "GXL" in norm_code(code):
            fallback = "配网光纤通信"
    elif cat_vol == "第七册" and "智能化" in (module.get("category") or ""):
        fallback = "低压线路电缆模块" if "0.4D" in norm_code(code) else "低压线路架空模块"
    for item in files:
        if cat_vol and item["vol"] != cat_vol:
            continue
        if fallback and fallback in item["rel"]:
            return item["rel"], "按册/篇/章节推断"
    target = structural_target((module.get("category") or "") + (module.get("name") or ""))
    if target:
        for item in files:
            if cat_vol and item["vol"] != cat_vol:
                continue
            if target in item["rel"]:
                return item["rel"], "按模块类别推断"
    return "", "未定位"


# ---------------------------------------------------------------------------
# DWG 编号提取（无缓存时）
# ---------------------------------------------------------------------------


def find_oda() -> str | None:
    for path in ODA_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def extract_codes_via_oda(dwg_path: str, oda: str) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="dianyuan-") as tmp:
        src = Path(tmp) / "src"
        out = Path(tmp) / "out"
        src.mkdir()
        out.mkdir()
        shutil_path = src / Path(dwg_path).name
        try:
            import shutil
            shutil.copy2(dwg_path, shutil_path)
            import subprocess
            subprocess.run([oda, str(src), str(out), "ACAD2018", "DXF", "0", "1", "*.dwg"],
                           capture_output=True, timeout=600)
        except Exception:
            return []
        dxf_files = list(out.glob("*.dxf"))
        if not dxf_files:
            return []
        data = dxf_files[0].read_bytes()
        codes = set()
        for match in CSG_RE.findall(data.decode("utf-8", "ignore")):
            code = norm_code(match)
            if code.startswith("CSG"):
                codes.add(code)
        return sorted(codes)


def load_codes(library: str, cache_path: str | None, cache_out: str | None) -> dict[str, list[str]]:
    files = sorted(str(Path(p).relative_to(library)) for p in Path(library).rglob("*.dwg")) + \
            sorted(str(Path(p).relative_to(library)) for p in Path(library).rglob("*.DWG"))
    cache: dict[str, list[str]] = {}
    if cache_path and Path(cache_path).exists():
        raw = json.loads(Path(cache_path).read_text(encoding="utf-8"))
        for key, value in raw.items():
            if key.startswith("lib::"):
                cache[key[5:]] = list(value.get("codes") or [])
    missing = [rel for rel in files if not cache.get(rel)]
    if missing:
        oda = find_oda()
        if not oda:
            raise SystemExit(f"缺少 {len(missing)} 个 DWG 的编号缓存，且未找到 ODA File Converter；"
                             f"请提供 --codes-cache 或安装 ODA。")
        print(f"需要 ODA 提取编号的 DWG：{len(missing)} 个（可能耗时数分钟）…")
        for index, rel in enumerate(missing, 1):
            codes = extract_codes_via_oda(str(Path(library) / rel), oda)
            cache[rel] = codes
            print(f"  [{index}/{len(missing)}] {rel}：{len(codes)} 个编号")
    if cache_out:
        payload = {f"lib::{rel}": {"codes": values} for rel, values in cache.items()}
        Path(cache_out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return cache


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def split_volume_chapter(volume_text: str) -> tuple[str, str]:
    text = (volume_text or "").strip()
    match = re.match(r"^(第[一二三四五六七八九十]+册)\s*(.*)$", text)
    if not match:
        return text, ""
    book, rest = match.group(1), match.group(2).strip()
    chapter_match = re.search(r"(第[一二三四五六七八九十]+篇.*)$", rest)
    chapter = chapter_match.group(1).strip() if chapter_match else rest
    return book, chapter


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="从本机南网 V3.1 典设库生成可导入的模块目录 CSV")
    parser.add_argument("--catalog", required=True, help="附件6-1 模块汇总表 xlsx 路径")
    parser.add_argument("--library", required=True, help="典设库根目录（7 册文件夹所在目录）")
    parser.add_argument("--out", required=True, help="输出 CSV 路径")
    parser.add_argument("--codes-cache", default="", help="DWG 编号缓存 JSON（可选，缺省则用 ODA 提取）")
    parser.add_argument("--codes-cache-out", default="", help="更新后的编号缓存输出路径（可选）")
    parser.add_argument("--all-scope", action="store_true", help="同时输出第八/十册等不在本机库的模块")
    args = parser.parse_args(argv)

    library = Path(args.library)
    if not library.is_dir():
        raise SystemExit(f"典设库目录不存在：{library}")
    modules = typdesign.parse_catalog_file(args.catalog, Path(args.catalog).name)
    if not modules:
        raise SystemExit("未从附件6-1 解析到模块（请检查表头/V3.1 模块汇总表）")
    print(f"目录模块：{len(modules)} 个（来自 {Path(args.catalog).name}）")
    codes = load_codes(str(library), args.codes_cache or None, args.codes_cache_out or None)
    files = []
    for rel in codes:
        files.append({"rel": rel, "vol": volume_of(rel)})

    rows = [["序号", "卷册", "篇", "类别", "模块编号", "模块名称", "模块单位", "图纸文件", "匹配依据"]]
    matched = 0
    for module in modules:
        book, chapter = split_volume_chapter(module["volume"])
        if not in_scope(module["volume"]) and not args.all_scope:
            continue
        rel, basis = best_file_for_module(module, files, codes)
        if rel:
            matched += 1
        rows.append([len(rows), book, chapter or module["chapter"], module["category"],
                     module["code"], module["name"], module["unit"], rel.replace(os.sep, "/"), basis])

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8-sig") as handle:
        csv.writer(handle).writerows(rows)
    print(f"输出：{out_path}（{len(rows) - 1} 个模块，其中 {matched} 个定位到图纸文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
