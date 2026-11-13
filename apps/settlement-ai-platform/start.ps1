param(
  [int]$Port = 8090,
  [switch]$Demo
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv")) {
  python -m venv .venv
}
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "data") | Out-Null
$env:PLATFORM_DATA_DIR = Join-Path $PSScriptRoot "data"
if ($Demo) { $env:PLATFORM_DEMO = "1" } else { $env:PLATFORM_DEMO = "0" }
if (-not $env:PLATFORM_SECRET) {
  $env:PLATFORM_SECRET = [guid]::NewGuid().ToString("N")
}
Write-Host "结算AI工作台启动：http://127.0.0.1:$Port"
Write-Host "首次登录：admin / admin123，登录后请立即修改密码并创建业务账号。"
& ".\.venv\Scripts\python.exe" -m uvicorn app:app --host 0.0.0.0 --port $Port
