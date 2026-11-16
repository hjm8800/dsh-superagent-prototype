# 设计图纸→建模→算量→计价 工作流原型 · Windows 启动脚本
#
# 用法：
#   .\start.ps1
#
# 解释器查找顺序：环境变量 PYTHON（完整路径）→ py 启动器 → PATH 中的 python。
# 若系统 python 是应用商店别名（WindowsApps），请先设置：
#   $env:PYTHON = "C:\path\to\python.exe"
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$candidates = @()
if ($env:PYTHON) { $candidates += $env:PYTHON }
$pyCmd = Get-Command py -ErrorAction SilentlyContinue
if ($pyCmd) { $candidates += $pyCmd.Source }
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCmd) { $candidates += $pythonCmd.Source }

$py = $null
$pyPrefix = @()
foreach ($candidate in $candidates) {
    if (-not $candidate) { continue }
    if ($candidate -like "*WindowsApps*") { continue }
    if ($candidate -like "*\py.exe") {
        $py = $candidate
        $pyPrefix = @("-3")
        break
    }
    if (Test-Path $candidate) {
        $py = $candidate
        break
    }
}

if (-not $py) {
    Write-Error "未找到可用的 Python 解释器。请安装 Python 3.10+，或设置环境变量 PYTHON 指向 python.exe 后重试。"
    exit 1
}

Write-Host "=============================================================="
Write-Host " 设计图纸 -> 建模 -> 算量 -> 计价  工作流原型"
Write-Host " 访问地址: http://127.0.0.1:8091"
Write-Host " 演示账号: admin/admin123  zaojia/123456  fuhe/123456"
Write-Host " 解释器  : $py $($pyPrefix -join ' ')"
Write-Host "=============================================================="
& $py @pyPrefix -m uvicorn app:app --host 127.0.0.1 --port 8091
