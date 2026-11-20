# 启用 / 检查本机 Cloudflare WARP 免费 SOCKS5 代理。
# 官方客户端的 proxy 模式只监听 127.0.0.1:40000，不接管全局路由，远程桌面不会断。
# 用法：以管理员身份运行 PowerShell，执行  ./tools/start_warp_proxy.ps1
$ErrorActionPreference = 'Stop'

$cli = "C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe"
if (-not (Test-Path $cli)) {
    Write-Host "未找到 warp-cli.exe，请先安装官方客户端：" -ForegroundColor Yellow
    Write-Host "  https://1111-releases.cloudflareclient.com/windows/Cloudflare_WARP_Release-x64.msi"
    Write-Host "  静默安装：msiexec /i Cloudflare_WARP.msi /quiet /norestart"
    exit 1
}

$svc = Get-Service CloudflareWARP -ErrorAction SilentlyContinue
if (-not $svc) { Write-Host "CloudflareWARP 服务不存在，请先安装客户端。" -ForegroundColor Red; exit 1 }
if ($svc.Status -ne 'Running') { Start-Service CloudflareWARP; Start-Sleep -Seconds 2 }

$reg = (& $cli registration show 2>&1 | Out-String)
if ($reg -notmatch 'Account type') {
    Write-Host '首次使用，注册免费 WARP 账户…'
    & $cli --accept-tos registration new | Out-Null
}

& $cli mode proxy | Out-Null
& $cli proxy port 40000 | Out-Null
& $cli connect | Out-Null
Start-Sleep -Seconds 3

Write-Host '--- warp-cli status ---'
& $cli status

$listen = Get-NetTCPConnection -LocalPort 40000 -State Listen -ErrorAction SilentlyContinue
if (-not $listen) {
    Write-Host 'SOCKS5 端口 40000 未监听，请检查 status 与防火墙。' -ForegroundColor Red
    exit 1
}
Write-Host 'WARP SOCKS5 已就绪：socks5://127.0.0.1:40000' -ForegroundColor Green
Write-Host '在 .env 中设置 MUSIC_PROXY=socks5://127.0.0.1:40000 并重启 API 服务。'
Write-Host '验证出口：curl -s --socks5-hostname 127.0.0.1:40000 https://ipinfo.io/json'
Write-Host ('关闭隧道：& "' + $cli + '" disconnect') -ForegroundColor DarkGray
