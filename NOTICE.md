# Notice

## Compliance

Music MP3 API is a technical demonstration / personal-use tool. It resolves
and transcodes audio from third-party platforms via `yt-dlp` and `ffmpeg`.

Most music on those platforms is protected by copyright. Downloading,
transcoding, caching, or redistributing such content may violate the platform's
terms of service and the copyright laws of your jurisdiction.

Use this software **only** for content you own, are licensed to use, or that the
platform explicitly permits for offline use; for personal study and self-use
only. **Do not** operate it as a public service, use it commercially, or
redistribute downloaded content. Before exposing any deployment publicly,
perform your own compliance assessment. The authors and contributors are not
responsible for how the software is used.

If you simply want to listen to music, prefer officially licensed services.

## Third-party components

This project depends on, but does not bundle:

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — Unlicense
- [FastAPI](https://github.com/fastapi/fastapi) — MIT
- [ffmpeg](https://ffmpeg.org/) — LGPL/GPL depending on build
- [LRCLIB](https://lrclib.net/) — optional external synced-lyrics fallback

See `requirements.txt` for the full runtime dependency list.
