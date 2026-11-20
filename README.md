# Music MP3 API

> GitHub: <https://github.com/joe12801/music-mp3-api> ｜ License: [MIT](LICENSE) ｜ CI: [![CI](https://github.com/joe12801/music-mp3-api/actions/workflows/ci.yml/badge.svg)](https://github.com/joe12801/music-mp3-api/actions/workflows/ci.yml) ｜ Docker: [![Docker](https://github.com/joe12801/music-mp3-api/actions/workflows/docker.yml/badge.svg)](https://github.com/joe12801/music-mp3-api/actions/workflows/docker.yml) ｜ 版本 `1.2.0`

一个本地自建的音乐音频接口：用户给一个关键词或 YouTube 链接，服务用 **yt-dlp** 拉取最佳音轨、用 **ffmpeg** 提取/转码成 MP3 返回，并把结果写入本地缓存；同样的请求再次调用时直接读缓存返回，不再联网、不再转码。

```bash
git clone https://github.com/joe12801/music-mp3-api.git
cd music-mp3-api
python -m pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
python run.py               # http://127.0.0.1:8000
```

> 仓库内所有域名、邮箱、路径均为示例占位符；真实部署值只放在本机 `.env`（已 gitignore）。完整接入文档见 [`docs/API.md`](docs/API.md)，机器可读规格见 [`docs/openapi.json`](docs/openapi.json)。

```
请求 url / q / video_id
   │
   ├─ 输入校验（默认只放行 YouTube 域名，防 SSRF / 任意 URL 代理）
   │
   ├─ 缓存查询  ── HIT ──► FileResponse（支持 Range，可拖动进度条）
   │     │                  响应头 X-Cache: HIT
   │    MISS
   │     ▼
   ├─ 按 video_id 去重加锁（同一首歌并发请求只下载一次）
   ├─ yt-dlp 解析元数据 → 时长/大小限制
   ├─ yt-dlp 下载 bestaudio → ffmpeg 转 MP3（指定码率）
   ├─ 原子写入缓存 + index.json（LRU 淘汰 / TTL 过期）
   └─► FileResponse，响应头 X-Cache: MISS
```

---

> **合规提示（务必先读）**
>
> 本项目是一个技术演示/自用工具。YouTube 上的绝大多数音乐受版权保护，下载、转码、缓存与再分发可能违反 YouTube 服务条款及所在国家/地区的著作权法规。
> 请**仅用于你拥有版权、已获授权或平台明确允许离线使用的内容**，仅限个人学习与自用，**不要对外提供公开服务、不要用于商业用途、不要传播下载内容**。
> 部署到公网前请自行完成合规评估；作者/贡献者不对使用方式承担责任。若你只是想听歌，优先使用官方授权的音乐服务。

---

## 功能特性

- **三种输入**：`url`（YouTube 链接）、`q`（关键词，取搜索第一条）、`video_id`（11 位 ID）。
- **搜索与解析分离**：`/api/search` 只返回列表，`/api/resolve` 只返回单个视频元数据，都不下载。
- **自动转码**：yt-dlp 只负责下载最佳音轨，服务**显式调用 ffmpeg** 提取音频并按 64/96/128/192/256/320 kbps 编码 MP3（不依赖 yt-dlp 的 postprocessor，避免源已是 MP3 时码率参数被忽略）；没有 ffmpeg 时自动降级返回原生音频流（m4a/webm/opus），服务不会挂。
- **本地缓存**：key = `sha1(video_id | bitrate | codec)`，`index.json` 持久化，进程重启后缓存仍在；支持 LRU 容量淘汰、滑动 TTL、单条删除与一键清空。
- **并发去重**：全局并发信号量 + 按 video_id 加锁，N 个人同时点同一首歌只下一次。
- **离线命中**：带 `video_id` 且已缓存时，直接返回文件，完全不触发网络请求。
- **两种调用方式**：同步 `/api/music`（等下载完直接返回文件）或异步任务 `/api/jobs`（进度条、适合慢网络）。
- **Range 支持**：`FileResponse` 原生支持 `Range`/`206 Partial Content`，播放器可拖动进度。
- **双后端**：优先用 `yt_dlp` Python 模块；模块缺失时自动回退到 `yt-dlp` 命令行（可用环境变量强制指定）。
- **安全**：默认域名白名单、禁用户名/非标端口、搜索词防参数注入、可选 API Key、CORS 可控。
- **自带演示页**：`/` 一个可搜索/播放/查看缓存的单页 UI，无任何外部 CDN 依赖。
- **同步歌词**：`/api/lyrics` 优先用 yt-dlp 拉取 YouTube 人工/自动字幕；视频没有字幕时自动回退到 LRCLIB，按标题/歌手/时长匹配同步 LRC 歌词。解析结果带时间轴并缓存，演示播放器逐行高亮、自动滚动、点击跳转。

## 环境要求

| 依赖 | 说明 |
| --- | --- |
| Python 3.10+ | 本仓库在 3.13 上开发 |
| ffmpeg | 需带 `libmp3lame`（常见发行版都带）。用 `ffmpeg -version` 验证 |
| yt-dlp | `pip install -r requirements.txt` 会装 Python 模块；也支持系统已装的 CLI |
| yt-dlp-ejs | requirements 已含，提供 YouTube JS challenge 求解脚本 |
| Node.js 18+ 或 Deno | **必须**：YouTube 的 n/signature challenge 需要 JS 运行时，否则只返回图片格式 |

### 平台支持

项目本身是纯 Python，跨平台运行；CI 在 **Ubuntu + Windows × Python 3.10/3.12** 上跑同一套测试。

| 平台 | 推荐运行方式 |
| --- | --- |
| **Linux**（含 ARM64 服务器 / 树莓派 / NAS） | `systemd` 服务（仓库提供 [`deploy/`](deploy/README.md) 模板）或 Docker；反向代理用 Nginx + certbot |
| **Windows** 10/11/Server | 直接 `python run.py`，或用 nssm 注册成开机自启服务 |
| **macOS** | 直接 `python run.py`（Homebrew 安装依赖） |

### Linux 依赖安装

```bash
# Debian / Ubuntu
sudo apt update && sudo apt install -y python3 python3-venv python3-pip ffmpeg nodejs curl
node -v                 # 需 >= 18
ffmpeg -version | head -1   # 需带 libmp3lame
```

```bash
# RHEL / Fedora / Rocky（ffmpeg 可能需要先启用 RPM Fusion 等第三方源）
sudo dnf install -y python3 python3-pip ffmpeg nodejs curl

# Arch
sudo pacman -S python ffmpeg nodejs
```

发行版自带的 Node 若低于 18，用 [NodeSource](https://github.com/nodesource/distributions) 或 nvm 安装新版本；`yt-dlp-ejs` 依赖它求解 YouTube 的 JS challenge。Docker 镜像已内置 ffmpeg，宿主机无需安装这些依赖。

## 快速开始

**Linux / macOS**

```bash
cd music-mp3-api
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env           # 按需修改端口、API Key、缓存上限
python run.py                  # 或 python -m uvicorn app.main:app --reload
```

**Windows**（CMD / PowerShell）

```bat
python -m pip install -r requirements.txt
copy .env.example .env
python run.py                  :: 也可直接双击 start.bat
```

打开 <http://127.0.0.1:8000> 用演示页，或 <http://127.0.0.1:8000/docs> 看 Swagger 文档（端口由 `MUSIC_PORT` 决定，改 `.env` 即可自定义）。

**Docker（Linux / macOS / Windows 都可用）**：官方镜像内置 Python 3.12、ffmpeg、Node.js、yt-dlp 与 PySocks，宿主机只需 Docker；CI 自动构建 **linux/amd64 + linux/arm64** 双架构并发布到 **GHCR** 与 **Docker Hub**。完整说明见 [`docs/DOCKER.md`](docs/DOCKER.md)。

```bash
# 直接拉官方镜像（GHCR；Docker Hub 地址为 <用户名>/music-mp3-api:1.2.0）
docker run -d --name music-api -p 127.0.0.1:8000:8000 \
  -e MUSIC_API_KEY=<你的 API Key> \
  -v mp3-cache:/app/data/cache \
  -v mp3-lyrics:/app/data/lyrics \
  ghcr.io/joe12801/music-mp3-api:1.2.0

# 或用仓库自带的编排文件（自动读取同目录 .env）
cp .env.example .env && docker compose up -d

# 也可本地构建
docker build -t music-mp3-api:local .
```

> 容器内必须 `MUSIC_HOST=0.0.0.0`（镜像已设好），否则端口映射访问不到；用 `--env-file .env` 时确认 `.env` 里的 `MUSIC_HOST` 不是 `127.0.0.1`。镜像以 UID 10001 运行，bind mount 宿主目录需先 `chown -R 10001:10001 ./data`；`.dockerignore` 白名单确保 `.env`、cookies、`*.log`、缓存音频不会进入镜像。

## 配置项

全部通过环境变量或项目根目录 `.env` 设置，前缀 `MUSIC_`，不配置也能跑。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MUSIC_HOST` / `MUSIC_PORT` | `127.0.0.1` / `8000` | 监听地址 |
| `MUSIC_API_KEY` | 空 | 设置后 `/api/*` 需带 `X-API-Key` 头或 `?api_key=`；空表示不鉴权 |
| `MUSIC_DEMO_UI` | `true` | 是否启用 `/` 演示页 |
| `MUSIC_CORS_ORIGINS` | `*` | 允许的前端来源，逗号分隔 |
| `MUSIC_CACHE_DIR` | `./data/cache` | 缓存目录（建议挂到独立磁盘） |
| `MUSIC_MAX_CACHE_MB` | `2048` | 缓存总上限，`0` = 不限制；超出按 LRU 淘汰 |
| `MUSIC_CACHE_TTL_DAYS` | `30` | 滑动过期天数（按最后访问时间），`0` = 永不过期 |
| `MUSIC_LYRICS_TTL_DAYS` | `7` | 字幕歌词缓存天数，独立于音频缓存，`0` = 永不过期 |
| `MUSIC_LYRICS_EXTERNAL` | `true` | YouTube 无字幕时是否回退到 LRCLIB 第三方歌词库；`false` 可完全关闭外部歌词请求 |
| `MUSIC_MAX_FILE_MB` | `60` | 单个音频上限，`0` = 不限制 |

> 上表是软件默认值。需要大容量缓存时可在 `.env` 里调大，例如 `MUSIC_MAX_CACHE_MB=1024000`（1000 × 1024 MB = 1000 GB）、`MUSIC_CACHE_TTL_DAYS=0`（0 = 不做 TTL 过期清理，缓存永久保留）。改完需重启服务生效，`/api/cache` 的 `limit_mb` 与 `ttl_seconds` 会显示新值。即使关闭 TTL，缓存仍受 LRU 容量上限约束：超过上限时按最近使用时间淘汰最旧文件，不会无限增长。
| `MUSIC_MP3_BITRATE` | `192` | 默认码率，可选 `64/96/128/192/256/320` |
| `MUSIC_MAX_DURATION_SEC` | `900` | 拒绝超过该时长的视频，`0` = 不限制（防止误下整场直播） |
| `MUSIC_MAX_CONCURRENT_DOWNLOADS` | `2` | 同时下载数上限 |
| `MUSIC_YTDLP_TIMEOUT` / `MUSIC_YTDLP_RETRIES` | `60` / `3` | 网络超时与重试 |
| `MUSIC_SEARCH_MAX_LIMIT` | `20` | 单次搜索最大返回数 |
| `MUSIC_YTDLP_BACKEND` | `auto` | `python` / `cli` / `auto` |
| `MUSIC_FFMPEG_PATH` | 空 | ffmpeg 路径，留空自动从 PATH 找 |
| `MUSIC_PROXY` | 空 | 代理，如 `http://127.0.0.1:7890` |
| `MUSIC_COOKIES_FILE` | 空 | Netscape 格式 cookies，用于登录态/年龄限制内容；相对路径按项目根解析 |
| `MUSIC_COOKIES_FROM_BROWSER` | 空 | `浏览器[:profile]`，如 `chrome:Profile 12`，让 yt-dlp 直接读浏览器 cookie 库（需关闭 Chrome） |
| `MUSIC_YTDLP_EXTRACTOR_ARGS` | 空 | JSON，透传 yt-dlp `extractor_args`，见下方「访问受限」 |
| `MUSIC_EXTRA_ALLOWED_HOSTS` | 空 | 逗号分隔的额外域名白名单 |
| `MUSIC_JOB_WORKERS` / `MUSIC_JOB_TTL_SEC` | `4` / `3600` | 后台任务线程数与结果保留时间 |

## 接口一览

> 📘 **给第三方调用方**：完整的独立接入文档见 [`docs/API.md`](docs/API.md)（认证、参数表、响应示例、错误码、curl / Python / JavaScript 示例），机器可读规格见 [`docs/openapi.json`](docs/openapi.json)，可直接导入 Postman / Apifox。下方为项目内快速索引。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | 演示页（可关闭） |
| `GET` | `/api/health` | 依赖状态、yt-dlp 版本、缓存概况 |
| `GET` | `/api/search?q=&limit=` | 搜索视频列表（不下载） |
| `GET` | `/api/resolve?url=&q=&video_id=` | 解析单个视频元数据（不下载） |
| `GET` | `/api/lyrics?video_id=&lang=` | 同步歌词（YouTube 字幕优先，LRCLIB 兜底，带时间轴） |
| `GET` | `/api/music?url=&q=&video_id=&bitrate=&download=` | **主接口**：返回音频文件 |
| `POST` | `/api/jobs` | 创建后台下载任务，body: `{url,q,video_id,bitrate}` |
| `GET` | `/api/jobs` / `/api/jobs/{id}` | 任务列表 / 状态与进度 |
| `GET` | `/api/jobs/{id}/download` | 下载任务产出的音频 |
| `GET` | `/api/cache` | 缓存统计与条目列表 |
| `GET` | `/api/cache/lookup?video_id=&bitrate=` | 纯本地查询是否已缓存（不联网） |
| `DELETE` | `/api/cache?video_id=&key=` | 删除指定缓存；不带参数则清空 |

`/api/music` 返回音频二进制，同时带这些响应头：`X-Cache: HIT|MISS`、`X-Video-Id`、`X-Audio-Bitrate`、`X-Audio-Codec`、`X-Audio-Size`、`Content-Disposition`（`inline` 播放 / `download=true` 时为 `attachment`）。

### curl 示例

```bash
# 1. 搜索
curl "http://127.0.0.1:8000/api/search?q=Chopin%20Nocturne&limit=5"

# 2. 关键词直接出音频（首次下载并缓存）
curl -o chopin.mp3 "http://127.0.0.1:8000/api/music?q=Chopin%20Nocturne%20Op9&bitrate=192"

# 3. 用 video_id 再次请求 —— 命中缓存，毫秒级返回，完全不联网
curl -o chopin2.mp3 "http://127.0.0.1:8000/api/music?video_id=dQw4w9WgXcQ&bitrate=192"
curl -I "http://127.0.0.1:8000/api/music?video_id=dQw4w9WgXcQ"   # 看 X-Cache

# 4. 直链 + 附件下载
curl -L -o song.mp3 "http://127.0.0.1:8000/api/music?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ&download=true"

# 5. 异步任务（首次下载慢时推荐）
JOB=$(curl -s -X POST "http://127.0.0.1:8000/api/jobs" \
      -H "Content-Type: application/json" \
      -d '{"q":"Chopin Nocturne","bitrate":192}' | python -c "import sys,json;print(json.load(sys.stdin)['job_id'])")
curl "http://127.0.0.1:8000/api/jobs/$JOB"        # 轮询 status/progress
curl -o out.mp3 "http://127.0.0.1:8000/api/jobs/$JOB/download"

# 6. 缓存管理
curl "http://127.0.0.1:8000/api/cache"
curl -X DELETE "http://127.0.0.1:8000/api/cache?video_id=dQw4w9WgXcQ"
curl -X DELETE "http://127.0.0.1:8000/api/cache"
```

带 API Key 时：`curl -H "X-API-Key: your-key" ...`；`<audio>`/`<a>` 标签无法设置请求头，用 `?api_key=your-key` 查询参数。

## 缓存机制

- 缓存键：`sha1(video_id | bitrate | codec)[:20]`，所以同一首歌换码率会各存一份，`mp3` 与降级后的原生音频也不会互相覆盖。
- 索引文件 `index.json` 用「临时文件 + `os.replace`」原子写入；下载产物先写 `.part` 再原子改名，不会读到半截文件。
- 命中时更新 `last_access`，滑动 TTL 让常听的曲目不会被清掉；超容量时按 `last_access` 最旧的淘汰。
- 启动时自动清理崩溃残留的 `.part` 和不在索引里的孤儿音频文件。
- 并发安全：缓存读写用 `RLock`；不同输入（搜索词 / 直链）解析到同一 `video_id` 时，按 video_id 加锁，只会下载一次。

## 访问受限 / 403 / Sign in to confirm 怎么办

YouTube 近年对自动化客户端限制较多，遇到 `403`、`Sign in to confirm you're not a bot`、`PO Token required` 时，最有效的办法是**给 yt-dlp 带上已登录 Chrome 的 cookies**。

### 用浏览器扩展导出登录态（推荐，全程不重启浏览器）

> **重要**：Chrome 127+ 的 App-Bound Encryption（v20）把登录 cookie 绑定到**原始 profile 路径**。任何"重启 Chrome / 复制 profile / 目录联接 / 用调试端口短路径启动"的自动提取，都会让 Chrome 判定 profile 环境变化并**丢弃 v20 登录 cookie**——表现就是账号被退出，且导出的文件里只剩访客态 cookie。本项目早期那些自动导出脚本在 Chrome 158 上实测会触发这个问题，**新版 Chrome 请勿使用**。

让**正在运行的浏览器内部**导出明文 cookie，是唯一稳妥的方式：

1. 在 Chrome 打开 `https://www.youtube.com`，确认账号已登录。
2. 点 **Cookie-Editor**（或 [Get cookies.txt LOCALLY](https://chromewebstore.google.com/)）图标 → **Export** → 选 **JSON**（或 Netscape）→ 把内容复制到剪贴板。
3. 把内容保存为 `data/_clipboard_raw.txt`，然后执行：

```bash
python tools/import_cookie_editor.py --from-file data/_clipboard_raw.txt --verify
```

脚本只保留 YouTube / Google 相关 cookie、转成 Netscape 格式写到 `data/cookies.txt`，并真实解析一个 YouTube 视频验证。看到标题/时长即表示登录态有效；不打印任何 cookie 值。

- 直接导出成 Netscape 文本的话，也可以自己存成 `data/cookies.txt`，跳过转换。
- 在项目根目录 `.env` 设置 `MUSIC_COOKIES_FILE=./data/cookies.txt`，服务启动时会自动加载（无需手动 export 环境变量）。
- cookies 过期后重复 1–3 步即可，**全程不需要关闭或重启 Chrome**。
- `data/_clipboard_raw.txt`、`data/cookies.txt`、`data/_profile_backup/` 都已加入 `.gitignore`。

> ⚠️ `tools/export_chrome_cookies*.py` 是早期自动提取方案，会重启 Chrome；仅适用于 Chrome 127 之前的版本，新版 Chrome 上使用会导致登录态丢失。保留仅作兼容参考。

> 安全提醒：`data/cookies.txt` 是明文登录凭据，等价于账号登录状态。不要提交到版本库、不要分享、不要放进 Docker 镜像；仅本机使用，用完可删除。

### Cookie 过期后的安全刷新（不会清登录态）

YouTube 的 `__Secure-1PSIDTS` 等 cookie 有时效，导出的 `cookies.txt` 过一段时间会失效，需要重新导出。刷新流程：

```bash
python tools/refresh_chrome.py
```

脚本会：优雅关闭 Chrome → 备份并**只读诊断** cookie 库（打印登录态 cookie 名，不打印值）→ 用**完全原始的启动方式**重开 Chrome 并打开 `youtube.com`，让 Chrome 自己续期短效 cookie。它**不传 `--user-data-dir`**，也不传调试端口、不加载扩展，因此登录态 100% 保留（实测重启前后 14 种登录 cookie 齐全）。

刷新后在 youtube.com 页面重新用 Cookie-Editor 导出并复制，再跑一次：

```bash
python tools/import_cookie_editor.py --from-file data/_clipboard_raw.txt --verify
```

> ⚠️ **不要用 `tools/export_chrome_cookies*.py` 来刷新**：那些脚本会重启 Chrome 并传路径变体（短路径/联接/复制 profile），在 Chrome 127+ 上会让 Chrome 丢弃 v20 登录 cookie，导致账号被退出。

### YouTube 2026 的 JS challenge（必须满足）

有登录 cookies 之后，YouTube 还会对 web 客户端强制 JS challenge（n / signature）。缺 JS 运行时会表现为 `Requested format is not available`、`Only images are available for download`、`Signature solving failed`。项目已内置处理，无需手工加参数：

- `requirements.txt` 含 `yt-dlp-ejs>=0.8.0`；本机需有 **Node.js**（或 Deno），服务会自动启用 `js_runtimes={'node': {}}`。
- 服务启动时自动清除会破坏 solver 的 `NODE_OPTIONS` 注入（沙箱 shim 会让 node 连自己的脚本都读不了，`ERR_ACCESS_DENIED`）。需要保留时设 `MUSIC_KEEP_NODE_OPTIONS=1`。
- **默认直连**：未设置 `MUSIC_PROXY` 时会显式禁用环境代理。很多代理出口被 YouTube 标记，会返回 `The page needs to be reloaded` 或空格式列表；需要代理请显式设置 `MUSIC_PROXY`。
- 默认的 python 后端已完整支持；系统 CLI 若是旧版（无 `--js-runtimes`）会自动跳过该参数，建议升级 CLI 或保持 `MUSIC_YTDLP_BACKEND=auto`。

实测可用组合：登录 cookies + `yt-dlp 2026.10.26` + `yt-dlp-ejs 0.8.0` + `Node 22` + **Cloudflare WARP SOCKS5 代理** → 真实解析并下载成功（192 kbps MP3 / 285 秒）。

### 其它手段

1. **换出口（最重要）**：`MUSIC_PROXY=socks5://127.0.0.1:40000`。部分云厂商（如 Oracle Cloud）的出口 IP 段被 YouTube 拉黑，需要用免费 Cloudflare WARP 换成 Cloudflare 出口，详见下方专门章节。
2. **让 yt-dlp 直接读浏览器**（不导出文件）：`MUSIC_COOKIES_FROM_BROWSER=chrome:Profile 12`。要求 Chrome 已完全退出，且 Chrome 127+ 的 v20 App-Bound 加密 yt-dlp 解不开，新版 Chrome 基本无效；可与 `MUSIC_COOKIES_FILE` 同时配置，yt-dlp 会合并两者。
3. **PO Token / player_client**：安装 `bgutil-ytdlp-pot-provider` 之类的插件后，用高级参数透传。注意 `player_client` / `player_skip` 是**列表**，传字符串不会生效：
   ```env
   MUSIC_YTDLP_EXTRACTOR_ARGS={"youtube":{"player_client":["tv_embedded"],"player_skip":["webpage","configs"]}}
   ```
4. **升级 yt-dlp**：`python -m pip install -U yt-dlp`，YouTube 接口变动很频繁，旧版本最容易失效。

`/api/health` 的 `download.cookies` 会显示当前生效的 cookie 来源（文件路径 / 是否存在 / 浏览器规格），排查问题时先看它。

### 重要：cookie 文件不能被 yt-dlp 回写

yt-dlp 的 `YoutubeDL.close()` 只要设置了 `cookiefile` 就会把 cookiejar 回写到该文件，而回写结果会丢掉 Cookie-Editor 导出的 HttpOnly 登录态（`SID` / `HSID` / `SSID` / `SAPISID` / `LOGIN_INFO` / `__Secure-1PSID` ...）。表现是：刚导入时正常，跑过一次请求后所有视频开始报 `Sign in to confirm you're not a bot`，且只有极少数视频能解析。

服务端已从 `MUSIC_COOKIES_FILE` 每次生成**临时副本**交给 yt-dlp，主凭证文件保持只读；`tools/import_cookie_editor.py --verify` 也改为只验证临时副本。不要再把 `--cookies` 直接指向主凭证文件。

### `Sign in to confirm you're not a bot` 排障 → 用免费 WARP 换出口

实测结论（2026-11）：该报错**与登录态无关**。带完整登录 cookie、不带 cookie、走环境代理、合并访客/rollout cookie（`VISITOR_INFO1_LIVE` / `__Secure-ROLLOUT_TOKEN`）、6 组 `player_client`、以及 embed / nocookie / music / m.youtube 入口，结果完全一致；同一时间只有少数视频（如 `dQw4w9WgXcQ`）能正常解析。

**根因**：部分云厂商（如 `AS31898 Oracle Corporation`）的出口 IP 段被 YouTube 拉黑。若环境里注入的 `HTTP_PROXY` 出口仍是同一个被限制的 IP，则「走代理」等于没换出口。cookie、Chrome 登录态、yt-dlp 版本都不是原因，**不需要重新登录或重启 Chrome**。

**解决：官方 Cloudflare WARP 免费 SOCKS5 代理（Linux / Windows 均可用，已验证通过）**

`warp-cli` 的 **proxy 模式**只在本机起一个 SOCKS5 代理，把 yt-dlp 的出口换成 Cloudflare `AS13335`，**不接管全局路由**（SSH / 远程桌面不会断）。

#### Windows

1. 安装官方客户端：<https://1111-releases.cloudflareclient.com/windows/Cloudflare_WARP_Release-x64.msi>
   （静默安装：`msiexec /i Cloudflare_WARP.msi /quiet /norestart`）。
2. 注册免费账户并切换模式（管理员 PowerShell）：
   ```powershell
   $cli = "C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe"
   & $cli --accept-tos registration new
   & $cli mode proxy          # 建立隧道仅供 SOCKS5 使用，不改全局路由
   & $cli proxy port 40000
   & $cli connect
   & $cli status             # Connected / healthy
   ```
   项目内提供 `tools/start_warp_proxy.ps1` 一键检查 / 恢复上述状态。

#### Linux（Debian / Ubuntu）

1. 安装官方客户端（或直接跑一键脚本 `sudo bash tools/start_warp_proxy.sh`，它会自动完成安装、注册与模式切换）：
   ```bash
   sudo install -d /usr/share/keyrings
   curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
     | sudo gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
   echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" \
     | sudo tee /etc/apt/sources.list.d/cloudflare-client.list
   sudo apt update && sudo apt install -y cloudflare-warp
   ```
2. 注册并切换（`warp-svc` 是系统服务，开机自启）：
   ```bash
   sudo systemctl enable --now warp-svc
   sudo warp-cli --accept-tos registration new
   sudo warp-cli mode proxy
   sudo warp-cli proxy port 40000
   sudo warp-cli connect
   warp-cli status             # Connected / healthy
   ```
   旧版 `warp-cli` 语法为 `register` / `set-mode` / `set-proxy-port`，一键脚本已兼容两种。RHEL/Fedora、Arch 请按 [Cloudflare 官方仓库](https://pkg.cloudflareclient.com/) 说明安装客户端，后续命令相同。若 API 也用 systemd 托管，可在 unit 里加 `After=warp-svc.service` / `Wants=warp-svc.service`（模板已给出注释）。

#### 通用步骤（两个平台一样）

1. 在 `.env` 配置并重启 API 服务：
   ```env
   MUSIC_PROXY=socks5://127.0.0.1:40000
   ```
   yt-dlp 走 SOCKS5 需要 `PySocks`：`python -m pip install PySocks`（Linux venv 用 `.venv/bin/python -m pip install PySocks`）。
2. 验证出口已切换：
   ```bash
   curl -s --noproxy '*' https://api.ipify.org                          # 直连：本机原出口 IP
   curl -s --socks5-hostname 127.0.0.1:40000 https://ipinfo.io/json     # WARP：Cloudflare IP
   ```

实测：出口从云厂商 IP（被 YouTube 限制）变为 Cloudflare `AS13335` 出口；`CtRQVrhg1HY`（Beyond《真的爱你》MV）从被拦变为正常返回 **43 个格式**，经 `/api/music` 下载出 192 kbps / 285 秒 MP3，二次请求 `X-Cache: HIT`。

> 备注：开源 userspace 实现 `warp-plus` 在部分环境隧道握手成功但内部连通性测试始终失败（`context deadline exceeded`），故采用官方客户端。若 WARP 出口日后也被标记，再考虑 PO Token provider 或其它地区代理。`warp-cli disconnect` 可随时关闭隧道（不影响全局网络）。

## 远程访问（Nginx 反向代理）

Linux 与 Windows 都可用 Nginx 把自有域名反代到 `127.0.0.1:8765`，**无需把 8765 暴露到公网，也不用改云安全组**。（下方域名、路径均为示例，部署时请替换为你自己的值。）

| 项 | 值 |
|---|---|
| 对外地址 | `https://mp3.example.com`（示例域名；HTTP 80 也可用） |
| 上游 | `http://127.0.0.1:8765`（`MUSIC_HOST=127.0.0.1`） |
| TLS | Let's Encrypt；Linux 用 certbot，Windows 可用 lego（见下） |
| 认证 | `MUSIC_API_KEY`，见下方 |

- **Linux**：复制 [`deploy/nginx.conf.example`](deploy/nginx.conf.example) 到 `/etc/nginx/sites-available/music-mp3-api`，替换域名后软链到 `sites-enabled`，`sudo nginx -t` 通过再 `sudo systemctl reload nginx`。证书一条命令：`sudo certbot --nginx -d <你的域名>`；certbot 自带 systemd timer 自动续期（`systemctl list-timers | grep certbot` 可确认），需要自定义 reload 钩子时用模板 `tools/renew_cert.example.sh`。
- **Windows**：反代配置写在独立的 `mp3_proxy.conf`，由 `nginx.conf` 里 `include mp3_proxy.conf;` 引入，按 `server_name` 精确匹配，可与同机其它站点隔离。证书续期模板为 `tools/renew_cert.example.cmd`（复制填写后另存，实际脚本已被 `.gitignore` 排除）；若 nginx 以系统服务运行，普通 `nginx -s reload` 可能被拒绝，改为重启对应服务。
- 音频场景做了 `proxy_buffering off` 与 600s 读写超时，Range / 206、大文件下载可正常透传；同时透传 `X-Forwarded-*` 与 `Range` / `If-Range` 头。
- 服务仍只监听本机 `127.0.0.1:8765`，本机防火墙（Windows Defender 防火墙 / `ufw` / `firewalld`）与云安全组都**不需要**为 8765 放行；公网入口只有既有的 80/443。

### API Key（对外必须启用）

密钥写在 `.env` 的 `MUSIC_API_KEY`（该文件已 gitignore，**切勿入库或提交**）。调用时二选一：

```bash
# 请求头（推荐，服务端调用）
curl -H "X-API-Key: <你的 API Key>" "https://mp3.example.com/api/search?q=Chopin&limit=3"

# Query 参数（<audio> 标签、浏览器直链等无法自定义请求头的场景）
curl -o song.mp3 "https://mp3.example.com/api/music?video_id=dQw4w9WgXcQ&bitrate=128&api_key=<你的 API Key>"
```

`GET /api/health` 无需认证，方便探活；其余 `/api/*` 与 `/downloads/*` 都需要密钥，缺失返回 `401`。

演示页支持直达链接自动填入并记住密钥（填入后会立即从地址栏抹掉，避免残留）：

```
https://mp3.example.com/?key=<你的 API Key>
```

## 生产部署建议

- **不要裸奔公网**：设置 `MUSIC_API_KEY`，并在反向代理层做认证、限流与请求体/超时限制。
- **绑定内网**：默认 `MUSIC_HOST=127.0.0.1`；确需外网时再改，并收紧 `MUSIC_CORS_ORIGINS`。
- **磁盘**：缓存目录单独挂载，合理设置 `MUSIC_MAX_CACHE_MB`；音频文件可能增长很快。
- **时长/大小限制**：`MUSIC_MAX_DURATION_SEC` + `MUSIC_MAX_FILE_MB` 能防止误下超长直播撑爆磁盘。
- **超时**：同步 `/api/music` 首次下载可能几十秒，前端建议使用 `/api/jobs` 异步模式。
- **日志**：uvicorn 访问日志 + `music_api` logger；建议接日志采集。
- **合规**：再次确认上方合规提示，勿作为公开音乐分发服务。

## 开机自启服务（可选）

把 API 注册成系统服务后，它与终端会话完全解耦：关闭终端、注销登录都不影响，重启机器自动恢复。Linux 用 **systemd**，Windows 用 **nssm**；模板与一键脚本都在仓库里。

### Linux（systemd，推荐）

| 项 | 值 |
|---|---|
| 服务名 | `music-mp3-api` |
| 运行账户 | 专用低权限用户 `musicapi`（非 root） |
| 工作目录 | `/opt/music-mp3-api`（`.env`、`data/cache`、`data/cookies.txt` 均按此解析） |
| 应用 | venv 内的 Python + `-u run.py` |
| 日志 | `journald`（`journalctl -u music-mp3-api -f`） |
| 崩溃恢复 | `Restart=always` + `RestartSec=5` |

**一键部署**（Debian/Ubuntu、RHEL/Fedora，需 root）：

```bash
sudo bash deploy/install-linux.sh                    # 默认装到 /opt/music-mp3-api
# 自定义目录： sudo bash deploy/install-linux.sh /srv/music-api
```

脚本会装系统依赖、建专用用户与 venv、复制代码、注册并启动服务，最后做一次 `/api/health` 探活。想自己控制每一步，就手动编辑 [`deploy/music-mp3-api.service`](deploy/music-mp3-api.service)（改安装目录与运行用户）后执行：

```bash
sudo cp deploy/music-mp3-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now music-mp3-api
```

常用命令：

```bash
systemctl status music-mp3-api              # 状态
sudo systemctl restart music-mp3-api     # 改完 .env / Python 代码后必须重启
sudo journalctl -u music-mp3-api -f         # 实时日志
sudo systemctl disable --now music-mp3-api # 停用
```

公网链路同样全部开机自启，无需手工干预：

- `warp-svc`（WARP 客户端，Auto）→ `127.0.0.1:40000` SOCKS5 出口
- `nginx`（Auto）→ 监听 80/443，反代到 `127.0.0.1:8765`
- `music-mp3-api`（本服务，Auto）→ `127.0.0.1:8765`

> 没有 systemd 的极简容器请改用 Docker；Nginx 配置见 [`deploy/nginx.conf.example`](deploy/nginx.conf.example)，证书用 `certbot --nginx`。

### Windows（nssm）

| 项 | 值 |
| --- | --- |
| 服务名 / 显示名 | `MusicMp3Api` / `Music MP3 API` |
| 运行账户 / 启动类型 | `LocalSystem` / `Automatic`（开机自启） |
| 工作目录 | 项目根目录（`.env`、`data/cache`、`data/cookies.txt` 均按此解析） |
| 应用 | 本机 Python 3.10+ + `-u run.py` |
| 日志 | `data\service.log`，10 MB 自动轮转 |
| 崩溃恢复 | 进程退出后 5 秒自动重启（`AppExit Default=Restart`） |

公网链路同样全部开机自启：

- `CloudflareWARP`（系统服务，Auto）→ `127.0.0.1:40000` SOCKS5 出口
- `XiaoZhiWeb` 一类的 nginx 服务（Auto）→ 监听 80/443，反代到 `127.0.0.1:8765`
- `MusicMp3Api`（本服务，Auto）→ `127.0.0.1:8765`

管理员 PowerShell 常用命令：

```powershell
Get-Service MusicMp3Api, XiaoZhiWeb        # 查看状态
Restart-Service MusicMp3Api               # 改完 .env / Python 代码后必须重启才生效
Get-Content .\data\service.log -Tail 50   # 查看日志
```

> **改完 `.env` 必须重启服务**：配置在进程启动时读取并缓存，不会热加载（Linux `systemctl restart`，Windows `Restart-Service`）。
>
> **日志含密钥**：uvicorn 访问日志会记录完整请求 URL（含 `api_key` query 参数）。`*.log` 已在 `.gitignore` 中，Linux 的 journald 导出文件同样请勿外传。
>
> **路径依赖**：服务指向 Python 解释器的绝对路径（Linux `/opt/music-mp3-api/.venv/bin/python`，Windows `<PYTHON_EXE>`）。解释器升级或目录变化后，改 unit 的 `ExecStart`，或用 `nssm set MusicMp3Api Application "<新路径>"` 更新。
>
> 卸载（Windows）：`nssm remove MusicMp3Api confirm`。

## 测试

```bash
python -m pytest -q
```

测试全部 mock 掉网络下载，覆盖 URL 白名单与注入防护、缓存读写/淘汰/TTL/持久化、接口状态码、Range 请求、API Key、异步任务流程，可离线运行。

## 目录结构

```
music-mp3-api/
├── app/
│   ├── config.py        # 环境变量配置
│   ├── security.py      # 输入归一化、域名白名单、文件名清洗
│   ├── cache.py         # 本地缓存（LRU / TTL / 原子索引）
│   ├── downloader.py    # yt-dlp 双后端 + ffmpeg 转码 + 去重编排
│   ├── jobs.py          # 后台任务队列
│   ├── lyrics.py        # YouTube 字幕 -> 同步歌词解析与缓存
│   └── main.py          # FastAPI 接口层
├── static/index.html    # 演示页（单文件、无外部依赖）
├── docs/
│   ├── API.md           # 第三方接入文档（可独立交付）
│   ├── DOCKER.md        # 镜像地址、运行、卷权限与 CI 发布说明
│   └── openapi.json     # OpenAPI 3.1 规格快照，可导入 Postman / Apifox
├── deploy/                           # Linux 部署模板（详见 deploy/README.md）
│   ├── install-linux.sh              # 一键装依赖 + venv + systemd 服务
│   ├── music-mp3-api.service          # systemd 单元（开机自启 / 崩溃重启）
│   ├── nginx.conf.example            # Nginx 反代 + certbot 示例
│   └── README.md
├── tools/
│   ├── start_warp_proxy.ps1           # Windows：一键启用/恢复 WARP SOCKS5
│   ├── start_warp_proxy.sh            # Linux：一键启用/恢复 WARP SOCKS5
│   ├── import_cookie_editor.py        # 推荐：导入 Cookie-Editor 导出的 cookies
│   ├── refresh_chrome.py              # 安全重启 Chrome 打开 YouTube 刷新短效 cookie
│   ├── export_chrome_cookies.py       # 旧版 Chrome（<127）自动导出，新版勿用
│   ├── export_chrome_cookies_extension.py
│   ├── export_chrome_cookies_hybrid.py
│   ├── renew_cert.example.cmd         # Windows（lego）证书续期模板
│   └── renew_cert.example.sh          # Linux（certbot）证书续期模板
├── tests/               # 离线单元测试（75 项，全部 mock 网络）
├── .github/workflows/ci.yml          # GitHub Actions：Ubuntu/Windows × py3.10/3.12 跑 pytest
├── .github/workflows/docker.yml      # GitHub Actions：镜像冒烟 + amd64/arm64 发布
├── data/cache/          # 音频缓存（已 gitignore）
├── data/lyrics/         # 字幕歌词缓存（已 gitignore）
├── data/cookies.txt     # 浏览器登录态（运行时生成，已 gitignore）
├── requirements.txt
├── .env                 # 本机配置（已 gitignore，自动加载）
├── .env.example
├── run.py / start.bat
├── Dockerfile / .dockerignore
├── docker-compose.yml               # 一键启动官方镜像
├── LICENSE              # MIT
├── NOTICE.md            # 合规声明与第三方组件说明
├── .gitattributes
└── README.md / docs/
```

## 开源与安全约定

- **绝不入库**：`.env`（含真实 `MUSIC_API_KEY`）、`data/cookies*.txt`、Cookie-Editor 导出的中间文件、Chrome profile 备份、`*.log`（访问日志含完整请求 URL 与 `api_key`）、缓存音频、`downloads/*.mp3`、本机运维脚本。`.gitignore` 已逐项覆盖。
- **文档脱敏**：README / `docs/API.md` 中的域名、邮箱、IP、绝对路径均为示例占位符，请替换为你自己的部署值。
- **镜像安全**：`.dockerignore` 是白名单，只放构建必需文件；`.env`、`data/cookies*.txt`、`*.log`、缓存音频不会进入构建上下文或镜像层。GHCR 由 CI 用 `GITHUB_TOKEN` 推送，Docker Hub 凭据仅存 GitHub Actions secrets，不入库。
- **合规**：本项目仅作技术演示与个人自用，请勿用于公开分发、商业服务或侵犯版权的内容，详见顶部合规提示。
