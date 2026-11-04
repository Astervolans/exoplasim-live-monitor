# 实时可视化监控 · 接口契约 v2

> English summary: the collector runs inside WSL, reads only the growing
> `plasim_snapshot` raw file (plus namelists / optional `spec.json`), and serves
> `/api/runs`, `/api/state`, `/api/frame` (EXOF binary: 9 float32 layers),
> `/api/land` and the static frontend. The UI shows **model frame only**, supports
> zh/en, K/°C, hover readouts and multi-run selection. Full details below are in Chinese.
>
> 本文件由 Lead 维护，是 backend（`monitor/collector/`）与 frontend（`monitor/web/`）之间**唯一的接口约定**。
> 任何一方改接口，必须先改这里。测试用 fixture 与 mock server 在 `monitor/test/`。

## 0. 目标与硬约束

- 在浏览器里实时看一颗正在跑的 ExoPlaSim 模拟：3D 球体 + 可切换图层（温度 / 降水 / 风力 / 海冰 / 云量）+ 参数面板。
- **只读**：监控端绝不写运行目录、不发信号、不改 namelist、不抢 CPU。只 `stat/open/read` 过程文件。
- **开销小**：轮询 1 Hz；只读文件新追加的字节；目标 CPU < 单核 2%，内存 < 100 MiB。
- **不依赖联网**：前端零第三方库（手写 WebGL2），后端只用 stdlib + numpy（WSL venv 里已有）。
- 测试模拟**最长 6 个模拟月**（12 个 15 天快照，墙钟约 35–45 s）。

## 1. 进程与拓扑

```
Windows 浏览器  ──HTTP──>  127.0.0.1:8765  (WSL2 内 python, /opt/exoplasim/env/bin/python)
                                   │ 只读
                                   ├── /opt/exoplasim/runs/<run_id>/plasim_snapshot    （权威实时源）
                                   ├── /opt/exoplasim/runs/<run_id>/plasim_output      （备用/月均，可选）
                                   ├── /opt/exoplasim/runs/<run_id>/plasim_diag        （可选，仅取状态文本）
                                   ├── /opt/exoplasim/runs/<run_id>/plasim_namelist    （参数）
                                   ├── /opt/exoplasim/runs/<run_id>/planet_namelist    （参数）
                                   ├── /opt/exoplasim/runs/<run_id>/radmod_namelist    （参数，可选）
                                   └── /opt/exoplasim/runs/<run_id>/<model>.cfg        （参数，可选）
```

- collector 由 `monitor/start.ps1`（Windows）经 `wsl -d Ubuntu-24.04 -u root -- ...` 启动；也可在 WSL 内手工运行。
  **注意**：本机 WSL 会在启动它的 `wsl.exe` 退出时回收同会话后台进程，因此 `start.ps1` 用
  `Start-Process` 起一个**前台常驻**的 `wsl.exe` 跑 collector（`launch_collector.sh` 里 `exec`），
  停止时 `pkill` collector。不要用 `nohup ... &` 之类后台化。
- collector 默认从 `/opt/exoplasim/runs` 自动发现**最新**且含 `plasim_namelist` 的运行目录；`--workdir` 可固定。
  `*_crashed/`（ExoPlaSim 崩溃转储）与 `_*` 临时目录一律排除。
- collector 同时把 `monitor/web/` 作为静态目录提供服务（默认 = `collector/` 的兄弟目录 `../web`）。

## 2. HTTP 接口

所有响应带 `Cache-Control: no-store`。CORS 允许 `*`（方便直接 `file://` 打开调试）。端口默认 8765。

**多模拟支持（硬要求）**：可能同时有多个 exo 运行；前端必须能选择看哪一个。
后端为每个发现的 run 维护独立 parser / 状态 / ring buffer；接口一律用 `?run=<run_id>` 指定，
缺省 = 最新活跃 run。

### 2.0 `GET /api/runs` → JSON

```json
{
  "ok": true,
  "default": "20261104-133732_pleiroma",
  "runs": [
    {"run_id": "20261104-133732_pleiroma", "modelname": "pleiroma",
     "status": "running",              // running|configuring|waiting|finished|error
     "workdir": "/opt/exoplasim/runs/20261104-133732_pleiroma",
     "frame_count": 7, "year_index": 1, "year_no": 2, "year_total": 20,
     "months_done": 13.5, "planned_months": 240, "planned_years": 20,
     "progress": 0.056,
     "wall_elapsed_s": 21.3, "last_update": 1762345688.9,
     "summary": {"resolution": "T21", "layers": 10, "ncpus": 8, "flux_W_m2": 1590.0}}
  ]
}
```

- 2 s 轮询一次目录发现新 run（`/opt/exoplasim/runs/*/plasim_namelist`）；
  已结束（目录被 finalize 删掉）的 run **保留在列表里**直到进程退出，`status:"finished"`。
- 排序：活跃在前，其后按 `last_update` 倒序。

### 2.1 `GET /api/state?run=<run_id>` → JSON

`run` 省略时取 `/api/runs` 的 `default`；未知 `run_id` → HTTP 404 `{"ok":false,"error":"unknown run"}`。

```json
{
  "ok": true,
  "version": "1.0",
  "server": {"pid": 123, "cpu_percent": 0.4, "rss_kib": 41200, "uptime_s": 123.4,
             "poll_ms": 1.8, "frames_parsed": 7, "bytes_read": 12345678},
  "run": {
    "run_id": "20261104-133732_pleiroma",
    "modelname": "pleiroma",
    "workdir": "/opt/exoplasim/runs/20261104-133732_pleiroma",
    "outdir": "<outbase>/runs/<run_id>",                       // 可空
    "status": "running",            // running | configuring | finished | waiting | error
    "status_text": "运行中 · 第 2 年 第 3 月",
    "wall_elapsed_s": 21.3,
    "year_index": 1,                 // 0 基：**已完成**的整年数
    "year_no": 2,                    // 1 基：正在跑的这一年（= year_index+1）
    "restart_year": 20,              // 可选：从 MOST_REST.00019 续跑时给出（物理起始年，1 基）
    "year_total": 20,                // 可选：本次 run 计划总年数
    "frames_total": 7,
    "planned_months": 6,             // 计划总模拟月数；未知为 null
    "planned_years": 0.5,            // planned_months/12；整年跑时为整数
    "months_done": 1.5,              // year_index*12 + 当前年已过月数
    "progress": 0.25,                // 0..1，相对 planned_months
    "eta_s": 26.0,
    "sim_day": 45.0,                 // 该"年"内第几天（360 天/年）
    "sim_date": "第 2 年 · 第 3 月 · 第 15 天",   // 年/月/日均为 1 基
    "step": 2160, "steps_per_year": 17280,
    "msg": null
  },
  "grid": {
    "nlat": 64, "nlon": 128,         // 统一网格：后端已完成高斯纬度重采样
    "lat": [89.0, 86.0, ...],        // 长度 nlat，自北向南递减
    "lon": [0.0, 1.40625, ...]       // 长度 nlon，自西向东递增
  },
  "layers": {
    "ts":  {"name": "地表温度", "unit": "K",   "range": [180, 330], "min": 217.9, "max": 327.0, "kind": "scalar"},
    "pr":  {"name": "降水",     "unit": "mm/d","range": [0, 20],    "min": 0.0,   "max": 8.1,   "kind": "scalar"},
    "spd": {"name": "风速",     "unit": "m/s","range": [0, 30],    "min": 0.1,   "max": 41.2,  "kind": "scalar"},
    "sic": {"name": "海冰覆盖", "unit": "1",  "range": [0, 1],     "min": 0.0,   "max": 1.0,   "kind": "scalar"},
    "sit": {"name": "海冰厚度", "unit": "m",  "range": [0, 9],     "min": 0.0,   "max": 9.0,   "kind": "scalar"},
    "clt": {"name": "云量",     "unit": "1",  "range": [0, 1],     "min": 0.0,   "max": 1.0,   "kind": "scalar"},
    "tas": {"name": "2 m 气温", "unit": "K",  "range": [180, 330], "min": 227.7, "max": 326.8, "kind": "scalar"}
  },
  "frame": {"id": 6, "step": 4319, "year": 0, "frame_in_year": 5,
            "wall": 1762345678.9, "sim_date": "第 0 年 · 第 3 月 · 0 天",
            "ts_mean": 271.42, "ntr_mean": -1.83, "complete": true},
  "history": [
    {"id": 0, "step": 719, "year": 0, "frame_in_year": 0, "wall": 1762345600.0,
     "sim_date": "第 0 年 · 第 0 月 · 15 天", "ts_mean": 271.5, "ntr_mean": -2.0}
  ],
  "year_series": [
    {"year": 0, "ts_area": 271.84, "ntr": -1.37, "sic_area": 0.42, "ts_at_step": [271.5, 271.6]}
  ],
  "params": {
    "resolution": "T21", "layers": 10, "ncpus": 8, "model_class": "Model",
    "timestep_min": 30.0, "snapshot_steps": 720, "steps_per_year": 17280,
    "planned_months": 6,
    "flux_W_m2": 1590.0, "startemp_K": 3000.0,
    "radius_Rearth": 1.25, "gravity_m_s2": 13.34, "planet_radius_m": 7964025.0,
    "rotationperiod_d": 18.3, "synchronous": true,
    "eccentricity": 0.035, "obliquity_deg": 0.0,
    "pN2_bar": 1.1856, "pO2_bar": 0.5472, "pAr_bar": 0.09047,
    "pCO2_bar": 0.0007296, "pCO2_ppmv": 729.6,
    "physicsfilter": "gp|exp|sp",
    "landmap": "/opt/exoplasim/topo/terra_rot_surf_0172.sra",
    "topomap": "/opt/exoplasim/topo/terra_rot_surf_0129.sra",
    "restartfile": "/opt/exoplasim/runs/pleiroma/MOST_REST.00019",
    "substellar_lon_deg": 180.0,
    "nlat_model": 32, "nlon_model": 64, "ntru": 21
  },
  "meta": {"updated": 1762345688.9, "source": "plasim_snapshot", "source_size": 31234567,
           "note": "只读监控；数据为模型原始输出，未做物理滤波以外的重建"}
}
```

字段落空规则：无数据时 `ok:true` 其余照常（`status:"waiting"`，`frame:null`，`history:[]`），
**绝不让前端面对 500**。`params` 中未知字段为 `null`。

### 2.2 `GET /api/frame?run=<run_id>&id=<int|latest>` → 二进制

- `run` 同 §2.1；`id` 缺省 = `latest`（该 run 最新完整帧）。`id` 超出 ring buffer 返回 HTTP 404。
- ring buffer 保留每个 run 最近 **48** 帧（6 个月 run 全部 12 帧；一年 run 约 2 年历史）。
  多 run 并发时内存按 run 数线性增长，48 帧 ≈ 12 MiB/run（T21 统一网格、8 层）。

二进制布局（little-endian）：

```
offset 0   : magic  b"EXOF"            (4 B)
offset 4   : uint32 version            = 1
offset 8   : uint32 header_len         (JSON 字节数)
offset 12  : header JSON (UTF-8)
之后        : 按 header.order 顺序，每个图层 nlat*nlon 个 float32（C 序，行=lat，列=lon）
```

header JSON：

```json
{"id": 6, "step": 4319, "year": 1, "frame_in_year": 5,
 "nlat": 64, "nlon": 128,
 "order": ["ts","tas","pr","spd","u","v","sic","sit","clt"],
 "units": {"ts":"K","tas":"K","pr":"mm/d","spd":"m/s","u":"m/s","v":"m/s","sic":"1","sit":"m","clt":"1"},
 "complete": true}
```

- 数组方向：`row 0 = 最北（lat[0]≈+89°）`，`col 0 = lon 0°`；`nlat/nlon` 必须与 `/api/state.grid` 一致。
- `u/v` 为**最底层（近地面）**风，已做 `np.roll(..., nlon//2)` 与谱→格变换；
  `spd`（后端算好的风速标量）优先用于着色，`u/v` 仅用于风矢箭头。
- `sic` = 海冰**覆盖率** 0..1；`sit` = 海冰**厚度** 0..9 m（模型 `xmaxd` 封顶）；`clt` = 云量覆盖率 0..1。
- 前端不得假设固定 `nlat/nlon`（T21→64×128，T42→128×256），也不得假设图层数固定（用 `order` 遍历）。

### 2.3 `GET /api/land?run=<run_id>` → 二进制

与 2.2 相同的 EXOF 封装，`order=["lsm"]`，`lsm` 0/1（模型陆海掩膜，统一网格）。变化时（每年重跑）可重新取。

### 2.4 静态文件

`GET /` → `web/index.html`；`GET /app.js`、`/globe.js`、`/style.css` 等 → `web/` 下同名文件。
路径穿越防护；`Content-Type` 按扩展名；ETag/Last-Modified 可选（前端会缓存）。

## 3. 后端数据约定（关键，错了图就错）

### 3.1 实时源：`plasim_snapshot`（原生 burn7 无格式）

- 结构：main record（`header=(code,0,?,?,nlon,nlat,nlev,ntru)` + zsig 数据）后跟若干 record：
  `marker(4) | header(nhdr*4) | marker(4) | datalen_marker(4) | data | marker(4)`，
  little-endian，`header[0]=code`，`header[1]=层号(1..nlev, 2D 谱变量为 0)`，`header[4]/[5]=nlon/nlat`，
  `header[6]=step`，`header[7]=ntru`。
- 只解析**完整的 record**（`offset + 4 + hdrlen + 4 + 4 + datalen + 4 <= size`）；文件被重命名/截断
  （size 变小或 inode 变化）→ 重置 offset 与当前帧。
- 每个 step 的记录块是连续的；当出现**更大的 step** 或文件被换掉时，上一个 step 视为完整帧。
- 需要的 raw code（均来自实测 `plasim_snapshot`）：

| 量 | code | 形式 | 换算 |
| --- | --- | --- | --- |
| `ts`  | 139 | grid 8192B (1024 float) | K |
| `tas` | 167 | grid | K |
| `pr`  | 142+143 | grid | m/s → mm/d ×`86400*1000` |
| `sic` | 210 | grid | 0..1 |
| `clt` | 164 | grid | 0..1 |
| `ps`  | 134 | grid | hPa |
| `lsm` | 172 | grid | 0..1 |
| `alb` | 175 | grid | 0..1 |
| `prw` | 230 | grid | kg/m² |
| `ntr` = 178+179 | grid | W/m² |
| `u`,`v` | 155(div), 138(vort) | spectral，每层 506 float，10 层 | `pyfft.spvgp(spu,spv,rdcostheta,nlon,ntru,filter)` |

- **grid 记录**数据 = `nlat*nlon` 个 float32，C 序，`row0 = 北`，`col0 = lon 0°`。
- **谱记录**每个码有 `nlev` 条（`header[1]=1..nlev`）；单帧拼成 `(nlev, 2*NCSP)`，
  `NCSP=(N+1)(N+2)/2`；`spvgp` 后 `reshape(np.transpose(u),(nlev,nlat,nlon))`，
  再 `np.roll(..., nlon//2, axis=-1)`（与 pyburn 一致），最底层 = `[-1]`。
  `rdcostheta = PLARAD_m / cos(lat_rad)`，`PLARAD_m = planet_radius_Rearth * 6371220.0`。
  `filter` 默认 **True**（项目默认要求：出图开物理滤波；可用 `--no-physfilter` 关掉做数值对照）。
- 环境：collector 用 `/opt/exoplasim/env/bin/python` 运行，可直接 `import exoplasim.pyfft`（只 import
  `pyfft`，不要实例化 Model，不要 import 会触发编译的模块）。若 import 失败，wind 层置空并在
  `run.msg` 里说明，其余图层照常。

### 3.2 参数来源

- `plasim_namelist`：`MPSTEP`、`NSTPS`、`NSTPW`、`N_RUN_STEPS`、`N_RUN_YEARS`、`N_RUN_MONTHS`、
  `N_DAYS_PER_YEAR`、`NGPTFILTER`、`NSPVFILTER`、`NSTPW`…
- `planet_namelist`：`GSOL0`、`PLARAD`（m）、`GA`、`ECCON`/`ECCEN`、`OBLIQ`、`ROTSPD`（1/day）、`NFIXORB`。
- `radmod_namelist`：`CO2`、`CH4`、`N2O`、`O3`、…（能读就填 `pCO2_ppmv` 等）
- `*.cfg`：ExoPlaSim 导出配置（位置式、无标签，**不要**用它当主来源）。
- 可选：`<outdir>/spec.json`（含 `input`/`resolved`，最完整；`--outdir` 传入时合并覆盖）。
- 数值守恒：`steps_per_year = N_RUN_STEPS`（整年）或 `round(86400/MPSTEP*360)`；不要硬编码 17280。
- 计划时长：`N_RUN_YEARS*12 + N_RUN_MONTHS`（月），用于 progress/ETA。

### 3.3 统一网格重采样（后端做，前端不做）

- `nlat_u = 2*nlat_model`，`nlon_u = 2*nlon_model`（T21→64×128，T42→128×256）。
- `lat_u[j] = 90 - (j+0.5)*180/nlat_u`（自北向南）；`lon_u[i] = i*360/nlon_u`。
- 纬度：对每一列按高斯纬度（自北向南）线性插值到 `lat_u`（两端夹取）。
- 经度：周期线性插值到 `lon_u`（环绕）。
- 面积加权全球平均用 `np.polynomial.legendre.leggauss(nlat)[1]`（高斯权重，**不是** linspace cos）。

### 3.4 状态机 / 健壮性

| 情况 | 行为 |
| --- | --- |
| 目录还没建 | `status:"waiting"`，每秒重扫（自动发现模式下） |
| configure 中（无 raw） | `status:"configuring"`，能读 namelist 就填 params |
| raw 出现/增长 | `status:"running"`，解析新 record，凑满一帧就发布 |
| raw 被改名（年末） | 完成最后一帧；下一年的 raw 出现后继续；`year_index += 1` |
| 运行结束（目录消失/最终 npz 出现） | `status:"finished"`；保留最后帧与 history；不再增长 |
| 记录撕裂/半条 | 只解析完整记录；下轮重试，不抛异常 |
| 解析/读取异常 | 记录到 `run.msg` 与日志，继续服务旧状态 |

### 3.5 性能自检

`/api/state.server` 暴露 `cpu_percent`（自身进程，`/proc/self/stat` 积分）与 `poll_ms`（最近一次轮询耗时）。
验收：6 个月 run 全程 `cpu_percent < 2.0`、`poll_ms < 30`。

## 4. 前端约定

- 纯静态、零依赖；`WebGL2` 优先，失败时给出清晰提示（不要白屏）。
- 球面：等经纬数据纹理（`R32F`/`R16F` 或 `RGBA8` LUT 均可）+ 片元着色器 colormap；
  光照可关；经纬网、陆地描边可开关。**只显示模型系**（见 §5），不提供地图系切换。
- 图层按钮：温度(ts) / 降水(pr) / 风力(spd + u/v 箭头) / 海冰覆盖(sic) / **海冰厚度(sit, m)** /
  云量(clt) / 2m气温(tas)；每个图层显示色标（单位、min/max）。
- **悬停读数**：鼠标在球面上移动时，把屏幕坐标反投影成球面点 → 模型系经纬度 → 最近格点，
  在光标附近显示一个小浮层：纬度/经度、**当前图层的值**，以及 `ts/tas/spd/sic/sit/clt` 的格点值；
  移出球体隐藏。温度按当前单位显示（见下）。浮层不得遮挡指针本身，靠近边缘时自动翻转。
- **温度单位开关**：`K / °C`，默认 K；影响温度类图层（ts/tas）的色标刻度、图层标题、悬停读数；
  选择记 localStorage。`?unit=c` 可深链到摄氏度。
- **云量**：数据是 0..1 覆盖率；图例/悬停显示为百分比（如 `42%`），底层仍传 0..1。
- 调试/截图深链：`?run=<run_id>`、`?layer=<ts|pr|spd|sic|sit|clt|tas>`、`?unit=c`、`?hover=<lat>,<lon>`（固定显示某点浮层）、`?lang=zh|en`。
- 风：着色 + 可选箭头（每 2×2 或 4×4 格一根，球面切向，长度/颜色按风速）。样式自定，但要能看清。
- 参数面板：`params` 表（分组：数值/恒星与轨道/大气/边界与几何/数据源），数值带单位。
- 进度：年/月/日、step、计划月数、进度条、ETA、监控开销（cpu%）。
- 序列：`history` 的 `ts_mean`（可选 `ntr_mean`）折线图；`year_series` 年度点。
- 回溯：滑块/播放按钮按 `history` 的 id 取 `/api/frame?id=`；暂停后保持当前帧。
- **多模拟选择器**（顶栏）：`/api/runs` 列表 → 下拉/分段控件；显示 `modelname + run_id 末 5 位`、
  状态点（运行中绿/配置中黄/等待灰/已结束蓝）、帧数；切换时清空当前图表并重取 state/frame/land；
  2 s 轮询 runs 以发现新 run；把选择记在 `localStorage`，刷新后仍看同一个 run（不存在则回退 default）。
  支持 `?run=<run_id>` 深链（优先于 localStorage，便于截图/分享）。
  标题区同时显示当前 run 的完整 `run_id` 与 workdir（可复制）。
- 状态徽标：运行中(绿)/配置中(黄)/等待(灰)/已结束(蓝)/错误(红)，以及 `updated` 距今秒数（>5s 变黄）。
- **语言**：中文 / English 切换（顶栏、localStorage 记忆、`?lang=zh|en` 深链、`<html lang>` 同步）。
  所有用户可见文案（含图层名、状态、参数标签、自检、悬停、aria/title）都要双语；
  英文模式不依赖后端返回的中文 `status_text`/`layer.name`（前端按结构化字段重建）。
- 深色主题；1366×768 可用；`prefers-reduced-motion` 尊重。
- **只用** `/api/state`、`/api/frame`、`/api/land`；不写死 8765 以外的东西，端口取 `location.origin`。
- 每 1 s 轮询 state；帧更新时若用户在拖动/暂停则不抢占视角。

## 5. 坐标系

监控器**只显示模型系**（自转轴为极），与 ExoPlaSim 的网格一致：

- 纹理按模型网格上传：`row0 = 北`（lat +90），`col0 = lon 0°`；
- 日下点 `(lat 0°, lon 180°)`，对侧点 `(0°, 0°)`，太阳方向固定为模型坐标 `(-1,0,0)`；
- 不再提供「地图系」显示（该项目早期曾把日下点旋转到极点，已按需求移除）；
  如果要把结果和用户原始地图对照，请在数据/绘图侧做旋转，监控器本身不做。

## 6. fixture 与 mock（前端独立开发用）

`monitor/test/`：

- `make_fixture.py`：从归档 `*_snapshot.npz` 生成 `fixture/frames.bin`（12 帧，EXOF 格式）、
  `fixture/state.json`（真参数 + history/year_series）、`fixture/land.bin`。
- `mock_server.py --port 8766`：静态服务 `web/` + 按 2 s/帧循环回放 fixture；接口与 §2 **完全一致**。
- 前端验收脚本：`python monitor/test/mock_server.py` 然后 `http://127.0.0.1:8766/`。

## 7. 目录与写权限

| 路径 | 归属 | 说明 |
| --- | --- | --- |
| `monitor/collector/**` | backend | 解析器、状态机、HTTP |
| `monitor/start.sh` | backend | WSL 启动脚本 |
| `monitor/web/**` | frontend | index.html / app.js / globe.js / style.css |
| `monitor/test/**` | Lead | fixture、mock、6 个月短跑 harness |
| `monitor/start.ps1`、`monitor/README.md`、`monitor/CONTRACT.md` | Lead | 集成与文档 |
