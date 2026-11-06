<#
  start.ps1 — 启动/停止 ExoPlaSim 实时监控（浏览器界面）

  用法：
    pwsh -NoProfile -File ".\monitor\start.ps1"                     # 自动发现最新运行的模拟
    pwsh -NoProfile -File ".\monitor\start.ps1" -Workdir /opt/exoplasim/runs/2026xxxx_pleiroma
    pwsh -NoProfile -File ".\monitor\start.ps1" -Port 8770 -NoBrowser
    pwsh -NoProfile -File ".\monitor\start.ps1" -Stop

  说明：
  - collector 跑在 WSL2 里（要读 /opt/exoplasim/runs 的 ext4 文件），只读、不碰模拟进程。
  - Windows 浏览器通过 127.0.0.1:<Port> 访问（WSL2 localhost 转发）。
  - 实际启动逻辑在 monitor/launch_collector.sh（setsid 脱离会话，避免脚本退出时误杀）。
  - 日志：monitor\collector.log（= WSL /tmp/monitor_collector.log 的副本）
#>
[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$Workdir = "",
    [string]$Outdir = "",
    [switch]$NoBrowser,
    [switch]$Stop,
    [switch]$Foreground
)

$ErrorActionPreference = 'Stop'
$env:WSL_UTF8 = '1'
$Distro = 'Ubuntu-24.04'
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$UnixHere = '/mnt/' + $Here.Substring(0, 1).ToLower() + $Here.Substring(2).Replace('\', '/')
$Launch = "$UnixHere/launch_collector.sh"
$LogWin = Join-Path $Here 'collector.log'
$LogWsl = '/opt/exoplasim/logs/monitor_collector.log'
$Url = "http://127.0.0.1:$Port/"

function ConvertTo-WslPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $Path }
    $p = $Path.Trim().Trim('"') -replace '\\', '/'
    if ($p -match '^([A-Za-z]):/(.*)$') { return "/mnt/$($Matches[1].ToLower())/$($Matches[2])" }
    return $p
}

function Test-Monitor {
    try {
        $resp = Invoke-WebRequest -Uri ($Url + 'api/runs') -UseBasicParsing -TimeoutSec 2
        return ($resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

if ($Stop) {
    & wsl.exe -d $Distro -u root -- bash -lc "pkill -f 'collector/server.py' 2>/dev/null; sleep 0.4; echo stopped" | Out-Null
    Write-Host "[monitor] collector 已停止（端口 $Port）"
    exit 0
}

# 已在运行：直接打开界面
if (Test-Monitor) {
    Write-Host "[monitor] 已有 collector 在 $Url"
    if (-not $NoBrowser) { Start-Process $Url }
    exit 0
}

$extra = @()
if ($Workdir) {
    $w = ConvertTo-WslPath $Workdir
    if ($w -match '\s') {
        Write-Warning "-Workdir 含空格：wsl.exe 传参会拆开，已忽略；请把运行目录放在无空格路径（项目约定 /opt/exoplasim/runs）"
    } else { $extra += @('--workdir', $w) }
}
if ($Outdir) {
    $o = ConvertTo-WslPath $Outdir
    if ($o -match '\s') {
        Write-Warning "-Outdir 含空格：spec.json 合并会失效（不影响监控本身）"
    } else { $extra += @('--outdir', $o) }
}

# 把启动脚本装到 WSL 内的无空格路径，并把真实的 monitor 目录写进配置文件。
# 这一步用 `& wsl.exe ... bash -lc <字符串>`（call operator 会正确传一个 argv）。
$setup = "printf '%s\n' '$UnixHere' > /opt/exoplasim/.monitor_dir; install -m 755 '$Launch' /opt/exoplasim/run_collector.sh"
& wsl.exe -d $Distro -u root -- bash -lc $setup | Out-Null

Write-Host "[monitor] 启动 collector: 端口=$Port workdir=$(if($Workdir){$Workdir}else{'auto'})"
if ($Foreground) {
    & wsl.exe -d $Distro -u root -- bash /opt/exoplasim/run_collector.sh $Port @extra
    exit $LASTEXITCODE
}

# collector 前台运行在 wsl.exe 里（见 launch_collector.sh 顶部注释：本机 WSL 会回收
# 后台子进程，所以后台化不可靠）。这里用 Start-Process + 单字符串参数把它挂起来，
# 不阻塞 PowerShell，也不依赖 PowerShell 进程存活。
$argLine = "-d $Distro -u root -- bash /opt/exoplasim/run_collector.sh $Port"
foreach ($item in $extra) { $argLine += " $item" }
$proc = Start-Process -FilePath 'wsl.exe' -ArgumentList $argLine -WindowStyle Hidden -PassThru
Write-Host "[monitor] wsl 进程 PID=$($proc.Id)"

$deadline = (Get-Date).AddSeconds(25)
$ok = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 400
    if (Test-Monitor) { $ok = $true; break }
}
if (-not $ok) {
    Write-Warning "[monitor] 25 s 内没等到 ${Url}api/runs。WSL 日志末尾："
    & wsl.exe -d $Distro -u root -- bash -lc "tail -40 $LogWsl 2>/dev/null || echo '(无日志)'"
    exit 1
}

# 把 WSL 日志同步一份到 Windows 侧，方便排查
& wsl.exe -d $Distro -u root -- bash -lc "cp -f $LogWsl '$UnixHere/collector.log' 2>/dev/null" | Out-Null

try {
    $state = (Invoke-WebRequest -Uri ($Url + 'api/state') -UseBasicParsing -TimeoutSec 3).Content | ConvertFrom-Json
    $runs = (Invoke-WebRequest -Uri ($Url + 'api/runs') -UseBasicParsing -TimeoutSec 3).Content | ConvertFrom-Json
    Write-Host ("[monitor] OK  run={0} status={1} frames={2}，发现 {3} 个模拟" -f `
        $state.run.run_id, $state.run.status, $state.run.frames_total, $runs.runs.Count)
    foreach ($entry in $runs.runs) {
        Write-Host ("           - {0} [{1}] frames={2}" -f $entry.run_id, $entry.status, $entry.frame_count)
    }
} catch {
    Write-Host "[monitor] OK（状态读取失败：$($_.Exception.Message)）"
}

if (-not $NoBrowser) {
    Start-Process $Url
    Write-Host "[monitor] 浏览器已打开：$Url"
} else {
    Write-Host "[monitor] 界面：$Url"
}
