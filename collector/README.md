# collector — ExoPlaSim 实时只读监控后端

> English summary: one Python process inside WSL. It tails `plasim_snapshot`
> (incremental, complete records only), derives the 9 unified-grid layers, serves
> `/api/runs|state|frame|land` and the static frontend. It opens files read-only and
> never writes to the run directory. Start it with `monitor/start.ps1` (recommended)
> or run it in the foreground inside WSL.

`monitor/CONTRACT.md` 的 backend 实现：WSL 内运行的单个 Python 进程，`stat/open/read`
运行目录，起 HTTP 服务（默认 8765）并顺带服务 `monitor/web/`。

## 启动

```bash
# 方式 A：启动脚本（WSL 内 / Windows 都行）
wsl -d Ubuntu-24.04 -u root -- bash "/mnt/<drive>/<repo>/monitor/start.sh"

# 方式 B：直接调用（start.sh 就是这条命令 + 环境变量）
wsl -d Ubuntu-24.04 -u root -- /opt/exoplasim/env/bin/python \
  "/mnt/<drive>/<repo>/monitor/collector/server.py" --port 8765 --auto
```

启动后浏览器打开 `http://127.0.0.1:8765/`。必须用 `/opt/exoplasim/env/bin/python`
（风场重建依赖该 venv 里编译好的 `exoplasim.pyfft`；没有它 wind 图层置零、其余照常）。

### 常用参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--port` | 8765 | HTTP 端口 |
| `--host` | `0.0.0.0` | WSL2 下只有绑 0.0.0.0 才能从 Windows 浏览器访问 |
| `--auto` / `--no-auto` | on | 自动扫描 `--runs-root`；`--no-auto` 时只看 `--workdir` |
| `--workdir` | 无 | 固定监控某个运行目录（不存在时状态 `waiting`，出现后自动接管） |
| `--runs-root` | `/opt/exoplasim/runs` | 运行目录根 |
| `--outdir` | 无 | 短跑/正式跑结束后产物目录；存在 `spec.json` 时合并参数 |
| `--spec` | 无 | 额外 spec.json（参数兜底，例如 `model_class` / `landmap` / `pN2` 等） |
| `--recent-minutes` | 120 | 只采纳最近 N 分钟有变动的运行目录（避免把历史 run 全塞进前端） |
| `--max-runs` | 8 | 最多同时监控的 run 数 |
| `--ring` | 48 | 每个 run 的 ring buffer 帧数 |
| `--interval` | 1.0 | 轮询周期（秒）；目录发现固定 2 s |
| `--no-physfilter` | off | 关掉风场物理滤波（**仅**用于和归档 npz 做数值对照） |
| `--web` | `<collector>/../web` | 静态目录 |
| `--once` | off | 只轮询一次并打印 `/api/runs`（自检） |
| `--log-level` | info | `debug` 会打印每个 HTTP 请求 |

## 接口

与 `CONTRACT.md` §2 一致：

* `GET /api/runs`
* `GET /api/state?run=<run_id>`
* `GET /api/frame?run=<run_id>&id=<int|latest>` → EXOF 二进制（8 图层 float32）
* `GET /api/land?run=<run_id>` → EXOF（`lsm` + `oro` 地形高度 m）
* 其余路径 → `monitor/web/` 静态文件（防路径穿越、`Cache-Control: no-store`、CORS `*`）

`run` 省略 = 最新活跃 run；未知 run → 404 `{"ok":false,"error":"unknown run"}`。
`/api/state` 在没有任何数据时也返回 200（`frame:null`、`history:[]`、`status:waiting/configuring`）。

## 实现结构

| 文件 | 职责 |
| --- | --- |
| `rawburn.py` | `plasim_snapshot`（burn7 无格式）增量解析：只读新追加字节、只认完整 record、跟随年末改名、1 帧收尾判定 |
| `state.py` | 派生场（单位换算/风场/
tr`）、统一网格重采样、namelist/spec 参数、进度/序列、EXOF 打包 |
| `discovery.py` | 扫描 `/opt/exoplasim/runs/*/plasim_namelist`，判定活跃/已结束 |
| `server.py` | HTTP + 多 run 轮询主循环 + 进程自检（`/proc/self/stat`） |

### 数据源要点（实测，不是猜的）

`plasim_snapshot` 每条 step 的记录顺序是：

```
谱记录 129,152,130×nlev,133×nlev,155×nlev,138×nlev
标量   50,51,52,53,54              ← 注意：在 grid 块**之前**（dlen=8）
grid   110,139,140,141,142,143,144,146,147,161×nlev,159,160,162×nlev,164,167,...,
       210,211,230,232,... ,318,319,320,...,322,...,329(gpi)   ← 329 是最后一条
```

因此：**用 code 329 作为「本 step 写完」的即时信号**（比等下一个 step 少 1 帧延迟）；
文件连续 2 轮不增长 / 被改名 / 被删除时兜底发布。已发布的 step 记入 `_done_step`，
其后迟到的 325..329 收尾记录会被忽略，不会拼出第二帧（这是实测踩过的坑：
`step 3599` 曾因「文件静止提前发布」而重复出现一次，且第二帧没有 `ts`）。

## 已完成的验收（6 个模拟月短跑，12 帧）

```
collector: /opt/exoplasim/env/bin/python server.py --port 8767 --auto --no-physfilter
run:       monitor/test/run_short.py --spec .../pleiroma_continue.json \
             --months 6 --ncpus 8 --outdir /mnt/<drive>/<repo>/monitor/demo
```

实测数据（单 run，墙钟约 42 s）：

| 项 | 结果 |
| --- | --- |
| 帧数 / step | `frames_total=12`，steps `[719,1439,…,8639]` 严格递增、无重复、id 连续 |
| 实时延迟 | 发布延迟 max **0.81 s**、mean 0.67 s（0.2 s 采样，判据 ≤4 s） |
| 自身 CPU | max **1.00 %**、mean **0.24 %**（1 Hz 客户端全程采样；判据 <2 %） |
| 轮询耗时 | `poll_ms` max **5.20 ms**、mean 1.99 ms（判据 <30 ms） |
| RSS | 约 100 MiB（含 pyfft 扩展；ring buffer 12 帧时约 3 MiB/run） |
| 数值对照 | 与 `pleiroma_snapshot.npz` 12 帧逐帧：`max|Δts|=max|Δpr|=max|Δsic|=max|Δu|=max|Δv|=0` |
| 官方验收 | `monitor/test/verify_api.py ... --no-physfilter-expected` → **ALL CHECKS PASSED** |
| 多 run | 两个并发短跑同时 `running`，各自 12 帧、`?run=` 取到各自 workdir；另有一个 1 个月 run 被实时解析出 3 帧 |
| 嵌套 run | 扫描 `<runs-root>/<case>/<round>/` 第二层，`run_id=parent__child`；实测发现 `pl_s1/run_round_002`（同时多个 round） |
| 跨年（5 年 / 2h） | `test/watch_crossyear.py`：180 帧，年份 0→4，过渡 `(Y,12,30)→(Y+1,1,20)`，无「第 N+1 年 12 月」；末帧 `第 4 年 · 第 12 月 · 30 天`（`months_done=60`） |
| 年份 / 日期 | 6 个月 run：`year_no=0`、`第 0 年 · 第 6 月 · 30 天`；年份从 0 开始（契约 v2） |

数值对照细节：`u/v` 用 `PLARAD_m = 7964025 m`（= 1.25 R⊕ × 6371220）作 `rdcostheta`
分子、`physfilter=False`，取最底层 `[-1]`，与 pyburn 归档逐点一致（误差 0）。
`pr = (code142+code143) × 8.64e7` mm/day；
tr = 178+179`；`spd = hypot(u,v)`。

验收辅助脚本（只走 HTTP，不 import collector）：`_assert_frames.py <base> <run_id> <frames>`
断言 `frames_total`、step 严格递增无重复、每帧 EXOF 可解析且 step 对齐；
`_apiprobe.py` 打印 state/EXOF/land 摘要。

## 文件头恢复（bad main header marker / frames=0）

模型刚创建 `plasim_snapshot`、文件头还没落盘时，文件开头可能是一段 0 字节。
旧解析器在 `header is None` 时会 `n += 4` 一路往下滑，滑过文件头后永远同步不回来，
日志表现为大量 `bad main header marker 0`，最后一条 `bad main header marker 131072`，
并且 `frames=0`（131072 不是文件头，而是一条 256×128 数据的长度）。

现在的处理：

- 文件头 record **整条到齐前不推进 offset**（含 dlen 数据 + 尾部 marker）；否则 offset 会停在 44，下一轮把数据区当 marker，报 `bad main header marker 1033691254`；
- 文件头不是 32 时**不消耗字节**，清空 header/zsig 并回到 offset 0，下一轮重读；
- 每次 `open/reset/truncate` 都调用 `_reset_parse()`，连 header 一起清掉；
- `_setup()` 对 nlon/nlat/nlev/ntru 做范围校验，读到半条记录/垃圾维度时拒绝并重试；
- 年长固定 360 天（不再从首块二维场的第 11 个 float 里猜）。

回归测试：`python collector/selftest_rawburn.py`（用真实 T85 前缀模拟 0 开头 → 正常 →
截断 → 重写，ALL PASS）。实测 T85（256×128 谱，32 ranks，493 MB raw）可直接解析出 25 帧。

## 自适应轮询（快照文件 mtime）

高分辨率 T85 的 raw 只在写帧时 burst 更新 `mtime`，静止时几乎不变。collector 用这个
「文件生成时间」做两件事：

1. **统计帧间隔**：每帧完成时记下 raw 的 `mtime`，相邻帧 mtime 差即该帧的生成时间
   （`<0.5 s` 的 backlog 批解析帧会被过滤）；`/api/state` 暴露
   `frame_period_s` / `frame_period_avg_s` / `frame_period_median_s` / `frame_period_est_s` /
   `frame_period_source`（`mtime` 或 `estimated`）/ `frame_rate_per_hour`。
2. **决定轮询周期**：`poll_interval = clamp(mtime_avg / 4, --interval, --max-interval)`。
   T21 约 5 s/帧 → ~1.25 s；T85 约 30–40 s/帧 → ~9 s（上限 30 s）。

**即时显示**：主循环仍是 1 s tick，但每 tick 只 `stat` 一次；`mtime` 一变就立刻
强制 poll（不等 `next_poll_at`）。所以“空闲轮询 8.5 s”不会变成新帧延迟：105 s 探针
实测新帧在 mtime 变化后 ≤0.5 s 内出现。`--no-adaptive` 可退回固定 `--interval`。

**内存**：首次遇到大文件时按 8 MiB 分块读、单次 poll 最多 64 MiB，避免一次性把
493 MB raw 读进内存；T85 RSS 从 ~540 MB 降到 ~300 MB（主要仍是 48 帧 ring + Python）。

## 已知限制

1. **风场依赖 `--no-physfilter` 才能与归档 npz 精确对齐**：pyburn 归档默认
   `physfilter=False`，所以做数值对照时 collector 也要关滤波；正式出图默认开滤波
   （两者差别是谱域截断，视觉上很小，但数值上会差几 m/s）。
2. `params.ncpus` 对**已结束**的 run 通常是 `null`——进程数不写进 namelist；
   运行中的 run 从 `/proc/<pid>/cmdline` 的 `most_plasim_*_p<N>.x` 推断（实测得到 8）。
   短跑 harness 的 workdir 里 `most_plasim_run` 是 `compile.sh` 通用模板（t42_p1），不可信。
3. `planned_months` 对 harness 截短的「年」按 `N_RUN_STEPS/每步每月` 折算
   （6 个月 → 6.0）；普通整年 run 用 `N_RUN_YEARS*12 + N_RUN_MONTHS`。
4. 一年以上的 run 在年末 `plasim_snapshot → MOST_SNAP.NNNNN` 改名：解析器跟随同 inode
   继续读完最后一帧，`year_index += 1`；新一年的 raw 重新出现后继续。
5. `/api/land` 的 `lsm`/`oro` 取最新帧（模型每年可能重算掩膜/地形；`oro` 由谱 code 129 经 `pyfft.sp2gp` 重建，失败时为 0）。
6. 运行目录被 finalize 删除后仍保留在 `/api/runs`（`status:"finished"`）直到进程退出；
   重启 collector 后从 `--recent-minutes` 窗口内的目录重建（已结束的 run 只有空 history，
   `status_text` 会写明「无帧（未产生原始快照）」）。
7. 只读：除 `/proc` 与运行目录的 `stat/open/read` 外不碰任何文件，不发信号，
   不写 namelist，不改运行目录。

## 自检

```bash
# 单个 run，等一帧后打印 state / EXOF / land 摘要
/opt/exoplasim/env/bin/python monitor/collector/_apiprobe.py http://127.0.0.1:8765 <run_id>
/opt/exoplasim/env/bin/python monitor/collector/selftest_rawburn.py   # 文件头恢复回归
/opt/exoplasim/env/bin/python monitor/collector/selftest_plan.py     # 年份/计划/图层
```
