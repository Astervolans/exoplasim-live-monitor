#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""server.py — ExoPlaSim 实时只读监控 collector（HTTP + 多 run 轮询主循环）。

启动（WSL 内、venv 解释器；等价于 ``monitor/start.sh``）::

    /opt/exoplasim/env/bin/python "<repo>/monitor/collector/server.py" \
        --port 8765 --auto \
        --outdir "<repo>/monitor/demo"

接口（契约见 ``monitor/CONTRACT.md``）：

* ``GET /api/runs``                        多 run 列表 + 默认 run
* ``GET /api/state?run=<id>``              状态 / 参数 / 进度 / 序列
* ``GET /api/frame?run=<id>&id=<int|latest>``  8 图层 EXOF 二进制
* ``GET /api/land?run=<id>``               lsm EXOF 二进制
* 其余路径                                 静态服务 ``monitor/web/``

设计要点：

* **只读**：只 ``stat`` / ``open`` / ``read`` 运行目录，绝不写、删、改，也不发信号。
* **1 Hz 轮询**、2 s 目录发现；每个 run 独立 :class:`rawburn.BurnStream` + ``state.FrameStore``
  （48 帧 ring buffer）。
* 自身开销从 ``/proc/self/stat`` 积分，``/api/state.server`` 暴露 ``cpu_percent`` / ``poll_ms``。
* HTTP 在工作线程，轮询在主线程，共享数据用 ``threading.RLock`` 保护；帧数组构造后不再修改。
* 任何解析/读取异常都写进 ``run.msg`` 并继续服务旧状态，绝不 500。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import posixpath
import socket
import struct
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import discovery          # noqa: E402  (同目录模块)
import rawburn            # noqa: E402
import state              # noqa: E402

VERSION = "1.0"
LOG = logging.getLogger("collector")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}

CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


# ------------------------------------------------------------------ 进程自检
def _self_cpu_ticks():
    try:
        with open("/proc/self/stat", "r") as handle:
            fields = handle.read().split()
        return int(fields[13]) + int(fields[14])
    except (OSError, IndexError, ValueError):
        return None


def _self_rss_kib():
    try:
        with open("/proc/self/statm", "r") as handle:
            fields = handle.read().split()
        return int(int(fields[1]) * PAGE_SIZE / 1024)
    except (OSError, IndexError, ValueError):
        return 0


# ------------------------------------------------------------------ 单 run
class RunMonitor:
    """一个运行目录的解析器 + 状态机 + ring buffer。"""

    def __init__(self, run_id, workdir, modelname, spec=None, physfilter=True,
                 ring=state.RING_FRAMES):
        self.run_id = run_id
        self.workdir = workdir
        self.modelname = modelname or run_id
        self.spec = spec or {}
        self.physfilter = bool(physfilter)
        self.stream = rawburn.BurnStream(log=lambda msg: LOG.info("[%s] %s", run_id, msg))
        self.frames = state.FrameStore(ring)
        self.params = {}
        self.raw_params = {}
        self.resampler = None
        self.year_index = 0
        self.plan = {}
        self.status = "waiting"
        self.msg = None
        self.last_update = time.time()
        self.first_seen = time.time()
        self.dir_ctime = None
        self.finished_at = None
        self.outdir = None
        self._params_key = None
        self._no_raw_since = None
        self._last_status = None
        self._last_growth = time.time()
        self._last_bytes = 0
        self._plan_at = 0.0
        self._plan_ttl = 2.0
        self._year_renames = 0        # 文件名改名事件计数（跨年兜底）
        self._active_raw = False
        # 当前 raw 文件属于第几年（0 基，用户要求年从 0 开始）。
        # 不能直接用 workdir 里 npz 的数量：年末改名/写出 npz 的那小段时间，最新帧
        # 仍属于上一年，混用会把最后一年叠加一次（第 1 年 11 月 → 第 2 年 12 月）。
        self.raw_year = None
        # raw 曾经消失过（年末改名/删除）：再次打开时应进入下一年。
        self._raw_was_missing = True

    # -------------------------------------------------------- 生命周期
    def poll(self, now=None):
        now = now or time.time()
        if not os.path.isdir(self.workdir):
            if self.finished_at is None:
                self._mark_finished(now)
            return
        if self.dir_ctime is None:
            try:
                self.dir_ctime = os.stat(self.workdir).st_ctime
            except OSError:
                self.dir_ctime = self.first_seen

        candidates = rawburn.scan_raw_candidates(self.workdir)
        self._active_raw = bool(candidates)
        if self.finished_at is not None and candidates:
            # 多年 run 的跨年空档可能被误判为结束；数据再出现就恢复
            LOG.info("[%s] new data after finish -> resume", self.run_id)
            self.finished_at = None
            self._no_raw_since = None
            self._last_status = None
            self.status = "running"

        # 年份/计划时长要在发布帧之前算好，新的帧才会落到正确的年份里
        self._refresh_plan(now)
        self.year_index = self._derive_year_index()
        self._refresh_params()

        events = self.stream.poll(candidates)
        for kind, payload, info in events:
            if kind == "frame":
                if payload is not None:
                    self._on_frame(payload, now)
            elif kind == "open":
                # 第一次打开 raw：它属于「已完成的整年数」对应的那一年（0 基）。
                # 如果 raw 之前消失过（年末改名/删除），重新出现说明进入了下一年。
                if self.raw_year is None:
                    self.raw_year = int(self.year_index or 0)
                    LOG.info("[%s] raw opened -> raw_year=%d",
                             self.run_id, self.raw_year)
                elif self._raw_was_missing:
                    self.raw_year += 1
                    LOG.info("[%s] raw reopened after gap -> raw_year=%d",
                             self.run_id, self.raw_year)
                self._raw_was_missing = False
            elif kind == "reset":
                reason = (info or {}).get("reason")
                if reason == "replaced":
                    self._year_renames += 1
                    # 旧 raw 被改名 = 旧的一年结束；接下来的 raw 是下一年。
                    if self.raw_year is None:
                        self.raw_year = int(self.year_index or 0)
                    self.raw_year += 1
                    self._raw_was_missing = False
                    LOG.info("[%s] year file renamed -> raw_year=%d (disk years=%d)",
                             self.run_id, self.raw_year, self.year_index)
                elif reason == "gone":
                    self._raw_was_missing = True
            elif kind == "error":
                self.msg = (info or {}).get("msg")
                LOG.warning("[%s] parse: %s", self.run_id, self.msg)

        if self.stream.bytes_read > self._last_bytes:
            self._last_bytes = self.stream.bytes_read
            self._last_growth = now

        self._refresh_params()
        self._update_status(now, candidates)

    def _derive_year_index(self):
        """已完成整年数（0 基）：优先看 workdir 里的 MOST.<NNNNN>* 文件。

        短跑（N_RUN_STEPS < 整年）不算年：harness 的单个截短“年”结束时也会写
        ``MOST.00000.npz``，但那只是 6 个月，不能当成完成 1 年。
        """
        if self.raw_params.get("short_run"):
            return 0
        from_disk = discovery.scan_year_counter(self.workdir)
        if from_disk:
            return from_disk
        # 兜底：极端配置下编号文件可能被清掉，用本进程见过的跨年改名次数
        return self._year_renames

    def _refresh_plan(self, now):
        """每 2 s 从 /proc 重新推断计划年数/月数（进程退出后保持上一次结果）。"""
        if now - self._plan_at < self._plan_ttl:
            return
        self._plan_at = now
        try:
            plan = discovery.infer_run_plan(self.workdir, self.run_id, self.spec)
        except Exception:  # noqa: BLE001 - 推断失败不能影响服务
            LOG.debug("[%s] infer_run_plan failed", self.run_id, exc_info=True)
            return
        if not (plan.get("months") or plan.get("years")):
            # 进程退出后 /proc 里就查不到了；不能把运行期间已经推断出的计划信息清空
            if self.plan.get("months") or self.plan.get("years"):
                return
        if plan != self.plan:
            self.plan = plan
            self._params_key = None      # 计划变了，参数面板要重建
            LOG.info("[%s] plan: months=%s years=%s mode=%s source=%s",
                     self.run_id, plan.get("months"), plan.get("years"),
                     plan.get("run_mode"), plan.get("source"))

    def _mark_finished(self, now):
        self.finished_at = now
        self.status = "finished"
        self.last_update = now
        if self.frames.total == 0 and not self.msg:
            self.msg = "未产生原始快照（配置/启动阶段结束或失败）"
        LOG.info("[%s] finished (workdir %s), frames=%d",
                 self.run_id,
                 "gone" if not os.path.isdir(self.workdir) else "finalized",
                 self.frames.total)

    def _has_final_npz(self):
        if os.path.isfile(os.path.join(self.workdir, "MOST.00000.npz")):
            return True
        snapdir = os.path.join(self.workdir, "snapshots")
        try:
            return any(name.endswith(".npz") for name in os.listdir(snapdir))
        except OSError:
            return False

    def _update_status(self, now, candidates):
        if self.finished_at is not None:
            self.status = "finished"
            return
        has_raw = bool(candidates)
        dir_mtime = 0.0
        try:
            dir_mtime = os.stat(self.workdir).st_mtime
        except OSError:
            pass
        dir_idle = now - dir_mtime if dir_mtime else 999.0

        if has_raw:
            self._no_raw_since = None
            if self.stream.header is None:
                self.status = "configuring"
            elif self.frames.total == 0:
                self.status = "running"          # 第一帧正在写
            elif now - self._last_growth > 30.0 and dir_idle > 30.0:
                self.status = "waiting"          # 文件很久没长（可能已停）
                self.msg = self.msg or "原始快照 30 秒未增长，运行可能已停止"
            else:
                self.status = "running"
        else:
            if self._no_raw_since is None:
                self._no_raw_since = now
            idle_for = now - self._no_raw_since
            if self._has_final_npz() and dir_idle >= 2.0 and (
                    idle_for >= 2.0 or self.frames.total > 0):
                self._mark_finished(now)
                self.status = "finished"
            elif (self.frames.total > 0 and idle_for >= 30.0 and dir_idle >= 30.0
                  and _dir_is_empty(self.workdir)):
                # 崩溃/被外部清理后 workdir 会变成空目录且不会再有 final npz；
                # 不处理的话会永远显示 running（保留旧帧）。30 s 只为排除配置间隙。
                self._mark_finished(now)
                self.status = "finished"
                self.msg = self.msg or ("运行目录已被清空（可能崩溃或被清理），"
                                        "保留最后 %d 帧" % self.frames.total)
            elif self.stream.header is None:
                self.status = "configuring"
            elif self.frames.total == 0:
                self.status = "waiting"
            else:
                self.status = "running"
        if self.status != self._last_status:
            self.last_update = now
            self._last_status = self.status

    # -------------------------------------------------------- 帧解析
    def _on_frame(self, raw, now):
        self._refresh_params()
        if self.resampler is None:
            self.msg = "模型网格未知，暂不发布帧"
            return
        year = self.raw_year if self.raw_year is not None else int(self.year_index or 0)
        try:
            frame = state.build_frame(
                raw, self.resampler, self.params, self.raw_params,
                year_index=year,
                frame_in_year=max(0, self.frames.total),
                frame_id=self.frames.total,
                physfilter=self.physfilter,
                plarad=self.params.get("planet_radius_m"),
            )
        except Exception as exc:  # noqa: BLE001 - 单帧失败不影响服务
            self.msg = "build frame failed: %s" % exc
            LOG.exception("[%s] build frame failed", self.run_id)
            return
        progress = state.progress_info(frame.step, self.params, self.raw_params,
                                       self.wall_elapsed(now),
                                       year_index=year, active=True)
        frame.sim_date = progress["sim_date"]
        self.frames.add(frame)
        self.last_update = now
        self.status = "running"
        LOG.info("[%s] frame #%d year=%d step=%d ts_mean=%.2fK (%d frames)",
                 self.run_id, frame.id, year, frame.step, frame.ts_mean, self.frames.total)

    # -------------------------------------------------------- 参数
    def _refresh_params(self):
        header = self.stream.header
        # 注意：workdir 可能在轮询中途被 finalize/崩溃清理清空，_stat_key 会返回 None。
        # 这里必须用「元组拼接」而不是 `+=`，否则 key 为 None 时会抛 TypeError
        # （实测：并发短跑崩溃后 workdir 变空，collector 每秒 poll failed）。
        key = (
            _stat_key(os.path.join(self.workdir, "plasim_namelist")),
            _stat_key(os.path.join(self.workdir, "planet_namelist")),
            _stat_key(os.path.join(self.workdir, "radmod_namelist")),
            tuple(sorted(self.plan.items())),
            int(self.year_index or 0),
        ) + (tuple(header) if header else ())
        if key == self._params_key:
            return
        self._params_key = key
        days_per_year = self.stream.days_per_year or 360
        params, raw_params = state.build_params(
            self.workdir, self._spec_for_run(), header, days_per_year,
            plan=self.plan, completed_years=self.year_index)
        self.params = params
        self.raw_params = raw_params
        if header and self.resampler is None:
            self.resampler = state.Resampler.from_model(header[5], header[4])

    def _spec_for_run(self):
        spec = self.spec or {}
        name = spec.get("modelname")
        if name and name != self.modelname:
            return {}
        return spec

    # -------------------------------------------------------- 汇总
    def wall_elapsed(self, now=None):
        start = self.dir_ctime if self.dir_ctime is not None else self.first_seen
        end = self.finished_at if self.finished_at is not None else (now or time.time())
        return max(0.0, end - start)

    def latest(self):
        return self.frames.latest

    def progress(self, now=None):
        frame = self.latest()
        # 进度必须以「最新帧所属年份」为准，而不是磁盘上已完成的年数：
        # 年末改名与 npz 落盘之间有一小段空档，用后者会把最后一年叠加一次。
        if frame is not None:
            year = int(frame.year or 0)
        elif self.raw_year is not None:
            year = int(self.raw_year)
        else:
            year = int(self.year_index or 0)
        return state.progress_info(frame.step if frame else 0, self.params,
                                   self.raw_params, self.wall_elapsed(now),
                                   year_index=year,
                                   active=self._active_raw)

    def status_text(self, now=None):
        if self.status == "running":
            prog = self.progress(now)
            frame = self.latest()
            if frame is not None:
                return "运行中 · 第 %d 年 第 %d 月" % (prog["year_no"], prog["month"])
            return "运行中 · 等待第一帧"
        if self.status == "finished":
            if self.frames.total == 0:
                return "已结束 · 无帧（未产生原始快照）"
            prog = self.progress(now)
            return "已结束 · 共 %d 帧（%d 年 %d 月）" % (
                self.frames.total, prog["year_no"], prog["month"])
        if self.status == "configuring":
            return "配置中 · 等待原始快照"
        if self.status == "waiting":
            return "等待运行目录" if not os.path.isdir(self.workdir) else "等待原始快照"
        if self.status == "error":
            return "错误 · %s" % (self.msg or "unknown")
        return self.status

    def runs_entry(self, now=None):
        prog = self.progress(now)
        return {
            "run_id": self.run_id,
            "modelname": self.modelname,
            "status": self.status,
            "workdir": self.workdir,
            "frame_count": self.frames.total,
            "year_index": prog["year_index"],
            "year_no": prog["year_no"],
            "completed_years": self.year_index,
            "months_done": prog["months_done"],
            "planned_months": self.params.get("planned_months"),
            "planned_years": self.params.get("planned_years"),
            "year_total": self.params.get("year_total"),
            "progress": prog["progress"],
            "wall_elapsed_s": round(self.wall_elapsed(now), 1),
            "last_update": round(self.last_update, 3),
            "summary": {
                "resolution": self.params.get("resolution"),
                "layers": self.params.get("layers"),
                "ncpus": self.params.get("ncpus"),
                "flux_W_m2": self.params.get("flux_W_m2"),
            },
        }

    def state_payload(self, now=None):
        now = now or time.time()
        prog = self.progress(now)
        frame = self.latest()
        grid = {"nlat": 0, "nlon": 0, "lat": [], "lon": []}
        if self.resampler is not None:
            grid = {
                "nlat": self.resampler.nlat_u,
                "nlon": self.resampler.nlon_u,
                "lat": [round(float(v), 4) for v in self.resampler.lat_u],
                "lon": [round(float(v), 4) for v in self.resampler.lon_u],
            }
        stats = frame.stats if frame is not None and frame.stats else {}
        layers = {}
        for name, meta in state.LAYER_META.items():
            item = dict(meta)
            item["range"] = list(meta["range"])
            entry = stats.get(name)
            item["min"] = entry["min"] if entry else None
            item["max"] = entry["max"] if entry else None
            layers[name] = item
        run_block = {
            "run_id": self.run_id,
            "modelname": self.modelname,
            "workdir": self.workdir,
            "outdir": self.outdir if self.status == "finished" else None,
            "status": self.status,
            "status_text": self.status_text(now),
            "wall_elapsed_s": round(self.wall_elapsed(now), 1),
            "year_index": prog["year_index"],
            "year_no": prog["year_no"],
            "completed_years": self.year_index,
            "restart_year": self.params.get("restart_year"),
            "year_total": self.params.get("year_total"),
            "frames_total": self.frames.total,
            "planned_months": self.params.get("planned_months"),
            "planned_years": self.params.get("planned_years"),
            "run_mode": self.params.get("run_mode"),
            "months_done": prog["months_done"],
            "progress": prog["progress"],
            "eta_s": prog["eta_s"],
            "sim_day": prog["sim_day"],
            "sim_date": prog["sim_date"],
            "step": prog["step"],
            "steps_per_year": prog["steps_per_year"],
            "msg": self.msg,
        }
        frame_block = None
        if frame is not None:
            frame_block = {
                "id": frame.id,
                "step": frame.step,
                "year": frame.year,
                "year_no": frame.year,
                "frame_in_year": frame.frame_in_year,
                "wall": frame.wall,
                "sim_date": frame.sim_date,
                "ts_mean": state._round(frame.ts_mean, 3),
                "ntr_mean": state._round(frame.ntr_mean, 3),
                "complete": bool(frame.complete),
            }
        source = os.path.basename(self.stream.path) if self.stream.path else "plasim_snapshot"
        return {
            "run": run_block,
            "grid": grid,
            "layers": layers,
            "frame": frame_block,
            "history": self.frames.history(),
            "year_series": self.frames.year_series(),
            "params": self.params,
            "meta": {
                "updated": round(self.last_update, 3),
                "source": source,
                "source_size": self.stream.source_size,
                "note": "只读监控；数据为模型原始输出，未做物理滤波以外的重建",
            },
        }


def _stat_key(path):
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _dir_is_empty(path):
    try:
        return not os.listdir(path)
    except OSError:
        return False


# ------------------------------------------------------------------ 全局
class Monitor:
    """多 run 管理：目录发现、轮询调度、进程自检。"""

    def __init__(self, args):
        self.args = args
        self.runs = {}
        self.order = []
        self.lock = threading.RLock()
        self.start_time = time.time()
        self.poll_ms = 0.0
        self.poll_ms_max = 0.0
        self.cpu_percent = 0.0
        self.rss_kib = _self_rss_kib()
        self.frames_parsed = 0
        self.bytes_read = 0
        self.discovered_at = 0.0
        self._cpu_last = (_self_cpu_ticks(), time.time())
        self.spec = state.load_spec(discovery.find_spec(args.outdir) or args.spec)
        self.spec_path = discovery.find_spec(args.outdir) or args.spec
        if self.spec and self.spec_path:
            LOG.info("merged spec: %s", self.spec_path)
        self._discover(force=True)

    # -------------------------------------------------------- 发现
    def _discover(self, force=False):
        forced = [self.args.workdir] if self.args.workdir else []
        entries = discovery.scan_runs(
            root=self.args.runs_root,
            recent_minutes=self.args.recent_minutes,
            max_runs=self.args.max_runs,
            forced=forced,
        )
        for entry in entries:
            run = self.runs.get(entry.run_id)
            if run is None:
                run = RunMonitor(
                    run_id=entry.run_id,
                    workdir=entry.workdir,
                    modelname=entry.modelname,
                    spec=self.spec,
                    physfilter=self.args.physfilter,
                )
                run.outdir = self.args.outdir
                self.runs[entry.run_id] = run
                self.order.append(entry.run_id)
                LOG.info("discovered run %s (%s)%s", entry.run_id,
                         entry.workdir, " raw" if entry.has_raw else "")
            else:
                run.modelname = entry.modelname or run.modelname
        if self.args.workdir:
            run_id = os.path.basename(os.path.abspath(self.args.workdir))
            if run_id not in self.runs:
                run = RunMonitor(run_id, os.path.abspath(self.args.workdir),
                                 discovery.modelname_of(self.args.workdir),
                                 spec=self.spec, physfilter=self.args.physfilter)
                run.outdir = self.args.outdir
                self.runs[run_id] = run
                self.order.append(run_id)
                LOG.info("docker watch %s (waiting for directory)", run_id)

    # -------------------------------------------------------- 轮询
    def poll_all(self):
        with self.lock:
            now = time.time()
            if now - self.discovered_at >= self.args.discover_interval:
                try:
                    self._discover()
                except Exception:  # noqa: BLE001
                    LOG.exception("discovery failed")
                self.discovered_at = now
            for run in list(self.runs.values()):
                try:
                    run.poll(now)
                except Exception as exc:  # noqa: BLE001
                    run.msg = "poll error: %s" % exc
                    LOG.exception("[%s] poll failed", run.run_id)
            self._update_self_stats()

    def _update_self_stats(self):
        ticks = _self_cpu_ticks()
        now = time.time()
        last_ticks, last_time = self._cpu_last
        if ticks is not None and last_ticks is not None and now > last_time + 1e-6:
            delta = (ticks - last_ticks) / float(CLK_TCK)
            self.cpu_percent = max(0.0, delta / (now - last_time) * 100.0)
        self._cpu_last = (ticks, now)
        self.rss_kib = _self_rss_kib()
        self.frames_parsed = sum(run.stream.frames_parsed for run in self.runs.values())
        self.bytes_read = sum(run.stream.bytes_read for run in self.runs.values())
        self.poll_ms_max = max(self.poll_ms_max, self.poll_ms)

    # -------------------------------------------------------- 选择默认 run
    def _sorted_runs(self):
        running = [run for run in self.runs.values() if run.status == "running"]
        others = [run for run in self.runs.values() if run.status != "running"]
        running.sort(key=lambda run: -run.last_update)
        others.sort(key=lambda run: -run.last_update)
        return running + others

    def default_run_id(self):
        ordered = self._sorted_runs()
        if ordered:
            return ordered[0].run_id
        return None

    def get_run(self, run_id):
        if not run_id:
            run_id = self.default_run_id()
        if run_id is None:
            return None
        return self.runs.get(run_id)

    # -------------------------------------------------------- HTTP 文档
    def server_block(self):
        return {
            "pid": os.getpid(),
            "cpu_percent": round(self.cpu_percent, 2),
            "rss_kib": self.rss_kib,
            "uptime_s": round(time.time() - self.start_time, 1),
            "poll_ms": round(self.poll_ms, 2),
            "frames_parsed": self.frames_parsed,
            "bytes_read": self.bytes_read,
        }

    def runs_doc(self):
        with self.lock:
            now = time.time()
            runs = [run.runs_entry(now) for run in self._sorted_runs()]
            return {"ok": True, "default": self.default_run_id(), "runs": runs}

    def state_doc(self, run_id):
        with self.lock:
            run = self.get_run(run_id)
            if run is None:
                return None
            payload = run.state_payload()
            doc = {"ok": True, "version": VERSION, "server": self.server_block()}
            doc.update(payload)
            return doc

    def frame_bytes(self, run_id, frame_id):
        with self.lock:
            run = self.get_run(run_id)
            if run is None:
                return None, "unknown run"
            frame = run.frames.get(frame_id)
            if frame is None:
                return None, "frame out of range"
            return state.frame_exof(frame), None

    def land_bytes(self, run_id):
        with self.lock:
            run = self.get_run(run_id)
            if run is None:
                return None, "unknown run"
            frame = run.frames.latest
            if frame is None:
                return None, "no frame yet"
            return state.land_exof(frame), None


# ------------------------------------------------------------------ HTTP
def make_handler(monitor, web_root):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ExoMonitor/" + VERSION
        protocol_version = "HTTP/1.1"

        # 关掉每请求一行日志（1 Hz 轮询时太吵）
        def log_message(self, fmt, *args):
            LOG.debug("http %s - %s", self.address_string(), fmt % args)

        # -------------------------------------------------- 工具
        def _send(self, status, body, content_type="application/json; charset=utf-8",
                  extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json_error(self, status, message):
            self._send(status, {"ok": False, "error": message})

        # -------------------------------------------------- GET
        def do_GET(self):
            try:
                self._handle_get()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:  # noqa: BLE001 - 兜底，绝不 500 裸奔
                LOG.exception("request failed: %s", self.path)
                try:
                    self._json_error(503, "internal error")
                except Exception:  # noqa: BLE001
                    pass

        do_HEAD = do_GET

        def _handle_get(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            run_id = (query.get("run") or [None])[0]

            if path == "/api/runs":
                self._send(200, monitor.runs_doc())
                return
            if path == "/api/state":
                doc = monitor.state_doc(run_id)
                if doc is None:
                    self._json_error(404, "unknown run")
                else:
                    self._send(200, doc)
                return
            if path == "/api/frame":
                frame_id = (query.get("id") or ["latest"])[0]
                if frame_id in (None, "", "latest", "-1"):
                    frame_id = None
                else:
                    try:
                        frame_id = int(frame_id)
                    except ValueError:
                        self._json_error(400, "bad frame id")
                        return
                body, error = monitor.frame_bytes(run_id, frame_id)
                if body is None:
                    self._json_error(404, error or "frame not found")
                else:
                    self._send(200, body, "application/octet-stream")
                return
            if path == "/api/land":
                body, error = monitor.land_bytes(run_id)
                if body is None:
                    self._json_error(404, error or "land not available")
                else:
                    self._send(200, body, "application/octet-stream")
                return
            if path.startswith("/api/"):
                self._json_error(404, "unknown api")
                return
            self._serve_static(path)

        # -------------------------------------------------- 静态文件
        def _serve_static(self, path):
            if web_root is None:
                self._json_error(404, "static root not configured")
                return
            rel = urllib.parse.unquote(path)
            if rel in ("", "/"):
                rel = "/index.html"
            rel = rel.lstrip("/")
            safe = posixpath.normpath(rel)
            if safe.startswith("..") or os.path.isabs(safe):
                self._json_error(404, "not found")
                return
            full = os.path.realpath(os.path.join(web_root, safe))
            root = os.path.realpath(web_root)
            if full != root and not full.startswith(root + os.sep):
                self._json_error(404, "not found")     # 路径穿越防护
                return
            if not os.path.isfile(full):
                self._json_error(404, "not found")
                return
            try:
                with open(full, "rb") as handle:
                    body = handle.read()
            except OSError:
                self._json_error(404, "not found")
                return
            ctype = CONTENT_TYPES.get(os.path.splitext(full)[1].lower(),
                                      "application/octet-stream")
            self._send(200, body, ctype)

    return Handler


# ------------------------------------------------------------------ CLI
def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="ExoPlaSim 实时只读监控 collector",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="0.0.0.0",
                        help="监听地址（WSL2 下 0.0.0.0 才能从 Windows 浏览器访问）")
    parser.add_argument("--auto", action="store_true", default=True,
                        help="自动扫描运行目录（默认开启）")
    parser.add_argument("--no-auto", dest="auto", action="store_false",
                        help="只监控 --workdir")
    parser.add_argument("--workdir", default=None, help="固定监控指定运行目录")
    parser.add_argument("--runs-root", default=discovery.DEFAULT_RUNS_ROOT)
    parser.add_argument("--outdir", default=None,
                        help="run 结束后归档目录（含 spec.json 时合并参数）")
    parser.add_argument("--spec", default=None, help="额外 spec.json（参数兜底）")
    parser.add_argument("--web", default=None,
                        help="静态目录，默认 <collector>/../web")
    parser.add_argument("--interval", type=float, default=1.0, help="轮询周期（秒）")
    parser.add_argument("--discover-interval", type=float, default=2.0,
                        help="目录发现周期（秒）")
    parser.add_argument("--recent-minutes", type=float,
                        default=discovery.DEFAULT_RECENT_MINUTES,
                        help="只采纳最近 N 分钟内有变动的运行目录")
    parser.add_argument("--max-runs", type=int, default=discovery.DEFAULT_MAX_RUNS)
    parser.add_argument("--ring", type=int, default=state.RING_FRAMES,
                        help="每个 run 的 ring buffer 帧数")
    parser.add_argument("--physfilter", dest="physfilter", action="store_true",
                        default=True, help="风场重建开物理滤波（默认）")
    parser.add_argument("--no-physfilter", dest="physfilter", action="store_false",
                        help="关闭物理滤波（仅用于与归档 npz 数值对照）")
    parser.add_argument("--log-level", default="info")
    parser.add_argument("--once", action="store_true",
                        help="只轮询一次并打印 /api/runs（自检用）")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    if not args.auto and not args.workdir:
        LOG.error("--no-auto 时必须给 --workdir")
        return 2

    web_root = args.web or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
    if not os.path.isdir(web_root):
        LOG.warning("静态目录不存在: %s（/api/* 仍可用）", web_root)
        web_root = None

    monitor = Monitor(args)
    # 预热 pyfft 扩展：把一次性的 ~60 ms import 成本放到启动阶段，
    # 避免第一帧重建时在 1 s 采样窗口里冒出一个 CPU 尖峰。
    if state.get_pyfft() is None:
        LOG.warning("exoplasim.pyfft 不可用（%s）：风场图层将置零",
                    state.pyfft_error())
    else:
        LOG.info("exoplasim.pyfft ready")
    handler = make_handler(monitor, web_root)
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), handler)
    except OSError as exc:
        LOG.error("无法监听 %s:%s -> %s", args.host, args.port, exc)
        return 3
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, name="http", daemon=True)
    thread.start()
    LOG.info("listening on http://%s:%d  (runs root %s, web %s, physfilter=%s)",
             args.host, args.port, args.runs_root, web_root, args.physfilter)

    if args.once:
        monitor.poll_all()
        print(json.dumps(monitor.runs_doc(), ensure_ascii=False, indent=2))
        httpd.shutdown()
        return 0

    interval = max(0.2, float(args.interval))
    next_tick = time.monotonic()
    try:
        while True:
            started = time.perf_counter()
            monitor.poll_all()
            monitor.poll_ms = (time.perf_counter() - started) * 1000.0
            next_tick += interval
            sleep = next_tick - time.monotonic()
            if sleep > 0:
                time.sleep(sleep)
            else:
                next_tick = time.monotonic()
    except KeyboardInterrupt:
        LOG.info("stopping")
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
