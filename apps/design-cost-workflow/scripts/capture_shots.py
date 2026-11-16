#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""capture_shots.py — 抓取 After（原型应用）截图，1440px 宽，供 PR/文档使用。

用法（在仓库根或 apps/design-cost-workflow 下运行；Windows 如 python 为应用商店别名，请改用完整解释器路径）：
  python ^
      apps/design-cost-workflow/scripts/capture_shots.py [--base-url URL] [--only 01,02] ...

前置条件：应用已在 http://127.0.0.1:8091 运行（WORKFLOW_DEMO=1，可用 ?as=admin 演示身份）。
每张图一次 Edge 调用：
  msedge --headless=new --disable-gpu --hide-scrollbars --window-size=1440,H ^
         --screenshot=docs/shots/design-cost/after-xx.png --virtual-time-budget=4000 URL
截图后用 Pillow 裁掉底部空白（页面背景色）；若内容超出视口则自动增大高度重试。
注意：本机由 Python 启动的 Edge 只能写工作区内路径，因此截图直接写入 docs/shots/design-cost/，
本脚本不产生任何临时文件，也不修改应用数据。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="抓取 After（原型应用）截图，1440px 宽")
    parser.add_argument("--base-url", default=BASE_URL, help="应用地址（默认 http://127.0.0.1:8091）")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR, help="PNG 输出目录")
    parser.add_argument("--only", default="", help="只抓指定编号，如 01,02")
    args = parser.parse_args(argv)
    if not Path(EDGE).exists():
        print(f"[错误] 未找到 Edge：{EDGE}")
        return 2
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    only = {x.strip().lstrip("0") or "0" for x in args.only.split(",") if x.strip()}
    shots = [(n, p, h) for n, p, h in SHOTS if not only or n.split("-")[1].lstrip("0") in only]
    if not shots:
        print(f"[错误] --only={args.only} 未匹配任何截图")
        return 2

    print("=" * 78)
    print(f"After 截图抓取 · {args.base_url}")
    print(f"输出目录：{out_dir}")
    failures = []
    for i, (name, path, height) in enumerate(shots, 1):
        url = args.base_url.rstrip("/") + path
        try:
            status = preflight(args.base_url, path)
            if status != 200:
                raise RuntimeError(f"页面返回 HTTP {status}")
            info = capture(url, out_dir / name, height)
            mark = "（可能截断）" if info["cut"] else ""
            print(f"[{i}/{len(shots)}] {name}  {WIDTH}×{info['height']}px  "
                  f"{info['bytes']:,} bytes  {mark}")
        except Exception as exc:  # noqa: BLE001 - 单张失败不影响其余
            failures.append((name, str(exc)))
            print(f"[{i}/{len(shots)}] {name}  失败：{exc}")
    print("-" * 78)
    if failures:
        print(f"[完成] {len(shots) - len(failures)}/{len(shots)} 张成功，失败 {len(failures)} 张")
        return 1
    print(f"[完成] {len(shots)} 张 PNG 已写入：{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
