# Deploy & debug on a new machine

This guide sets up **ExoPlaSim + the live monitor** from scratch on Windows + WSL2,
and lists the failure modes we actually hit. It contains no machine-specific paths:
every path below is either relative to this project or configurable.

For the Chinese version see [DEPLOY.zh-CN.md](DEPLOY.zh-CN.md).

---

## 0. What you need

| Item | Notes |
| --- | --- |
| Windows 10 21H2+ / Windows 11 | WSL2 is required (the model needs a Linux Fortran toolchain). |
| WSL2 + Ubuntu 24.04 | `wsl --install -d Ubuntu-24.04`; run everything as root (`sudo` may need a password you don't have). |
| CPU / RAM | T21 works well with 4–8 MPI ranks; 16 GB RAM is plenty. T42/T85 need more. |
| Disk | ExoPlaSim + one T21 run is a few GB; each year writes ~20 MB `.npz` + ~40 MB raw. |
| Python | The project uses its own venv, **not** the system Python. |

The monitor itself has **no third-party frontend dependencies**. The collector only
needs the Python standard library + `numpy` (and the ExoPlaSim venv's `pyfft` for wind).

---

## 1. Install the Linux toolchain (WSL)

```powershell
wsl --install -d Ubuntu-24.04
```

Then, from Windows (root shell, no password prompt):

```powershell
wsl -d Ubuntu-24.04 -u root -- bash -lc "apt-get update && apt-get install -y \
  build-essential gfortran python3-dev python3-venv \
  openmpi-bin libopenmpi-dev libnetcdf-dev"
```

> **Why root?** In many setups the WSL user's `sudo` asks for a password that is not
> known. Running as root via `wsl -u root` avoids that entirely.

---

## 2. Create the ExoPlaSim venv and install packages

Keep everything on the Linux filesystem (`/opt`), **not** on `/mnt/c` or `/mnt/e`
(9p is 10–100× slower and will starve the model).

```bash
python3 -m venv /opt/exoplasim/env
/opt/exoplasim/env/bin/pip install --upgrade pip
/opt/exoplasim/env/bin/pip install numpy scipy matplotlib netCDF4 exoplasim
```

If outbound HTTPS is restricted, use a reachable mirror, e.g.:

```bash
mkdir -p /root/.config/pip
printf '[global]\nindex-url = https://pypi.tuna.tsinghua.edu.cn/simple\n' \
  > /root/.config/pip/pip.conf
```

Then initialise the Fortran core:

```bash
export PATH=/opt/exoplasim/env/bin:$PATH     # REQUIRED for f2py/meson/ninja
/opt/exoplasim/env/bin/python -c "import exoplasim; exoplasim.sysconfigure()"
```

### Known trap: silent `pyfft` failure

If `exoplasim.pyfft` is missing, the model still runs but **post-processing crashes**
("crashed or begun producing garbage"). `compile_pyfft()` only prints an error code.
Always export the venv `bin` to `PATH` before `sysconfigure()` and verify:

```bash
/opt/exoplasim/env/bin/python -c "import exoplasim.pyfft; print('pyfft ok')"
```

### Known trap: `-ffpe-trap` breaks aquaplanets

The default `MOST_F90_OPTS` includes `-ffpe-trap=...`, which turns harmless IEEE
exceptions during initialisation into a hard `SIGFPE` for `aquaplanet=True`.
Use production flags:

```
MOST_F90_OPTS = -O3 -cpp -ffixed-line-length-132
```

Then delete old binaries (`.../exoplasim/plasim/run/most_plas*.x`) before rebuilding,
because the executable name does **not** encode compiler flags.

---

## 3. Run directories

The model and the monitor share one convention:

| Path | Purpose |
| --- | --- |
| `/opt/exoplasim/env` | venv |
| `/opt/exoplasim/runs` | model work directories (ext4) |
| `/opt/exoplasim/topo` | terrain / initial-condition `.sra` files, **no spaces in paths** |
| `/opt/exoplasim/logs` | monitor log (`monitor_collector.log`) |

`/opt/exoplasim/topo` must not contain spaces: ExoPlaSim copies `landmap`/`topomap`
with an unquoted `os.system("cp ...")`; a space silently disables the terrain
(`lsm` all 1, `sg` all 0). The Windows workspace path (e.g. `E:\EXO Plasim`) also
contains a space — keep all model-facing paths under `/opt`.

---

## 4. Install the monitor

Copy the `monitor/` directory anywhere readable from Windows **and** WSL
(this project keeps it next to the run data, but that is not required).

```powershell
# from the repository root
pwsh -NoProfile -File ".\monitor\start.ps1" -NoBrowser
```

`start.ps1` does three things:

1. copies its launcher to `/opt/exoplasim/run_collector.sh` (space-free path);
2. starts a **foreground** `wsl.exe` hosting the collector on `127.0.0.1:8765`;
3. waits for `/api/state` and optionally opens the browser.

Useful flags:

| Flag | Meaning |
| --- | --- |
| `-Port 8770` | change the HTTP port |
| `-Workdir /opt/exoplasim/runs/<id>` | pin one run instead of auto-discovery |
| `-NoBrowser` | do not open a browser |
| `-Foreground` | run the collector in the current console (debug) |
| `-Stop` | stop the collector |

If the collector runs inside WSL on another host, open `http://<host>:8765/`.

---

## 5. First data

Either attach to a run you already started, or create a 6-month test case
(≈40 s wall on 8 cores; this is the recommended way to validate an installation):

```bash
/opt/exoplasim/env/bin/python "/mnt/<drive>/<repo>/monitor/test/run_short.py" \
  --spec "/mnt/<drive>/<repo>/<your-spec>.json" \
  --months 6 --outdir "/mnt/<drive>/<repo>/monitor/demo" --ncpus 8
```

`run_short.py` keeps the physics identical and only shortens the integration interval.
For a real run use the project's normal entry point (e.g. `exo.ps1 run`).

The collector auto-discovers any `/opt/exoplasim/runs/**/plasim_namelist` (top level and one nested level, e.g. `<case>/run_round_002/`) within
`--recent-minutes` (default 120) and lists it in the run selector.

---

## 6. LAN / phone access

By default the UI is only on `http://127.0.0.1:8765/`, because the collector runs inside
WSL2's NAT network. To watch from a phone (connected to the PC's mobile hotspot) or another
machine on the same LAN:

```powershell
pwsh -NoProfile -File ".\monitor\start.ps1" -Lan
```

`start.ps1` starts the collector and then invokes `monitor/expose_lan.ps1` elevated. The
helper:

1. picks the host IP (the mobile-hotspot gateway `192.168.137.1` when present, otherwise the
   primary adapter IPv4; override with `-ListenIp`);
2. adds a Windows port proxy `<host-ip>:8765 → <wsl-ip>:8765` (WSL2 is behind NAT);
3. adds an inbound firewall rule for TCP 8765 scoped to `LocalSubnet`.

It prints the URL, e.g. `http://192.168.137.1:8765/`. Use that URL on the phone (the phone
must be on the hotspot/Wi-Fi; mobile data can stay on).

Notes:
* WSL2 gets a new IP after `wsl --shutdown`; re-run `start.ps1 -Lan` to refresh the proxy.
* `pwsh -File .\monitor\start.ps1 -LanRemove` removes the proxy and firewall rule.
* The helper needs administrator rights (UAC prompt); the collector itself remains read-only.
* Windows Firewall must be on; antivirus/VPN tools that filter inbound traffic may also block
  the port. Test from Windows first: `curl http://<host-ip>:8765/api/runs`.

## 7. Debugging checklist

| Symptom | Cause / fix |
| --- | --- |
| `mpiexec has detected an attempt to run as root` | Export `OMPI_ALLOW_RUN_AS_ROOT=1` and `OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1`, or use the project's scripts (they do). |
| Model aborts in <1 s, `SIGFPE ← roffini_` | Rebuild with production flags (no `-ffpe-trap`). |
| `No module named 'exoplasim.pyfft'` | `export PATH=/opt/exoplasim/env/bin:$PATH` then re-run `sysconfigure()`. |
| Terrain ignored (`lsm` all 1) | `landmap`/`topomap` path contains a space; move `.sra` to `/opt/exoplasim/topo`. |
| Restart silently ignored | Same space problem for `restartfile`; `check` should reject it. |
| Collector exits when the launcher exits | This WSL kills background session children. Run the collector in a **foreground** `wsl.exe` (as `start.ps1` does) or keep the launching shell alive. |
| `/api/state` is 0 frames forever, status `configuring` | The model is still compiling/configuring; check `/opt/exoplasim/logs/monitor_collector.log` and that `plasim_snapshot` appears in the workdir. |
| Latest run shows 0 frames, log repeats `bad main header marker 0` / `131072` / `1033691254` | A freshly created `plasim_snapshot` had a zero/partial header. Current collector retries from offset 0 and clears the old header on every reset; if you run an older build, update/restart it. Regression test: `collector/selftest_rawburn.py`. |
| Browser cannot reach the port | Confirm the collector binds `0.0.0.0`, test inside WSL with `curl http://127.0.0.1:8765/api/state`, then check Windows firewall / WSL localhost forwarding. |
| Wind layer is flat/zero | `pyfft` import failed (see log); other layers still work. |
| Very high CPU | Reduce `--max-runs`, raise `--interval` (e.g. 2 s), or limit `--ring`. |
| `*_crashed/` directories appear in the run list | The collector now ignores `*_crashed`; those are ExoPlaSim crash dumps, not live runs. |
| Old finished runs show 12 planned months | Planned length is inferred from the process/spec while alive; for a finished run with no spec, the namelist's 1 year is the only fallback. |

Collector log: `/opt/exoplasim/logs/monitor_collector.log`
(and a Windows copy at `monitor/collector.log` when started via `start.ps1`).

Self-tests:

```bash
# API + EXOF + numeric comparison with an archived npz
/opt/exoplasim/env/bin/python monitor/test/verify_api.py \
  --base http://127.0.0.1:8765 --run <run_id> --npz <archive>/pleiroma_snapshot.npz

# frontend (open in a browser)
http://127.0.0.1:8765/?selftest=1
```

---

## 8. Performance notes

* The collector is **mtime-adaptive**: the frame period is measured from the raw file's
  `mtime`, and the idle poll is `clamp(period/4, 1 s, 30 s)`. A `mtime` change wakes the
  collector on the next 1 s tick, so a new snapshot is shown within ~1 s even though the
  idle interval can be much longer. `--no-adaptive` restores a fixed `--interval`;
  `--max-interval` caps the idle period.
* Reads are chunked (8 MiB, ≤64 MiB per poll); a 493 MB T85 snapshot settles at
  ≈355 MB RSS instead of ≈540 MB. Typical CPU is **0.2–1 % of one core per active run**
  (≈2 ms poll on T21, ≈35 ms when a T85 frame is built).
* The API returns 9 float32 layers on a 64×128 grid (T21) per frame; this is ~300 KB
  per update and is not a bottleneck on localhost.
* Keep the ring at its default 48 frames/run unless memory-constrained.
* Never run `burn7`/post-processing from the monitor; it only reads raw files.
