#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rawburn.py — ExoPlaSim ``plasim_snapshot``（burn7 无格式）增量解析器。

只读契约：本模块只对运行目录做 ``stat`` / ``open`` / ``read``，绝不写、不删、不改、
不发信号。解析方式为「只读新追加的字节」：文件 offset 单调前进，跨轮询保留半条
record 的解析位置，因此 1 Hz 轮询的 CPU 开销与文件总大小无关。

磁盘格式（由 model 的 ``outmod.f90`` 写入，little-endian，4 字节 record marker）::

    [32][ ihead(8*int32) ][32]                    <- main header（code=333）
    [N*4][ zsig(N=NLAT*NLON float32) ][N*4]       <- 二维场（地形/位势；不是 sigma）
    [32][ h(8*int32) ][32][dlen][ data(dlen) ][dlen]   <- 之后每条变量记录

    h[0]=code  h[1]=层号(1..nlev，2D 谱变量为 0)  h[3]=日内时刻编码
    h[2]=日期编码(nday+100*nmonth+10000*nyear)    h[4]/h[5]=nlon/nlat
    h[6]=step（= nstep - nstep1，自本次运行起算）  h[7]=ntru

年长固定按 360 天（ExoPlaSim 的 year 定义），不从首块数据里猜。

一条 step 的全部记录连续写出；看到**更大的 step**、文件被改名/截断、或文件连续
若干秒不再增长，即视上一条 step 为完整帧。年末模型会把 ``plasim_snapshot`` 改名为
``MOST_SNAP.00000``（同名首次出现在下一年重新创建），因此本解析器会在旧路径消失时
自动跟随同 inode 的新路径，尽量把最后一帧也读完。
"""
from __future__ import annotations

import os
import struct
import sys
import time

import numpy as np

I32 = struct.Struct("<i")
HDR = struct.Struct("<8i")

# 需要收集的 grid 变量（data = nlat*nlon 个 float32，行=北→南，列=lon 0→）
GRID_CODES = {
    110: "mld", 139: "ts", 140: "mrso", 141: "snd", 142: "prl", 143: "prc",
    144: "prsn", 146: "hfss", 147: "hfls", 159: "u3", 160: "mrro", 164: "clt",
    167: "tas", 169: "tsa", 170: "tsod", 172: "lsm", 173: "z0", 175: "alb",
    174: "alb1", 184: "alb2", 176: "rss", 177: "rls", 178: "rst", 179: "rlut",
    180: "tauu", 181: "tauv", 182: "evap", 183: "tso", 203: "rsut", 204: "ssru",
    205: "stru", 207: "tso2", 208: "tso3", 209: "tso4", 210: "sic", 211: "sit",
    218: "snm", 221: "sndc", 229: "dwmax", 230: "prw", 232: "glac", 266: "weath",
    267: "gornz", 268: "giccz", 269: "netz", 318: "czen", 319: "sigrain",
    320: "mint", 321: "maxt", 322: "cape", 323: "lnb", 324: "sdef", 325: "absz",
    326: "umax", 327: "vent", 328: "vrumax", 329: "gpi", 134: "ps",
}
# 三维 grid 变量（每层一条，h[1]=1..nlev）——本监控不需要，仅用于长度判别
GRID_3D_CODES = {133, 161, 162, 265, 404, 405, 406, 407, 408, 409, 410, 411}

# 需要收集的谱变量：130 温度、152 对数地面气压、155 散度、138 涡度（风用后两个）
SPEC_CODES = {129: "oro", 152: "lnps", 130: "ta", 133: "hus", 155: "div", 138: "vort"}

# 每条 step 的记录顺序（实测，outmod.f90）：谱记录(129,152,130×nlev,133×nlev,
# 155×nlev,138×nlev) → 标量 50..54（**不是**在最后！）→ grid 记录（110,139,...329）。
# 因此用「grid 块最后一条 code 329 (gpi)」作为本 step 收尾信号：看到它立即发布该帧，
# 比等下一个 step 出现少 1 帧延迟。若某配置不写 329，则退回「新 step / 文件静止」判定。
END_CODES = (329, 328)

# 发布一帧的最小变量集合；缺任何一个就只发已就绪的部分（并在 msg 里说明）
REQUIRED_GRID = ("ts",)
WIND_CODES = (155, 138)


class RawFrame:
    """一条 step 的原始变量集合（未重采样）。"""

    __slots__ = ("step", "date_code", "time_code", "grid", "spectral", "wall", "source_size", "mtime")

    def __init__(self, step):
        self.step = int(step)
        self.date_code = 0
        self.time_code = 0
        self.grid = {}       # name -> (nlat, nlon) float32
        self.spectral = {}   # code -> {lev: float32(2*NCSP,)}
        self.wall = time.time()
        self.source_size = 0
        self.mtime = 0.0           # 文件 mtime（快照生成时间，供周期统计）

    def has_wind(self):
        return all(code in self.spectral and self.spectral[code] for code in WIND_CODES)

    def __repr__(self):  # pragma: no cover - 调试用
        return "<RawFrame step=%d grid=%d spec=%d>" % (
            self.step, len(self.grid), sum(len(v) for v in self.spectral.values()))


class BurnStream:
    """增量解析一个（可能正在增长的）plasim_snapshot 文件。

    用法::

        stream = BurnStream()
        stream.poll(["/opt/exoplasim/runs/<run>/plasim_snapshot",
                     "/opt/exoplasim/runs/<run>/MOST_SNAP.00000"]) -> events

    ``events`` 是 ``(kind, payload, info)`` 三元组列表：

    * ``("frame", RawFrame, None)`` —— 一条完整 step，可发布
    * ``("reset", None, {"reason": ...})`` —— 源文件被换掉/截断，下一帧从 0 开始
    * ``("error", None, {"msg": ...})`` —— 解析告警（不抛异常，服务继续）
    """

    #: 文件静止多少轮（poll）后认为当前 step 已写完（1 Hz 轮询下约 2 秒）
    IDLE_FLUSH_POLLS = 2
    READ_CHUNK = 8 * 1024 * 1024          # 单次 read 分块
    MAX_READ_PER_POLL = 64 * 1024 * 1024  # 单次 poll 最多处理 64 MiB

    def __init__(self, log=None):
        self._log = log or (lambda msg: None)
        self.path = None
        self.src_key = None          # (st_dev, st_ino)
        self.offset = 0              # 已完整消费的字节数
        self.header = None           # main header 的 8 个 int
        self.zsig = None             # float32(nlat*nlon)
        self.nlat = 0
        self.nlon = 0
        self.nlev = 0
        self.ntru = 0
        self.ncsp = 0
        self.days_per_year = 360
        self.sigmah = None
        self.reset_count = 0
        self.idle = 0
        self.last_size = 0
        self.last_mtime = 0.0
        self.last_mtime_ns = 0
        self.bytes_read = 0
        self.frames_parsed = 0
        self._pending = None         # RawFrame 正在拼装
        self._done_step = None       # 最近一次已发布的 step（忽略其后到达的收尾记录）
        self._scratch = b""
        self._bad_header = 0         # 连续看到「文件头不是 32」的轮次数（用于降噪日志）
        self._catching_up = False    # 本次 poll 是否还没读完文件（首次/大 backlog）
        self._backlog = False        # 本次要读的字节超过单次预算（统计周期时要跳过）

    # ------------------------------------------------------------------ 状态
    @property
    def ready(self):
        return self.header is not None

    @property
    def source_size(self):
        return self.last_size

    def sigmah_levels(self):
        """模型 sigma 半层坐标（自顶向下）。"""
        if self.sigmah is None:
            return None
        return np.asarray(self.sigmah, dtype=np.float64)

    # ------------------------------------------------------------------ 轮询
    def _reset_parse(self):
        """丢弃解析状态并回到文件起点。

        文件名被换掉 / 被截断 / 文件头暂时是 0（模型刚创建文件、还没写 header）时，
        必须把 header 一起清掉；只重置 offset 会让下一轮把文件头当成普通 record 解析，
        进而出现 ``bad main header marker 131072`` 之类的连锁错误。
        """
        self.header = None
        self.zsig = None
        self.sigmah = None
        self.nlat = self.nlon = self.nlev = self.ntru = self.ncsp = 0
        self.days_per_year = 360
        self.offset = 0
        self._pending = None
        self._done_step = None

    def poll(self, candidates):
        """读取新追加的字节并解析。返回事件列表。"""
        events = []
        path = self._resolve(candidates)
        if path is None:
            # 源文件消失：把当前拼装中的 step 当作完整帧发出，然后等下一年的文件
            if self._pending is not None and self._pending.grid:
                events.append(("frame", self._finish(), None))
            if self.path is not None:
                self._log("source gone: %s" % self.path)
                self.path = None
                self.src_key = None
                self.last_size = 0
                self._reset_parse()
                self.idle = 0
                events.append(("reset", None, {"reason": "gone"}))
            return events

        try:
            st = os.stat(path)
        except OSError:
            return events

        key = (st.st_dev, st.st_ino)
        if self.path is None:
            self.path = path
            self.src_key = key
            self._reset_parse()
            self.idle = 0
            events.append(("open", None, {"path": path}))
        elif key != self.src_key or st.st_size < self.offset:
            # 文件被换掉（年末 rename/重建）或被截断：先把旧帧发完，再从 0 解析
            if self._pending is not None and self._pending.grid:
                events.append(("frame", self._finish(), None))
            events.append(("reset", None, {
                "reason": "replaced" if key != self.src_key else "truncated"}))
            self.path = path
            self.src_key = key
            self._reset_parse()
            self.idle = 0

        size = st.st_size
        self.last_size = size
        self.last_mtime = st.st_mtime
        self.last_mtime_ns = st.st_mtime_ns
        if size > self.offset:
            # 分块读：T85 的 raw 可到数 GB，一次性 read() 会把 RSS 顶到 ~500 MB+。
            # 每块 8 MiB、单次 poll 最多 64 MiB；没追平就下一轮继续（_catching_up）。
            budget = self.MAX_READ_PER_POLL
            self._backlog = (size - self.offset) > budget
            try:
                with open(path, "rb") as handle:
                    while size > self.offset and budget > 0:
                        want = min(size - self.offset, self.READ_CHUNK, budget)
                        handle.seek(self.offset)
                        chunk = handle.read(want)
                        if not chunk:
                            break
                        before = self.offset
                        self.bytes_read += len(chunk)
                        self.idle = 0
                        events.extend(self._consume(chunk))
                        budget -= len(chunk)
                        if self.offset <= before:
                            break          # 文件头重置/没有任何前进，避免死循环
            except OSError as exc:
                self._log("read failed: %s" % exc)
                return events
            # 只有「还有明显一大段没读」才算积压；文件尾部半条 record 属于正常情况，
            # 否则 T85 这种持续写入的文件会永远被判成 catching_up，轮询卡在 1 s。
            self._catching_up = (size - self.offset) > self.READ_CHUNK
        else:
            self._catching_up = False
            self._backlog = False
            self.idle += 1
            if (self._pending is not None and self._pending.grid
                    and self.idle >= self.IDLE_FLUSH_POLLS):
                events.append(("frame", self._finish(), None))
        return events

    def _resolve(self, candidates):
        """返回应读取的路径：优先当前路径，其次同 inode 的改名文件，再次最新的 raw。"""
        if not candidates:
            return None
        if self.path and os.path.exists(self.path):
            return self.path
        # 当前路径不存在：先找同 inode 的改名文件（年末 rename），保持 offset 连续
        if self.src_key is not None:
            for cand in candidates:
                try:
                    st = os.stat(cand)
                except OSError:
                    continue
                if (st.st_dev, st.st_ino) == self.src_key and st.st_size >= self.offset:
                    self._log("follow rename -> %s" % cand)
                    self.path = cand
                    return cand
        # 否则挑第一个存在的（通常是下一年新建的 plasim_snapshot）。
        # 注意：这里**只返回路径**，不要提前改 self.path/src_key/offset——
        # 交给 poll() 的 open/replaced 分支处理，否则第一次打开（以及跨年后新文件）
        # 不会产生任何事件，collector 的年份计数就永远停在初始值。
        for cand in candidates:
            try:
                st = os.stat(cand)
            except OSError:
                continue
            if st.st_size > 0:
                return cand
        return None

    # ------------------------------------------------------------------ 解析
    def _consume(self, chunk):
        events = []
        buf = chunk
        n = 0
        total = len(buf)
        while n + 8 <= total:
            if self.header is None:
                # 先解析 main header + zsig 两条 record。
                # 关键：整条 header record（marker + 8 ints + trailer + dlen +
                # data + trailer）到齐之前，绝不能推进 offset——否则下一轮会从
                # 数据区中间开始，被当成文件头，报 bad main header marker
                # 1033691254 (offset=44)。
                if total - n < 44:
                    break
                hlen = I32.unpack_from(buf, n)[0]
                if hlen != 32:
                    # 文件刚被创建/还在写 header 时，开头可能是 0 或半条记录。
                    # 回到 offset 0，下一轮重新读；等 header 真正落盘即可恢复。
                    self._bad_header += 1
                    if self._bad_header <= 3 or self._bad_header % 60 == 0:
                        events.append(("error", None, {
                            "msg": "bad main header marker %d (offset=%d, retry from 0)"
                                   % (hlen, self.offset)}))
                    self._reset_parse()
                    return events
                if I32.unpack_from(buf, n + 36)[0] != 32:
                    self._bad_header += 1
                    if self._bad_header <= 3:
                        events.append(("error", None, {
                            "msg": "bad main header trailer at offset %d"
                                   % (self.offset + n)}))
                    self._reset_parse()
                    return events
                head = HDR.unpack_from(buf, n + 4)
                dlen = I32.unpack_from(buf, n + 40)[0]
                if dlen < 0 or total - n < 44 + dlen + 4:
                    break                      # 半条 header record：下轮从 0 重读
                if I32.unpack_from(buf, n + 44 + dlen)[0] != dlen:
                    self._bad_header += 1
                    if self._bad_header <= 3:
                        events.append(("error", None, {
                            "msg": "bad main header data trailer at offset %d"
                                   % (self.offset + n)}))
                    self._reset_parse()
                    return events
                if not self._setup(head, np.frombuffer(buf, dtype="<f4", count=dlen // 4,
                                                       offset=n + 44)):
                    self._reset_parse()
                    return events
                n += 44 + dlen + 4
                continue

            # 普通变量 record: [32][hdr][32][dlen][data][dlen]
            if total - n < 44:
                break
            if I32.unpack_from(buf, n)[0] != 32:
                events.append(("error", None, {
                    "msg": "bad header marker at offset %d" % (self.offset + n)}))
                n += 4
                continue
            head = HDR.unpack_from(buf, n + 4)
            if I32.unpack_from(buf, n + 36)[0] != 32:
                events.append(("error", None, {
                    "msg": "bad header trailer at offset %d" % (self.offset + n)}))
                n += 4
                continue
            dlen = I32.unpack_from(buf, n + 40)[0]
            if dlen < 0 or total - n < 44 + dlen + 4:
                break                      # 半条 record：下轮再来
            data_start = n + 44
            if I32.unpack_from(buf, data_start + dlen)[0] != dlen:
                events.append(("error", None, {
                    "msg": "bad data trailer code=%d" % head[0]}))
                n += 4
                continue
            frame_events = self._on_record(head, buf, data_start, dlen)
            events.extend(frame_events)
            n = data_start + dlen + 4

        self.offset += n
        if n < total:
            # 剩余半条 record：下一轮从 offset 继续（不重复解析）
            pass
        return events

    def _setup(self, head, zsig):
        nlon, nlat, nlev, ntru = (int(head[4]), int(head[5]),
                                  int(head[6]), int(head[7]))
        # 防呆：正常网格 32×64（T21）到 256×512（T85+）；dims 不合理说明读到的是
        # 半条记录/错误偏移，宁可回到 0 重试，也不要把垃圾当网格。
        if not (2 <= nlon <= 4096 and 2 <= nlat <= 4096
                and 1 <= nlev <= 200 and 1 <= ntru <= 512):
            self._log("reject main header nlon=%d nlat=%d nlev=%d ntru=%d"
                      % (nlon, nlat, nlev, ntru))
            return False
        self.header = head
        self.zsig = np.array(zsig, dtype=np.float32)
        self.nlon = nlon
        self.nlat = nlat
        self.nlev = nlev
        self.ntru = ntru
        self.ncsp = (self.ntru + 1) * (self.ntru + 2) // 2
        # 首块是 nlat*nlon 个 float32 的二维场（约 0.5 MiB），不是 sigma 层；
        # 之前把它的第 nlev 个值当年天数会随机得到错误年份长度。统一按 360 天。
        self.sigmah = None
        self.days_per_year = 360
        self._bad_header = 0
        self._log("main header nlon=%d nlat=%d nlev=%d ntru=%d days/year=%d"
                  % (self.nlon, self.nlat, self.nlev, self.ntru, self.days_per_year))
        return True

    def _on_record(self, head, buf, start, dlen):
        """处理一条变量 record；必要时发出上一帧。"""
        code = int(head[0])
        lev = int(head[1])
        step = int(head[6])
        events = []

        if code in END_CODES:
            # grid 块最后一条（gpi）：本 step 的记录已全部写出，立即发布
            if self._pending is not None and self._pending.step == step:
                events.append(("frame", self._finish(), None))
            return events

        if self._pending is None and self._done_step is not None:
            if step == self._done_step:
                # 该 step 已经发布过（例如“文件静止”提前发布），
                # 迟到的收尾记录不能拼成第二帧
                return events
            if step < self._done_step:
                return events

        if self._pending is None:
            self._pending = RawFrame(step)
        elif step != self._pending.step:
            if step < self._pending.step:
                self._log("step went backwards %d -> %d (offset=%d)"
                          % (self._pending.step, step, self.offset))
            # 出现更大的 step：上一条 step 的记录块已结束
            events.append(("frame", self._finish(), None))
            self._pending = RawFrame(step)

        frame = self._pending
        if frame.date_code == 0:
            frame.date_code = int(head[2])
            frame.time_code = int(head[3])

        if dlen == self.nlat * self.nlon * 4 and code in GRID_CODES:
            frame.grid[GRID_CODES[code]] = np.frombuffer(
                buf, dtype="<f4", count=dlen // 4, offset=start).reshape(
                    self.nlat, self.nlon).copy()
        elif dlen == self.ncsp * 2 * 4 and code in SPEC_CODES:
            levels = frame.spectral.setdefault(code, {})
            levels[lev] = np.frombuffer(
                buf, dtype="<f4", count=dlen // 4, offset=start).copy()
            # 谱变量 1..nlev 写全后才算完整；不必在此判断
        # 其余 code 只跳过（省内存、省时间）
        return events

    def _finish(self, next_step=None):
        """把拼装中的 RawFrame 定型并返回；无有效数据时返回 None。"""
        frame = self._pending
        self._pending = None
        if frame is None or not frame.grid:
            return None
        if "ts" not in frame.grid:
            # 只读到某个 step 的收尾记录（例如 325..329），不是可用的帧
            self._log("drop frame step=%d: 无 ts（grid=%s, offset=%d/%d）"
                      % (frame.step, sorted(frame.grid.keys()), self.offset,
                         self.last_size))
            return None
        self._done_step = frame.step
        self.frames_parsed += 1
        frame.source_size = self.last_size
        frame.wall = time.time()
        frame.mtime = self.last_mtime or frame.wall
        return frame


def scan_raw_candidates(workdir):
    """按优先级列出运行目录里可能的原始快照文件（plasim_snapshot 优先，其次改名文件）。"""
    out = []
    primary = os.path.join(workdir, "plasim_snapshot")
    if os.path.exists(primary):
        out.append(primary)
    try:
        names = sorted(os.listdir(workdir))
    except OSError:
        names = []
    for name in names:
        if not name.startswith("MOST_SNAP"):
            continue
        if name.endswith(".npz"):
            continue
        path = os.path.join(workdir, name)
        if os.path.isfile(path):
            out.append(path)
    return out


def _selftest():  # pragma: no cover - 手工调试入口
    """``python rawburn.py <path>``：把整个文件当离线文件解析，打印帧摘要。"""
    if len(sys.argv) < 2:
        print("usage: rawburn.py <plasim_snapshot>")
        return 2
    path = sys.argv[1]
    stream = BurnStream(log=lambda m: print("[burn]", m))
    stream.path = path
    stream.src_key = (os.stat(path).st_dev, os.stat(path).st_ino)
    print("frames:", len(stream.poll([path])))
    for name, arr in sorted(stream._pending.grid.items()) if stream._pending else []:
        print(name, arr.shape)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_selftest())
