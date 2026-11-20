# Docker 镜像使用与发布

Music MP3 API 提供官方容器镜像，内置 **Python 3.12 + ffmpeg（libmp3lame）+ Node.js + yt-dlp / yt-dlp-ejs + PySocks**，宿主机无需安装任何依赖。镜像以非 root 用户（UID 10001）运行，并自带 `HEALTHCHECK`。

## 镜像地址与标签

| 仓库 | 地址 |
| --- | --- |
| GitHub Container Registry（GHCR） | `ghcr.io/joe12801/music-mp3-api` |
| Docker Hub | `joe1280/music-mp3-api`（<https://hub.docker.com/r/joe1280/music-mp3-api>） |
| 阿里云 ACR（华南3·广州，国内加速） | `crpi-wv2uaegt8g2y0g3c.cn-guangzhou.personal.cr.aliyuncs.com/joeloveqt-project/music-mp3-api` |

标签：`latest`（main 分支）、`1.2.0`（与 `app/__init__.py` 版本一致）、`sha-<短提交>`、以及打 `v*` tag 时的版本标签。CI 同时构建 **linux/amd64** 与 **linux/arm64** 两种架构，`docker pull` 会自动选匹配本机的镜像；另有 `1.2.0-arm64` 为 ARM64 单架构镜像（在 ARM64 服务器上原生构建，GHCR 与 Docker Hub 均有）。

三处仓库由**同一条 CI 流水线**以 `provenance: false` 构建并推送，manifest list 与 amd64 / arm64 子镜像的 digest 完全相同（`1.2.0` 当前为 `sha256:9ff84ed198b…`），是同一份镜像。国内服务器建议优先用阿里云 ACR 地址，拉取速度通常明显快于 GHCR / Docker Hub。

```bash
docker pull ghcr.io/joe12801/music-mp3-api:1.2.0
docker pull ghcr.io/joe12801/music-mp3-api:latest
docker pull joe1280/music-mp3-api:1.2.0
# 国内加速（华南3·广州）
docker pull crpi-wv2uaegt8g2y0g3c.cn-guangzhou.personal.cr.aliyuncs.com/joeloveqt-project/music-mp3-api:1.2.0
```

## 快速运行

```bash
docker run -d --name music-api \
  -p 127.0.0.1:8000:8000 \
  -e MUSIC_API_KEY=<你的 API Key> \
  -e MUSIC_MAX_CACHE_MB=2048 \
  -v mp3-cache:/app/data/cache \
  -v mp3-lyrics:/app/data/lyrics \
  ghcr.io/joe12801/music-mp3-api:1.2.0

curl http://127.0.0.1:8000/api/health      # 探活无需密钥
curl -H "X-API-Key: <你的 API Key>" "http://127.0.0.1:8000/api/search?q=Chopin&limit=3"
```

或用 [`docker-compose.yml`](../docker-compose.yml)：

```bash
cp .env.example .env     # 填 MUSIC_API_KEY / MUSIC_PROXY 等，compose 会自动读取
docker compose up -d
docker compose logs -f
```

> 容器内默认 `MUSIC_HOST=0.0.0.0`（必须，否则端口映射访问不到）。若改成 `--env-file .env`，请确认 `.env` 里的 `MUSIC_HOST` 不是 `127.0.0.1`——环境变量优先级更高。

## 数据与权限

- 持久化目录：`/app/data/cache`（音频缓存）、`/app/data/lyrics`（歌词缓存）。镜像声明为 `VOLUME`，建议用命名卷或 bind mount。
- 进程用户是 UID/GID **10001**。用 bind mount 时，先让宿主目录可写：
  ```bash
  sudo mkdir -p ./data/cache ./data/lyrics
  sudo chown -R 10001:10001 ./data
  ```
- 需要登录态时**只读挂载** cookies，不要把凭据打进镜像：
  ```bash
  -v "$PWD/data/cookies.txt:/app/data/cookies.txt:ro"
  -e MUSIC_COOKIES_FILE=/app/data/cookies.txt
  ```
- `.dockerignore` 是白名单：只把 `Dockerfile`、`requirements.txt`、`run.py`、`app/`、`static/` 送进 build context，`.env`、cookies、`*.log`、缓存音频、Chrome profile 备份都不会进入构建上下文或镜像层。

## 代理（被 YouTube 风控的机房出口）

容器里已经装好 `PySocks`，只需传入代理地址：

```bash
-e MUSIC_PROXY=socks5://127.0.0.1:40000
```

WARP 跑在宿主机时，容器内不能用 `127.0.0.1`（那是容器自己），要用 `host.docker.internal`（需 `--add-host=host.docker.internal:host-gateway`）或把 WARP 也容器化，见 [`../README.md`](../README.md) 的 WARP 章节。

## 自己构建

```bash
docker build -t music-mp3-api:local .
docker run --rm -p 8000:8000 music-mp3-api:local
```

多架构构建（需 buildx + qemu）：

```bash
docker buildx create --use --name mp3builder
docker buildx build --platform linux/amd64,linux/arm64 -t <你的仓库>/music-mp3-api:dev --push .
```

## CI 自动发布

仓库的 [`.github/workflows/docker.yml`](../.github/workflows/docker.yml) 在 `push main`、打 `v*` tag 或手动触发时执行：

1. `smoke`：原生 amd64 构建 → 启动容器 → 检查 `/api/health`、`node --version`、`ffmpeg -version`；
2. `publish`：QEMU + buildx 构建 amd64/arm64 → 推送 **GHCR**（用 `GITHUB_TOKEN`，无需额外凭据）；若配置了对应 secrets，同时推送 **Docker Hub** 与 **阿里云 ACR**。

**启用 Docker Hub 推送**：在 GitHub 仓库 `Settings → Secrets and variables → Actions` 添加两个 Repository secrets：

| Secret | 值 |
| --- | --- |
| `DOCKERHUB_USERNAME` | Docker Hub 用户名（当前为 `joe1280`） |
| `DOCKERHUB_TOKEN` | Docker Hub **Access Token**（不是登录密码，建议只读/写范围限定到该仓库） |

**启用阿里云 ACR 推送**（个人版实例，地域华南3·广州）：添加以下 Repository secrets：

| Secret | 值 |
| --- | --- |
| `ACR_USERNAME` | ACR 控制台「仓库管理 → 访问凭证」显示的**登录名**（主账号为阿里云账号全名，常为手机号/邮箱；RAM 用户去掉 `.onaliyun.com` 后缀） |
| `ACR_PASSWORD` | 同页面「设置固定密码」设置的 **Registry 固定密码**（docker login 专用，**不是**阿里云账号登录密码） |
| `ACR_REGISTRY` | 可选，默认 `crpi-wv2uaegt8g2y0g3c.cn-guangzhou.personal.cr.aliyuncs.com`（2024 年 9 月后新建的**个人版实例**域名，从「访问凭证」页复制）；只有旧版实例才用 `registry.cn-guangzhou.aliyuncs.com` |
| `ACR_NAMESPACE` | 可选，默认 `joeloveqt-project`（需是账号下已创建的命名空间，ACR 个人版不会自动创建命名空间） |

> **ACR 个人版的两个坑**（已在本项目处理）：
> 1. 新版实例域名是 `crpi-*.personal.cr.aliyuncs.com`，用老的 `registry.cn-*.aliyuncs.com` 登录会一直 `unauthorized`；
> 2. 它不接受带 provenance attestation 的 OCI index（报 `unknown manifest class for application/vnd.oci.empty.v1+json`），CI 已设 `provenance: false` / `sbom: false`。手动推送可用 `docker pull <repo>@<子镜像 digest>` 取纯单架构镜像，再用 `docker manifest create` 组合。
>
> 自动创建的仓库**默认是私有**的。若希望像 GHCR / Docker Hub 一样匿名 `docker pull`，请在控制台把 `music-mp3-api` 仓库类型改为「公开」（或在命名空间设置里把默认仓库类型设为公有）。

保存后手动触发一次 `Docker` workflow（Actions → Docker → Run workflow），或再推一次代码即可。未配置某个仓库的 secrets 时 workflow 会打印提示并跳过该仓库，不会失败。

> GHCR 包通过 `GITHUB_TOKEN` 推送，默认继承仓库可见性（本仓库为 public）。若显示为 private，在 GitHub 个人页 `Packages → music-mp3-api → Package settings` 改为 Public 并关联仓库。

## 安全提醒

- 容器访问日志会记录完整 URL（含 `?api_key=`），不要公开日志；对外必须设置 `MUSIC_API_KEY`。
- 不要把镜像当作公开音乐分发服务，合规说明见仓库根目录 README 顶部。
- 升级前先备份/保留缓存卷；`MUSIC_MAX_CACHE_MB=0` 会取消容量上限，请自行控制磁盘。
