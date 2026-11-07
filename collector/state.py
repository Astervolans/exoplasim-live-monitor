#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""state.py — 派生场、统一网格重采样、参数解析、进度与序列计算。

职责边界：
* :class:`Resampler`  高斯纬度 → 统一等经纬网格（``nlat*2 x nlon*2``，自北向南）。
* :func:`wind_lowest` 谱系数 (155 散度, 138 涡度) → 近地面 u/v（``exoplasim.pyfft``）。
* :func:`build_frame` 一条 :class:`rawburn.RawFrame` → 一帧监控数据（8 个统一网格图层
                      + 面积加权统计）。
* :class:`FrameStore` 每 run 的 ring buffer（48 帧）。
* :func:`read_namelist` / :func:`build_params` 参数面板与进度换算。
* :func:`progress_info` 年/月/日、months_done、progress、ETA。

所有量都按契约单位：``ts/tas`` K、``pr`` mm/day、``spd/u/v`` m/s、``sic/clt/lsm`` 0..1。
"""
from __future__ import annotations

import json
import math
import os
import re
import struct

import numpy as np

# ---------------------------------------------------------------- 图层定义
#: 顺序即 EXOF 二进制的 ``order``；前端按这个顺序取数组。
FRAME_ORDER = ("ts", "tas", "pr", "spd", "u", "v", "sic", "sit", "clt")
FRAME_UNITS = {"ts": "K", "tas": "K", "pr": "mm/d", "spd": "m/s",
               "u": "m/s", "v": "m/s", "sic": "1", "sit": "m", "clt": "1"}
LAYER_META = {
    "ts":  {"name": "地表温度", "unit": "K",    "range": [180, 330], "kind": "scalar"},
    "pr":  {"name": "降水",     "unit": "mm/d", "range": [0, 20],    "kind": "scalar"},
    "spd": {"name": "风速",     "unit": "m/s",  "range": [0, 30],    "kind": "scalar"},
    "sic": {"name": "海冰覆盖", "unit": "1",    "range": [0, 1],     "kind": "scalar"},
    "sit": {"name": "海冰厚度", "unit": "m",    "range": [0, 9],     "kind": "scalar"},
    "clt": {"name": "云量",     "unit": "1",    "range": [0, 1],     "kind": "scalar"},
    "tas": {"name": "2 m 气温", "unit": "K",    "range": [180, 330], "kind": "scalar"},
}

#: 降水 m/s → mm/day
PR_MM_DAY = 86400.0 * 1000.0
#: 地球半径（ExoPlaSim 内部把 PLARAD 换算成地球半径的基准）
EARTH_RADIUS_M = 6371220.0
#: 模型一年固定 360 天（与 namelist N_DAYS_PER_YEAR 无关，PLASIM 输出按 360 天分月）
DAYS_PER_YEAR = 360.0
#: ring buffer 容量（契约 §2.2）
RING_FRAMES = 48


# ---------------------------------------------------------------- namelist
_NAMELIST_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def read_namelist(path):
    """读取 Fortran namelist（``KEY = VALUE`` 形式），返回字符串字典。"""
    out = {}
    try:
        with open(path, "r", errors="replace") as handle:
            for line in handle:
                line = line.split("!")[0]
                match = _NAMELIST_RE.match(line)
                if not match:
                    continue
                key = match.group(1).upper()
                value = match.group(2).strip().rstrip(",")
                if value and not value.startswith("/"):
                    out[key] = value
    except OSError:
        return {}
    return out


def _num(store, key, default=None, cast=float):
    raw = store.get(key)
    if raw is None:
        return default
    try:
        return cast(float(raw))
    except (TypeError, ValueError):
        return default


def _bool(store, key, default=None):
    raw = store.get(key)
    if raw is None:
        return default
    try:
        return bool(int(float(raw)))
    except (TypeError, ValueError):
        return default


def load_spec(path):
    """读取 spec.json（configure 段），失败返回空字典。"""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


# ---------------------------------------------------------------- 重采样
class Resampler:
    """高斯网格（lat 自北向南、lon 0→360）→ 统一等经纬网格。

    目标网格按契约 §3.3：``nlat_u = 2*nlat``、``nlon_u = 2*nlon``、
    ``lat_u[j] = 90 - (j+0.5)*180/nlat_u``、``lon_u[i] = i*360/nlon_u``。
    纬度线性插值（两端夹取，与 ``np.interp`` 等价的向量化实现），经度周期线性插值。
    """

    def __init__(self, lat_model, nlon):
        self.lat_model = np.asarray(lat_model, dtype=np.float64)   # 自北向南
        if self.lat_model.ndim != 1:
            raise ValueError("lat_model must be 1-D")
        self.nlat = int(self.lat_model.size)
        self.nlon = int(nlon)
        self.nlat_u = 2 * self.nlat
        self.nlon_u = 2 * self.nlon
        self.lat_u = 90.0 - (np.arange(self.nlat_u) + 0.5) * (180.0 / self.nlat_u)
        self.lon_u = np.arange(self.nlon_u) * (360.0 / self.nlon_u)

        # 纬度：源按升序处理（内部乘法只用到两个相邻点）
        lat_asc = self.lat_model[::-1]
        idx = np.searchsorted(lat_asc, self.lat_u) - 1
        idx = np.clip(idx, 0, self.nlat - 2)
        denom = (lat_asc[idx + 1] - lat_asc[idx])
        weight = np.clip((self.lat_u - lat_asc[idx]) / denom, 0.0, 1.0)
        self._lat_i0 = idx
        self._lat_w = weight

        # 经度：目标网格间距恰为源网格的一半，仍按通用周期线性插值处理
        x = self.lon_u / 360.0 * self.nlon
        floor = np.floor(x)
        self._lon_i0 = floor.astype(np.int64) % self.nlon
        self._lon_t = (x - floor)

    @classmethod
    def from_model(cls, nlat, nlon):
        """由模型网格尺寸构造（高斯纬度用 numpy 的 Gauss-Legendre 节点，无需 pyfft）。

        ``pyfft.inigau`` 返回的正是同一组高斯节点（自北向南），所以这里可以完全
        脱离 exoplasim 扩展做重采样；纬度顺序按契约「自北向南递减」。
        """
        node, _weight = np.polynomial.legendre.leggauss(int(nlat))
        lat_desc = np.degrees(np.arcsin(np.sort(node)[::-1]))
        return cls(lat_desc, nlon)

    def upsample(self, field):
        """(nlat, nlon) float32 → (nlat_u, nlon_u) float32。"""
        src = np.asarray(field, dtype=np.float64)[::-1, :]      # 升序
        lat_out = (src[self._lat_i0, :] * (1.0 - self._lat_w)[:, None]
                   + src[self._lat_i0 + 1, :] * self._lat_w[:, None])
        i0 = self._lon_i0
        i1 = (i0 + 1) % self.nlon
        t = self._lon_t
        out = lat_out[:, i0] * (1.0 - t) + lat_out[:, i1] * t
        return out.astype(np.float32)

    def upsample_many(self, fields):
        return {name: self.upsample(field) for name, field in fields.items()}


def gauss_weights(nlat):
    """高斯网格面积权重（sum = 2），用于面积加权全球平均。"""
    node, weight = np.polynomial.legendre.leggauss(int(nlat))
    return weight


def global_mean(field, weights):
    """面积加权的全球平均（weights = 高斯纬向权重）。"""
    arr = np.asarray(field, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    if arr.ndim == 1:
        arr = arr[np.newaxis, :]
    total = w.sum() * arr.shape[-1]
    if total <= 0:
        return float("nan")
    return float((arr.sum(axis=-1) * w).sum() / total)


# ---------------------------------------------------------------- 风场
_PYFFT = None
_PYFFT_ERROR = None


def get_pyfft():
    """惰性 import ``exoplasim.pyfft``（只 import 该扩展，不实例化 Model）。"""
    global _PYFFT, _PYFFT_ERROR
    if _PYFFT is not None:
        return _PYFFT
    try:
        import exoplasim.pyfft as pyfft  # noqa: WPS433 (运行时按需加载)
        _PYFFT = pyfft
    except Exception as exc:  # pragma: no cover - 环境缺失时的保底
        _PYFFT_ERROR = "%s: %s" % (type(exc).__name__, exc)
        _PYFFT = False
    return _PYFFT or None


def pyfft_error():
    return _PYFFT_ERROR


def orography_grid(spectral, nlat, nlon, ntru, physfilter=True):
    """谱系数 129（oro）→ 地形高度 (nlat,nlon)，单位 m。

    与风场一样来自 ``pyfft.sp2gp``：返回 (nlon,nlat)，转置后 ``np.roll``
    半圈对齐模型输出（实测与 topomap .sra 相关系数 ≈0.97）。
    缺失/失败时返回 ``None``（前端按无地形处理）。
    """
    levels = spectral.get(129) or {}
    if not levels:
        return None
    lev = 0 if 0 in levels else min(levels)
    sp = np.asarray(levels[lev], dtype=np.float64)
    expect = 2 + 3 * int(ntru) + int(ntru) * int(ntru)
    if sp.size != expect:
        return None
    pyfft = get_pyfft()
    if pyfft is None:
        return None
    try:
        gp = np.asarray(pyfft.sp2gp(sp.reshape(-1, 1), int(nlat), int(nlon),
                                    int(ntru), int(bool(physfilter))))
        grid = np.transpose(gp[:, :, 0])
        grid = np.roll(grid, int(nlon) // 2, axis=1)
        return np.ascontiguousarray(grid, dtype=np.float32)
    except Exception:  # noqa: BLE001 - 地形是可选装饰，失败不影响主图层
        return None


def wind_lowest(spectral, nlat, nlon, nlev, ntru, plarad, physfilter=True,
                lat_rad=None):
    """谱系数 (155 散度, 138 涡度) → 最底层 u/v，形状 (nlat, nlon)。

    与 ``pyburn._transformvectorvar`` 完全一致::

        spu/spv : (2*NCSP, nlev) Fortran 序
        rdcostheta = PLARAD_m / cos(lat)
        u = reshape(transpose(spvgp(...)), (nlev, nlat, nlon))
        u = np.roll(u, nlon//2, axis=-1)      # 与模型输出 E->W 偏移对齐
        取 u[-1]（sigma 最大 = 近地面）

    ``physfilter=False`` 用于与归档 npz 的数值对照。
    """
    pyfft = get_pyfft()
    if pyfft is None:
        raise RuntimeError("exoplasim.pyfft 不可用: %s" % _PYFFT_ERROR)
    div = spectral.get(155) or {}
    vort = spectral.get(138) or {}
    if len(div) < nlev or len(vort) < nlev:
        raise ValueError("spectral levels incomplete (div=%d vort=%d nlev=%d)"
                         % (len(div), len(vort), nlev))
    spu = np.empty((2 * (ntru + 1) * (ntru + 2) // 2, nlev), dtype=np.float32,
                   order="F")
    spv = np.empty_like(spu, order="F")
    for lev in range(1, nlev + 1):
        spu[:, lev - 1] = div[lev]
        spv[:, lev - 1] = vort[lev]

    if lat_rad is None:
        sid, _gwd = pyfft.inigau(nlat)
        lat_rad = np.arcsin(sid)
    rdcostheta = plarad / np.cos(lat_rad)
    gridu, gridv = pyfft.spvgp(spu, spv, rdcostheta, nlon, ntru,
                               int(bool(physfilter)))
    shape = (nlev, nlat, nlon)
    u = np.reshape(np.transpose(gridu), shape)
    v = np.reshape(np.transpose(gridv), shape)
    u = np.roll(u, nlon // 2, axis=-1)
    v = np.roll(v, nlon // 2, axis=-1)
    return u[-1].astype(np.float32), v[-1].astype(np.float32)


# ---------------------------------------------------------------- 参数
def build_params(workdir, spec=None, header=None, days_per_year=None, plan=None,
                 completed_years=0):
    """从三份 namelist（+ 可选 spec.json + 运行进程推断）解析参数面板。

    ``plan`` 来自 :func:`discovery.infer_run_plan`：运行中的 exo_run/run_short 进程
    能给出真实的计划年数/月数（namelist 永远是 1 年，因为 exo 逐年调用
    ``model.run(years=1)``，不能作为计划时长来源）。

    返回 ``(params, raw)``：``params`` 已按契约字段名整理，未知为 ``None``；
    ``raw`` 保留原始 namelist 值与派生标志（如 ``short_run``），供进度换算使用。
    """
    spec = spec or {}
    plan = plan or {}
    conf = spec.get("configure") if isinstance(spec.get("configure"), dict) else {}
    plasim = read_namelist(os.path.join(workdir, "plasim_namelist"))
    planet = read_namelist(os.path.join(workdir, "planet_namelist"))
    radmod = read_namelist(os.path.join(workdir, "radmod_namelist"))

    nlev = int(header[6]) if header else None
    nlat = int(header[5]) if header else None
    nlon = int(header[4]) if header else None
    ntru = int(header[7]) if header else None

    mpstep = _num(plasim, "MPSTEP", 30.0)
    # MPSTEP 单位是**分钟**：一天 = 1440 / MPSTEP 步，一年固定 360 天
    physical_year_steps = (int(round((1440.0 / mpstep) * DAYS_PER_YEAR))
                           if mpstep else None)

    n_run_steps = _num(plasim, "N_RUN_STEPS", None, int)
    n_run_years = _num(plasim, "N_RUN_YEARS", 1, int)
    n_run_months = _num(plasim, "N_RUN_MONTHS", 0, int)
    n_run_days = _num(plasim, "N_RUN_DAYS", 0, int)

    # 被 harness 截短的“年”（N_RUN_STEPS < 一个物理年）；短跑时 workdir 里的
    # MOST.00000.* 只代表 6 个月，不能当成「完成整年」，因此不计入 year_index。
    short_run = bool(n_run_steps and physical_year_steps
                     and n_run_steps < physical_year_steps * 0.95)

    # 计划时长优先级：运行进程/规范推断 > namelist。
    # exo_run 逐年 run(years=1)，namelist 永远是 1 年（=12 个月），不能直接用。
    planned_months = None
    planned_years = None
    year_total = None
    plan_source = plan.get("source")
    if plan.get("months") is not None:
        planned_months = round(float(plan["months"]), 3)
        planned_years = round(planned_months / 12.0, 3)
        year_total = plan.get("years")
    elif short_run and n_run_steps and physical_year_steps:
        # harness 截短年：N_RUN_STEPS 就是这段时间的真实长度（6 个月 → 6）
        planned_months = round(n_run_steps / (physical_year_steps / 12.0), 3)
        planned_years = round(planned_months / 12.0, 3)
        year_total = None
    elif plan.get("years") is not None:
        year_total = int(plan["years"])
        planned_months = float(year_total * 12)
        planned_years = float(year_total)
    elif completed_years and int(completed_years) > 1 and not short_run:
        # 进程已退出、也没有 spec 可读（例如 collector 中途重启后看到别人跑完的 20 年 run）：
        # namelist 只写 1 年，直接用会谎报 12 个月。已完成 >1 年时，用「已完成的年数」
        # 作为计划年数的下界（对正常结束的多 runs 通常就是实际总年数）。
        year_total = int(completed_years)
        planned_months = float(completed_years * 12)
        planned_years = float(completed_years)
        plan_source = "completed_years"
    elif n_run_steps and physical_year_steps and n_run_years is not None:
        planned_months = float(n_run_years * 12 + n_run_months
                               + n_run_days / 30.0)
        planned_years = round(planned_months / 12.0, 3)
        year_total = int(n_run_years + (1 if n_run_months else 0))
    elif n_run_years is not None:
        planned_months = float(n_run_years * 12 + n_run_months)
        planned_years = round(planned_months / 12.0, 3)
        year_total = int(n_run_years)
    if year_total is None and plan.get("year_total") is not None:
        year_total = int(plan["year_total"])

    # restart_year：从 spec.configure.restartfile 的 MOST_REST.NNNNN 解析（1 基）
    restart_file = conf.get("restartfile") or plan.get("restartfile")
    restart_year = parse_restart_year(restart_file)

    run_mode = plan.get("run_mode") or spec.get("run_mode")
    if not run_mode:
        run_conf = spec.get("run") if isinstance(spec.get("run"), dict) else {}
        run_mode = run_conf.get("mode")
    if not run_mode and planned_years is not None:
        run_mode = "months" if short_run else "years"
    plarad = _num(planet, "PLARAD", None)
    if plarad is None:
        plarad = _num(conf, "radius", 1.0) * EARTH_RADIUS_M
    radius_rearth = plarad / EARTH_RADIUS_M if plarad else None
    rotspd = _num(planet, "ROTSPD", None)
    psurf_pa = _num(plasim, "PSURF", None)
    psurf_bar = psurf_pa / 1.0e5 if psurf_pa else None

    co2_ppm = _num(radmod, "CO2", None)
    pco2_bar = None
    if co2_ppm is not None and psurf_bar:
        pco2_bar = co2_ppm * 1.0e-6 * psurf_bar
    elif conf.get("pCO2") is not None:
        pco2_bar = float(conf["pCO2"])

    ncpus = _parse_ncpus(workdir, spec)

    params = {
        "resolution": ("T%d" % ntru) if ntru else None,
        "layers": nlev,
        "ncpus": ncpus,
        "model_class": spec.get("model_class"),
        "timestep_min": mpstep,
        "snapshot_steps": _num(plasim, "NSTPS", None, int),
        "steps_per_year": physical_year_steps,
        "planned_months": planned_months,
        "planned_years": planned_years,
        "year_total": year_total,
        "run_mode": run_mode,
        "restart_year": restart_year,
        "plan_source": plan_source,
        "flux_W_m2": _num(planet, "GSOL0", None),
        "startemp_K": _num(radmod, "STARBBTEMP", None),
        "radius_Rearth": radius_rearth,
        "gravity_m_s2": _num(planet, "GA", None),
        "planet_radius_m": plarad,
        "rotationperiod_d": (1.0 / rotspd) if rotspd else None,
        "synchronous": (_bool(plasim, "NSYNC", None) == 1) if plasim.get("NSYNC") else None,
        "eccentricity": _num(planet, "ECCEN", _num(planet, "ECCON", None)),
        "obliquity_deg": _num(planet, "OBLIQ", None),
        "pN2_bar": _num(conf, "pN2", None),
        "pO2_bar": _num(conf, "pO2", None),
        "pAr_bar": _num(conf, "pAr", None),
        "pCO2_bar": pco2_bar,
        "pCO2_ppmv": (pco2_bar * 1.0e6) if pco2_bar is not None else None,
        "physicsfilter": conf.get("physicsfilter"),
        "landmap": conf.get("landmap"),
        "topomap": conf.get("topomap"),
        "restartfile": conf.get("restartfile"),
        "substellar_lon_deg": _num(plasim, "FIXEDLON", None),
        "nlat_model": nlat,
        "nlon_model": nlon,
        "ntru": ntru,
        # 进度/日期换算一律按 PLASIM 约定的 360 天/年（与 step↔快照间隔一致）；
        # zsig 里的 m_days_per_year 在本环境实测会给出 359/439 之类的值，不参与换算。
        "days_per_year": int(DAYS_PER_YEAR),
        "psurf_bar": psurf_bar,
        "day_night": {},
    }
    raw = {
        "mpstep": mpstep,
        "physical_year_steps": physical_year_steps,
        "n_run_steps": n_run_steps,
        "n_run_years": n_run_years,
        "n_run_months": n_run_months,
        "n_run_days": n_run_days,
        "nstps": _num(plasim, "NSTPS", None, int),
        "planned_months": planned_months,
        "short_run": short_run,
        "year_total": year_total,
        "plan_source": plan_source,
        "n_days_per_year": _num(plasim, "N_DAYS_PER_YEAR", None, int),
    }
    return params, raw


def parse_restart_year(path):
    """从 ``.../MOST_REST.00019`` 解析 1 基物理起始年（20）；解析不到返回 None。"""
    if not path:
        return None
    match = re.search(r"MOST_REST\.(\d{5})", str(path))
    if not match:
        return None
    try:
        return int(match.group(1)) + 1
    except ValueError:
        return None


def _parse_ncpus(workdir, spec):
    """ncpus：spec 显式给出优先；其次从运行进程 cmdline 里的 ``p<N>.x`` 推断。

    workdir 里的 ``most_plasxim_run`` 是 compile.sh 的通用模板（t42_p1），不代表
    本次实际进程数，因此不作为主来源。运行中的进程 cmdline 是唯一可靠的现场证据。
    """
    if spec.get("ncpus"):
        try:
            return int(spec["ncpus"])
        except (TypeError, ValueError):
            pass
    value = _ncpus_from_proc(workdir)
    if value:
        return value
    try:
        with open(os.path.join(workdir, "most_plasim_run"), "r", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return None
    # 注意：most_plasim_run 是 compile.sh 的通用模板（t42_l10_p1），其中的 p1 不能
    # 当作本次进程数，因此只在脚本里出现显式 -np N 时才采信。
    match = re.search(r"(?:-np|--np)\s+(\d+)", text)
    if match:
        return int(match.group(1))
    return None


def _ncpus_from_proc(workdir):
    """在 /proc 里找 cwd == workdir 的模型进程，从 cmdline 读 p<N>.x（只读）。"""
    target = os.path.realpath(workdir)
    try:
        pids = [name for name in os.listdir("/proc") if name.isdigit()]
    except OSError:
        return None
    for pid in pids:
        try:
            if os.path.realpath("/proc/%s/cwd" % pid) != target:
                continue
            with open("/proc/%s/cmdline" % pid, "rb") as handle:
                cmdline = handle.read().decode("utf-8", "replace")
        except OSError:
            continue
        match = re.search(r"most_plasim_t\d+_l\d+_p(\d+)\.x", cmdline)
        if match:
            return int(match.group(1))
    return None


# ---------------------------------------------------------------- 帧构建
class FrameData:
    """一帧监控数据：统一网格上的 8 个图层 + 统计量（不可变，供多线程读）。"""

    __slots__ = ("id", "step", "year", "frame_in_year", "wall", "sim_date",
                 "ts_mean", "ntr_mean", "sic_area", "ts_area", "arrays",
                 "raw_step_code", "complete", "msg", "stats")

    def __init__(self, **kwargs):
        for key in self.__slots__:
            setattr(self, key, kwargs.get(key))

    def history_item(self):
        return {
            "id": self.id,
            "step": self.step,
            "year": self.year,
            "frame_in_year": self.frame_in_year,
            "wall": self.wall,
            "sim_date": self.sim_date,
            "ts_mean": _round(self.ts_mean, 3),
            "ntr_mean": _round(self.ntr_mean, 3),
        }


def _round(value, digits):
    if value is None:
        return None
    try:
        if math.isnan(value):
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def build_frame(raw, resampler, params, raw_params, year_index, frame_in_year,
                frame_id, physfilter=True, plarad=None):
    """RawFrame → :class:`FrameData`。

    ``raw.grid`` 里的变量按模型原始单位；这里做单位换算、风场重建、统一网格
    重采样与面积加权统计。缺失的可选变量用 0 填充（模型没开对应模块时）。
    """
    grid = raw.grid
    nlat = resampler.nlat
    nlon = resampler.nlon
    zeros = np.zeros((nlat, nlon), dtype=np.float32)
    msg = []

    def field(name):
        arr = grid.get(name)
        if arr is None or arr.shape != (nlat, nlon):
            return zeros
        return arr

    ts = field("ts")
    tas = field("tas")
    pr = (field("prl") + field("prc")) * PR_MM_DAY
    sic = field("sic")
    sit = field("sit")
    clt = field("clt")
    ps = field("ps")
    lsm = field("lsm")
    alb = field("alb")
    prw = field("prw")
    ntr = field("rst") + field("rlut")

    oro = None
    ntru = params.get("ntru")
    if ntru:
        oro = orography_grid(raw.spectral, nlat, nlon, int(ntru),
                             physfilter=physfilter)
    if oro is None or oro.shape != (nlat, nlon):
        oro = zeros

    u = v = None
    if raw.has_wind() and params.get("ntru"):
        try:
            u, v = wind_lowest(raw.spectral, nlat, nlon, int(params["layers"]),
                               int(params["ntru"]), plarad or EARTH_RADIUS_M,
                               physfilter=physfilter)
        except Exception as exc:  # noqa: BLE001 - 风场失败不能拖垮整帧
            msg.append("风场重建失败: %s" % exc)
    else:
        msg.append("谱系数不完整，风场置零")
    if u is None:
        u = zeros
        v = zeros
    spd = np.sqrt(u.astype(np.float64) ** 2 + v.astype(np.float64) ** 2).astype(np.float32)

    fields = {"ts": ts, "tas": tas, "pr": pr, "spd": spd, "u": u, "v": v,
              "sic": sic, "sit": sit, "clt": clt}
    unified = resampler.upsample_many(fields)
    unified["lsm"] = resampler.upsample(lsm)      # 只给 /api/land，不进 EXOF order
    unified["oro"] = resampler.upsample(oro)      # 地形高度（m），只给 /api/land
    weights = gauss_weights(nlat)

    # 供 /api/state.layers 的 min/max（在发布时一并算好，避免每请求重扫）
    stats = frame_stats_extra(unified)

    ts_mean = global_mean(ts, weights)
    ntr_mean = global_mean(ntr, weights)
    sic_area = global_mean((sic > 0.5).astype(np.float32), weights)
    ts_area = ts_mean

    steps_per_year = raw_params.get("physical_year_steps") or 17280
    nstps = raw_params.get("nstps") or 720
    step = int(raw.step)
    fiy = step // nstps if nstps else frame_in_year

    return FrameData(
        id=frame_id,
        step=step,
        year=int(year_index),
        frame_in_year=int(fiy),
        wall=raw.wall,
        sim_date=None,                     # 由 server 统一按进度换算后填入
        ts_mean=ts_mean,
        ntr_mean=ntr_mean,
        sic_area=sic_area,
        ts_area=ts_area,
        arrays=unified,
        raw_step_code=step,
        complete=not msg,
        msg="；".join(msg) if msg else None,
        stats=stats,
    )


def frame_stats_extra(unified):
    return {name: {"min": float(np.nanmin(arr)), "max": float(np.nanmax(arr))}
            for name, arr in unified.items()}


# ---------------------------------------------------------------- 进度
def frame_period_stats(intervals=None, wall_elapsed_s=None, months_done=None,
                       months_per_frame=0.5):
    """快照产生周期统计（秒）。

    ``intervals``：快照文件 mtime 的相邻差（秒，已在 RunMonitor 里过滤掉
    同一批 backlog 解析出的 <0.5 s 间隔）；``wall_elapsed/months_done`` 是模型
    自身的时间进度，用来估算「模型多久产出一帧」。

    返回 dict：
      * ``last_s`` / ``median_s`` / ``avg_s``：实测的上一次/中位/平均间隔（可能为 None）；
      * ``est_s``：按模型进度估算的间隔（可能为 None）；
      * ``period_s``：自适应采用值（优先实测平均，其次估算）；
      * ``source``：``mtime``（文件生成时间实测）/ ``estimated`` / ``unknown``；
      * ``samples``：参与统计的实测样本数。
    """
    vals = sorted(float(v) for v in (intervals or []) if v and v > 0)
    last_s = round(float(intervals[-1]), 2) if intervals else None
    median_s = round(vals[len(vals) // 2], 2) if vals else None
    avg_s = round(sum(vals) / len(vals), 2) if vals else None
    est_s = None
    try:
        if wall_elapsed_s and months_done and months_done > 0:
            est = float(wall_elapsed_s) / float(months_done) * float(months_per_frame or 0.5)
            if est > 0:
                est_s = round(est, 2)
    except (TypeError, ValueError, ZeroDivisionError):
        est_s = None
    if avg_s and avg_s > 0:
        return {"last_s": last_s, "median_s": median_s, "avg_s": avg_s,
                "est_s": est_s, "period_s": avg_s, "source": "mtime",
                "samples": len(vals)}
    if est_s:
        return {"last_s": last_s, "median_s": median_s, "avg_s": avg_s,
                "est_s": est_s, "period_s": est_s, "source": "estimated",
                "samples": len(vals)}
    return {"last_s": last_s, "median_s": median_s, "avg_s": avg_s,
            "est_s": est_s, "period_s": None, "source": "unknown",
            "samples": len(vals)}


def adaptive_poll_interval(base_interval, max_interval, frame_period_s=None,
                           has_frames=False, finished=False):
    """按快照产出周期自适应轮询周期（秒）。

    ``poll = clamp(frame_period / 4, base, max)``：

    * 无帧 / 未知周期 → base（默认 1 s，尽快看到首帧）；
    * 已结束 → max（没有新帧）；
    * T21 帧约 5 s → 1.25 s；T85 帧约 30 s → 封顶 max（默认 5 s），
      新快照出现后最多 ``max_interval`` 秒内显示。

    取周期 1/4 是为了在「帧间隔抖动/变快」时仍能及时命中；最大间隔给用户
    一个明确的延迟上限（可用 ``--max-interval`` 调小/调大）。
    """
    base = max(0.2, float(base_interval or 1.0))
    top = max(base, float(max_interval or base))
    if finished:
        return top
    if not has_frames or not frame_period_s or frame_period_s <= 0:
        return base
    return min(top, max(base, float(frame_period_s) / 4.0))


def progress_info(step, params, raw_params, wall_elapsed_s, frames_total=0,
                  year_index=0, active=True):
    """由「当前帧所属年份 + 该帧 step」推年/月/日、months_done、progress、ETA。

    ``year_index`` 是**当前帧所属的年**（0 基，用户要求从 0 开始数）。它不再用
    workdir 里的 ``MOST.?????.npz`` 数量直接推——因为年末 raw 被改名、npz 刚写出的
    那一小段时间里，最新帧仍属于上一年，直接用「已完成年数」会把最后一年再叠加一次
    （实测表现为：第 1 年 11 月 → 第 2 年 12 月）。帧的年份由 collector 按 raw 文件
    代次（每换一次 raw +1）维护，见 ``server.Run.raw_year``。

    * ``months_done = year_index*12 + (step+1)/每步每月``；一年内截断在 12 个月。
    * ``sim_date`` 的年/日为 1 基的“显示编号”，**年从 0 开始**（第 0 年 = 第一个模拟年）。
    """
    steps_per_year = raw_params.get("physical_year_steps") or 17280
    steps_per_month = steps_per_year / 12.0
    steps_per_day = steps_per_year / DAYS_PER_YEAR
    step = max(0, int(step or 0))
    year_index = max(0, int(year_index or 0))
    planned = params.get("planned_months")

    months_in_year = (step + 1) / steps_per_month if steps_per_month else 0.0
    # 一年最多 12 个月（step 越过整年时截断，防止末帧越界）
    months_in_year = min(12.0, months_in_year)
    months_done = year_index * 12 + months_in_year
    if planned and planned > 0:
        months_done = min(months_done, float(planned))

    progress = None
    eta_s = None
    if planned and planned > 0:
        progress = max(0.0, min(1.0, months_done / planned))
        if months_done > 0 and wall_elapsed_s is not None:
            rate = wall_elapsed_s / months_done          # 秒 / 模拟月
            eta_s = max(0.0, (planned - months_done) * rate)

    total_days = (step + 1) / steps_per_day if steps_per_day else 0.0
    day_of_year = math.fmod(total_days, DAYS_PER_YEAR)
    if day_of_year == 0.0 and total_days > 0.0:
        day_of_year = DAYS_PER_YEAR                      # 年末最后一帧算第 12 月
    month1 = min(12, max(1, int(math.ceil(day_of_year / 30.0))))
    day1 = int(round(day_of_year - (month1 - 1) * 30.0))
    day1 = min(30, max(1, day1))                          # 1 基：月初显示第 1 天
    return {
        "step": step,
        "steps_per_year": steps_per_year,
        "year_index": year_index,
        "year_no": year_index,                           # 0 基（第 0 年 = 第一年）
        "months_done": round(months_done, 3),
        "progress": _round(progress, 4),
        "eta_s": _round(eta_s, 1),
        "sim_day": round(day_of_year, 2),
        "sim_date": "第 %d 年 · 第 %d 月 · %d 天" % (year_index, month1, day1),
        "month": month1,
        "day": day1,
    }


# ---------------------------------------------------------------- ring buffer
class FrameStore:
    """每个 run 独立的 ring buffer（默认 48 帧）。"""

    def __init__(self, capacity=RING_FRAMES):
        self.capacity = int(capacity)
        self._frames = []          # 升序
        self._by_id = {}
        self.next_id = 0
        self.total = 0             # 历史累计帧数（超过 capacity 后仍增长）

    def __len__(self):
        return len(self._frames)

    @property
    def latest(self):
        return self._frames[-1] if self._frames else None

    def add(self, frame):
        frame.id = self.next_id
        self.next_id += 1
        self.total += 1
        self._frames.append(frame)
        self._by_id[frame.id] = frame
        while len(self._frames) > self.capacity:
            old = self._frames.pop(0)
            self._by_id.pop(old.id, None)
        return frame

    def get(self, frame_id):
        if frame_id is None:
            return self.latest
        return self._by_id.get(int(frame_id))

    def items(self):
        return list(self._frames)

    def history(self):
        return [frame.history_item() for frame in self._frames]

    def year_series(self):
        """按年聚合：``ts_area`` / ``ntr`` / ``sic_area`` 为该年各帧面积平均的均值。"""
        buckets = {}
        order = []
        for frame in self._frames:
            year = frame.year
            if year not in buckets:
                buckets[year] = {"ts": [], "ntr": [], "sic": [], "steps": []}
                order.append(year)
            bucket = buckets[year]
            bucket["ts"].append(frame.ts_area)
            bucket["ntr"].append(frame.ntr_mean)
            bucket["sic"].append(frame.sic_area)
            bucket["steps"].append(_round(frame.ts_area, 3))
        series = []
        for year in order:
            bucket = buckets[year]
            series.append({
                "year": year,
                "ts_area": _mean(bucket["ts"]),
                "ntr": _mean(bucket["ntr"]),
                "sic_area": _mean(bucket["sic"]),
                "ts_at_step": bucket["steps"],
            })
        return series


def _mean(values):
    clean = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 3)


# ---------------------------------------------------------------- EXOF
def frame_exof(frame, order=FRAME_ORDER, units=FRAME_UNITS):
    """按契约 §2.2 打包 EXOF 二进制。"""
    header = {
        "id": frame.id,
        "step": frame.step,
        "year": frame.year,
        "frame_in_year": frame.frame_in_year,
        "nlat": frame.arrays[order[0]].shape[0],
        "nlon": frame.arrays[order[0]].shape[1],
        "order": list(order),
        "units": {name: units.get(name, "1") for name in order},
        "complete": bool(frame.complete),
    }
    payload = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    parts = [b"EXOF", struct.pack("<II", 1, len(payload)), payload]
    for name in order:
        parts.append(np.ascontiguousarray(frame.arrays[name], dtype="<f4").tobytes())
    return b"".join(parts)


def land_exof(frame):
    """``/api/land``：EXOF 封装 ``order=["lsm","oro"]``（陆海掩膜 + 地形高度 m）。"""
    lsm = frame.arrays.get("lsm")
    if lsm is None:
        lsm = np.zeros_like(frame.arrays["ts"])
    oro = frame.arrays.get("oro")
    if oro is None:
        oro = np.zeros_like(lsm)
    header = {
        "id": frame.id,
        "step": frame.step,
        "year": frame.year,
        "nlat": int(lsm.shape[0]),
        "nlon": int(lsm.shape[1]),
        "order": ["lsm", "oro"],
        "units": {"lsm": "1", "oro": "m"},
        "complete": True,
    }
    payload = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return b"".join([b"EXOF", struct.pack("<II", 1, len(payload)), payload,
                     np.ascontiguousarray(lsm, dtype="<f4").tobytes(),
                     np.ascontiguousarray(oro, dtype="<f4").tobytes()])
