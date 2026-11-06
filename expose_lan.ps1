<#
  expose_lan.ps1 — 把监控页面暴露到局域网 / 手机热点（Windows 侧端口转发）

  collector 跑在 WSL2 的 NAT 网络里，Windows 只把端口映射到 127.0.0.1。
  因此手机 / 局域网设备无法直接访问。本脚本（需要管理员）：

    1. 选择监听地址：默认取本机热点网关（192.168.137.x），否则取主网卡 IPv4；
    2. 添加端口代理：<监听 IP>:<端口>  →  <WSL IP>:<端口>
    3. 添加入站防火墙规则（TCP，LocalSubnet）

  用法（管理员 PowerShell）：
      pwsh -NoProfile -File .\monitor\expose_lan.ps1
      pwsh -NoProfile -File .\monitor\expose_lan.ps1 -Port 8770
      pwsh -NoProfile -File .\monitor\expose_lan.ps1 -ListenIp 192.168.137.1
      pwsh -NoProfile -File .\monitor\expose_lan.ps1 -Remove

  注意：
  * WSL 重启后 IP 会变化，需要重新运行一次（start.ps1 -Lan 会重新执行）。
  * 脚本把日志写到同目录 expose_lan.log（gitignore）。
#>
[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$Distro = 'Ubuntu-24.04',
    [string]$WslExe = 'wsl.exe',
    [string]$ListenIp = '',
    [string]$LogPath = '',
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

function Write-Log {
    param([string]$Message)
    $line = "[lan] $Message"
    Write-Host $line
    if ($LogPath) {
        try { Add-Content -LiteralPath $LogPath -Value $line -Encoding utf8 } catch { }
    }
}

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $LogPath) { $LogPath = Join-Path $Here 'expose_lan.log' }
if (Test-Path -LiteralPath $LogPath) {
    Remove-Item -LiteralPath $LogPath -Force -ErrorAction SilentlyContinue
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Log 'ERROR: 需要管理员权限（右键 PowerShell “以管理员身份运行”，或改用 start.ps1 -Lan 自动提权）。'
    exit 2
}

# ---------------------------------------------------------------- 监听 IP
if (-not $ListenIp) {
    $candidates = @()
    try {
        $candidates = Get-NetIPAddress -AddressFamily IPv4 |
            Where-Object {
                $_.IPAddress -notlike '127.*' -and
                $_.IPAddress -notlike '169.254.*' -and
                $_.AddressState -eq 'Preferred' -and
                ($_.IPAddress -like '192.168.137.*' -or $_.PrefixOrigin -ne 'WellKnown')
            } |
            Select-Object -ExpandProperty IPAddress
    } catch { }
    if ($candidates -contains '192.168.137.1') {
        $ListenIp = '192.168.137.1'
    } elseif ($candidates) {
        $ListenIp = $candidates[0]
    } else {
        $ListenIp = '192.168.137.1'
    }
}
Write-Log "listen IP = $ListenIp"

# ---------------------------------------------------------------- WSL IP
function Get-WslIPv4 {
    try {
        $raw = & $WslExe -d $Distro -u root -- bash -lc "hostname -I" 2>$null
    } catch { $raw = $null }
    if (-not $raw) { return $null }
    foreach ($token in ([string]$raw).Split(' ', [System.StringSplitOptions]::RemoveEmptyEntries)) {
        if ($token -match '^\d{1,3}(\.\d{1,3}){3}$' -and $token -notlike '127.*') { return $token }
    }
    return $null
}

$wslIp = Get-WslIPv4
if (-not $wslIp) {
    Write-Log "ERROR: 无法获取 WSL IP（wsl -d $Distro 不可用？）"
    exit 3
}
Write-Log "WSL IP = $wslIp"

$ruleName = "ExoPlaSim Monitor TCP $Port"
if ($Remove) {
    & netsh interface portproxy delete v4tov4 listenaddress=$ListenIp listenport=$Port 2>&1 | Out-Null
    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    Write-Log "已清理端口代理与防火墙规则（$ListenIp`:$Port）"
    exit 0
}

# ---------------------------------------------------------------- 端口代理
$svc = Get-Service iphlpsvc -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -ne 'Running') { try { Start-Service iphlpsvc } catch { } }

& netsh interface portproxy delete v4tov4 listenaddress=$ListenIp listenport=$Port 2>&1 | Out-Null
& netsh interface portproxy add v4tov4 listenaddress=$ListenIp listenport=$Port connectaddress=$wslIp connectport=$Port | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR: netsh portproxy 添加失败（exit $LASTEXITCODE）"
    exit 4
}
Write-Log "端口代理：$ListenIp`:$Port -> $wslIp`:$Port"

# ---------------------------------------------------------------- 防火墙
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $Port -RemoteAddress LocalSubnet -Profile Any `
    -Description "ExoPlaSim live monitor (WSL port proxy)" | Out-Null
Write-Log "已设置防火墙规则：$ruleName（TCP $Port，LocalSubnet）"
Write-Log ""
Write-Log "局域网/手机访问： http://$ListenIp`:$Port/"
Write-Log "手机需连接本机热点；WSL 重启后重新运行本脚本或 start.ps1 -Lan。"
