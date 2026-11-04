#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""collector 自检（synthetic，不依赖真实 run）：年份计数 / 计划时长 / 进度 / sit。

用法: /opt/exoplasim/env/bin/python monitor/collector/selftest_plan.py

覆盖：
  * ``discovery.scan_year_counter``：0 / 1 / 5 个 MOST.?????.npz → year_index 0/1/5
  * ``discovery.apply_proc_plan``：伪造 cmdline
      - exo_run.py --params spec(run.years=20) / --years 20 → 20 年 → 240 个月
      - run_short.py --months 6 → 6 个月
      - --run-mode balance + maxyears=120 → 120 年
  * ``state.build_params``：上述 plan 合并进 params（planned_months/planned_years/
    year_total/run_mode/restart_year）
  * ``state.progress_info``：1 基年/月、跨年后 months_done 连续、6 个月 → 6.0
  * ``state.FRAME_ORDER`` / ``LAYER_META`` 含 sit（EXOF 9 层）
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import discovery  # noqa: E402
import state      # noqa: E402

FAILED = []


def check(condition, message):
    print(("  [ok]  " if condition else "  [FAIL] ") + message)
    if not condition:
        FAILED.append(message)


NAMELIST = """ &plasim_nl
 MPSTEP  =  30.0
 N_RUN_STEPS = {:d}
 N_RUN_YEARS = 1
 N_RUN_MONTHS = 0
 N_RUN_DAYS = 0
 NSTPS  =  720
 FIXEDLON = 180.0
 NSYNC = 1
 PSURF  =  182399.96
 /END
"""


def make_workdir(root, n_run_steps=17280):
    wd = tempfile.mkdtemp(prefix="coltest_", dir=root)
    with open(os.path.join(wd, "plasim_namelist"), "w") as handle:
        handle.write(NAMELIST.format(n_run_steps))
    with open(os.path.join(wd, "planet_namelist"), "w") as handle:
        handle.write(" &planet_nl\n PLARAD = 7964025.0\n GA = 13.34\n"
                     " GSOL0 = 1590.0\n ECCEN = 0.035\n OBLIQ = 0.0\n"
                     " ROTSPD = 0.0546448087431694\n NSYNC = 1\n /END\n")
    with open(os.path.join(wd, "radmod_namelist"), "w") as handle:
        handle.write(" &radmod_nl\n CO2 = 400.0\n STARBBTEMP = 3000.0\n /END\n")
    return wd


def main():
    root = tempfile.mkdtemp(prefix="coltest_root_")
    try:
        print("== scan_year_counter ==")
        wd = make_workdir(root)
        check(discovery.scan_year_counter(wd) == 0, "0 个 MOST.* → year_index 0")
        open(os.path.join(wd, "MOST.00000.npz"), "wb").close()
        check(discovery.scan_year_counter(wd) == 1, "1 个 MOST.00000.npz → year_index 1")
        for index in range(1, 5):
            open(os.path.join(wd, "MOST.%05d.npz" % index), "wb").close()
        check(discovery.scan_year_counter(wd) == 5, "MOST.00000..00004 → year_index 5")
        # 各类 *NNNNN* 文件都要算，且 _metadata 不能误算
        wd2 = make_workdir(root)
        open(os.path.join(wd2, "MOST_REST.00008"), "wb").close()
        open(os.path.join(wd2, "MOST_DIAG.00008"), "wb").close()
        open(os.path.join(wd2, "MOST.00008_metadata.npz"), "wb").close()
        check(discovery.scan_year_counter(wd2) == 9,
              "MOST_REST/DIAG.00008 → year_index 9（metadata 不误算）")
        wd3 = make_workdir(root)
        os.makedirs(os.path.join(wd3, "snapshots"), exist_ok=True)
        open(os.path.join(wd3, "snapshots", "MOST_SNAP.00002.npz"), "wb").close()
        check(discovery.scan_year_counter(wd3) == 0,
              "snapshots/ 子目录不参与年份计数（只看 workdir 顶层）")

        print("\n== apply_proc_plan（伪造 cmdline） ==")
        spec_path = os.path.join(root, "exo_spec.json")
        with open(spec_path, "w") as handle:
            json.dump({"model_class": "Model", "run": {"mode": "years", "years": 20},
                       "configure": {"restartfile": "/x/MOST_REST.00019"}}, handle)
        plan = discovery.apply_proc_plan({}, [
            "python", "/opt/exoplasim/env/bin/exo_run.py", "--params", spec_path,
            "--outdir", "/opt/exoplasim/runs/abc_pleiroma"])
        check(plan["years"] == 20 and plan["run_mode"] == "years",
              "exo_run --params run.years=20 → years=20, mode=years")
        check(plan["restartfile"] == "/x/MOST_REST.00019", "spec restartfile 被带出")

        plan = discovery.apply_proc_plan({}, [
            "python", "/opt/exoplasim/env/bin/exo_run.py", "--years", "20",
            "--run-mode", "balance"])
        check(plan["years"] == 20 and plan["run_mode"] == "balance",
              "exo_run --years 20 --run-mode balance → years=20")

        plan = discovery.apply_proc_plan({}, [
            "python", os.path.join(os.path.dirname(__file__), "..", "test", "run_short.py"),
            "--spec", spec_path, "--months", "6", "--outdir", "/tmp/x"])
        check(plan["months"] == 6.0, "run_short --months 6 → months=6")

        spec2 = os.path.join(root, "balance.json")
        with open(spec2, "w") as handle:
            json.dump({"run": {"mode": "balance", "maxyears": 120}}, handle)
        plan = discovery.apply_proc_plan({}, [
            "python", "exo_run.py", "--params", spec2])
        check(plan["years"] == 120 and plan["run_mode"] == "balance",
              "balance maxyears=120 → years=120（计划上界）")

        print("\n== build_params 合并 plan ==")
        wd = make_workdir(root)
        plan = {"months": None, "years": 20, "run_mode": "years",
                "restartfile": "/x/MOST_REST.00019", "source": "exo_run"}
        params, raw = state.build_params(wd, spec={}, header=None, plan=plan)
        check(params["planned_months"] == 240.0,
              "20 年 → planned_months 240（实际 %s）" % params["planned_months"])
        check(params["planned_years"] == 20.0 and params["year_total"] == 20,
              "planned_years=20 / year_total=20")
        check(params["restart_year"] == 20, "restart_year 20（MOST_REST.00019）")
        check(raw["short_run"] is False, "整年 run short_run=False")

        plan = {"months": 6.0, "years": None, "run_mode": "months",
                "restartfile": None, "source": "run_short"}
        params, raw = state.build_params(wd, spec={}, header=None, plan=plan)
        check(params["planned_months"] == 6.0, "run_short 6 个月 → planned_months 6.0")
        check(raw["short_run"] is False, "（namelist 是整年，short_run 看 namelist）")

        wd_short = make_workdir(root, n_run_steps=8640)
        params, raw = state.build_params(wd_short, spec={}, header=None, plan={})
        check(raw["short_run"] is True and params["planned_months"] == 6.0,
              "N_RUN_STEPS=8640 无 plan → short_run + planned_months 6.0")

        print("\n== progress_info ==")
        params = {"planned_months": 240.0}
        raw = {"physical_year_steps": 17280, "nstps": 720}
        prog = state.progress_info(719, params, raw, 10.0, year_index=0, active=True)
        check(prog["year_no"] == 1 and prog["months_done"] == 0.5,
              "第 1 帧：year_no=1, months_done=0.5（实际 %s）" % prog["months_done"])
        check(prog["sim_date"] == "第 1 年 · 第 1 月 · 15 天",
              "sim_date 1 基: %s" % prog["sim_date"])
        prog = state.progress_info(719, params, raw, 10.0, year_index=10, active=True)
        check(abs(prog["months_done"] - 120.5) < 1e-9,
              "第 11 年第 1 帧 → months_done 120.5（实际 %s）" % prog["months_done"])
        check(prog["sim_date"].startswith("第 11 年"),
              "第 11 年 sim_date: %s" % prog["sim_date"])
        prog = state.progress_info(8639, {"planned_months": 6.0}, raw, 40.0,
                                   year_index=0, active=True)
        check(prog["months_done"] == 6.0, "6 个月短跑末帧 months_done=6.0（实际 %s）"
              % prog["months_done"])
        prog = state.progress_info(8639, {"planned_months": 240.0}, raw, 40.0,
                                   year_index=20, active=False)
        check(prog["months_done"] == 240.0,
              "20 年跑完（active=False）→ months_done 240（实际 %s）"
              % prog["months_done"])
        prog = state.progress_info(8639, {"planned_months": 6.0}, raw, 40.0,
                                   year_index=0, active=False)
        check(prog["months_done"] == 6.0, "6 个月短跑结束仍报 6.0")

        print("\n== sit 图层 ==")
        check("sit" in state.FRAME_ORDER, "FRAME_ORDER 含 sit")
        check(len(state.FRAME_ORDER) == 9, "EXOF 9 层（实际 %d）" % len(state.FRAME_ORDER))
        check(state.FRAME_UNITS.get("sit") == "m", "sit 单位 m")
        meta = state.LAYER_META.get("sit") or {}
        check(meta.get("name") == "海冰厚度" and meta.get("range") == [0, 9],
              "LAYER_META.sit 名/范围正确")
        check(state.LAYER_META["sic"]["name"] == "海冰覆盖", "sic 改名海冰覆盖")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n结论:", "ALL PASS" if not FAILED else "FAIL %d" % len(FAILED))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
