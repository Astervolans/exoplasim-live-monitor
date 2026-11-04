#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Acceptance assertions for one run's history (dev helper, strict per Lead).

用法: _assert_frames.py <base_url> <run_id> <expected_frames>
检查：frames_total == expected、steps 严格递增且无重复、每帧 EXOF 可解析、
history id 连续、layer min/max 非空。
"""
import json
import struct
import sys
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8767"
RUN = sys.argv[2]
EXPECT = int(sys.argv[3]) if len(sys.argv) > 3 else 12

failures = []


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as resp:
        return resp.read()


def check(cond, msg):
    print(("  [ok]  " if cond else "  [FAIL] ") + msg)
    if not cond:
        failures.append(msg)


q = "?run=" + urllib.parse.quote(RUN)
state = json.loads(get("/api/state" + q).decode())
run = state["run"]
hist = state["history"]
steps = [item["step"] for item in hist]
print("run", run["run_id"], "status", run["status"], "frames_total", run["frames_total"])
print("steps:", steps)
check(run["frames_total"] == EXPECT, "frames_total == %d (got %s)" % (EXPECT, run["frames_total"]))
check(len(hist) == min(EXPECT, 48), "history 长度 == %d" % min(EXPECT, 48))
check(all(b > a for a, b in zip(steps, steps[1:])), "steps 严格递增（无重复）")
check(len(steps) == len(set(steps)), "steps 无重复")
ids = [item["id"] for item in hist]
check(ids == list(range(ids[0], ids[0] + len(ids))), "frame id 连续")
check(all(item["ts_mean"] not in (None, 0) for item in hist), "ts_mean 全非空且非 0")

# 每帧 EXOF 解析
for item in hist:
    body = get("/api/frame" + q + "&id=%d" % item["id"])
    assert body[:4] == b"EXOF"
    version, hlen = struct.unpack("<II", body[4:12])
    header = json.loads(body[12:12 + hlen].decode())
    n = header["nlat"] * header["nlon"]
    expect_len = 12 + hlen + len(header["order"]) * n * 4
    if len(body) != expect_len:
        check(False, "frame %d EXOF 长度 %d != %d" % (item["id"], len(body), expect_len))
    if header["step"] != item["step"]:
        check(False, "frame %d step %d != history %d" % (
            item["id"], header["step"], item["step"]))
check(True, "%d 帧 EXOF 全部可解析且 step 对齐" % len(hist))

layers = state["layers"]
check(all(layers[k]["min"] is not None and layers[k]["max"] is not None
          for k in ("ts", "pr", "spd", "sic", "clt", "tas")), "layers min/max 全非空")
check(state["params"]["planned_months"] == 6.0,
      "planned_months == 6.0 (got %s)" % state["params"]["planned_months"])
check(state["params"]["ncpus"] == 8, "ncpus == 8 (got %s)" % state["params"]["ncpus"])
print("\n结论:", "ALL PASS" if not failures else "FAIL %d" % len(failures))
sys.exit(0 if not failures else 1)
