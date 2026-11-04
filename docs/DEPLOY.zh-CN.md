# 新设备部署与调试教程

本文件说明如何在一台新机器上从零部署 **ExoPlaSim + 实时监控器**，并列出我们实际踩过的坑。
文中不包含任何本机专用路径（`/opt/exoplasim` 只是约定，可按需修改）。

英文版见 [DEPLOY.md](DEPLOY.md)。

---

## 0. 环境要求

| 项 | 说明 |
| --- | --- |
| Windows 10 21H2+ / 11 | 必须用 WSL2（模型需要 Linux 下的 Fortran 工具链）。 |
| WSL2 + Ubuntu 24.04 | `wsl --install -d Ubuntu-24.04`；建议全程用 root（`sudo` 可能要密码）。 |
| CPU / 内存 | T21 用 4–8 个 MPI 进程；16 GB 内存足够。T42/T85 需要更多。 |
| 磁盘 | ExoPlaSim + 一个 T21 算例数 GB；每年约 20 MB `.npz` + 40 MB raw。 |
| Python | 用项目自己的 venv，不要用系统 Python。 |

监控器前端**零第三方依赖**；collector 只需要 Python 标准库 + `numpy`（风场重建用 venv 里的 `pyfft`）。

---

## 1. 安装 WSL 与编译工具链

```powershell
wsl --install -d Ubuntu-24.04
```

然后从 Windows 侧以 root 执行（不需要密码）：

```powershell
wsl -d Ubuntu-24.04 -u root -- bash -lc "apt-get update && apt-get install -y \
  build-essential gfortran python3-dev python3-venv \
  openmpi-bin libopenmpi-dev libnetcdf-dev"
```

> 为什么用 root：很多环境里 WSL 用户的 `sudo` 需要未知密码，`wsl -u root` 可以直接绕过。

---

## 2. 建 venv 并安装 ExoPlaSim

**所有模型相关文件放 Linux 原生文件系统（`/opt`）**，不要放 `/mnt/c` 或 `/mnt/e`
（9p 文件系统慢 10–100 倍，会把模型拖死）。

```bash
python3 -m venv /opt/exoplasim/env
/opt/exoplasim/env/bin/pip install --upgrade pip
/opt/exoplasim/env/bin/pip install numpy scipy matplotlib netCDF4 exoplasim
```

如果访问 PyPI 受限，可换镜像：

```bash
mkdir -p /root/.config/pip
printf '[global]\nindex-url = https://pypi.tuna.tsinghua.edu.cn/simple\n' \
  > /root/.config/pip/pip.conf
```

初始化 Fortran 核心：

```bash
export PATH=/opt/exoplasim/env/bin:$PATH      # f2py 需要 meson/ninja
/opt/exoplasim/env/bin/python -c "import exoplasim; exoplasim.sysconfigure()"
```

### 坑 1：`pyfft` 静默失败

现象：模型能跑完，但**后处理必然报** "crashed or begun producing garbage"。
根因：`compile_pyfft()` 只打印返回码、不抛异常；numpy 的 f2py 按名字在 `PATH` 找
`meson`/`ninja`，没把 venv 的 `bin` 放进 `PATH` 就静默失败。验证：

```bash
/opt/exoplasim/env/bin/python -c "import exoplasim.pyfft; print('pyfft ok')"
```

### 坑 2：`-ffpe-trap` 让海洋行星必崩

默认 `MOST_F90_OPTS` 带 `-ffpe-trap=...`，会把初始化阶段无害的 IEEE 异常升级成
`SIGFPE`（`aquaplanet=True` 必崩）。生产配置：

```
MOST_F90_OPTS = -O3 -cpp -ffixed-line-length-132
```

改完 flag **必须先删除** `.../exoplasim/plasim/run/most_plas*.x` 再重建
（可执行文件名不编码编译选项，否则会出现"同配置有时能跑有时崩"）。

---

## 3. 目录约定

| 路径 | 用途 |
| --- | --- |
| `/opt/exoplasim/env` | venv |
| `/opt/exoplasim/runs` | 模型工作目录（ext4） |
| `/opt/exoplasim/topo` | 地形/初值 `.sra`，**路径不能含空格** |
| `/opt/exoplasim/logs` | 监控日志 `monitor_collector.log` |

地形路径不能含空格的原因：ExoPlaSim 用不加引号的 `os.system("cp ...")` 拷
`landmap`/`topomap`，空格会让 `cp` 静默失败（`lsm` 全 1、`sg` 全 0，模型照跑）。
Windows 工作区路径（如 `E:\EXO Plasim`）本身含空格，所以模型侧路径一律放 `/opt`。

---

## 4. 安装监控器

把 `monitor/` 目录放到 Windows 与 WSL 都能读到的地方（不要求和数据同目录）。

```powershell
pwsh -NoProfile -File ".\monitor\start.ps1" -NoBrowser
```

`start.ps1` 做三件事：

1. 把启动脚本复制到 `/opt/exoplasim/run_collector.sh`（无空格路径）；
2. 用**前台常驻**的 `wsl.exe` 在 `127.0.0.1:8765` 起 collector；
3. 轮询 `/api/state`，成功后可选打开浏览器。

常用参数：

| 参数 | 作用 |
| --- | --- |
| `-Port 8770` | 换端口 |
| `-Workdir /opt/exoplasim/runs/<id>` | 固定某个 run（不自动发现） |
| `-NoBrowser` | 不开浏览器 |
| `-Foreground` | 前台运行（调试） |
| `-Stop` | 停止 collector |

如果 collector 跑在另一台 WSL/主机上，浏览器打开 `http://<host>:8765/`。

---

## 5. 准备第一份数据

可以接一个已经在跑的模拟，也可以先做 **6 个模拟月** 的安装验证（8 进程约 40 s）：

```bash
/opt/exoplasim/env/bin/python "/mnt/<盘符>/<仓库>/monitor/test/run_short.py" \
  --spec "/mnt/<盘符>/<仓库>/verify/pleiroma/batch1_T21_20yr/specs/pleiroma_continue.json" \
  --months 6 --outdir "/mnt/<盘符>/<仓库>/monitor/demo" --ncpus 8
```

`run_short.py` 不改物理参数，只缩短积分区间。正式算例仍走项目原本的入口
（例如 `exo.ps1 run`）。

collector 会自动发现最近 `--recent-minutes`（默认 120）分钟内变动过、且含
`plasim_namelist` 的 `/opt/exoplasim/runs/*` 目录，并列进顶栏的模拟选择器。

---

## 6. 调试清单（都是踩过的）

| 现象 | 原因 / 解决 |
| --- | --- |
| `mpiexec has detected an attempt to run as root` | 导出 `OMPI_ALLOW_RUN_AS_ROOT=1` 与 `OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1`（项目脚本已内置）。 |
| 模型 <1 s 崩溃，`SIGFPE ← roffini_` | 用生产 flag 重建（去掉 `-ffpe-trap`）。 |
| `No module named 'exoplasim.pyfft'` | `export PATH=/opt/exoplasim/env/bin:$PATH` 后重跑 `sysconfigure()`。 |
| 地形没生效（`lsm` 全 1） | `landmap`/`topomap` 路径含空格；把 `.sra` 放到 `/opt/exoplasim/topo`。 |
| restart 没生效、模型冷启动 | 同上，`restartfile` 路径含空格；`check` 会拦。 |
| 启动器退出后 collector 也跟着没了 | 本机 WSL 会回收同会话后台进程；collector 必须以**前台** `wsl.exe` 常驻（`start.ps1` 已如此），不要 `nohup ... &`。 |
| `/api/state` 永远 0 帧、`configuring` | 模型还在编译/配置；看 `/opt/exoplasim/logs/monitor_collector.log`，确认 `plasim_snapshot` 出现。 |
| 浏览器连不上端口 | collector 绑 `0.0.0.0`；先在 WSL 内 `curl http://127.0.0.1:8765/api/state`，再查 Windows 防火墙 / WSL localhost 转发。 |
| 风场全 0 | `pyfft` import 失败（看日志）；其余图层不受影响。 |
| CPU 偏高 | 减少 `--max-runs`、把 `--interval` 调到 2 s、或缩小 `--ring`。 |
| run 列表里出现 `*_crashed/` | 那是 ExoPlaSim 崩溃转储，collector 已忽略。 |
| 已结束的多年 run 显示 12 个月 | 计划时长靠运行中的进程/spec 推断；进程和 spec 都没了就只剩 namelist 的 1 年，属已知限制。 |

日志：`/opt/exoplasim/logs/monitor_collector.log`
（用 `start.ps1` 启动时还会在 `monitor/collector.log` 留一份）。

自检：

```bash
# 接口 + EXOF + 与归档 npz 的数值对照
/opt/exoplasim/env/bin/python monitor/test/verify_api.py \
  --base http://127.0.0.1:8765 --run <run_id> --npz <archive>/pleiroma_snapshot.npz
```

浏览器自检：`http://127.0.0.1:8765/?selftest=1`。

---

## 7. 性能提示

* collector 每秒轮询一次、只读新追加字节；每个活跃 run 典型 CPU 为单核 **0.2–1 %**
  （轮询约 2 ms，RSS ≈100 MB）。
* 每帧 API 返回 9 个 float32 图层（T21 统一网格 64×128，约 300 KB），localhost 上完全不是瓶颈。
* 默认 ring buffer 48 帧/run；内存紧张时再调小。
* 监控端**绝不**跑 burn7/后处理，只读 raw 文件。
