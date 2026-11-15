# win-auto-probe

只读 Windows 自动化探针：列出目标进程的窗口、导出子控件树和窗口截图。它不点击、不输入、不注入、不修改目标，只用于在授权测试环境里判断“UI 自动化能否覆盖”，再决定是否需要更低层的集成。

## 运行

```powershell
cd tools/win-auto-probe
python -m pip install -r requirements.txt
python probe.py --list
python probe.py --process BoweiCost.exe --out probe-out
python probe.py --title "博微" --include-hidden --out probe-out
```

输出：

- `windows-*.json`：窗口列表（PID、进程名、标题、类名、位置）。
- `*-N.json`：目标窗口的子控件（类名、文本、控件ID、位置）。
- `*-N.png`：窗口截图（仅在有交互桌面时）。

## 测试记录

在 Notepad 上验证通过：可列窗口、导出 13—15 个子控件和截图。不同软件（Win32/Delphi/WinForms/WPF/UIA）暴露的控件数量不同；控件树为空或很少，说明可能需要 UI Automation/图像识别/注入等更低层方案。

## 边界

- 仅用于**有授权的测试环境**和测试数据；不要对生产库直接试写操作。
- 不包含 DLL 注入代码，也不绕过授权、加密、反调试或安全控制。
- 注入 POC 的前置条件、路线对比和测试步骤见 `docs/settlement/bowei-automation-assessment.md`。
