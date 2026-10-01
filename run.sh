#!/usr/bin/env bash
# 一键启动脚本：创建虚拟环境、安装依赖、启动服务
#
#   ./run.sh                    # 监听地址与端口取 config.toml 的 [server]
#   PORT=9000 ./run.sh          # 临时换端口（环境变量优先于配置）
#   HOST=127.0.0.1 ./run.sh     # 只监听本机
#   VENV_DIR=.venv PYTHON_BIN=python3.12 ./run.sh
set -euo pipefail

cd "$(dirname "$0")"

VENV_DIR="${VENV_DIR:-.venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

# ---------------------------------------------------------------------------
# 虚拟环境
#   注意：不能用“目录是否存在”来判断。venv 创建失败（例如 Debian/Ubuntu 没装
#   python3-venv）会留下一个没有 bin/activate 的空壳目录，这样之后再跑就会一直
#   失败，所以这里直接检查 venv 里的 python 能否真正启动。
# ---------------------------------------------------------------------------
venv_ok() {
  [ -x "$VENV_DIR/bin/python" ] && "$VENV_DIR/bin/python" -c "" >/dev/null 2>&1
}

if ! venv_ok; then
  if [ -d "$VENV_DIR" ]; then
    echo "==> 检测到不可用的虚拟环境 $VENV_DIR（上次可能创建失败），重新创建"
    rm -rf "$VENV_DIR"
  else
    echo "==> 创建虚拟环境 $VENV_DIR"
  fi
  if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
    cat >&2 <<'EOF'

创建虚拟环境失败：当前 Python 缺少 venv 模块。多数发行版需要单独安装：
  Debian / Ubuntu : sudo apt install python3-venv
  Fedora / RHEL   : sudo dnf install python3
  Arch            : sudo pacman -S python          （已自带）
  Alpine          : sudo apk add python3 py3-pip
装好后再执行一次 ./run.sh 即可（脚本会自动重建）。
EOF
    exit 1
  fi
fi

VENV_PY="$VENV_DIR/bin/python"

# ---------------------------------------------------------------------------
# 依赖（已装好就跳过，避免每次启动都跑一遍 pip）
# ---------------------------------------------------------------------------
if ! "$VENV_PY" -c "import fastapi, uvicorn, psutil, httpx" >/dev/null 2>&1; then
  echo "==> 安装依赖"
  "$VENV_PY" -m pip install --upgrade pip >/dev/null
  "$VENV_PY" -m pip install -r requirements.txt
fi

# 监听地址与端口默认取自 config.toml 的 [server]，环境变量 HOST / PORT 可临时覆盖。
# 这里故意不传 --host/--port，否则会把 config.toml 里的 [server] 默默覆盖掉。
echo "==> 启动服务（host/port 取自 config.toml [server]，可用 HOST/PORT 覆盖）"
exec "$VENV_PY" app.py
