#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_fixture.py — 从归档的 `*_snapshot.npz` 生成前端开发用 fixture。

产物（monitor/test/fixture/）：
  runs.json                  → /api/runs 的载荷（两个假 run，验证多模拟选择器）
  <run>/state.json           → /api/state 的模板（真参数 + 统一网格 + history）
  <run>/frames.bin           → 连续 EXOF 帧的拼接
  <run>/frames.json          → [{offset, length, meta}, ...]
  <run>/land.bin             → EXOF(order=["lsm"])

EXOF 格式与 CONTRACT.md §2.2 完全一致，mock_server.py 直接回放这些字节。

用法（Windows 侧 python 或 WSL venv 均可）：
  python monitor/test/make_fixture.py \
      --npz "monitor/_devrun/runs/<id>/pleiroma_snapshot.npz" \
      --spec "monitor/_devrun/runs/<id>/spec.json" \
      --out monitor/test/fixture --frames 12
"""
from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys

import numpy as np


def gauss_lat_weights(nlat: int):
    from numpy.polynomial.legendre import leggauss

    nodes, weights = leggauss(nlat)
    return nodes, weights


def resample_uniform(field: np.ndarray, lat_src_desc: np.ndarray, nlat_u: int, nlon_u: int) -> np.ndarray:
    """(nlat,nlon) 高斯纬度（自北向南）→ (nlat_u,nlon_u) 均匀网格。"""
    nlat, nlon = field.shape
    lat_u = 90.0 - (np.arange(nlat_u) + 0.5) * (180.0 / nlat_u)
    lat_asc = lat_src_desc[::-1]
    field_flip = field[::-1, :]
    out_lat = np.empty((nlat_u, nlon), dtype=np.float64)
    for j in range(nlon):
        out_lat[:, j] = np.interp(lat_u, lat_asc, field_flip[:, j])
    # 经度周期线性插值
    lon_src = np.arange(nlon) * (360.0 / nlon)
    lon_u = np.arange(nlon_u) * (360.0 / nlon_u)
    out = np.empty((nlat_u, nlon_u), dtype=np.float32)
    x = lon_u / 360.0 * nlon
    i0 = np.floor(x).astype(int) % nlon
    i1 = (i0 + 1) % nlon
    t = (x - np.floor(x)).astype(np.float32)
    out[:] = out_lat[:, i0] * (1.0 - t) + out_lat[:, i1] * t
    return out


def exof_blob(header: dict, arrays: list[np.ndarray]) -> bytes:
    hjson = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    parts = [b"EXOF", struct.pack("<II", 1, len(hjson)), hjson]
    for arr in arrays:
        parts.append(np.ascontiguousarray(arr, dtype="<f4").tobytes())
    return b"".join(parts)


def build_params(spec: json, grid: dict) -> dict:
    resolved = (spec or {}).get("resolved") or {}
    cfg = resolved.get("configure") or (spec or {}).get("input", {}).get("configure") or {}
    p = resolved.get("params") or {}
    model_name = (spec or {}).get("input", {}).get("modelname") or "pleiroma"
    pco2_bar = cfg.get("pCO2")
    return {
        "resolution": resolved.get("resolution") or (spec or {}).get("input", {}).get("resolution") or "T21",
        "layers": resolved.get("layers") or (spec or {}).get("input", {}).get("layers") or 10,
        "ncpus": resolved.get("ncpus") or 8,
        "model_class": resolved.get("model_class") or "Model",
        "timestep_min": cfg.get("timestep", 30.0),
        "snapshot_steps": cfg.get("snapshots", 720),
        "steps_per_year": grid.get("steps_per_year", 17280),
        "planned_months": grid.get("planned_months", 6),
        "flux_W_m2": cfg.get("flux"),
        "startemp_K": cfg.get("startemp"),
        "radius_Rearth": cfg.get("radius"),
        "gravity_m_s2": cfg.get("gravity"),
        "planet_radius_m": (cfg.get("radius") or 1.0) * 6371220.0,
        "rotationperiod_d": cfg.get("rotationperiod"),
        "synchronous": bool(cfg.get("synchronous")),
        "eccentricity": cfg.get("eccentricity"),
        "obliquity_deg": cfg.get("obliquity"),
        "pN2_bar": cfg.get("pN2"),
        "pO2_bar": cfg.get("pO2"),
        "pAr_bar": cfg.get("pAr"),
        "pCO2_bar": pco2_bar,
        "pCO2_ppmv": None if pco2_bar is None else round(pco2_bar * 1e6, 1),
        "physicsfilter": cfg.get("physicsfilter"),
        "landmap": cfg.get("landmap"),
        "topomap": cfg.get("topomap"),
        "restartfile": cfg.get("restartfile"),
        "substellar_lon_deg": 180.0,
        "nlat_model": grid["nlat_model"],
        "nlon_model": grid["nlon_model"],
        "ntru": grid.get("ntru", 21),
        "modelname": model_name,
    }


def fmt_date(step: float, steps_per_day: float) -> str:
    day = step / steps_per_day
    year = int(day // 360)
    month = int((day % 360) // 30)
    dom = int(day % 30)
    return "第 %d 年 · 第 %d 月 · 第 %d 天" % (year, month, dom)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--npz", required=True)
    parser.add_argument("--spec", default=None)
    parser.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixture"))
    parser.add_argument("--frames", type=int, default=12)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    z = np.load(args.npz, allow_pickle=True)
    lat = np.asarray(z["lat"], dtype=np.float64)
    lon = np.asarray(z["lon"], dtype=np.float64)
    time = np.asarray(z["time"], dtype=np.float64)
    nlat, nlon = lat.size, lon.size
    nlat_u, nlon_u = 2 * nlat, 2 * nlon
    lat_u = 90.0 - (np.arange(nlat_u) + 0.5) * (180.0 / nlat_u)
    lon_u = np.arange(nlon_u) * (360.0 / nlon_u)

    nframes = min(args.frames, time.size)
    steps_per_year = 17280.0
    steps_per_day = 17280.0 / 360.0
    planned_months = 6

    # 逐帧派生量
    ua = np.asarray(z["ua"], dtype=np.float32) if "ua" in z else None
    va = np.asarray(z["va"], dtype=np.float32) if "va" in z else None
    spd = np.asarray(z["spd"], dtype=np.float32) if "spd" in z else None
    keys = ["ts", "tas", "pr", "spd", "u", "v", "sic", "sit", "clt"]

    spec = None
    if args.spec and os.path.exists(args.spec):
        with open(args.spec, "r", encoding="utf-8") as handle:
            spec = json.load(handle)
        spec_input = spec.get("input") or {}
        spec_input.setdefault("modelname", os.path.basename(args.npz).split("_")[0])
    params = build_params(spec, {"nlat_model": nlat, "nlon_model": nlon,
                                 "steps_per_year": steps_per_year,
                                 "planned_months": planned_months})

    run_id = args.run_id or ("20260101-000000_" + str(params["modelname"]))
    run_dir = os.path.join(out_dir, "runA")
    os.makedirs(run_dir, exist_ok=True)

    grid = {"nlat": nlat_u, "nlon": nlon_u,
            "lat": [round(float(v), 6) for v in lat_u],
            "lon": [round(float(v), 6) for v in lon_u]}
    steps_per_day = steps_per_year / 360.0
    weights = gauss_lat_weights(nlat)[1]

    blobs = []
    metas = []
    history = []
    ts_series = []
    ntr_series = []
    for index in range(nframes):
        ts = np.asarray(z["ts"][index], dtype=np.float32)
        tas = np.asarray(z["tas"][index], dtype=np.float32)
        pr = (np.asarray(z["prl"][index], dtype=np.float32)
              + np.asarray(z["prc"][index], dtype=np.float32)) * (86400.0 * 1000.0)
        sic = np.asarray(z["sic"][index], dtype=np.float32)
        sit = np.asarray(z["sit"][index], dtype=np.float32) if "sit" in z else np.zeros_like(ts)
        clt = np.asarray(z["clt"][index], dtype=np.float32)
        if spd is not None:
            spd2d = np.asarray(spd[index][-1], dtype=np.float32)
        elif ua is not None and va is not None:
            spd2d = np.hypot(ua[index][-1], va[index][-1]).astype(np.float32)
        else:
            spd2d = np.zeros_like(ts)
        u2d = np.asarray(ua[index][-1], dtype=np.float32) if ua is not None else np.zeros_like(ts)
        v2d = np.asarray(va[index][-1], dtype=np.float32) if va is not None else np.zeros_like(ts)

        fields = {
            "ts": ts, "tas": tas, "pr": pr, "spd": spd2d, "u": u2d, "v": v2d,
            "sic": sic, "sit": sit, "clt": clt,
        }
        order = [k for k in keys]
        uniform = [resample_uniform(fields[k], lat, nlat_u, nlon_u) for k in order]
        step = float(time[index])
        ts_mean = float(np.sum(weights * ts.mean(axis=1)) / np.sum(weights)) if ts.shape[0] == nlat else float(ts.mean())
        ntr_inst = None
        if "ntr" in z:
            ntr_inst = np.asarray(z["ntr"][index], dtype=np.float32)
        ntr_mean = float(np.mean(ntr_inst)) if ntr_inst is not None else None

        header = {
            "id": index, "step": int(round(step)), "year": 0,
            "frame_in_year": index, "nlat": nlat_u, "nlon": nlon_u,
            "order": order,
            "units": {"ts": "K", "tas": "K", "pr": "mm/d", "spd": "m/s",
                      "u": "m/s", "v": "m/s", "sic": "1", "sit": "m", "clt": "1"},
            "complete": True,
        }
        blob = exof_blob(header, uniform)
        blobs.append(blob)
        metas.append({"id": index, "step": int(round(step)), "year": 0,
                      "frame_in_year": index, "offset": 0, "length": len(blob)})
        ts_series.append(ts_mean)
        if ntr_mean is not None:
            ntr_series.append(ntr_mean)
        history.append({
            "id": index, "step": int(round(step)), "year": 0, "frame_in_year": index,
            "wall": 0.0, "sim_date": fmt_date(step, steps_per_day),
            "ts_mean": round(ts_mean, 3),
            "ntr_mean": None if ntr_mean is None else round(ntr_mean, 3),
        })

    # 拼接 + 回填 offset
    body = bytearray()
    for meta, blob in zip(metas, blobs):
        meta["offset"] = len(body)
        body.extend(blob)
    with open(os.path.join(run_dir, "frames.bin"), "wb") as handle:
        handle.write(body)
    with open(os.path.join(run_dir, "frames.json"), "w", encoding="utf-8") as handle:
        json.dump(metas, handle)

    # 陆地掩膜
    lsm_src = np.asarray(z["lsm"][0], dtype=np.float32) if "lsm" in z else np.zeros((nlat, nlon), dtype=np.float32)
    lsm_u = resample_uniform(lsm_src, lat, nlat_u, nlon_u)
    with open(os.path.join(run_dir, "land.bin"), "wb") as handle:
        handle.write(exof_blob({"order": ["lsm"], "nlat": nlat_u, "nlon": nlon_u}, [lsm_u]))

    ts_area = float(np.mean(ts_series))
    year_series = [{"year": 0, "ts_area": round(ts_area, 3),
                    "ntr": round(float(np.mean(ntr_series)), 3) if ntr_series else None,
                    "sic_area": 0.42, "ts_at_step": [round(v, 3) for v in ts_series[:6]]}]
    layers = {
        "ts": {"name": "地表温度", "unit": "K", "range": [180, 330], "kind": "scalar"},
        "tas": {"name": "2 m 气温", "unit": "K", "range": [180, 330], "kind": "scalar"},
        "pr": {"name": "降水", "unit": "mm/d", "range": [0, 20], "kind": "scalar"},
        "spd": {"name": "风速", "unit": "m/s", "range": [0, 30], "kind": "scalar"},
        "sic": {"name": "海冰覆盖", "unit": "1", "range": [0, 1], "kind": "scalar"},
        "sit": {"name": "海冰厚度", "unit": "m", "range": [0, 9], "kind": "scalar"},
        "clt": {"name": "云量", "unit": "1", "range": [0, 1], "kind": "scalar"},
    }
    state = {
        "ok": True, "version": "1.0",
        "server": {"pid": 0, "cpu_percent": 0.6, "rss_kib": 42000, "uptime_s": 60.0,
                   "poll_ms": 1.4, "frames_parsed": nframes, "bytes_read": len(body)},
        "run": {
            "run_id": run_id, "modelname": params["modelname"], "workdir": "/opt/exoplasim/runs/" + run_id,
            "outdir": None, "status": "running", "status_text": "运行中（fixture 回放）",
            "wall_elapsed_s": 35.0, "year_index": 0, "year_no": 0,
            "year_total": 20, "planned_years": 20, "restart_year": 20, "run_mode": "years",
            "frames_total": nframes,
            "planned_months": 240, "months_done": 1.5, "progress": 0.00625,
            "eta_s": 25.0, "sim_day": 45.0, "sim_date": "第 0 年 · 第 2 月 · 第 15 天",
            "step": 719, "steps_per_year": int(steps_per_year), "msg": None,
        },
        "grid": grid, "layers": layers,
        "frame": None, "history": history, "year_series": year_series,
        "params": params,
        "meta": {"updated": 0, "source": "plasim_snapshot", "source_size": 0,
                 "note": "fixture 回放；真实数据由 collector 提供"},
    }
    params.update({"planned_months": 240, "planned_years": 20, "year_total": 20,
                   "run_mode": "years", "restart_year": 20})
    # 每层 min/max（直接从源 npz 算，不解析 EXOF）
    for name in layers:
        vals = []
        for index in range(nframes):
            if name == "pr":
                a = (np.asarray(z["prl"][index], dtype=np.float32) + np.asarray(z["prc"][index], dtype=np.float32)) * 8.64e7
            elif name in ("ts", "tas", "sic", "sit", "clt"):
                a = np.asarray(z[name][index], dtype=np.float32)
            elif name == "spd":
                a = np.asarray(spd[index][-1], dtype=np.float32) if spd is not None else np.zeros((nlat, nlon), np.float32)
            else:
                continue
            vals.extend([float(np.nanmin(a)), float(np.nanmax(a))])
        layers[name]["min"] = round(min(vals), 3) if vals else None
        layers[name]["max"] = round(max(vals), 3) if vals else None

    with open(os.path.join(run_dir, "state.json"), "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=1)

    # 第二个 run（更短，用于验证多模拟切换）：只取前 6 帧，参数微调
    run_b = os.path.join(out_dir, "runB")
    os.makedirs(run_b, exist_ok=True)
    n_b = min(6, nframes)
    body_b = bytearray()
    metas_b = []
    for index in range(n_b):
        blob = blobs[index]
        metas_b.append({"id": index, "step": int(metas[index]["step"]), "year": 0,
                        "frame_in_year": index, "offset": len(body_b), "length": len(blob)})
        body_b.extend(blob)
    with open(os.path.join(run_b, "frames.bin"), "wb") as handle:
        handle.write(body_b)
    with open(os.path.join(run_b, "frames.json"), "w", encoding="utf-8") as handle:
        json.dump(metas_b, handle)
    with open(os.path.join(run_b, "land.bin"), "wb") as handle:
        handle.write(exof_blob({"order": ["lsm"], "nlat": nlat_u, "nlon": nlon_u}, [lsm_u]))
    state_b = json.loads(json.dumps(state))
    state_b["run"]["run_id"] = "20260101-000000_pleiroma_b"
    state_b["run"]["modelname"] = "pleiroma_b"
    state_b["run"]["planned_months"] = 36
    state_b["run"]["planned_years"] = 3
    state_b["run"]["year_total"] = 3
    state_b["run"]["year_no"] = 0
    state_b["run"]["progress"] = 0.04
    state_b["run"]["status_text"] = "运行中（fixture 回放 B）"
    state_b["params"]["modelname"] = "pleiroma_b"
    state_b["params"]["ncpus"] = 16
    state_b["params"]["flux_W_m2"] = 1200.0
    state_b["params"]["planned_months"] = 36
    state_b["params"]["planned_years"] = 3
    state_b["history"] = state_b["history"][:n_b]
    state_b["run"]["frames_total"] = n_b
    with open(os.path.join(run_b, "state.json"), "w", encoding="utf-8") as handle:
        json.dump(state_b, handle, ensure_ascii=False, indent=1)

    runs = {
        "ok": True,
        "default": run_id,
        "runs": [
            {"run_id": run_id, "modelname": params["modelname"], "status": "running",
             "workdir": "/opt/exoplasim/runs/" + run_id, "frame_count": nframes,
             "year_index": 0, "year_no": 0, "year_total": 20, "months_done": round(nframes * 0.5, 1),
             "planned_months": 240, "planned_years": 20,
             "progress": round(min(1.0, nframes * 0.5 / 240), 4),
             "wall_elapsed_s": 35.0, "last_update": 0,
             "summary": {"resolution": params["resolution"], "layers": params["layers"],
                         "ncpus": params["ncpus"], "flux_W_m2": params["flux_W_m2"]}},
            {"run_id": "20260101-000000_pleiroma_b", "modelname": "pleiroma_b", "status": "running",
             "workdir": "/opt/exoplasim/runs/20260101-000000_pleiroma_b", "frame_count": n_b,
             "year_index": 0, "year_no": 0, "year_total": 3, "months_done": 1.5, "planned_months": 36,
             "planned_years": 3, "progress": 0.04, "wall_elapsed_s": 30.0, "last_update": 0,
             "summary": {"resolution": "T21", "layers": 10, "ncpus": 16, "flux_W_m2": 1200.0}},
        ],
    }
    with open(os.path.join(out_dir, "runs.json"), "w", encoding="utf-8") as handle:
        json.dump(runs, handle, ensure_ascii=False, indent=1)

    print("fixture written to", out_dir)
    print("  frames:", nframes, "frame bytes:", len(blobs[0]), "grid:", nlat_u, "x", nlon_u)
    return 0


if __name__ == "__main__":
    sys.exit(main())
