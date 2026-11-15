"""Read-only Windows automation probe (dependency-free Win32).

Lists top-level windows of a target process, dumps its child controls and takes a
screenshot. It never clicks, types, injects, or modifies the target. Use it to
inventory the UI of a licensed application before choosing an automation path.

Usage:
  python probe.py --list
  python probe.py --process BoweiCost.exe --out probe-out
  python probe.py --title "博微" --include-hidden --out probe-out
"""
from __future__ import annotations

import argparse
import ctypes
import json
import sys
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
EnumChildProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def text(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def rect(hwnd):
    r = wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return [r.left, r.top, r.right, r.bottom]
    return [0, 0, 0, 0]


def pid_of(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def process_name(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name
    finally:
        kernel32.CloseHandle(h)
    return ""


def windows(process: str = "", title: str = "", include_hidden: bool = False):
    out = []

    def cb(hwnd, _):
        t = text(hwnd)
        if not include_hidden and (not user32.IsWindowVisible(hwnd) or not t):
            return True
        pid = pid_of(hwnd)
        pname = process_name(pid)
        if process and process.lower() not in pname.lower():
            return True
        if title and title.lower() not in t.lower():
            return True
        out.append({
            "handle": int(hwnd),
            "title": t,
            "class_name": class_name(hwnd),
            "process_id": pid,
            "process_name": pname,
            "rectangle": rect(hwnd),
            "visible": bool(user32.IsWindowVisible(hwnd)),
        })
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


def controls(hwnd: int, max_items: int = 600):
    out = []

    def cb(h, _):
        if len(out) >= max_items:
            return False
        out.append({
            "handle": int(h),
            "class_name": class_name(h),
            "text": text(h),
            "control_id": int(user32.GetDlgCtrlID(h)),
            "rectangle": rect(h),
            "visible": bool(user32.IsWindowVisible(h)),
        })
        return True

    user32.EnumChildWindows(wintypes.HWND(hwnd), EnumChildProc(cb), 0)
    return out


def screenshot(hwnd: int, path: Path):
    try:
        from PIL import ImageGrab
        l, t, r, b = rect(hwnd)
        if r <= l or b <= t:
            return
        ImageGrab.grab(bbox=(l, t, r, b)).save(str(path))
    except Exception as e:
        print(f"截图失败: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list top-level windows")
    ap.add_argument("--process", default="", help="filter by executable name, e.g. BoweiCost.exe")
    ap.add_argument("--title", default="", help="filter by window title substring")
    ap.add_argument("--out", default="probe-out", help="output directory")
    ap.add_argument("--include-hidden", action="store_true", help="include hidden/untitled windows")
    args = ap.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    wins = windows(args.process, args.title, args.include_hidden)
    if args.list:
        p = outdir / f"windows-{stamp}.json"
        p.write_text(json.dumps(wins, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已导出 {p}，共 {len(wins)} 个窗口")
        for w in wins[:20]:
            print(f"  pid={w['process_id']} {w['process_name']} title={w['title']!r}")
        return 0 if wins else 2
    if not wins:
        print("未找到匹配窗口。先运行 --list，确认进程名/窗口标题。", file=sys.stderr)
        return 2
    for i, w in enumerate(wins):
        base = outdir / f"{stamp}-{i}"
        data = {"window": w, "controls": controls(w["handle"])}
        base.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        screenshot(w["handle"], base.with_suffix(".png"))
        print(f"已导出 {base}.json / .png；控件 {len(data['controls'])} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
