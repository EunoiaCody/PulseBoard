#!/usr/bin/env bash
# 一键启动脚本：创建虚拟环境、安装依赖、启动服务
set -euo pipefail

cd "$(dirname "$0")"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8080}"
VENV_DIR="${VENV_DIR:-.venv}"

if [ ! -d "$VENV_DIR" ]; then
  echo "==> 创建虚拟环境 $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "==> 安装依赖"
pip install --upgrade pip >/dev/null
pip install -r requirements.txt

echo "==> 启动服务： http://${HOST}:${PORT}"
exec uvicorn app:app --host "$HOST" --port "$PORT"
