#!/usr/bin/env bash
# 设计图纸→建模→算量→计价 工作流原型 · Linux/macOS 启动脚本
set -e
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"

echo "=============================================================="
echo " 设计图纸 -> 建模 -> 算量 -> 计价  工作流原型"
echo " 访问地址: http://127.0.0.1:8091"
echo " 演示账号: admin/admin123  zaojia/123456  fuhe/123456"
echo "=============================================================="
exec "$PYTHON" -m uvicorn app:app --host 127.0.0.1 --port 8091
