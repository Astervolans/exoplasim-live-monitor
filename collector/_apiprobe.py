#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ad-hoc API probe (dev helper): summarize /api/runs and one /api/state."""
import json
import struct
import sys
import urllib.parse
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8767"
ONLY = sys.argv[2] if len(sys.argv) > 2 else None


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as resp:
        return resp.read()


def jget(path):
    return json.loads(get(path).decode("utf-8"))


runs = jget("/api/runs")
print("default:", runs["default"])
for entry in runs["runs"]:
    print("  %-42s %-11s frames=%-3s months=%-6s planned=%-6s wall=%ss" % (
        entry["run_id"], entry["status"], entry["frame_count"],
        entry["months_done"], entry["planned_months"], entry["wall_elapsed_s"]))

run_id = ONLY or runs["default"]
if not run_id:
    sys.exit(0)
q = "?run=" + urllib.parse.quote(run_id)
doc = jget("/api/state" + q)
run = doc["run"]
server = doc["server"]
grid = doc["grid"]
print("\nrun:", run["run_id"], run["status"], "|", run["status_text"])
print("frames=%s step=%s year=%s planned=%s months=%.3f progress=%s eta=%s" % (
    run["frames_total"], run["step"], run["year_index"], run["planned_months"],
    run["months_done"] or 0, run["progress"], run["eta_s"]))
print("sim_date=%s sim_day=%s steps_per_year=%s msg=%s" % (
    run["sim_date"], run["sim_day"], run["steps_per_year"], run["msg"]))
print("grid=%sx%s lat[0]=%s lon[0]=%s" % (
    grid["nlat"], grid["nlon"],
    grid["lat"][0] if grid["lat"] else None,
    grid["lon"][0] if grid["lon"] else None))
print("server: cpu=%.2f%% poll=%.2fms rss=%sKiB frames=%s bytes=%s" % (
    server["cpu_percent"], server["poll_ms"], server["rss_kib"],
    server["frames_parsed"], server["bytes_read"]))
print("params:", json.dumps(doc["params"], ensure_ascii=False)[:900])
print("frame:", json.dumps(doc["frame"], ensure_ascii=False))
print("history:", len(doc["history"]), "year_series:", json.dumps(
    doc["year_series"], ensure_ascii=False)[:400])
print("layers:", json.dumps({k: [v["min"], v["max"]] for k, v in doc["layers"].items()}))

if doc["frame"] is None:
    print("\n(no frame yet: skip /api/frame & /api/land)")
    sys.exit(0)

body = get("/api/frame" + q + "&id=latest")
assert body[:4] == b"EXOF"
version, hlen = struct.unpack("<II", body[4:12])
header = json.loads(body[12:12 + hlen].decode("utf-8"))
print("EXOF version=%s header_len=%s order=%s complete=%s total=%dB" % (
    version, hlen, header["order"], header.get("complete"), len(body)))
expect = 12 + hlen + len(header["order"]) * header["nlat"] * header["nlon"] * 4
print("EXOF size ok:", expect == len(body), "nlat/nlon:", header["nlat"], header["nlon"])

lbody = get("/api/land" + q)
lheader = json.loads(lbody[12:12 + struct.unpack("<II", lbody[4:12])[1]].decode("utf-8"))
print("land EXOF order=%s nlat=%s nlon=%s" % (
    lheader["order"], lheader["nlat"], lheader["nlon"]))
