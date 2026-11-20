# Linux 部署模板

本目录提供 Linux（systemd + Nginx）部署用的模板文件，**全部是示例值，不含任何密钥或真实域名**，复制后按注释替换即可。完整说明见仓库根目录 [`README.md`](../README.md) 的「环境要求 / Linux 快速开始 / 远程访问 / Linux 开机自启服务」章节。

| 文件 | 用途 |
| --- | --- |
| `install-linux.sh` | 一键安装：装系统依赖、建专用用户与 venv、部署代码、注册并启动 systemd 服务 |
| `music-mp3-api.service` | systemd 单元模板（开机自启、崩溃 5 秒重启、日志进 journald、基础沙箱） |
| `nginx.conf.example` | Nginx 反代示例（`proxy_buffering off`、600s 超时、Range 透传、certbot 签发） |

需要替换的占位符：`/opt/music-mp3-api`（安装目录）、`musicapi`（运行用户）、`mp3.example.com`（域名）、`127.0.0.1:8765`（上游端口）。

部署后常用命令：

```bash
sudo systemctl status music-mp3-api        # 状态
sudo systemctl restart music-mp3-api      # 改完 .env / 代码后必须重启
sudo journalctl -u music-mp3-api -f       # 实时日志（含完整 URL 与 api_key，勿外传）
sudo nginx -t && sudo systemctl reload nginx
```

> 若机器没有 systemd（如极简容器），请改用仓库根目录的 `Dockerfile`，或直接用 `python run.py` 前台运行 + `tmux`/`supervisord` 托管。
