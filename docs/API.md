# Music MP3 API · 第三方接入文档

> **版本** 1.2.0 ｜ **协议** HTTP/1.1 ｜ **数据格式** JSON（`UTF-8`）/ 音频二进制 ｜ **更新** 2026-11-20
>
> Music MP3 API 提供「搜索 → 解析 → 提取 MP3 → 本地缓存」的一站式音频接口。除 `/api/music` 与任务产物下载返回二进制外，其余接口均返回 JSON。

---

## ⚠️ 0. 合规声明（调用前必读）

- 调用方**必须确保对所请求的内容拥有版权或已获得合法授权**，且仅用于个人学习 / 自用。
- **不得**将本接口用于公开分发、商业服务、批量抓取，或绕过内容提供方的访问控制与地域限制。
- 服务运营方可随时通过 API Key、限流、域名白名单等手段限制或停止访问；调用方应自行承担合规责任。

---

## 1. 接入信息

| 项 | 说明 |
|---|---|
| Base URL | 由部署方提供。本文示例统一用 `https://mp3.example.com`（占位域名，请替换）；服务本机默认 `http://127.0.0.1:8765` |
| 交互式文档 | `GET {BASE_URL}/docs`（Swagger UI，可直接 Try it out） |
| 机器可读规格 | `GET {BASE_URL}/openapi.json`；仓库内静态快照 `docs/openapi.json` |
| 健康检查 | `GET {BASE_URL}/api/health`（**无需认证**，接入前先调用） |
| 演示页 | `GET {BASE_URL}/`（非接口，仅供人工测试） |

> 生产部署通常经 Nginx 反向代理对外提供（域名 → `127.0.0.1:8765`），服务本体不监听公网，调用方只需访问部署方给出的域名。演示页可用 `GET {BASE_URL}/?key=<your-key>` 直达并自动填入密钥。

下文示例统一使用变量 `BASE`：

```bash
BASE="https://mp3.example.com"       # 示例地址，请替换为部署方提供的 Base URL
```

---

## 2. 认证

服务端设置 `MUSIC_API_KEY` 后启用认证；未设置时所有接口开放（可从 `GET /api/health` 的 `config.api_key_required` 字段确认）。是否启用由部署方决定，密钥由部署方单独提供，请妥善保管，不要写入前端仓库或公开页面。

除 `GET /api/health`（探活）与文档页 `/docs`、`/openapi.json` 外，所有 `/api/*` 以及 `/downloads/*` 静态下载都需要密钥。

支持**两种等价方式**：

| 方式 | 用法 | 适用场景 |
|---|---|---|
| 请求头（推荐） | `X-API-Key: <your-key>` | 服务端调用、Ajax/fetch |
| Query 参数 | `?api_key=<your-key>` | `<audio src>`、`<a href>` 等无法自定义请求头的场景 |

```bash
curl -H "X-API-Key: $API_KEY" "$BASE/api/search?q=Chopin&limit=3"
```

认证失败：

```http
HTTP/1.1 401 Unauthorized
{"detail":"无效或缺失的 API Key"}
```

> 安全建议：优先用请求头传递；使用 query 参数时注意它会出现在浏览器历史、日志与 Referer 中。生产环境请在 HTTPS 下调用。

---

## 3. 通用约定

### 3.1 定位视频：三选一

绝大多数接口通过以下**互斥参数**指定目标，**必须提供其中一个**：

| 参数 | 类型 | 说明 |
|---|---|---|
| `url` | string | YouTube 链接，仅 `https`、仅白名单域名、仅 443 端口 |
| `video_id` | string | 11 位视频 ID，字符集 `[A-Za-z0-9_-]`，例如 `dQw4w9WgXcQ` |
| `q` | string | 搜索关键词，**自动取搜索结果第一条**；最长 200 字符，会做 NFKC / 空白归一化 |

优先级：**`url` > `video_id` > `q`**。三者都不传返回 `422`。

> 建议流程：先 `/api/search` 让用户选，再用选中的 `video_id` 请求音频。直接用 `q` 虽然方便，但结果由YouTube 排序决定，可能不是目标曲目。

### 3.2 码率 `bitrate`

| 允许值 | `64` / `96` / `128` / `192` / `256` / `320`（单位 kbps） |
|---|---|
| 默认值 | `192`（以 `GET /api/health` 的 `config.default_bitrate` 为准） |
| 非法值 | 返回 `422` |

**同一视频的不同码率各自独立缓存**，切换码率会触发一次新的下载与转码。

### 3.3 响应与响应头

JSON 接口统一返回对象；音频接口返回二进制流并附带以下响应头：

| 响应头 | 示例 | 说明 |
|---|---|---|
| `Content-Type` | `audio/mpeg` | 默认 MP3 |
| `Content-Disposition` | `inline; filename*=utf-8''xxx.mp3` | `download=true` 时为 `attachment` |
| `X-Cache` | `MISS` / `HIT` | **本次是否命中本地缓存**，可用于统计与调试 |
| `X-Video-Id` | `dQw4w9WgXcQ` | 实际视频 ID（`q` 输入时尤其有用） |
| `X-Audio-Bitrate` | `128` | 实际码率 kbps |
| `X-Audio-Codec` | `mp3` | 编码 |
| `X-Audio-Size` | `3409580` | 文件字节数 |
| `Cache-Control` | `public, max-age=86400` | 客户端可缓存 1 天 |
| `Accept-Ranges` | `bytes` | 支持断点 / 拖动播放 |

**CORS**：默认允许所有来源，并已暴露上述 `X-*` 头，浏览器端 JavaScript 可直接读取 `X-Cache`。跨域白名单由部署方通过 `MUSIC_CORS_ORIGINS` 配置。

### 3.4 错误响应

业务错误：

```json
{"detail": "必须提供 url、video_id 或 q 之一"}
```

框架参数校验错误（`detail` 为数组）：

```json
{"detail": [{"type": "greater_than_equal", "loc": ["query", "limit"], "msg": "Input should be greater than or equal to 1"}]}
```

| 状态码 | 含义 | 常见原因 |
|---|---|---|
| `200` | 成功 | — |
| `202` | 已接受 | 异步任务创建成功 |
| `206` | 部分内容 | 带 `Range` 请求音频，正常播放行为 |
| `401` | 未授权 | API Key 缺失 / 错误 |
| `404` | 不存在 | 任务 ID 不存在或已过期；缓存文件丢失 |
| `409` | 冲突 | 任务尚未 `done` 就请求产物下载 |
| `413` | 过大 | 视频超过最大时长 / 产物超过最大文件大小 |
| `422` | 参数不合法 | 缺少三选一参数、非法 `bitrate`、非白名单域名、非法 `video_id` |
| `500` | 服务内部错误 | 未预期异常 |
| `502` | 上游失败 | yt-dlp 解析 / 下载失败（视频不可用、地区限制、内容风控等） |
| `503` | 依赖缺失 | 服务端缺少 ffmpeg / yt-dlp 等依赖 |

### 3.5 服务端限制（默认值，可通过 `GET /api/health` 核对）

| 限制 | 默认值 |
|---|---|
| 单次最大时长 | 900 秒 |
| 最大产物文件 | 60 MB |
| 最大并发下载 | 2 |
| `/api/search` 单页条数 | 默认 8，最大 20 |
| 异步任务保留时长 | 3600 秒（过期后 `404`） |
| 缓存容量 / TTL | 由部署方在 `.env` 配置；软件默认 2048 MB / 30 天（滑动过期，LRU 淘汰），可设为 `MUSIC_MAX_CACHE_MB=1024000` + `MUSIC_CACHE_TTL_DAYS=0`（1000 GB / 永不过期）。关闭 TTL 后仍受容量上限约束，超过时按 LRU 淘汰最旧文件 |
| 歌词缓存 TTL | 7 天（独立于音频缓存，可通过 `MUSIC_LYRICS_TTL_DAYS` 调整） |
| 外部歌词兜底 | 默认开启，YouTube 无字幕时请求 LRCLIB；可用 `MUSIC_LYRICS_EXTERNAL=false` 关闭 |

> **超时设置**：首次请求（缓存 `MISS`）需完成下载 + 转码，通常耗时十几秒到数分钟。客户端建议给同步 `/api/music` 设置 **≥ 120 秒**超时，或改用 `/api/jobs` 异步模式。

---

## 4. 接口参考

### 4.1 `GET /api/health` — 服务健康与配置

无需认证、无参数。用于探活、查看依赖状态与当前生效配置。

```bash
curl "$BASE/api/health"
```

<details>
<summary>响应示例（字段已裁剪）</summary>

```json
{
  "status": "ok",
  "service": "Music MP3 API",
  "version": "1.0.0",
  "download": {
    "backend": "python",
    "ytdlp_version": "2026.10.26",
    "ffmpeg_available": true,
    "codec": "mp3",
    "bitrate": 192,
    "max_duration_sec": 900,
    "max_concurrent_downloads": 2,
    "cookies": { "file_exists": true, "from_browser": null }
  },
  "cache": { "count": 3, "total_mb": 12.21, "limit_mb": 1024000.0, "ttl_seconds": 0 },
  "config": {
    "api_key_required": true,
    "default_bitrate": 192,
    "allowed_bitrates": [64, 96, 128, 192, 256, 320],
    "max_duration_sec": 900,
    "search_max_limit": 20,
    "lyrics_ttl_days": 7,
    "lyrics_external": true
  },
  "jobs": { "tracked": 1 }
}
```
</details>

关键字段：`download.ffmpeg_available`、`download.ytdlp_version`、`config.api_key_required`、`config.allowed_bitrates`。

> 若部署方启用了认证（`api_key_required=true`）：除 `GET /api/health`（探活，无需密钥）外，`/api/*` 与 `/downloads/*` 都必须带密钥，否则返回 `401`。上面示例里的 `cache` / `jobs` 数值仅为示意，实际以响应为准。

---

### 4.2 `GET /api/search` — 搜索（不下载）

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `q` | string | 是 | — | 搜索关键词 |
| `limit` | int | 否 | `8` | 返回条数，`1..20`（超出按上限截断） |

```bash
curl -H "X-API-Key: $API_KEY" "$BASE/api/search?q=Chopin%20Nocturne&limit=2"
```

```json
{
  "query": "Chopin Nocturne",
  "count": 2,
  "results": [
    {
      "id": "IVpuTD-2SEo",
      "title": "Chopin: Complete Nocturnes (Luke Faulkner)",
      "channel": "HALIDONMUSIC",
      "duration": 6867,
      "thumbnail": "https://i.ytimg.com/vi/IVpuTD-2SEo/hq720.jpg?...",
      "webpage_url": "https://www.youtube.com/watch?v=IVpuTD-2SEo",
      "description": "Buy the album on the Official Halidon Music Store: ...",
      "view_count": 17398327,
      "upload_date": null
    }
  ]
}
```

`results[]` 字段：`id`（可用于后续所有接口）、`title`、`channel`、`duration`（秒）、`thumbnail`、`webpage_url`、`description`（截断至 300 字符）、`view_count`、`upload_date`（`YYYYMMDD` 或 `null`）。部分字段可能为 `null`。

---

### 4.3 `GET /api/resolve` — 解析元数据（不下载）

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `url` / `q` / `video_id` | string | 三选一 | 见 3.1 |
| `bitrate` | int | 否 | 仅用于判断该码率缓存是否命中 |

```bash
curl "$BASE/api/resolve?video_id=dQw4w9WgXcQ&bitrate=128"
```

```json
{
  "video": {
    "id": "dQw4w9WgXcQ",
    "title": "Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)",
    "channel": "Rick Astley",
    "duration": 213,
    "thumbnail": "https://i.ytimg.com/vi_webp/dQw4w9WgXcQ/maxresdefault.webp",
    "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "description": "The official video for ...",
    "view_count": 1839024554,
    "upload_date": "20091025"
  },
  "cache": { "bitrate": 128, "codec": "mp3", "hit": true },
  "audio_url": "/api/music?video_id=dQw4w9WgXcQ&bitrate=128"
}
```

`audio_url` 是**相对路径**，拼接 Base URL 即可使用；已启用 API Key 时记得加 `api_key`。`cache.hit=true` 表示下一次请求会秒回，不会联网。

---

### 4.3.1 `GET /api/lyrics` — 获取同步歌词（字幕 / 自动字幕）

返回带时间轴的歌词行，供播放器逐行高亮。服务端优先复用 yt-dlp 的 cookie / 代理配置拉取 YouTube 人工字幕或自动字幕；**视频没有字幕时自动回退到 LRCLIB**，用标题 / 歌手 / 时长匹配同步 LRC 歌词并按 `video_id + 语言` 缓存 7 天。外部歌词可通过 `MUSIC_LYRICS_EXTERNAL=false` 关闭。两边都拿不到时返回 `lines: []` 而不是报错。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `url` / `video_id` | string | 二选一 | — | 见 3.1 |
| `lang` | string | 否 | `auto` | `auto` / `zh-Hans` / `zh-Hant` / `en` / `ja` / `ko`；`auto` 优先中文再英文 |
| `api_key` | string | 鉴权时必填 | — | 也可放 `X-API-Key` 请求头 |

```bash
curl -H "X-API-Key: $API_KEY" "$BASE/api/lyrics?video_id=dQw4w9WgXcQ&lang=auto"
```

```json
{
  "video_id": "dQw4w9WgXcQ",
  "title": "Rick Astley - Never Gonna Give You Up",
  "requested_lang": "auto",
  "language": "en",
  "language_label": "English",
  "source": "manual",
  "available": true,
  "line_count": 61,
  "cache_hit": false,
  "stale": false,
  "lines": [
    { "start": 1.36, "end": 3.04, "text": "[♪♪♪]" },
    { "start": 18.64, "end": 21.88, "text": "♪ We're no strangers to love ♪" }
  ]
}
```

字段说明：`start` / `end` 为秒（浮点），`text` 已去除 VTT 标签与滚动字幕重复片段；`source` 为 `manual`（YouTube 人工字幕）、`auto`（YouTube 自动字幕）或 `lrclib`（第三方 LRC 歌词）。命中外部歌词时还会返回 `external: { provider, track, artist, duration, id }`，`language_label` 为「外部歌词」。`stale=true` 表示刷新失败后返回了旧缓存。前端可在 `audio` 的 `timeupdate` 事件里找到最后一个 `start <= currentTime` 的行并高亮，点击歌词行可设置 `audio.currentTime = start` 跳转。

---

### 4.4 `GET /api/music` — 获取音频 ★ 主接口

首次请求：下载 + 转码 + 写缓存，返回 `X-Cache: MISS`；后续相同 `video_id + bitrate`：`X-Cache: HIT`，完全离线、毫秒级。

| 参数 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `url` / `q` / `video_id` | string | 三选一 | — | 见 3.1 |
| `bitrate` | int | 否 | `192` | 见 3.2 |
| `download` | bool | 否 | `false` | `true` → `Content-Disposition: attachment` |
| `Range`（请求头） | string | 否 | — | 例如 `bytes=0-1023`，返回 `206` |

```bash
# 直接点歌（取搜索第一条）
curl -o song.mp3 "$BASE/api/music?q=Never%20Gonna%20Give%20You%20Up&bitrate=192"

# 已知 video_id + 附件下载
curl -o song.mp3 "$BASE/api/music?video_id=dQw4w9WgXcQ&bitrate=128&download=true"

# 验证 MISS → HIT（只看响应头）
curl -s -D - -o /dev/null "$BASE/api/music?video_id=dQw4w9WgXcQ&bitrate=128" \
  | grep -iE "HTTP/|x-cache|x-audio-bitrate"

# Range / 拖动播放
curl -H "Range: bytes=0-1023" -D - -o /dev/null \
  "$BASE/api/music?video_id=dQw4w9WgXcQ&bitrate=128"
```

**成功响应（`200`）**

```http
HTTP/1.1 200 OK
content-type: audio/mpeg
content-disposition: inline; filename*=utf-8''Rick%20Astley%20-%20....mp3
x-cache: HIT
x-video-id: dQw4w9WgXcQ
x-audio-bitrate: 128
x-audio-codec: mp3
x-audio-size: 3409580
cache-control: public, max-age=86400
accept-ranges: bytes
content-length: 3409580
```

**Range 响应（`206`）**：额外带 `Content-Range: bytes 0-1023/3409580`，body 仅包含请求区间。浏览器 `<audio>` 拖动播放会自动发送 `Range`，无需调用方处理。

> 注意：这是**二进制流**，不要按 JSON 解析。判断成功与否请用 HTTP 状态码 + `Content-Type`。

---

### 4.5 `POST /api/jobs` — 创建后台下载任务

适合首次下载较慢、需要展示进度条的场景。请求体为 JSON。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `url` / `q` / `video_id` | string | 三选一 | 见 3.1 |
| `bitrate` | int | 否 | 见 3.2 |

```bash
curl -X POST -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" \
  -d '{"video_id":"dQw4w9WgXcQ","bitrate":128}' "$BASE/api/jobs"
```

`202 Accepted`：

```json
{
  "job_id": "62abf470a77448bc865b141d99c3460d",
  "status": "queued",
  "progress": 0.0,
  "message": "排队中",
  "created_at": 1795159666.3284934,
  "finished_at": null,
  "params": { "kind": "video_id", "value": "dQw4w9WgXcQ", "video_id": "dQw4w9WgXcQ", "bitrate": 128 },
  "result": null,
  "error": null
}
```

`status` 取值：`queued` → `running` → `done` / `error`；`progress` 为 `0.0..100.0`。

---

### 4.6 `GET /api/jobs` — 任务列表

| 参数 | 类型 | 默认 | 范围 |
|---|---|---|---|
| `limit` | int | `50` | `1..200` |

返回 `{"count": N, "jobs": [ ...任务对象... ]}`，按创建时间倒序。

---

### 4.7 `GET /api/jobs/{job_id}` — 查询任务状态

```bash
curl "$BASE/api/jobs/$JOB_ID"
```

`done` 时 `result` 包含产物信息与取件链接：

```json
{
  "job_id": "62abf470a77448bc865b141d99c3460d",
  "status": "done",
  "progress": 100.0,
  "message": "完成",
  "created_at": 1795159666.3284934,
  "finished_at": 1795159666.3287983,
  "params": { "kind": "video_id", "value": "dQw4w9WgXcQ", "video_id": "dQw4w9WgXcQ", "bitrate": 128 },
  "result": {
    "cache_hit": true,
    "codec": "mp3",
    "bitrate": 128,
    "size": 3409580,
    "ext": ".mp3",
    "key": "9364247aab997569244b",
    "id": "dQw4w9WgXcQ",
    "title": "Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster)",
    "channel": "Rick Astley",
    "duration": 213,
    "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "thumbnail": "https://i.ytimg.com/vi_webp/dQw4w9WgXcQ/maxresdefault.webp",
    "download_url": "/api/jobs/62abf470a77448bc865b141d99c3460d/download",
    "audio_url": "/api/music?video_id=dQw4w9WgXcQ&bitrate=128"
  },
  "error": null
}
```

`error` 状态下 `error` 字段为错误文本，`result` 为 `null`。任务不存在 / 已过期返回 `404`。

> 任务对象里的 `download_url` / `audio_url` 均为**相对路径**。推荐轮询间隔 1–2 秒；任务保留 3600 秒。

---

### 4.8 `GET /api/jobs/{job_id}/download` — 下载任务产物

返回与 `/api/music` 相同的二进制流，但强制 `attachment`。仅 `status=done` 可用；未完成返回 `409`，任务不存在返回 `404`。

```bash
curl -o song.mp3 "$BASE/api/jobs/$JOB_ID/download"
```

---

### 4.9 `GET /api/cache` — 缓存统计与条目

```bash
curl "$BASE/api/cache"
```

```json
{
  "enabled": true,
  "directory": "<服务端缓存目录>",
  "count": 3,
  "total_bytes": 12807396,
  "total_mb": 12.21,
  "limit_mb": 1024000.0,
  "ttl_seconds": 0,
  "entries": [
    {
      "key": "9364247aab997569244b",
      "video_id": "dQw4w9WgXcQ",
      "title": "Rick Astley - ...",
      "channel": "Rick Astley",
      "duration": 213,
      "bitrate": 128,
      "codec": "mp3",
      "ext": ".mp3",
      "size": 3409580,
      "created_at": 1795156728.05849,
      "last_access": 1795159666.3239493,
      "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    }
  ]
}
```

时间戳为 Unix 秒（浮点）。`entries` 按 `last_access` 倒序。

---

### 4.10 `GET /api/cache/lookup` — 探测是否已缓存（纯本地，不联网）

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `video_id` | string | 是 | 11 位视频 ID |
| `bitrate` | int | 否 | 默认码率 |

```bash
curl "$BASE/api/cache/lookup?video_id=dQw4w9WgXcQ&bitrate=128"
```

```json
{
  "hit": true,
  "video_id": "dQw4w9WgXcQ",
  "bitrate": 128,
  "codec": "mp3",
  "entry": { "key": "9364247aab997569244b", "size": 3409580, "...": "..." }
}
```

未命中时 `hit=false`、`entry=null`。这是判断「会不会触发慢速下载」的最快方式，**不产生任何外网请求**。

---

### 4.11 `DELETE /api/cache` — 清理缓存

| 参数 | 类型 | 说明 |
|---|---|---|
| `video_id` | string | 只删除该视频的所有码率缓存 |
| `key` | string | 只删除指定缓存键（来自 `/api/cache` 的 `entries[].key`） |

```bash
curl -X DELETE "$BASE/api/cache?video_id=dQw4w9WgXcQ"
curl -X DELETE "$BASE/api/cache?key=9364247aab997569244b"
```

```json
{"removed": 1, "cache": { "count": 2, "total_mb": 8.97, "...": "..." }}
```

> ⚠️ **两个参数都不传会清空全部缓存**，请谨慎调用。该接口属于管理操作，不建议暴露给普通第三方调用方。

---

### 4.12 `GET /downloads/{filename}` — 静态音频文件（可选）

服务端把 `downloads/` 目录以静态文件方式挂载，供演示页下载手动保存的音频。文件名需 URL 编码。该路由**不属于核心 API**，部署方可关闭；第三方集成建议使用 `/api/music` 或任务产物接口。

---

## 5. 缓存与音频语义

| 概念 | 说明 |
|---|---|
| 缓存键 | `sha1(video_id \| bitrate \| codec)` 的前 20 位，同一视频不同码率互不覆盖 |
| `X-Cache: MISS` | 本次发生了真实下载 + 转码，响应较慢 |
| `X-Cache: HIT` | 直接读本地文件，不联网，通常毫秒级 |
| 范围请求 | 原生支持 `Range`，返回 `206` + `Content-Range`，可拖动播放 / 断点续传 |
| 过期与淘汰 | `MUSIC_CACHE_TTL_DAYS=0` 可关闭 TTL 过期（缓存不按时间清理；软件默认 30 天滑动过期，命中即刷新）+ `MUSIC_MAX_CACHE_MB` LRU 容量淘汰（默认 2048 MB） |
| 并发去重 | 相同缓存键的并发请求会合并为一次下载 |

**典型时序**：`search` → 用户选中 `video_id` → `resolve`（可先看 `cache.hit`）→ `music`。二次点播直接 `music`，`HIT` 秒回。

---

## 6. 集成示例

### 6.1 curl：三步拿到音频

```bash
BASE="https://api.example.com"
KEY="your-api-key"

# 1) 搜索
curl -s -H "X-API-Key: $KEY" "$BASE/api/search?q=Chopin%20Nocturne&limit=3"

# 2) 确认元数据与缓存状态
curl -s -H "X-API-Key: $KEY" "$BASE/api/resolve?video_id=IVpuTD-2SEo&bitrate=192"

# 3) 下载
curl -H "X-API-Key: $KEY" -o song.mp3 \
  "$BASE/api/music?video_id=IVpuTD-2SEo&bitrate=192&download=true"
```

### 6.2 Python（`requests`，含异步任务）

> 依赖：`pip install requests`

```python
import time
import requests

BASE = "https://api.example.com"
HEADERS = {"X-API-Key": "your-api-key"}
TIMEOUT = 180  # 首次 MISS 可能较久

# 同步方式（简单）
r = requests.get(
    f"{BASE}/api/music",
    params={"video_id": "dQw4w9WgXcQ", "bitrate": 192, "download": True},
    headers=HEADERS,
    timeout=TIMEOUT,
)
r.raise_for_status()
print("cache =", r.headers.get("X-Cache"), "bytes =", len(r.content))
open("song.mp3", "wb").write(r.content)

# 异步方式（推荐给前端 / 长音频）
resp = requests.post(
    f"{BASE}/api/jobs",
    json={"video_id": "dQw4w9WgXcQ", "bitrate": 192},
    headers=HEADERS, timeout=30,
)
resp.raise_for_status()
job = resp.json()
job_id = job["job_id"]

while True:
    st = requests.get(f"{BASE}/api/jobs/{job_id}", headers=HEADERS, timeout=30).json()
    print(st["status"], st["progress"], st["message"])
    if st["status"] in ("done", "error"):
        break
    time.sleep(1.5)

if st["status"] == "done":
    audio = requests.get(f"{BASE}/api/jobs/{job_id}/download", headers=HEADERS, timeout=60)
    audio.raise_for_status()
    open("song.mp3", "wb").write(audio.content)
else:
    raise RuntimeError(st["error"])
```

### 6.3 JavaScript（fetch）

```javascript
const BASE = "https://api.example.com";
const KEY  = "your-api-key";
const H    = { "X-API-Key": KEY };

// 搜索
const { results } = await (await fetch(
  `${BASE}/api/search?q=${encodeURIComponent("Chopin Nocturne")}&limit=5`, { headers: H }
)).json();

// 解析 + 读取缓存状态
const info = await (await fetch(
  `${BASE}/api/resolve?video_id=${results[0].id}&bitrate=128`, { headers: H }
)).json();
console.log(info.cache.hit ? "缓存命中，秒回" : "首次下载，请耐心等待");

// 播放（浏览器会自动发 Range）
const player = document.querySelector("audio");
player.src = `${BASE}/api/music?video_id=${results[0].id}&bitrate=128&api_key=${encodeURIComponent(KEY)}`;
await player.play();

// 读取自定义响应头（服务端已 expose）
const probe = await fetch(`${BASE}/api/music?video_id=${results[0].id}&bitrate=128`, {
  headers: { ...H, Range: "bytes=0-1023" },
});
console.log(probe.status, probe.headers.get("X-Cache"), probe.headers.get("Content-Range"));
```

### 6.4 HTML 直链（无需自定义请求头）

```html
<audio controls
  src="https://api.example.com/api/music?video_id=dQw4w9WgXcQ&bitrate=128&api_key=your-api-key">
</audio>

<a href="https://api.example.com/api/music?video_id=dQw4w9WgXcQ&bitrate=192&download=true&api_key=your-api-key">
  下载 MP3
</a>
```

---

## 7. 常见问题排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `401 {"detail":"无效或缺失的 API Key"}` | 服务端启用了 API Key | 加 `X-API-Key` 头或 `api_key` query |
| `422 {"detail":"必须提供 url、video_id 或 q 之一"}` | 三选一参数都没传 | 补上其中一个 |
| `422 bitrate 必须是 ... 之一` | 码率不在白名单 | 改用 `64/96/128/192/256/320` |
| `422 域名 ... 不在允许列表中` | `url` 不是 YouTube 白名单域名 | 改用 `video_id` 或让部署方配置 `MUSIC_EXTRA_ALLOWED_HOSTS` |
| `413` | 视频超长 / 产物超 60 MB | 换短视频，或由部署方调整上限 |
| `502` + 含 `Sign in to confirm you're not a bot` | 服务端出口 IP 被内容平台风控 | 属**服务端网络问题**，调用方重试无效；联系部署方更换出口（如 WARP 代理） |
| `502` 其它 | 视频下架 / 地区限制 / 需要登录 | 换 `video_id`，或联系部署方检查登录态 |
| `409` | 任务未完成就取产物 | 轮询到 `status=done` 再下载 |
| `404 任务不存在或已过期` | 任务超过保留时长 | 重新创建任务 |
| 首次请求很慢 | 缓存 `MISS`，正在下载 + 转码 | 用 `/api/jobs` 异步，或提高客户端超时 |
| 浏览器读不到 `X-Cache` | 跨域自定义头未暴露 | 服务端已配置 `expose_headers`；若走反向代理请确认未剥离 |

---

## 8. 服务端配置（供部署方核对，调用方可跳过）

以下为 `MUSIC_*` 环境变量，实际生效值以 `GET /api/health` 为准，完整清单见仓库 `.env.example`。

| 变量 | 默认值 | 作用 |
|---|---|---|
| `MUSIC_HOST` / `MUSIC_PORT` | `127.0.0.1` / `8000` | 监听地址与端口 |
| `MUSIC_API_KEY` | 空（不启用认证） | 启用 API Key |
| `MUSIC_CORS_ORIGINS` | `*` | 跨域白名单（逗号分隔） |
| `MUSIC_MP3_BITRATE` | `192` | 默认码率 |
| `MUSIC_MAX_DURATION_SEC` | `900` | 最大时长 |
| `MUSIC_MAX_FILE_MB` | `60` | 最大产物 |
| `MUSIC_MAX_CONCURRENT_DOWNLOADS` | `2` | 并发下载数 |
| `MUSIC_SEARCH_MAX_LIMIT` | `20` | 搜索条数上限 |
| `MUSIC_JOB_TTL_SEC` | `3600` | 任务保留时长 |
| `MUSIC_CACHE_TTL_DAYS` / `MUSIC_MAX_CACHE_MB` | `30` / `2048` | 缓存 TTL（天）与容量（MB）默认值；设为 `0` / `1024000` 即永不过期 / 1000 GB |
| `MUSIC_LYRICS_TTL_DAYS` | `7` | 字幕歌词缓存 TTL（天），独立于音频缓存 |
| `MUSIC_LYRICS_EXTERNAL` | `true` | YouTube 无字幕时是否回退到 LRCLIB；`false` 可关闭所有第三方歌词请求 |
| `MUSIC_PROXY` | 空 | 出站代理（被风控的机房出口需配置） |
| `MUSIC_FFMPEG_PATH` | 空 | ffmpeg 绝对路径；留空则从 PATH 查找。以 Windows 服务（LocalSystem）运行时 PATH 常不含 ffmpeg，建议写死 `C:/ffmpeg/bin/ffmpeg.exe` |
| `MUSIC_COOKIES_FILE` | 空 | 登录态 cookies 文件 |
| `MUSIC_DEMO_UI` | `true` | 是否启用演示页 |

> 生产部署请务必：启用 `MUSIC_API_KEY`、收紧 `MUSIC_CORS_ORIGINS`、在反向代理层做认证与限流、仅绑定内网或 HTTPS。

---

## 9. 机器可读规格

| 资源 | 地址 |
|---|---|
| 实时 OpenAPI 3.x | `GET {BASE_URL}/openapi.json`（**以此为准**） |
| 交互式文档 | `GET {BASE_URL}/docs` |
| 仓库静态快照 | `docs/openapi.json`（v1.2.0 快照，可导入 Postman / Apifox / 代码生成器） |

FastAPI 自动生成规格，`/api/health` 无需认证即可探活。注意 `DELETE /api/cache`、`/downloads/*` 等管理/静态能力不全部体现在规格中，调用前请与部署方确认开放范围。

---

## 10. 版本记录

| 版本 | 日期 | 说明 |
|---|---|---|
| 1.2.0 | 2026-11-20 | 歌词接口新增 LRCLIB 兜底；YouTube 无字幕时按标题/歌手/时长匹配同步歌词 |
| 1.1.0 | 2026-11-20 | 新增 `GET /api/lyrics` 同步歌词接口与演示页逐行高亮播放器 |
| 1.0.0 | 2026-11-20 | 首个第三方接入文档；9 组接口、Range/206、缓存、异步任务、API Key 认证 |
