#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_api.py — 独立验收 collector（不 import collector 代码，只走 HTTP）。

检查项：
  1. /api/runs 结构、run 唯一性、default 合法
  2. /api/state 必填字段、网格尺寸、图层元数据、params 关键量
  3. /api/frame EXOF 解析：magic/version/order/shape/finite/范围
  4. /api/land EXOF 解析
  5. 与 run 结束后归档的 *_snapshot.npz 逐帧对照（ts/pr/sic 应≈0；风默认开物理滤波，
     只在 --no-physfilter-expected 时严格对照）
  6. 多 run：指定 --also-run 时再取另一个 run 的 state/frame

用法（WSL venv python）：
  /opt/exoplasim/env/bin/python "<repo>/monitor/test/verify_api.py" \
      --base http://127.0.0.1:8765 \
      --npz "<archive>/pleiroma_snapshot.npz"
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import urllib.request

import numpy as np


def fetch(base: str, path: str) -> bytes:
    url = base.rstrip("/") + path
    with urllib.request.urlopen(url, timeout=15) as response:
        return response.read()


def fetch_json(base: str, path: str):
    return json.loads(fetch(base, path).decode("utf-8"))


def parse_exof(body: bytes):
    assert body[:4] == b"EXOF", "bad magic"
    version, hlen = struct.unpack("<II", body[4:12])
    header = json.loads(body[12:12 + hlen].decode("utf-8"))
    offset = 12 + hlen
    arrays = {}
    n = header["nlat"] * header["nlon"]
    for name in header["order"]:
        arrays[name] = np.frombuffer(body, dtype="<f4", count=n, offset=offset).reshape(
            header["nlat"], header["nlon"]).copy()
        offset += n * 4
    assert offset == len(body), "trailing bytes: %d != %d" % (offset, len(body))
    return header, arrays


def resample_uniform(field: np.ndarray, lat_src_desc: np.ndarray, nlat_u: int, nlon_u: int) -> np.ndarray:
    nlat, nlon = field.shape
    lat_u = 90.0 - (np.arange(nlat_u) + 0.5) * (180.0 / nlat_u)
    lat_asc = lat_src_desc[::-1]
    flipped = field[::-1, :]
    lat_out = np.empty((nlat_u, nlon), dtype=np.float64)
    for j in range(nlon):
        lat_out[:, j] = np.interp(lat_u, lat_asc, flipped[:, j])
    lon_u = np.arange(nlon_u) * (360.0 / nlon_u)
    x = lon_u / 360.0 * nlon
    i0 = np.floor(x).astype(int) % nlon
    i1 = (i0 + 1) % nlon
    t = (x - np.floor(x)).astype(np.float64)
    return (lat_out[:, i0] * (1 - t) + lat_out[:, i1] * t).astype(np.float32)


def gauss_weights(nlat: int):
    from numpy.polynomial.legendre import leggauss
    return leggauss(nlat)[1]


def check(condition: bool, message: str, failures: list):
    if condition:
        print("  [ok]  " + message)
    else:
        print("  [FAIL] " + message)
        failures.append(message)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--run", default=None, help="指定 run_id；缺省取 default")
    parser.add_argument("--npz", default=None, help="归档 *_snapshot.npz，用于逐帧数值对照")
    parser.add_argument("--no-physfilter-expected", action="store_true",
                        help="collector 用 --no-physfilter 启动时，严格对照 u/v/spd")
    parser.add_argument("--also-run", default=None, help="并发 run 的 run_id，验证多 run")
    args = parser.parse_args(argv)

    failures = []
    print("== /api/runs ==")
    runs_doc = fetch_json(args.base, "/api/runs")
    check(runs_doc.get("ok") is True, "/api/runs ok", failures)
    runs = runs_doc.get("runs") or []
    check(len(runs) >= 1, "至少一个 run（实际 %d）" % len(runs), failures)
    ids = [r["run_id"] for r in runs]
    check(len(ids) == len(set(ids)), "run_id 唯一", failures)
    check(runs_doc.get("default") in ids, "default 在列表内", failures)
    for entry in runs:
        print("   - %s [%s] frames=%s workdir=%s" % (
            entry["run_id"], entry.get("status"), entry.get("frame_count"),
            entry.get("workdir")))
        check(entry.get("workdir", "").startswith("/opt/exoplasim/runs"),
              "workdir 在 /opt/exoplasim/runs", failures)

    run_id = args.run or runs_doc["default"]
    query = "?run=" + urllib.parse.quote(run_id)

    print("\n== /api/state ==")
    state = fetch_json(args.base, "/api/state" + query)
    check(state.get("ok") is True, "state ok", failures)
    for key in ("run", "grid", "layers", "params", "meta", "history"):
        check(key in state, "state.%s 存在" % key, failures)
    grid = state["grid"]
    nlat, nlon = grid["nlat"], grid["nlon"]
    check(len(grid["lat"]) == nlat and len(grid["lon"]) == nlon, "grid 长度一致", failures)
    check(abs(grid["lat"][0] - (90 - 90.0 / nlat)) < 1.5, "lat[0] 在最北", failures)
    check(grid["lat"][0] > grid["lat"][-1], "lat 自北向南", failures)
    check(0 <= grid["lon"][0] < 360 and grid["lon"][-1] < 360, "lon 范围 [0,360)", failures)
    for name, layer in (state["layers"] or {}).items():
        for key in ("name", "unit", "range", "min", "max"):
            check(key in layer, "layers.%s.%s" % (name, key), failures)
    params = state["params"]
    for key in ("resolution", "layers", "timestep_min", "steps_per_year",
                "flux_W_m2", "radius_Rearth", "gravity_m_s2", "planet_radius_m"):
        check(params.get(key) is not None, "params.%s 非空" % key, failures)
    check(params.get("steps_per_year", 0) > 0, "steps_per_year > 0", failures)
    print("   run=%s status=%s frames=%s months=%.2f/%.2f progress=%.2f"
          % (state["run"]["run_id"], state["run"]["status"], state["run"].get("frames_total"),
             state["run"].get("months_done") or 0, state["run"].get("planned_months") or 0,
             state["run"].get("progress") or 0))
    print("   grid=%dx%d  params: %s %s layers ncpus=%s dt=%s min"
          % (nlat, nlon, params.get("resolution"), params.get("layers"),
             params.get("ncpus"), params.get("timestep_min")))
    server = state.get("server") or {}
    print("   server: cpu=%.2f%% poll=%.2f ms frames=%s bytes=%s"
          % (server.get("cpu_percent") or -1, server.get("poll_ms") or -1,
             server.get("frames_parsed"), server.get("bytes_read")))
    check((server.get("cpu_percent") or 0) < 5.0, "collector cpu < 5%（目标 <2%）", failures)

    print("\n== /api/frame ==")
    body = fetch(args.base, "/api/frame" + query + "&id=latest")
    header, arrays = parse_exof(body)
    check(header["nlat"] == nlat and header["nlon"] == nlon, "frame 网格与 state 一致", failures)
    check(set(header["order"]) >= {"ts", "pr", "spd", "sic", "sit", "u", "v", "clt", "tas"},
          "frame 图层齐全（含 sit）: %s" % ",".join(header["order"]), failures)
    for name, arr in arrays.items():
        check(arr.shape == (nlat, nlon), "%s shape %s" % (name, arr.shape), failures)
        check(np.isfinite(arr).all(), "%s 全部有限" % name, failures)
    ts = arrays["ts"]
    print("   ts  min/max/mean = %.2f / %.2f / %.2f K" % (ts.min(), ts.max(), ts.mean()))
    print("   pr  min/max      = %.3f / %.3f mm/d" % (arrays["pr"].min(), arrays["pr"].max()))
    print("   spd min/max      = %.2f / %.2f m/s" % (arrays["spd"].min(), arrays["spd"].max()))
    print("   sit min/max      = %.2f / %.2f m" % (arrays["sit"].min(), arrays["sit"].max()))
    check(150 < ts.mean() < 400, "ts 全球平均物理合理", failures)
    check(arrays["pr"].min() >= 0, "pr 非负", failures)
    check(arrays["sic"].min() >= 0 and arrays["sic"].max() <= 1.001, "sic 在 [0,1]", failures)
    check(arrays["sit"].min() >= 0 and arrays["sit"].max() <= 9.001, "sit 在 [0,9] m", failures)
    check(arrays["clt"].min() >= 0 and arrays["clt"].max() <= 1.001, "clt 在 [0,1]（覆盖率）", failures)

    run = state["run"]
    if run.get("year_no") is not None:
        check(run["year_no"] >= 1, "year_no 是 1 基（= %s）" % run["year_no"], failures)
        check(run.get("sim_date") and "第 0 年" not in str(run.get("sim_date")),
              "sim_date 不是第 0 年（= %s）" % run.get("sim_date"), failures)
    if run.get("planned_months"):
        check(run["planned_months"] >= (run.get("months_done") or 0) * 0.5,
              "planned_months 与 months_done 不矛盾（%s vs %s）"
              % (run["planned_months"], run.get("months_done")), failures)

    print("\n== /api/land ==")
    lbody = fetch(args.base, "/api/land" + query)
    lheader, larrays = parse_exof(lbody)
    check("lsm" in larrays, "land 含 lsm", failures)
    check(larrays["lsm"].shape == (nlat, nlon), "land 网格一致", failures)

    if args.also_run:
        print("\n== 多 run: %s ==" % args.also_run)
        other_q = "?run=" + urllib.parse.quote(args.also_run)
        other = fetch_json(args.base, "/api/state" + other_q)
        check(other.get("ok") is True, "第二个 run state ok", failures)
        other_frame = fetch(args.base, "/api/frame" + other_q + "&id=latest")
        oheader, _ = parse_exof(other_frame)
        check(oheader["nlat"] == other["grid"]["nlat"], "第二个 run frame 网格一致", failures)
        check(other["run"]["run_id"] == args.also_run, "第二个 run id 一致", failures)

    if args.npz:
        print("\n== 与 npz 逐帧对照 ==")
        z = np.load(args.npz, allow_pickle=True)
        lat_src = np.asarray(z["lat"], dtype=np.float64)
        time = np.asarray(z["time"], dtype=np.float64)
        steps = [int(round(v)) for v in time]
        # 取 collector /api/state 历史里出现的 step，映射到 npz 帧
        hist = state.get("history") or []
        print("   npz 帧数=%d, collector history=%d" % (len(steps), len(hist)))
        worst = {}
        compared = 0
        for item in hist:
            step = item["step"]
            if step not in steps:
                continue
            index = steps.index(step)
            fbody = fetch(args.base, "/api/frame" + query + "&id=%d" % item["id"])
            fheader, farr = parse_exof(fbody)
            ref_ts = resample_uniform(np.asarray(z["ts"][index], dtype=np.float32), lat_src, nlat, nlon)
            ref_pr = resample_uniform(
                (np.asarray(z["prl"][index], dtype=np.float32)
                 + np.asarray(z["prc"][index], dtype=np.float32)) * 8.64e7, lat_src, nlat, nlon)
            ref_sic = resample_uniform(np.asarray(z["sic"][index], dtype=np.float32), lat_src, nlat, nlon)
            ref_sit = resample_uniform(np.asarray(z["sit"][index], dtype=np.float32), lat_src, nlat, nlon)
            for name, ref in (("ts", ref_ts), ("pr", ref_pr), ("sic", ref_sic), ("sit", ref_sit)):
                err = float(np.nanmax(np.abs(farr[name] - ref)))
                worst[name] = max(worst.get(name, 0.0), err)
            if args.no_physfilter_expected and "ua" in z:
                ref_u = resample_uniform(np.asarray(z["ua"][index][-1], dtype=np.float32), lat_src, nlat, nlon)
                ref_v = resample_uniform(np.asarray(z["va"][index][-1], dtype=np.float32), lat_src, nlat, nlon)
                worst["u"] = max(worst.get("u", 0.0), float(np.nanmax(np.abs(farr["u"] - ref_u))))
                worst["v"] = max(worst.get("v", 0.0), float(np.nanmax(np.abs(farr["v"] - ref_v))))
            compared += 1
        print("   对照帧数:", compared)
        for name, err in sorted(worst.items()):
            tol = {"ts": 0.02, "pr": 0.05, "sic": 0.02, "sit": 0.02, "u": 0.05, "v": 0.05}[name]
            check(err <= tol, "max|Δ%s| = %.5f (tol %.3f)" % (name, err, tol), failures)

    print("\n== 结论 ==")
    if failures:
        print("FAILED (%d)" % len(failures))
        for item in failures:
            print("  - " + item)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    import urllib.parse  # noqa: E402
    sys.exit(main())
