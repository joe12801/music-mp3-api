#!/usr/bin/env bash
# 在 Linux（Debian/Ubuntu）启用 / 检查 Cloudflare WARP 免费 SOCKS5 代理。
# proxy 模式只监听 127.0.0.1:40000，不接管全局路由，SSH 不会断。
#
#   sudo bash tools/start_warp_proxy.sh
#   WARP_PORT=40001 sudo -E bash tools/start_warp_proxy.sh   # 自定义端口
#
# 兼容新旧 warp-cli 语法（registration new / register、mode / set-mode）。
set -euo pipefail

PORT="${WARP_PORT:-40000}"
PROXY_URL="socks5://127.0.0.1:${PORT}"

log() { printf '\n[warp] %s\n' "$*"; }
die() { printf '[warp] ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "请用 root 运行：sudo bash tools/start_warp_proxy.sh"

# ---------------------------------------------------------------- 1. 安装客户端
if ! command -v warp-cli >/dev/null 2>&1; then
    log "安装 Cloudflare WARP 官方客户端…"
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y -qq curl gnupg lsb-release ca-certificates
        keyring=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
        [ -f "$keyring" ] || curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
            | gpg --yes --dearmor -o "$keyring"
        codename="$(lsb_release -cs)"
        echo "deb [signed-by=${keyring}] https://pkg.cloudflareclient.com/ ${codename} main" \
            > /etc/apt/sources.list.d/cloudflare-client.list
        apt-get update -qq
        apt-get install -y -qq cloudflare-warp
    else
        die "未找到 warp-cli，且本脚本只自动支持 Debian/Ubuntu。请先按官方文档安装客户端：https://pkg.cloudflareclient.com/"
    fi
fi

# ---------------------------------------------------------------- 2. 服务与注册
log "启动 warp-svc…"
systemctl enable --now warp-svc >/dev/null 2>&1 || service warp-svc start || true
sleep 2

if warp-cli registration show 2>/dev/null | grep -qi "Account type"; then
    log "已注册 WARP 账户，跳过注册"
else
    log "注册免费 WARP 账户…"
    warp-cli --accept-tos registration new >/dev/null 2>&1 \
        || warp-cli register >/dev/null 2>&1 \
        || die "注册失败，请手动执行 warp-cli --accept-tos registration new"
fi

# ---------------------------------------------------------------- 3. proxy 模式
log "切换到 proxy 模式，端口 ${PORT}…"
warp-cli mode proxy >/dev/null 2>&1 || warp-cli set-mode proxy >/dev/null 2>&1 || true
warp-cli proxy port "$PORT" >/dev/null 2>&1 \
    || warp-cli set-proxy-port "$PORT" >/dev/null 2>&1 \
    || die "设置代理端口失败，请检查 warp-cli 版本"
warp-cli connect >/dev/null 2>&1 || true
sleep 3
warp-cli status || true

# ---------------------------------------------------------------- 4. 验证
log "验证 SOCKS5 出口…"
if ! (exec 3<>/dev/tcp/127.0.0.1/"$PORT") 2>/dev/null; then
    die "端口 ${PORT} 未监听，请检查上面的 warp-cli status"
fi

if command -v curl >/dev/null 2>&1; then
    trace="$(curl -s --max-time 15 --socks5-hostname "127.0.0.1:${PORT}" \
        https://www.cloudflare.com/cdn-cgi/trace || true)"
    echo "$trace" | grep -E '^(warp|ip|loc)=' || true
    echo "$trace" | grep -q 'warp=on' && log "WARP 已生效 ✓" \
        || log "警告：trace 未显示 warp=on，请检查 warp-cli status"
fi

cat <<EOF

[warp] 完成 ✓
  在项目 .env 中设置：MUSIC_PROXY=${PROXY_URL}
  再装 SOCKS5 依赖：  .venv/bin/python -m pip install PySocks
  最后重启服务：      systemctl restart music-mp3-api
  验证出口：          curl -s --socks5-hostname 127.0.0.1:${PORT} https://ipinfo.io/json
  关闭隧道：          warp-cli disconnect
EOF
