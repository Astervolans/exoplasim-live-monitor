#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""selftest_rawburn.py — rawburn 文件头恢复回归测试

针对真实事故：模型刚创建 ``plasim_snapshot``、文件头还没落盘（开头是 0 字节）时，
旧解析器会 ``n += 4`` 一路滑过文件头，之后报
``bad main header marker 131072`` 并永远 frames=0。

本测试用一段真实 raw 前缀（默认自动找最新的 plasim_snapshot）模拟：

  1. 文件开头 64 KiB 全是 0  -> 必须不前进、不发布帧，等 header；
  2. 写入真实前缀          -> 必须解析出 T85 头（nlon/nlat/nlev/ntru）和至少 1 帧；
  3. 截断为 0                -> 必须 reset 并清掉 header/offset；
  4. 重新写入真实前缀        -> 必须再次解析（证明可以恢复，而不是永久错位）。

用法：
  python selftest_rawburn.py [--snapshot /path/to/plasim_snapshot] [--prefix-mb 40]
"""
import argparse
import glob
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rawburn  # noqa: E402

FAILED = []


def check(ok, what):
    print("  [%s]  %s" % ("ok" if ok else "FAIL", what))
    if not ok:
        FAILED.append(what)


def find_snapshot():
    pats = [
        "/opt/exoplasim/runs/*/run_round_*/plasim_snapshot",
        "/opt/exoplasim/runs/*/plasim_snapshot",
    ]
    found = []
    for pat in pats:
        found.extend(glob.glob(pat))
    found = [p for p in found if os.path.getsize(p) > 2 * 1024 * 1024]
    if not found:
        return None
    return max(found, key=lambda p: os.stat(p).st_mtime)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", default="")
    ap.add_argument("--prefix-mb", type=int, default=40)
    args = ap.parse_args()

    src = args.snapshot or find_snapshot()
    if not src or not os.path.isfile(src):
        print("SKIP: 找不到可用的 plasim_snapshot（用 --snapshot 指定）")
        return 2
    print("snapshot: %s" % src)

    nbytes = args.prefix_mb * 1024 * 1024
    with open(src, "rb") as fh:
        prefix = fh.read(nbytes)
    if len(prefix) < 1024 * 1024:
        print("SKIP: 快照太小，无法测试")
        return 2
    print("prefix: %.1f MiB" % (len(prefix) / 1048576.0))

    root = tempfile.mkdtemp(prefix="rawburn_selftest_")
    raw = os.path.join(root, "plasim_snapshot")
    try:
        stream = rawburn.BurnStream(log=lambda msg: None)

        # 1) 开头是 0：不前进、不发布帧
        with open(raw, "wb") as fh:
            fh.write(b"\x00" * (64 * 1024))
        events = stream.poll([raw])
        check(stream.header is None and stream.offset == 0,
              "零开头：header=None, offset=0（实际 offset=%d）" % stream.offset)
        check(not [e for e in events if e[0] == "frame"], "零开头：不发布帧")

        # 2) 真实 header 落盘：恢复并解析
        with open(raw, "wb") as fh:
            fh.write(prefix)
        events = stream.poll([raw])
        frames1 = [e for e in events if e[0] == "frame"]
        check(stream.header is not None,
              "写入真实前缀后解析出 main header（nlon=%d nlat=%d nlev=%d ntru=%d）"
              % (stream.nlon, stream.nlat, stream.nlev, stream.ntru))
        check(stream.nlon >= 32 and stream.nlat >= 16 and stream.nlev >= 1,
              "header 维度合理")
        if not frames1:
            print("SKIP: 前缀 %.0f MiB 还没有完整帧，请加大 --prefix-mb（默认 40）"
                  % (len(prefix) / 1048576.0))
            return 2
        check(True, "至少发布 1 帧（实际 %d）" % len(frames1))

        # 3) 文件被截断：清状态
        with open(raw, "wb"):
            pass
        events = stream.poll([raw])
        check(any(e[0] == "reset" for e in events), "截断触发 reset 事件")
        check(stream.header is None and stream.offset == 0,
              "截断后 header/offset 清空（offset=%d）" % stream.offset)

        # 4) 再次写入：必须再次解析（不会永久错位）
        with open(raw, "wb") as fh:
            fh.write(prefix)
        events = stream.poll([raw])
        frames2 = [e for e in events if e[0] == "frame"]
        check(stream.header is not None, "重写后再次解析出 header")
        check(bool(frames2), "重写后再次发布帧（实际 %d）" % len(frames2))
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print("\n结论:", "ALL PASS" if not FAILED else "FAIL %d" % len(FAILED))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
