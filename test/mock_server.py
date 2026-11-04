#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mock_server.py — 用 fixture 回放，模拟 collector 的 HTTP 接口（供前端独立开发/验收）。

  python monitor/test/mock_server.py --port 8766 [--no-advance]

接口与 CONTRACT.md §2 一致：/api/runs /api/state /api/frame /api/land + 静态 web/。
每 2 s 前进一帧（循环），两个 run 用不同相位，便于验证多模拟选择器。
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
WEB_ROOT = os.path.abspath(os.path.join(HERE, "..", "web"))
FIXTURE = os.path.join(HERE, "fixture")

MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json; charset=utf-8",
        ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon"}

RUNS = {}      # run_id -> dict(frames=list meta, blobs bytes, state template, step counter)
LOCK = threading.Lock()
STARTED = time.time()


def load_runs() -> None:
    with open(os.path.join(FIXTURE, "runs.json"), "r", encoding="utf-8") as handle:
        runs_doc = json.load(handle)
    for entry in runs_doc["runs"]:
        run_id = entry["run_id"]
        run_dir = os.path.join(FIXTURE, "runA" if entry["modelname"] != "pleiroma_b" else "runB")
        with open(os.path.join(run_dir, "state.json"), "r", encoding="utf-8") as handle:
            template = json.load(handle)
        with open(os.path.join(run_dir, "frames.json"), "r", encoding="utf-8") as handle:
            frames = json.load(handle)
        with open(os.path.join(run_dir, "frames.bin"), "rb") as handle:
            body = handle.read()
        with open(os.path.join(run_dir, "land.bin"), "rb") as handle:
            land = handle.read()
        RUNS[run_id] = {"entry": entry, "frames": frames, "body": body, "land": land,
                        "template": template, "cursor": 0}
    # 第二个 run 错开半帧
    ids = list(RUNS)
    if len(ids) > 1:
        RUNS[ids[1]]["cursor"] = 3


def parse_frame(run: dict, frame_id: int):
    frame = run["frames"][frame_id]
    body = run["body"][frame["offset"]: frame["offset"] + frame["length"]]
    if body[:4] != b"EXOF":
        return None, None
    version, hlen = struct.unpack("<II", body[4:12])
    header = json.loads(body[12:12 + hlen].decode("utf-8"))
    offset = 12 + hlen
    n = header["nlat"] * header["nlon"]
    arrays = {}
    for name in header["order"]:
        arrays[name] = np.frombuffer(body, dtype="<f4", count=n, offset=offset).reshape(
            header["nlat"], header["nlon"])
        offset += n * 4
    return header, arrays


def advance_loop(advance: bool) -> None:
    while True:
        time.sleep(2.0)
        if not advance:
            continue
        with LOCK:
            for run in RUNS.values():
                run["cursor"] = (run["cursor"] + 1) % len(run["frames"])


def run_state(run_id: str) -> dict:
    run = RUNS[run_id]
    template = json.loads(json.dumps(run["template"]))
    frame_id = run["cursor"]
    header, arrays = parse_frame(run, frame_id)
    meta = run["frames"][frame_id]
    history = []
    for index, item in enumerate(template["history"][: frame_id + 1]):
        year_no = 1 + index // 4
        month = 1 + (index % 4) * 3
        history.append({
            "id": index, "step": item["step"], "year": year_no - 1,
            "year_no": year_no, "frame_in_year": index % 4,
            "wall": time.time() - (frame_id - index) * 3.0,
            "sim_date": "第 %d 年 · 第 %d 月 · 15 天" % (year_no, month),
            "ts_mean": item["ts_mean"], "ntr_mean": item.get("ntr_mean"),
        })
    year_no = 1 + frame_id // 4
    month = 1 + (frame_id % 4) * 3
    months_done = round((year_no - 1) * 12 + (month - 1) + 0.5, 2)
    planned = template["run"]["planned_months"] or (len(run["frames"]) * 0.5)
    state = template
    state["server"].update({"uptime_s": round(time.time() - STARTED, 1),
                            "frames_parsed": len(history),
                            "cpu_percent": 0.7 if frame_id % 3 else 0.4})
    state["run"].update({
        "run_id": run_id,
        "modelname": template["params"]["modelname"],
        "status": "running",
        "status_text": "运行中 · 第 %d 年 第 %d 月" % (year_no, month),
        "wall_elapsed_s": round(2.5 * (frame_id + 1), 1),
        "frames_total": frame_id + 1,
        "planned_months": planned,
        "planned_years": round(planned / 12.0, 2),
        "year_index": year_no - 1,
        "year_no": year_no,
        "months_done": months_done,
        "progress": round(min(1.0, months_done / planned), 4) if planned else 0.0,
        "eta_s": max(0.0, (planned - months_done) * 5.0),
        "sim_day": meta["step"] / (template["params"]["steps_per_year"] / 360.0),
        "sim_date": "第 %d 年 · 第 %d 月 · 15 天" % (year_no, month),
        "step": meta["step"],
        "frames_in_year": frame_id + 1,
    })
    state["frame"] = {
        "id": frame_id, "step": meta["step"], "year": year_no - 1,
        "year_no": year_no, "frame_in_year": frame_id % 4,
        "wall": time.time(), "sim_date": state["run"]["sim_date"],
        "ts_mean": float(np.mean(arrays["ts"])),
        "ntr_mean": history[-1].get("ntr_mean"),
        "complete": True,
    }
    state["history"] = history
    for name in ("ts", "tas", "pr", "spd", "sic", "sit", "clt"):
        if name in arrays:
            state["layers"][name]["min"] = round(float(np.nanmin(arrays[name])), 3)
            state["layers"][name]["max"] = round(float(np.nanmax(arrays[name])), 3)
    state["meta"].update({"updated": time.time(), "source": "plasim_snapshot",
                          "source_size": len(run["body"]),
                          "note": "fixture 回放；真实数据由 collector 提供"})
    return state


def runs_doc() -> dict:
    out = []
    for run_id, run in RUNS.items():
        entry = dict(run["entry"])
        with LOCK:
            frame_id = run["cursor"]
        months = round((frame_id // 4) * 12 + (frame_id % 4) * 3 + 1.5, 2)
        planned = entry.get("planned_months") or 6
        entry.update({"frame_count": frame_id + 1, "months_done": months,
                      "year_index": frame_id // 4, "year_no": 1 + frame_id // 4,
                      "planned_years": round(planned / 12.0, 2),
                      "progress": round(min(1.0, months / planned), 4),
                      "last_update": time.time()})
        entry.pop("frame_count_total", None)
        out.append(entry)
    return {"ok": True, "default": out[0]["run_id"] if out else None, "runs": out}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静音
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if path == "/api/runs":
            return self._json(runs_doc())
        if path in ("/api/state", "/api/frame", "/api/land"):
            run_id = (query.get("run") or [None])[0] or runs_doc()["default"]
            if run_id not in RUNS:
                return self._json({"ok": False, "error": "unknown run"}, 404)
            if path == "/api/state":
                return self._json(run_state(run_id))
            if path == "/api/land":
                return self._send(200, RUNS[run_id]["land"], "application/octet-stream")
            frame_id = (query.get("id") or ["latest"])[0]
            run = RUNS[run_id]
            if frame_id == "latest":
                frame_id = run["cursor"]
            try:
                frame_id = int(frame_id) % len(run["frames"])
            except ValueError:
                return self._json({"ok": False, "error": "bad id"}, 400)
            body = run["body"]
            meta = run["frames"][frame_id]
            return self._send(200, body[meta["offset"]: meta["offset"] + meta["length"]],
                              "application/octet-stream")
        # 静态文件
        rel = path.lstrip("/") or "index.html"
        full = os.path.abspath(os.path.join(WEB_ROOT, rel))
        if not full.startswith(WEB_ROOT + os.sep) and full != WEB_ROOT:
            return self._json({"ok": False, "error": "forbidden"}, 403)
        if not os.path.isfile(full):
            return self._json({"ok": False, "error": "not found"}, 404)
        with open(full, "rb") as handle:
            data = handle.read()
        ext = os.path.splitext(full)[1].lower()
        return self._send(200, data, MIME.get(ext, "application/octet-stream"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--no-advance", action="store_true", help="不自动前进（截图/稳定态）")
    args = parser.parse_args(argv)
    if not os.path.isdir(FIXTURE):
        print("missing fixture; run make_fixture.py first")
        return 2
    load_runs()
    threading.Thread(target=advance_loop, args=(not args.no_advance,), daemon=True).start()
    print("mock server on http://127.0.0.1:%d  (web=%s)" % (args.port, WEB_ROOT), flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
