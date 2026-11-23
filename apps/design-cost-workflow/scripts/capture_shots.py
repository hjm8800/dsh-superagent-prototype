#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""capture_shots.py — 抓取 After（原型应用）截图，1440px 宽，供 PR/文档使用。

用法（在仓库根或 apps/design-cost-workflow 下运行；Windows 如 python 为应用商店别名，请改用完整解释器路径）：
  python ^
      apps/design-cost-workflow/scripts/capture_shots.py [--base-url URL] [--only v3|v2|01,02|v3-04]

  --only 支持：
    "v3"            只抓第三轮新增的 v3-01…v3-05（开始工作流/新建项目/下一步引导）
    "v2"            只抓第二轮新增的 v2-01…v2-06（典型设计匹配）
    "01,02"         只抓 after-01/after-02（第一轮页面截图）
    "v3-04,v2-05"   只抓指定截图（v3-04 会先 POST /project/new 创建“演示新建项目-时间戳”再截跳转页；
                    v2-05 会先执行项目C 的“执行匹配”演示动作）
    不传则抓全部 after-01…09 + v2-01…06 + v3-01…05

前置条件：应用已在 http://127.0.0.1:8091 运行（WORKFLOW_DEMO=1，可用 ?as=admin 演示身份），
并已包含对应的 v2/v3 能力（/project/{pid}/typdesign、/project/new 路由）。
每张图一次 Edge 调用：
  msedge --headless=new --disable-gpu --hide-scrollbars --window-size=1440,H ^
         --screenshot=docs/shots/design-cost/*.png --virtual-time-budget=4000 URL
截图后用 Pillow 裁掉底部空白（页面背景色）；若内容超出视口则自动增大高度重试。
注意：本机由 Python 启动的 Edge 只能写工作区内路径，因此截图直接写入 docs/shots/design-cost/，
本脚本不产生任何临时/profile 文件；演示动作（执行匹配/新建项目）只改演示数据，属契约允许。
基线截图（v3-before-*.png 等）不要覆盖。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:  # 中文输出在 GBK 控制台/重定向时都尽量正常
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
APP = Path(__file__).resolve().parents[1]          # apps/design-cost-workflow
ROOT = APP.parents[1]                               # 仓库根
OUT_DIR = ROOT / "docs" / "shots" / "design-cost"
WIDTH = 1440
BASE_URL = "http://127.0.0.1:8091"

# (文件名, 路径, 初始视口高度)  —— 高度足够容纳整页，捕获后自动裁剪空白
SHOTS = [
    ("after-01-login.png", "/login", 1000),
    ("after-02-dashboard.png", "/?as=admin", 1600),
    ("after-03-project-a-workflow.png", "/project/1?as=admin", 1800),
    ("after-04-drawings-c.png", "/project/3/drawings?as=admin", 1800),
    ("after-05-model-a.png", "/project/1/model?as=admin", 2600),
    ("after-06-quantities-a.png", "/project/1/quantities?as=admin", 2800),
    ("after-07-pricing-a.png", "/project/1/pricing?as=admin", 7800),
    ("after-08-compare-b.png", "/project/2/compare?as=admin", 2600),
    ("after-09-demo.png", "/demo?as=admin", 2000),
]

# v2 第二轮新增（典型设计匹配，contract2 §7）：(文件名, 路径, 初始高度, 前置演示动作)
# 顺序有意为之：v2-05 之前才执行“项目C 执行匹配”，使 v2-01 保持干净 seed 的未匹配状态。
V2_SHOTS = [
    ("v2-01-drawings-c-typdesign.png", "/project/3/drawings?as=admin", 1900, None),
    ("v2-02-project-a-typdesign.png", "/project/1?as=admin", 1900, None),
    ("v2-03-typdesign-a.png", "/project/1/typdesign?as=admin", 2800, None),
    ("v2-04-typdesign-b.png", "/project/2/typdesign?as=admin", 2800, None),
    ("v2-05-typdesign-c-after-match.png", "/project/3/typdesign?as=admin", 2800, "match_c"),
    ("v2-06-model-a-typdesign.png", "/project/1/model?as=admin", 3000, None),
]

# v3 第三轮新增（工作流开始入口/新建项目/下一步引导，contract3 §5）：
# (文件名, 路径, 初始高度, 前置演示动作)；v3-04 路径为 None，由 create_project 动作返回跳转页
V3_SHOTS = [
    ("v3-01-dashboard-start.png", "/?as=admin", 2000, None),
    ("v3-02-project-c-next-step.png", "/project/3?as=admin", 1900, None),
    ("v3-03-new-project-form.png", "/project/new?as=admin", 1500, None),
    ("v3-04-new-project-created.png", None, 2000, "create_project"),
    ("v3-05-demo-start-hint.png", "/demo?as=admin", 2000, None),
]

V4_SHOTS = [
    ("v4-01-new-project-form-code.png", "/project/new?as=admin", 1500, None),
    ("v4-02-new-project-created-with-code.png", None, 1800, "create_project_with_code"),
    ("v4-03-drawings-upload-delete.png", "/project/3/drawings?as=admin", 2200, None),
]


def wait_file(path: Path, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    last = -1
    stable = 0
    while time.time() < deadline:
        if path.exists():
            size = path.stat().st_size
            stable = stable + 1 if size > 0 and size == last else 0
            if stable >= 2:
                return True
            last = size
        time.sleep(0.15)
    return path.exists() and path.stat().st_size > 0


def trim_bottom(path: Path, pad: int = 26) -> int:
    """裁掉底部页面背景空白；返回 -1 表示内容可能被截断，否则返回最终高度。

    逐行从下往上判断“空白行”：该行横向颜色基本一致（容忍渐变背景），且与下方相邻行
    颜色平滑过渡。登录页等渐变背景页面同样适用。
    """
    from PIL import Image

    with Image.open(path) as raw:
        image = raw.convert("RGB")
    width, height = image.size
    px = image.load()
    xs = list(range(0, width, 4))

    def row_blank(y: int) -> bool:
        samples = [px[x, y] for x in xs]
        if any(max(c[k] for c in samples) - min(c[k] for c in samples) > 26 for k in range(3)):
            return False
        if y + 4 < height:
            below = [px[x, y + 4] for x in xs]
            mean_a = [sum(c[k] for c in samples) / len(samples) for k in range(3)]
            mean_b = [sum(c[k] for c in below) / len(below) for k in range(3)]
            if sum(abs(mean_a[k] - mean_b[k]) for k in range(3)) > 20:
                return False
        return True

    last = 0
    for y in range(height - 1, -1, -1):
        if not row_blank(y):
            last = y
            break
    if last == 0:
        image.close()
        raise RuntimeError("截图为空白（页面未渲染）")
    cut = last >= height - 6
    final_height = min(height, last + 1 + pad)
    image.crop((0, 0, width, final_height)).save(path, format="PNG", optimize=True)
    image.close()
    return -1 if cut else final_height


def preflight(base_url: str, path: str) -> int:
    """用 httpx 预检页面 HTTP 状态，避免截到错误页。"""
    import httpx

    with httpx.Client(base_url=base_url, follow_redirects=True, timeout=15) as client:
        response = client.get(path)
        return response.status_code


def capture(url: str, out_png: Path, height: int) -> dict:
    for attempt in range(3):
        if out_png.exists():
            out_png.unlink()
        cmd = [EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
               f"--window-size={WIDTH},{height}", f"--screenshot={out_png}",
               "--virtual-time-budget=4000", url]
        subprocess.run(cmd, capture_output=True, timeout=120)
        if not wait_file(out_png):
            if attempt < 2:
                print(f"      Edge 未产出截图，重试（{height}px）…")
                time.sleep(0.4)
                continue
            raise RuntimeError(f"Edge 截图超时：{out_png.name}")
        result = trim_bottom(out_png)
        if result < 0:
            if attempt < 2:
                height += 900
                print(f"      页面内容超出视口，扩展到 {height}px 重试…")
                continue
            print(f"      [警告] {out_png.name} 可能底部截断，请人工确认")
            return {"height": _height(out_png), "bytes": out_png.stat().st_size, "cut": True}
        return {"height": result, "bytes": out_png.stat().st_size, "cut": False}
    raise RuntimeError(f"截图失败：{out_png.name}")


def _height(path: Path) -> int:
    from PIL import Image

    with Image.open(path) as image:
        return image.size[1]


def run_prep(action, base_url: str):
    """执行截图前需要的演示动作；返回可覆盖的截图路径（None=沿用截图项原路径）。"""
    import httpx

    if not action:
        return None
    base = base_url.rstrip("/")
    if action == "match_c":
        url = base + "/project/3/typdesign/match?as=zaojia"
        print("      [演示动作] 项目C：执行典型设计匹配（POST /project/3/typdesign/match）…")
        with httpx.Client(follow_redirects=True, timeout=25) as client:
            response = client.post(url)
        if response.status_code != 200:
            raise RuntimeError(f"执行匹配失败：HTTP {response.status_code}")
        if "匹配" not in response.text:
            raise RuntimeError("执行匹配后页面未出现匹配结果")
        print("      [演示动作] 执行匹配完成")
        return None
    if action == "create_project":
        name = f"演示新建项目-{datetime.now().strftime('%H%M%S')}"
        print(f"      [演示动作] 新建项目「{name}」（GET+POST /project/new?as=zaojia）…")
        with httpx.Client(base_url=base, follow_redirects=True, timeout=25) as client:
            form = client.get("/project/new?as=zaojia")
            if form.status_code != 200 or "新建项目" not in form.text:
                raise RuntimeError(f"新建项目表单页异常：HTTP {form.status_code}")
            response = client.post("/project/new?as=zaojia", data={
                "name": name, "ptype": "业扩配套", "location": "某供电所",
                "note": "v3 证据截图演示项目（QA 自动创建）",
            })
        match = re.search(r"/project/(\d+)/drawings", str(response.url))
        if response.status_code != 200 or not match:
            raise RuntimeError(f"新建项目失败：HTTP {response.status_code}，URL {response.url}")
        project_id = match.group(1)
        print(f"      [演示动作] 已创建项目 #{project_id}，跳转图纸解析页")
        return f"/project/{project_id}/drawings?as=admin"
    if action == "create_project_with_code":
        code = f"DEMO-CODE-{datetime.now().strftime('%H%M%S')}"
        name = f"演示编码项目-{datetime.now().strftime('%H%M%S')}"
        print(f"      [演示动作] 新建项目「{name}」（编码 {code}）…")
        with httpx.Client(base_url=base, follow_redirects=True, timeout=25) as client:
            response = client.post("/project/new?as=zaojia", data={
                "name": name, "code": code, "ptype": "业扩配套",
                "location": "某供电所", "note": "v4 证据截图演示项目（脚本自动创建）",
            })
        match = re.search(r"/project/(\d+)/drawings", str(response.url))
        if response.status_code != 200 or not match:
            raise RuntimeError(f"新建项目失败：HTTP {response.status_code}，URL {response.url}")
        project_id = match.group(1)
        print(f"      [演示动作] 已创建项目 #{project_id}（编码 {code}）")
        return f"/project/{project_id}?as=admin"
    raise RuntimeError(f"未知的演示动作：{action}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="抓取 After（原型应用）截图，1440px 宽")
    parser.add_argument("--base-url", default=BASE_URL, help="应用地址（默认 http://127.0.0.1:8091）")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR, help="PNG 输出目录")
    parser.add_argument(
        "--only", default="",
        help='只抓指定截图："v4"=v4-01…v4-03，"v3"=v3-01…v3-05，"v2"=v2-01…v2-06，'
             '"01,02"=第一轮，"v3-04,v2-05"=指定项')
    args = parser.parse_args(argv)
    if not Path(EDGE).exists():
        print(f"[错误] 未找到 Edge：{EDGE}")
        return 2
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    tokens = [x.strip().lower() for x in args.only.split(",") if x.strip()]

    def after_selected(name: str) -> bool:
        if not tokens:
            return True
        code = name.split("-")[1].lstrip("0") or "0"
        return any((t.replace("after-", "").lstrip("0") or "0") == code for t in tokens)

    def series_selected(prefix: str, name: str) -> bool:
        """prefix 为 'v2' 或 'v3'；支持 --only v3 / --only v3-04,v3-05。"""
        if not tokens:
            return True
        code = name.split("-")[1].lstrip("0") or "0"
        for token in tokens:
            if token == prefix:
                return True
            if token.startswith(prefix + "-") and (token[len(prefix) + 1:].lstrip("0") or "0") == code:
                return True
        return False

    # (类型, 文件名, 路径, 初始高度, 前置动作)
    shots = [("after", n, p, h, None) for n, p, h in SHOTS if after_selected(n)]
    shots += [("v2", n, p, h, a) for n, p, h, a in V2_SHOTS if series_selected("v2", n)]
    shots += [("v3", n, p, h, a) for n, p, h, a in V3_SHOTS if series_selected("v3", n)]
    shots += [("v4", n, p, h, a) for n, p, h, a in V4_SHOTS if series_selected("v4", n)]
    if not shots:
        print(f"[错误] --only={args.only} 未匹配任何截图")
        return 2
    counts = {kind: sum(1 for k, *_ in shots if k == kind) for kind in ("after", "v2", "v3", "v4")}

    print("=" * 78)
    print(f"After 截图抓取 · {args.base_url}")
    print(f"输出目录：{out_dir}")
    print(f"截图数量：{len(shots)}（after {counts['after']} / v2 {counts['v2']} / v3 {counts['v3']} / v4 {counts['v4']}）")
    failures = []
    for i, (kind, name, path, height, action) in enumerate(shots, 1):
        tag = f"[{i}/{len(shots)}]"
        try:
            path = run_prep(action, args.base_url) or path
            if not path:
                raise RuntimeError("未确定截图路径")
            url = args.base_url.rstrip("/") + path
            status = preflight(args.base_url, path)
            if status != 200:
                raise RuntimeError(f"页面返回 HTTP {status}")
            info = capture(url, out_dir / name, height)
            mark = "（可能截断）" if info["cut"] else ""
            print(f"{tag} {name}  {WIDTH}×{info['height']}px  {info['bytes']:,} bytes  {mark}")
        except Exception as exc:  # noqa: BLE001 - 单张失败不影响其余
            failures.append((name, str(exc)))
            print(f"{tag} {name}  失败：{exc}")
    print("-" * 78)
    if failures:
        print(f"[完成] {len(shots) - len(failures)}/{len(shots)} 张成功，失败 {len(failures)} 张")
        return 1
    print(f"[完成] {len(shots)} 张 PNG 已写入：{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
