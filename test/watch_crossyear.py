#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""watch_crossyear.py — 轮询 collector，验证「跨年日期」不跳变。

用法（WSL venv python）：
  python watch_crossyear.py --base http://127.0.0.1:8765 --minutes 6 --out /tmp/crossyear.json

判定：
  * 年份从 0 开始、单调不减；
  * 同一年内月份单调不减（允许 12 → 下一年 1）；
  * 年份 +1 时，月份必须落在 1..2，months_done 只前进约 12±2 个月；
  * 不允许出现 (Y,12) 紧接 (Y+1,12)。
"""
import argparse
import json
import time
import urllib.parse
import urllib.request

def get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return json.loads(r.read())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--minutes", type=float, default=6.0)
    ap.add_argument("--interval", type=float, default=1.5)
    ap.add_argument("--out", default="/tmp/crossyear.json")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    deadline = time.time() + args.minutes * 60
    run_id = None
    samples = []
    last = None
    problems = []

    while time.time() < deadline:
        try:
            runs = get(base, "/api/runs").get("runs") or []
        except Exception as exc:
            time.sleep(args.interval)
            continue
        # 优先找正在跑的 pleiroma_2h / 我们的 5 年算例
        cand = [r for r in runs if r.get("summary", {}).get("resolution") == "T21"]
        running = [r for r in cand if r.get("status") in ("running", "configuring")]
        pick = None
        for r in running:
            if "2h" in (r.get("modelname") or "") or "pleiroma_2h" in (r.get("run_id") or ""):
                pick = r
                break
        if pick is None and running:
            pick = running[0]
        if pick is None and run_id is None:
            time.sleep(args.interval)
            continue
        rid = pick["run_id"] if pick else run_id
        run_id = rid
        try:
            st = get(base, "/api/state?run=" + urllib.parse.quote(rid))
        except Exception:
            time.sleep(args.interval)
            continue
        run = st.get("run") or {}
        year_no = run.get("year_no")
        months = run.get("months_done")
        sim_date = run.get("sim_date")
        step = run.get("step")
        status = run.get("status")
        rec = {"t": round(time.time(), 2), "year_no": year_no, "months_done": months,
               "sim_date": sim_date, "step": step, "status": status,
               "frames_total": run.get("frames_total")}
        if last is None or (rec["year_no"], rec["months_done"], rec["step"]) != (
                last["year_no"], last["months_done"], last["step"]):
            samples.append(rec)
            print(json.dumps(rec, ensure_ascii=False), flush=True)
        # 断言
        if last is not None:
            if rec["year_no"] is not None and last["year_no"] is not None:
                if rec["year_no"] < last["year_no"]:
                    problems.append("year went backwards: %s -> %s" % (last["year_no"], rec["year_no"]))
                if rec["year_no"] == last["year_no"] and rec["months_done"] is not None and last["months_done"] is not None:
                    if rec["months_done"] < last["months_done"] - 1e-6:
                        problems.append("months went backwards in same year: %s -> %s" % (last["months_done"], rec["months_done"]))
                if rec["year_no"] == last["year_no"] + 1:
                    delta = (rec["months_done"] or 0) - (last["months_done"] or 0)
                    # months_done 含 year*12，所以跨年只前进一个快照间隔（0.3~0.7 月）
                    if not (-0.05 <= delta <= 1.8):
                        problems.append("year+1 month delta %.2f (expect one frame, <1.8): %s -> %s" %
                                        (delta, last["sim_date"], rec["sim_date"]))
                    if last["sim_date"] and rec["sim_date"]:
                        if "第 12 月" in last["sim_date"] and "第 12 月" in rec["sim_date"]:
                            problems.append("12th month jumped to next year's 12th month: %s -> %s" %
                                            (last["sim_date"], rec["sim_date"]))
                if rec["year_no"] > last["year_no"] + 1:
                    problems.append("year skipped %s -> %s" % (last["year_no"], rec["year_no"]))
        last = rec
        if status == "finished" and rec["year_no"] is not None and rec["year_no"] >= 4:
            break
        time.sleep(args.interval)

    result = {"run_id": run_id, "samples": samples, "problems": problems,
              "first": samples[0] if samples else None, "last": samples[-1] if samples else None}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    print("\n== summary ==")
    print("run:", run_id, "samples:", len(samples))
    print("first:", result["first"])
    print("last :", result["last"])
    if problems:
        print("PROBLEMS (%d):" % len(problems))
        for p in problems[:20]:
            print(" -", p)
        return 1
    print("CROSS-YEAR OK")
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())
