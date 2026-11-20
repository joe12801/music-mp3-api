#!/usr/bin/env bash
# 证书续期模板（Linux / certbot）。复制为 tools/renew_cert.sh 并填入自己的域名/邮箱后使用，
# 实际脚本请自行保管，不要提交含真实域名/邮箱的文件。
#
# 多数发行版安装 certbot 后已自带 systemd timer（systemctl list-timers | grep certbot），
# 通常什么都不用做；本模板仅用于需要自定义 deploy-hook 的场景。
set -euo pipefail

DOMAIN="mp3.example.com"
EMAIL="admin@example.com"
WEBROOT="/var/www/html"

case "${1:-renew}" in
    issue)
        # 首次签发：HTTP-01，需要 80 端口可从公网访问
        certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" \
            --email "$EMAIL" --agree-tos --no-eff-email
        systemctl reload nginx
        ;;
    renew)
        # certbot 自带 timer 也可用：systemctl enable --now certbot.timer
        certbot renew --quiet --deploy-hook "systemctl reload nginx"
        ;;
    *)
        echo "用法: $0 issue|renew" >&2
        exit 1
        ;;
esac
