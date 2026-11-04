#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_short.py — 6 个模拟月以内的短跑 harness（监控测试专用）。

为什么存在：预设 exo.ps1 只支持整年（mode=years）。做可视化快速迭代时，
一年 70 s 且 24 帧采样太密；本脚本把 *同一个 spec* 的 N_RUN_STEPS 按比例缩短到
   months/12 * steps_per_year
再走 ExoPlaSim 正常路径（configure → run → postprocess），因此产物
(MOST.00000.npz / snapshots/MOST_SNAP.00000.npz / DIAG / REST) 与整年跑完全同构，
监控端无需任何特判。**不改模型物理参数，只改积分步数**。

在 WSL 里运行（必须用 venv 解释器）:

  /opt/exoplasim/env/bin/python "<repo>/monitor/test/run_short.py" \
      --spec "<repo>/verify/pleiroma/batch1_T21_20yr/specs/pleiroma_continue.json" \
      --months 6 --outdir "<repo>/monitor/demo" --ncpus 8

输出：最后一行 `## SHORT_RUN ## {json}`，含 workdir / artifacts / 墙钟 / 计划步数。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys
import time

DEFAULT_TOPO = "/opt/exoplasim/topo"

# 与 exo.env.json 的 extraEnv 对齐：本机以 root 跑，OpenMPI 默认拒绝 root 启动 mpiexec。
os.environ.setdefault("OMPI_ALLOW_RUN_AS_ROOT", "1")
os.environ.setdefault("OMPI_ALLOW_RUN_AS_ROOT_CONFIRM", "1")
os.environ.setdefault("MPLCONFIGDIR", "/opt/exoplasim/.mplconfig")
os.environ.setdefault("PYTHONUNBUFFERED", "1")


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _read_namelist_value(path: str, key: str):
    try:
        with open(path, "r", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith(key):
                    rest = line[len(key):].strip()
                    if rest.startswith("="):
                        value = rest[1:].strip().replace(",", "")
                        return value
    except OSError:
        return None
    return None


def _set_namelist_value(path: str, key: str, value: str) -> None:
    """原位改写 &plasim_nl 里的整数键（保持行长，Fortran 固定格式安全）。"""
    with open(path, "r", errors="replace") as handle:
        lines = handle.readlines()
    changed = False
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(key):
            indent = line[: len(line) - len(stripped)]
            lines[index] = "%s%s = %s\n" % (indent, key, value)
            changed = True
            break
    if not changed:
        lines.append(" %s = %s\n" % (key, value))
    with open(path, "w") as handle:
        handle.writelines(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    parser.add_argument("--months", type=float, default=6.0)
    parser.add_argument("--outdir", default=None, help="artifacts 目录（Windows 或 WSL 路径均可）")
    parser.add_argument("--workdir", default=None)
    parser.add_argument("--ncpus", type=int, default=0)
    parser.add_argument("--modelname", default=None)
    parser.add_argument("--keep-raw", action="store_true")
    args = parser.parse_args(argv)

    months = float(args.months)
    if not (0 < months <= 12):
        print("months must be in (0, 12]")
        return 2

    import exoplasim as exo  # noqa: E402  (需要 WSL venv)

    spec_path = os.path.abspath(args.spec)
    with open(spec_path, "r", encoding="utf-8") as handle:
        spec = json.load(handle)

    modelname = args.modelname or spec.get("modelname") or "monitor"
    run_id = "%s_%s" % (_stamp(), modelname)
    workdir = args.workdir or os.path.join("/opt/exoplasim/runs", "monitor_" + run_id)
    if os.path.isdir(workdir):
        shutil.rmtree(workdir)
    os.makedirs(workdir, exist_ok=True)
    outdir = args.outdir or os.path.join("/opt/exoplasim/runs", "_monitor_artifacts", run_id)
    if outdir.startswith("/mnt/") and " " in outdir:
        pass  # WSL 侧可以处理空格；只有 ExoPlaSim 内部 cp 不行
    os.makedirs(outdir, exist_ok=True)

    cfg = dict(spec.get("configure") or {})
    ncpus = args.ncpus or int(spec.get("ncpus") or 8)
    print("[short] workdir  : %s" % workdir, flush=True)
    print("[short] outdir   : %s" % outdir, flush=True)
    print("[short] months   : %s" % months, flush=True)

    started = time.time()
    model = exo.Model(
        workdir=workdir,
        modelname=modelname,
        resolution=spec.get("resolution") or "T21",
        layers=int(spec.get("layers") or 10),
        ncpus=ncpus,
        outputtype=spec.get("outputtype") or ".npz",
    )
    model.configure(**cfg)

    # configure() 会 rm workdir/*.sra；额外的边界文件必须之后再注入（与 exo_run.py 一致）
    for source in spec.get("afterConfigure") or []:
        if not os.path.exists(source):
            source = os.path.join(DEFAULT_TOPO, os.path.basename(source))
        shutil.copy2(source, os.path.join(workdir, os.path.basename(source)))
        print("[short] injected: %s" % os.path.basename(source), flush=True)

    # 缩短积分步数：保持 timestep 不变，只截短“年”的长度
    namelist = os.path.join(workdir, "plasim_namelist")
    steps_per_year = int(float(_read_namelist_value(namelist, "N_RUN_STEPS") or 17280))
    nsteps = max(1, int(round(steps_per_year * months / 12.0)))
    _set_namelist_value(namelist, "N_RUN_STEPS", str(nsteps))
    _set_namelist_value(namelist, "N_RUN_YEARS", "1")
    _set_namelist_value(namelist, "N_RUN_MONTHS", "0")
    _set_namelist_value(namelist, "N_RUN_DAYS", "0")
    # 6 个月短跑仍要每 15 天一个快照；NSTPS 若超过总步数会只输出 1 帧，兜底缩小
    nstps = int(float(_read_namelist_value(namelist, "NSTPS") or 720))
    if nstps > nsteps:
        _set_namelist_value(namelist, "NSTPS", str(max(1, nsteps // 12)))
    model.exportcfg()
    print("[short] N_RUN_STEPS=%d (%.2f months of %d), NSTPS=%s"
          % (nsteps, months, steps_per_year, _read_namelist_value(namelist, "NSTPS")), flush=True)

    model.run(years=1, crashifbroken=True)
    elapsed = time.time() - started

    artifacts = []
    os.makedirs(outdir, exist_ok=True)
    candidates = []
    candidates += glob.glob(os.path.join(workdir, "MOST.00000.npz"))
    candidates += glob.glob(os.path.join(workdir, "MOST.00000_metadata.npz"))
    candidates += glob.glob(os.path.join(workdir, "snapshots", "MOST_SNAP.00000.npz"))
    candidates += glob.glob(os.path.join(workdir, "snapshots", "MOST_SNAP.00000_metadata.npz"))
    candidates += glob.glob(os.path.join(workdir, "MOST_DIAG.00000"))
    candidates += glob.glob(os.path.join(workdir, "MOST_REST.00000"))
    candidates += glob.glob(os.path.join(workdir, "*.cfg"))
    for path in candidates:
        base = os.path.basename(path)
        if base.startswith("MOST.00000_metadata"):
            target = os.path.join(outdir, modelname + "_metadata.npz")
        elif base == "MOST.00000.npz":
            target = os.path.join(outdir, modelname + ".npz")
        elif base.startswith("MOST_SNAP.00000_metadata"):
            target = os.path.join(outdir, modelname + "_snapshot_metadata.npz")
        elif base.startswith("MOST_SNAP.00000"):
            target = os.path.join(outdir, modelname + "_snapshot.npz")
        elif base.startswith("MOST_DIAG"):
            target = os.path.join(outdir, modelname + ".DIAG")
        elif base.startswith("MOST_REST"):
            target = os.path.join(outdir, modelname + "_restart")
        else:
            target = os.path.join(outdir, base)
        shutil.copy2(path, target)
        artifacts.append(target)
    shutil.copy2(spec_path, os.path.join(outdir, "spec.json"))

    summary = {
        "ok": True,
        "workdir": workdir,
        "outdir": outdir,
        "modelname": modelname,
        "months": months,
        "steps": nsteps,
        "wall_seconds": round(elapsed, 1),
        "artifacts": [os.path.basename(p) for p in artifacts],
    }
    print("## SHORT_RUN ## " + json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
