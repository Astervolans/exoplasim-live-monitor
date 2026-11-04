#!/usr/bin/env bash
# monitor/start.sh — 在 WSL 内启动 ExoPlaSim 实时只读监控 collector。
#
# 用法（Windows 侧）:
#   wsl -d Ubuntu-24.04 -u root -- bash "/mnt/<drive>/<repo>/monitor/start.sh"
#   wsl -d Ubuntu-24.04 -u root -- bash "/mnt/<drive>/<repo>/monitor/start.sh" --port 8765
#
# 之后浏览器打开 http://127.0.0.1:8765/ 。
#
# 说明：
# * 必须用 /opt/exoplasim/env/bin/python：风场重建依赖该 venv 里编译好的 exoplasim.pyfft；
# * collector 只读运行目录，不需要 MPI，但保留 OMPI 环境变量以防将来在同一个 shell 里
#   顺手起模型（root 下 mpiexec 默认拒绝启动）；
# * 其余参数原样透传给 collector（--no-physfilter / --workdir / --outdir / --once ...）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=/opt/exoplasim/env/bin/python

export PATH=/opt/exoplasim/env/bin:${PATH}
export OMPI_ALLOW_RUN_AS_ROOT=1
export OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1
export MPLCONFIGDIR=/opt/exoplasim/.mplconfig
export PYTHONUNBUFFERED=1

if [ ! -x "$PY" ]; then
  echo "start.sh: 找不到 $PY（venv 是否已装？）" >&2
  exit 1
fi

exec "$PY" "$HERE/collector/server.py" \
  --port 8765 \
  --auto \
  --web "$HERE/web" \
  "$@"
