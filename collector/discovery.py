#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""discovery.py — 扫描 ``/opt/exoplasim/runs`` 发现正在跑 / 刚跑完的模拟。

只读：只做 ``os.listdir`` / ``os.stat`` / ``os.path.isfile``，不打开数据文件。

判定一个目录是「运行目录」的依据（契约 §2.0）：目录里有 ``plasim_namelist``。
是否**采纳**为监控对象（见 :func:`scan_runs`）：

* 目录里有原始快照（``plasim_snapshot`` 或年末改名后的 ``MOST_SNAP.*``）—— 活跃；
* 或运行目录最近 ``recent_minutes`` 分钟内被修改过 —— 刚起/刚结束时也能看到；
* ``--workdir`` 显式给出的目录无条件采纳（即使还没建出来，状态为 waiting）。

这样既能自动发现新 run，又不会把几个月前跑完的几十个历史目录全塞进前端。
"""
from __future__ import annotations

import json
import os
import re
import time

DEFAULT_RUNS_ROOT = "/opt/exoplasim/runs"
#: 只采纳最近这段时间内有变动的运行目录（分钟）
DEFAULT_RECENT_MINUTES = 120.0
#: 最多同时监控多少个 run（防止误扫历史目录把内存打爆）
DEFAULT_MAX_RUNS = 8


class RunDir:
    """一次目录扫描的结果。"""

    __slots__ = ("run_id", "workdir", "mtime", "ctime", "has_raw", "raw_size",
                 "has_final", "modelname", "age_min")

    def __init__(self, **kwargs):
        for key in self.__slots__:
            setattr(self, key, kwargs.get(key))

    def __repr__(self):  # pragma: no cover - 调试
        return "<RunDir %s raw=%s size=%s>" % (self.run_id, self.has_raw, self.raw_size)


def modelname_of(run_dir):
    """从目录名推 modelname：``monitor_20261104-215519_pleiroma`` → ``pleiroma``。"""
    base = os.path.basename(str(run_dir).rstrip("/"))
    parts = base.split("_")
    if len(parts) >= 2:
        tail = parts[-1]
        if tail and not tail.replace("-", "").isdigit():
            return tail
    return base


def scan_runs(root=DEFAULT_RUNS_ROOT, recent_minutes=DEFAULT_RECENT_MINUTES,
              max_runs=DEFAULT_MAX_RUNS, forced=()):
    """扫描运行根目录，返回按「活跃优先、最近修改在前」排序的 :class:`RunDir` 列表。"""
    now = time.time()
    seen = set()
    found = []

    for workdir in list(forced):
        workdir = os.path.abspath(workdir)
        entry = _inspect(workdir, now)
        if entry is not None:
            found.append(entry)
            seen.add(entry.run_id)

    try:
        names = sorted(os.listdir(root))
    except OSError:
        return found

    def consider(workdir, run_id, nested=False):
        if workdir in seen:
            return
        entry = _inspect(workdir, now, run_id=run_id)
        if entry is None:
            return
        if entry.has_raw or (entry.age_min is not None
                             and entry.age_min <= recent_minutes):
            found.append(entry)
            seen.add(entry.run_id)

    for name in names:
        if name.startswith("_"):
            # exo 的临时目录（_stage_* / _restarts / _monitor_artifacts）不是运行目录
            continue
        if name.endswith("_crashed"):
            # ExoPlaSim 崩溃时把工作文件 mv 到 <modelname>_crashed/；
            # 里面的 plasim_namelist/raw 是死的，不能当成活跃 run（否则会显示"运行中"僵尸）。
            continue
        workdir = os.path.join(root, name)
        if workdir in seen:
            continue
        consider(workdir, name)
        # 第二层：一些驱动脚本把每个 round 放在 <root>/<case>/run_round_NNN/ 下，
        # 真正的工作目录（含 plasim_namelist）在下一层，必须递归发现。
        try:
            children = sorted(os.listdir(workdir))
        except OSError:
            continue
        for child in children:
            if child.startswith("_") or child.endswith("_crashed"):
                continue
            sub = os.path.join(workdir, child)
            try:
                if not os.path.isdir(sub):
                    continue
            except OSError:
                continue
            consider(sub, "%s__%s" % (name, child), nested=True)

    found.sort(key=lambda item: (not item.has_raw, -(item.mtime or 0)))
    if max_runs and len(found) > max_runs:
        # 活跃的一律保留，超额的按修改时间砍尾部
        active = [item for item in found if item.has_raw]
        idle = [item for item in found if not item.has_raw]
        found = active[:max_runs] + idle[:max(0, max_runs - len(active))]
    return found


def _inspect(workdir, now, run_id=None):
    try:
        st = os.stat(workdir)
    except OSError:
        return None
    if not os.path.isdir(workdir):
        return None
    namelist = os.path.join(workdir, "plasim_namelist")
    if not os.path.isfile(namelist):
        return None

    raw_path = os.path.join(workdir, "plasim_snapshot")
    raw_size = 0
    has_raw = False
    if os.path.isfile(raw_path):
        has_raw = True
        try:
            raw_size = os.stat(raw_path).st_size
        except OSError:
            raw_size = 0
    if not has_raw:
        # 年末/结束时模型把 raw 改名成 MOST_SNAP.00000（随后 burn7 再删除）
        try:
            for name in os.listdir(workdir):
                if name.startswith("MOST_SNAP") and not name.endswith(".npz"):
                    candidate = os.path.join(workdir, name)
                    if os.path.isfile(candidate):
                        has_raw = True
                        try:
                            raw_size = max(raw_size, os.stat(candidate).st_size)
                        except OSError:
                            pass
        except OSError:
            pass

    has_final = os.path.isfile(os.path.join(workdir, "MOST.00000.npz"))
    if not has_final:
        snapdir = os.path.join(workdir, "snapshots")
        try:
            has_final = any(name.endswith(".npz") for name in os.listdir(snapdir))
        except OSError:
            has_final = False

    mtime = max(st.st_mtime, _safe_mtime(namelist))
    return RunDir(
        run_id=run_id or os.path.basename(workdir.rstrip("/")),
        workdir=workdir,
        mtime=mtime,
        ctime=st.st_ctime,
        has_raw=has_raw,
        raw_size=raw_size,
        has_final=has_final,
        modelname=modelname_of(workdir),
        age_min=(now - mtime) / 60.0,
    )


def _safe_mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def find_spec(outdir):
    """返回 ``<outdir>/spec.json`` 的路径（不存在返回 None）。"""
    if not outdir:
        return None
    candidate = os.path.join(outdir, "spec.json")
    return candidate if os.path.isfile(candidate) else None


# ------------------------------------------------------------------ 年份计数
#: 模型每年写一组 MOST.<NNNNN>* 文件（年份序号 0 基）：.npz / _metadata 之外的
#: dataname（可能带 .STORM 后缀）、_REST / _DIAG / _SNAP / _HC。
_YEAR_FILE_RE = re.compile(r"^MOST(?:_REST|_DIAG|_SNAP|_HC)?\.(\d{5})(?:\.[A-Za-z0-9]+)?$")


def scan_year_counter(workdir):
    """扫描 workdir 里 ``MOST.<NNNNN>*`` 的最大年份序号，返回**已完成整年数**。

    序号是 0 基（第一个模拟年写 ``MOST.00000``），所以已完成年数 = 最大序号 + 1；
    没有编号文件（比如刚开始第一年）返回 0。运行中每个 poll 都会重算，不依赖
    文件名改名事件——exo_run 是逐年 ``model.run(years=1)``，只有文件才是跨年证据。
    """
    try:
        names = os.listdir(workdir)
    except OSError:
        return 0
    best = -1
    for name in names:
        match = _YEAR_FILE_RE.match(name)
        if not match:
            continue
        try:
            index = int(match.group(1))
        except ValueError:
            continue
        if index > best:
            best = index
    return best + 1 if best >= 0 else 0


# ------------------------------------------------------------------ 计划时长
def _pids():
    try:
        return [name for name in os.listdir("/proc") if name.isdigit()]
    except OSError:
        return []


def _read_cmdline(pid):
    """返回 (argv 列表, cwd, cmdline 字符串)，读不到返回 (None, None, None)。"""
    try:
        with open("/proc/%s/cmdline" % pid, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None, None, None
    if not raw:
        return None, None, None
    argv = [part.decode("utf-8", "replace") for part in raw.split(b"\x00") if part]
    try:
        cwd = os.path.realpath("/proc/%s/cwd" % pid)
    except OSError:
        cwd = None
    return argv, cwd, " ".join(argv)


def _arg_value(argv, names):
    """取 ``--name value`` 或 ``--name=value`` 形式的值（字符串）。"""
    for index, token in enumerate(argv):
        for name in names:
            if token == name and index + 1 < len(argv):
                return argv[index + 1]
            prefix = name + "="
            if token.startswith(prefix):
                return token[len(prefix):]
    return None


def _arg_float(argv, names):
    value = _arg_value(argv, names)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _first_number(*values):
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _to_wsl_path(path):
    """把 Windows 路径 (``E:\\...``) 转成 WSL 路径 (``/mnt/e/...``)；已是 POSIX 原样返回。"""
    if not path:
        return path
    text = str(path).strip().strip('"').replace("\\", "/")
    if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
        return "/mnt/%s/%s" % (text[0].lower(), text[2:])
    return text


def apply_spec_plan(plan, doc):
    """把 spec JSON 里的计划信息合进 ``plan``（只在 plan 空着时填）。

    支持 exo_run 的 ``{"run": {"years": N, "mode": "years"}}``、preset spec 的
    顶层 ``years``/``months``、以及 balance 模式的 ``maxyears``。
    """
    if not isinstance(doc, dict):
        return plan
    run = doc.get("run") if isinstance(doc.get("run"), dict) else {}
    rnd = doc.get("round") if isinstance(doc.get("round"), dict) else {}
    conf = doc.get("configure") if isinstance(doc.get("configure"), dict) else {}

    months = _first_number(run.get("months"), doc.get("months"))
    years = _first_number(run.get("years"), doc.get("years"), rnd.get("years"))
    if months is not None:
        plan["months"] = float(months)
    elif years is not None:
        plan["years"] = int(years)
    elif str(run.get("mode") or doc.get("mode") or "").lower() == "balance":
        maxyears = _first_number(run.get("maxyears"), doc.get("maxyears"))
        if maxyears:
            plan["years"] = int(maxyears)
    if not plan.get("run_mode"):
        plan["run_mode"] = run.get("mode") or doc.get("mode")
    if not plan.get("restartfile"):
        plan["restartfile"] = conf.get("restartfile")
    if not plan.get("source"):
        plan["source"] = "spec"
    return plan


def apply_proc_plan(plan, argv, seen_paths=None):
    """从一条进程 ``argv`` 解析计划信息（纯函数，单测可直接伪造 argv）。

    * ``run_short.py --months M`` → 计划 M 个月（harness 把“年”截短）；
    * ``exo_run.py --years N`` / ``--params spec.json`` 里的 ``run.years`` → N 年；
    * ``--run-mode balance`` 等模式名原样带回；
    返回 ``plan``（就地修改）。
    """
    seen_paths = seen_paths if seen_paths is not None else []
    if not argv:
        return plan
    joined = " ".join(argv)
    is_short = "run_short" in joined
    is_exo = "exo_run" in joined

    # run_short：--months 最权威（harness 会把“年”截短到 6 个月）
    if is_short:
        months = _arg_float(argv, ("--months",))
        if months is not None:
            plan["months"] = float(months)
            plan["source"] = "run_short"

    years = _arg_float(argv, ("--years", "--maxyears"))
    if years is not None and plan.get("months") is None:
        plan["years"] = int(years)
        plan["source"] = plan.get("source") or ("exo_run" if is_exo else "proc")
    months_arg = _arg_float(argv, ("--months",))
    if months_arg is not None and not is_short:
        plan["months"] = float(months_arg)
        plan["source"] = plan.get("source") or "proc"
    mode = _arg_value(argv, ("--run-mode",))
    if not mode:
        fallback_mode = _arg_value(argv, ("--mode",))
        # exo_run 的 --mode 是 action（run/check/read），只有像 run-mode 的值才采纳
        if fallback_mode in ("years", "months", "balance", "smoke"):
            mode = fallback_mode
    if mode:
        plan["run_mode"] = mode

    params_path = _arg_value(argv, ("--params", "--spec"))
    if params_path:
        params_path = _to_wsl_path(params_path)
    if params_path and os.path.isfile(params_path):
        doc = _load_json(params_path)
        if doc is not None:
            seen_paths.append(params_path)
            if plan.get("months") is None and plan.get("years") is None:
                apply_spec_plan(plan, doc)
            else:
                # 只补 restartfile / run_mode，不覆盖已解析的时长
                filler = {"months": None, "years": None,
                          "run_mode": plan.get("run_mode"),
                          "restartfile": plan.get("restartfile"),
                          "source": plan.get("source")}
                apply_spec_plan(filler, doc)
                plan["restartfile"] = filler.get("restartfile") or plan.get("restartfile")
                plan["run_mode"] = filler.get("run_mode") or plan.get("run_mode")

    # exo_run 的 --outdir 下有 spec.json（最完整：含 configure.restartfile / run 块）
    outdir = _arg_value(argv, ("--outdir",))
    if outdir:
        outdir = _to_wsl_path(outdir)
        spec_path = os.path.join(outdir, "spec.json")
        if os.path.isfile(spec_path) and spec_path not in seen_paths:
            doc = _load_json(spec_path)
            if doc is not None:
                seen_paths.append(spec_path)
                merged = {"months": None, "years": None, "run_mode": None,
                          "restartfile": None, "source": None}
                for part in ("resolved", "input"):
                    sub = doc.get(part)
                    if isinstance(sub, dict):
                        apply_spec_plan(merged, sub)
                plan["restartfile"] = plan.get("restartfile") or merged.get("restartfile")
                plan["run_mode"] = plan.get("run_mode") or merged.get("run_mode")

    if plan.get("months") is None and plan.get("years") is None:
        _plan_from_driver_script(plan, argv, seen_paths)
    return plan


def infer_run_plan(workdir, run_id=None, spec=None):
    """推断本次 run 的计划月数/年数（只读 /proc 与 JSON）。

    为什么需要：exo_run / 各 driver 都是逐年调用 ``model.run(years=1)``，workdir 里的
    ``plasim_namelist`` 永远是 ``N_RUN_YEARS=1``，靠它只能得到 12 个月。

    优先级（后者覆盖前者）：
      1. 合并进来的 spec（``--outdir/spec.json``）里的 ``run.years`` / ``years``；
      2. 运行进程：``cwd == workdir`` 或命令行含 run_id 的 python 进程，
         解析 ``--months`` / ``--years`` / ``--run-mode`` / ``--params|--spec <json>``；
      3. 兜底：读该进程的主脚本，找脚本里硬编码的 spec JSON 路径（如
         ``SPEC = "/path/spec.json"``）再读 ``years``。
    返回 ``{"months","years","run_mode","restartfile","source"}``，未知字段为 None。
    """
    plan = {"months": None, "years": None, "run_mode": None,
            "restartfile": None, "source": None}
    if spec:
        apply_spec_plan(plan, spec)
    target = os.path.realpath(workdir) if workdir else None
    run_id = str(run_id or "")
    seen_paths = []

    for pid in _pids():
        argv, cwd, cmd = _read_cmdline(pid)
        if not argv:
            continue
        joined = " ".join(argv)
        is_short = "run_short" in joined
        is_exo = "exo_run" in joined
        short_id = run_id.split("__")[-1] if run_id else ""
        matched = (target is not None and cwd == target) or (
            is_exo and ((run_id and run_id in joined)
                        or (short_id and short_id in joined)))
        if not matched:
            continue
        apply_proc_plan(plan, argv, seen_paths)
    return plan


def _plan_from_driver_script(plan, argv, seen_paths):
    """兜底：读 driver 主脚本，找脚本里写死的 spec JSON 路径。"""
    script = None
    for token in argv:
        if token.endswith(".py") and os.path.isfile(token):
            script = token
            break
    if not script:
        return
    try:
        if os.path.getsize(script) > 2 * 1024 * 1024:
            return
        with open(script, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return
    for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*\s*=\s*[\"']([^\"']+\.json)[\"']", text):
        candidate = match.group(1)
        if candidate in seen_paths or not os.path.isfile(candidate):
            continue
        doc = _load_json(candidate)
        if doc is None:
            continue
        seen_paths.append(candidate)
        trial = {"months": None, "years": None, "run_mode": None,
                 "restartfile": None, "source": "driver_script"}
        apply_spec_plan(trial, doc)
        if trial.get("months") is not None or trial.get("years") is not None:
            plan.update(trial)
            plan["source"] = "driver_script"
            return

