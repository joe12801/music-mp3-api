#!/usr/bin/env bash
# Music MP3 API —— Linux 一键部署为 systemd 服务（Debian/Ubuntu、RHEL/Fedora）
#
#   sudo bash deploy/install-linux.sh                 # 默认装到 /opt/music-mp3-api
#   sudo bash deploy/install-linux.sh /srv/music-api   # 自定义目录
#   APP_USER=music sudo bash deploy/install-linux.sh # 自定义运行用户
#
# 脚本幂等：重复执行不会重建 venv、不会覆盖已有 .env。
# 若源目录含本机 .env / data/cookies.txt，会一并复制到目标机并设为 600，
# 请确认目标机器可信；不想要就先删掉源文件再执行。
set -euo pipefail

APP_DIR="${1:-/opt/music-mp3-api}"
APP_USER="${APP_USER:-musicapi}"
SERVICE="music-mp3-api"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log()  { printf '\n[install] %s\n' "$*"; }
die()  { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "请用 root 运行：sudo bash deploy/install-linux.sh"
[ -d /run/systemd/system ] || die "当前环境没有 systemd（容器？），请改用仓库根目录的 Dockerfile，或前台运行 python run.py"

# ---------------------------------------------------------------- 1. 系统依赖
log "1/6 安装系统依赖（Python 3.10+ / ffmpeg / Node.js 18+）…"
if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip ffmpeg curl ca-certificates nodejs
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 python3-pip ffmpeg curl ca-certificates nodejs
elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 python3-pip ffmpeg curl ca-certificates nodejs
else
    die "未识别的包管理器，请手动安装 python3 / python3-venv / ffmpeg / nodejs 后重跑"
fi

command -v ffmpeg >/dev/null 2>&1 || die "ffmpeg 安装失败（RHEL 系可能需要先启用 RPM Fusion 等第三方源）"
if command -v node >/dev/null 2>&1; then
    node_major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
    [ "$node_major" -ge 18 ] 2>/dev/null \
        || log "警告：Node.js 版本偏低（$node_major），建议 >= 18，否则 YouTube JS challenge 可能失败"
else
    die "未找到 Node.js（yt-dlp 的 n/signature challenge 需要），请先安装 Node.js 18+"
fi

# ---------------------------------------------------------------- 2. 用户与目录
log "2/6 创建专用用户与安装目录：$APP_DIR（用户 $APP_USER）…"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi
install -d -m 0755 "$APP_DIR"

# 复制代码，排除 git 元数据与运行时数据；保留 .env / cookies（若存在）以便直接接管
tar -C "$SRC" \
    --exclude=.git --exclude=__pycache__ --exclude=.pytest_cache \
    --exclude='data/cache/*' --exclude='data/lyrics/*' \
    --exclude='downloads/*.mp3' --exclude='*.log' \
    -cf - . | tar -C "$APP_DIR" -xf -

if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    log "已从 .env.example 生成 .env：请填写 MUSIC_API_KEY / MUSIC_PORT / MUSIC_PROXY 等"
fi
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"
if [ -f "$APP_DIR/data/cookies.txt" ]; then chmod 600 "$APP_DIR/data/cookies.txt"; fi

# ---------------------------------------------------------------- 3. Python 环境
log "3/6 创建 venv 并安装依赖…"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
    python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip -q
"$APP_DIR/.venv/bin/python" -m pip install -q -r "$APP_DIR/requirements.txt"
chown -R "$APP_USER":"$APP_USER" "$APP_DIR/.venv"

# ---------------------------------------------------------------- 4. systemd
log "4/6 注册 systemd 服务…"
sed -e "s|/opt/music-mp3-api|$APP_DIR|g" \
    -e "s|^User=.*|User=$APP_USER|" \
    -e "s|^Group=.*|Group=$APP_USER|" \
    "$SRC/deploy/music-mp3-api.service" > "/etc/systemd/system/${SERVICE}.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null

# ---------------------------------------------------------------- 5. 启动
log "5/6 启动服务…"
systemctl restart "$SERVICE"
sleep 2
systemctl is-active --quiet "$SERVICE" || {
    journalctl -u "$SERVICE" -n 30 --no-pager || true
    die "服务未能启动，请查看上面的日志"
}

# ---------------------------------------------------------------- 6. 验证
log "6/6 健康检查…"
port="$(sed -n 's/^MUSIC_PORT=//p' "$APP_DIR/.env" | tail -1 | tr -d ' \"' || true)"
port="${port:-8000}"
if curl -fsS --max-time 5 "http://127.0.0.1:${port}/api/health" >/dev/null 2>&1; then
    log "健康检查通过：http://127.0.0.1:${port}/api/health"
else
    log "健康检查暂未通过（可能仍在启动），用 journalctl -u $SERVICE -f 查看"
fi

cat <<EOF

[install] 完成 ✓
  状态    : systemctl status $SERVICE
  日志    : journalctl -u $SERVICE -f          # 访问日志含 api_key，勿外传
  重启    : systemctl restart $SERVICE         # 改完 .env / 代码后必须重启
  卸载    : systemctl disable --now $SERVICE && rm /etc/systemd/system/$SERVICE.service

  对外访问请另外配置 Nginx（模板：deploy/nginx.conf.example）并用 certbot 签发证书；
  API 只监听 127.0.0.1:${port}，防火墙/云安全组无需为该端口放行。
  若出口 IP 被 YouTube 限制，先跑 tools/start_warp_proxy.sh，再在 .env 设置
  MUSIC_PROXY=socks5://127.0.0.1:40000 并重启服务。
EOF
