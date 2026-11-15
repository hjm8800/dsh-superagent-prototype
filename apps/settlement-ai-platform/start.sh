#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt
mkdir -p data
export PLATFORM_DATA_DIR="${PLATFORM_DATA_DIR:-$(pwd)/data}"
export PLATFORM_DEMO="${PLATFORM_DEMO:-0}"
export PLATFORM_SECRET="${PLATFORM_SECRET:-$(python3 -c 'import secrets; print(secrets.token_hex(24))')}"
PORT="${PORT:-8090}"
echo "结算AI工作台启动：http://127.0.0.1:${PORT}"
echo "首次登录：admin / admin123，登录后请立即修改密码并创建业务账号。"
exec python3 -m uvicorn app:app --host 0.0.0.0 --port "${PORT}"
