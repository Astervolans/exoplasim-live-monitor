#!/usr/bin/env bash
# launch_collector.sh — 由 monitor/start.ps1 复制到 /opt/exoplasim/run_collector.sh 后执行。
#
# 关键点：collector 必须**前台**运行（exec），让调用它的 wsl.exe 进程一直活着。
#   - 本机 WSL 的实测行为：`wsl.exe <cmd>` 退出时，该会话里 setsid/nohup 的后台子进程
#     会被一起回收（/tmp 是 tmpfs，连日志都会被清掉）。所以后台化不可靠。
#   - 由 Windows 侧用 Start-Process 起一个 **前台** wsl.exe，collector 就随它存活；
#     停止时 pkill 掉 collector，wsl.exe 自然退出。
#
# 用法： bash run_collector.sh [port] [extra args...]
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF=/opt/exoplasim/.monitor_dir
if [ -z "${MONITOR_DIR:-}" ] && [ -f "$CONF" ]; then
  MONITOR_DIR="$(head -1 "$CONF")"
fi
ROOT="${MONITOR_DIR:-$HERE}"
if [ ! -f "$ROOT/collector/server.py" ]; then
  echo "monitor root not found: $ROOT (set MONITOR_DIR or $CONF)" >&2
  exit 2
fi

PORT="${1:-8765}"
shift || true
LOG_DIR="${MONITOR_LOG_DIR:-/opt/exoplasim/logs}"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG="${MONITOR_LOG:-$LOG_DIR/monitor_collector.log}"

export PATH=/opt/exoplasim/env/bin:${PATH}
export OMPI_ALLOW_RUN_AS_ROOT=1
export OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
export MPLCONFIGDIR=/opt/exoplasim/.mplconfig
export PYTHONUNBUFFERED=1

# 同一端口只留一个实例（注意别把自己的启动命令匹配进去）
if command -v pkill >/dev/null 2>&1; then
  pkill -f "[c]ollector/server.py --port ${PORT}" 2>/dev/null || true
  sleep 0.3
fi

echo "=== $(date '+%F %T') starting collector on port $PORT (root=$ROOT) ===" >> "$LOG"
exec /opt/exoplasim/env/bin/python "$ROOT/collector/server.py" \
  --port "$PORT" --auto --web "$ROOT/web" "$@" >> "$LOG" 2>&1
