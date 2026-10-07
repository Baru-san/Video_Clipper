# Video Clipper — Technical Guide

A mentor's field guide to the concepts, pitfalls, and engineering decisions you must
understand before and while building this video clipper.

Stack: **React + TypeScript + Vite · FastAPI (Python) · FFmpeg · Nginx · systemd · Debian VPS**.

Target production server: **1 vCPU · 1 GB RAM · 15 GB SSD**. Every design decision below is
filtered through that constraint.

---

## 0. The Golden Rule

> **Your app is an orchestrator. FFmpeg is the engine.**

The quality of your product depends far more on how correctly you *drive FFmpeg*, *stream
large files without loading them into RAM*, and *manage a long-running process on a tiny
server* than on any cleverness in Python or React. Everything below serves that truth.

---

## 1. Video Fundamentals (the part most beginners skip and then suffer for)

You cannot build a reliable clipper without understanding the media model. Learn these
terms cold.

### 1.1 Container vs Codec
- **Container** (`.mp4`, `.mkv`, `.mov`, `.webm`): the box. Holds video streams, audio
  streams, subtitles, metadata, and an index. It is *not* the video itself.
- **Codec** (H.264, H.265/HEVC, VP9, AV1, AAC, Opus): how the actual frames/samples are
  compressed.
- Consequence: you can change the container without touching the codec (`-c copy`) — fast.
  You cannot change the codec without re-encoding — slow and CPU-heavy.

### 1.2 Keyframes (I-frames) — the #1 gotcha
- Video is compressed as groups of frames (GOPs). Only **keyframes** can start playback.
- **`-c copy` cuts can only begin on a keyframe.** If a user asks to cut at 00:03.217 but
  the nearest keyframe is at 00:02.500, a stream-copy cut will be off, or start early.
- This is the single biggest source of "why is my clip wrong?" bugs.
- Two solutions (see §3.3): re-encode the cut for frame accuracy, or accept
  keyframe-snapped cuts. **On a 1 vCPU box, prefer keyframe-snapped cuts by default.**

### 1.3 Frame rate, timebase, PTS/DTS
- **PTS (Presentation Timestamp):** when a frame should be shown.
- **DTS (Decode Timestamp):** when it must be decoded (differs due to B-frames).
- **Timebase:** the unit PTS/DTS are measured in (e.g. `1/90000`). Mismatched timebases
  cause A/V drift when concatenating clips from different sources.
- **Variable Frame Rate (VFR):** common in phone/screen recordings. VFR sources break naive
  timestamp math. Normalize to CFR when you must re-encode.

### 1.4 Audio
- Usually interleaved in the same container. Audio seek granularity differs from video.
- Watch for: mono/stereo, sample rate (44.1k vs 48k), and audio starting slightly off from
  video (needs `-async` or re-encode).

### 1.5 Color & HDR
- `yuv420p` is the universally compatible pixel format for output; HDR (`bt2020`,
  `smpte2084`) sources need explicit tonemapping or they look washed out after re-encode.
- Always pass `-pix_fmt yuv420p` when re-encoding for browser compatibility.

**Action items:**
- [ ] Learn to read `ffprobe -v quiet -print_format json -show_streams -show_format file.mp4`.
- [ ] Build a helper that extracts: duration, fps, codec, resolution, keyframe interval,
      VFR/CFR, audio layout, HDR flag.
- [ ] Never trust file extension or client-supplied MIME type; probe the actual file.

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────┐
│            Browser (React + Vite)             │
│  Upload · Timeline · In/Out · Progress · DL   │
└───────────────┬───────────────────────────────┘
                │ HTTP / SSE
┌───────────────▼───────────────────────────────┐
│                    Nginx                        │
│  static build · reverse proxy · body-size cap   │
└───────────────┬───────────────────────────────┘
                │
┌───────────────▼───────────────────────────────┐
│              FastAPI (uvicorn, 1 worker)        │
│  routes · validation · job queue · SSE progress │
└───────────────┬───────────────────────────────┘
                │
┌───────────────▼───────────────────────────────┐
│            Media Engine (FFmpeg adapter)        │
│  probe() · build_argv() · run() · parse()       │
└───────────────┬───────────────────────────────┘
                │ subprocess (NO shell=True)
┌───────────────▼───────────────────────────────┐
│                ffmpeg / ffprobe                  │
└──────────────────────────────────────────────────┘
```

**Layering rule:** API routes never build FFmpeg commands or call `subprocess` directly.
Routes ↔ Core (queue/validation) ↔ Media engine. This keeps the engine testable without a
running server and prevents the API layer from leaking media details.

---

## 3. The Media Engine — FFmpeg Deep Dive

### 3.1 The two ways to cut

**Lossless / stream copy (fast — DEFAULT):**
```bash
ffmpeg -ss 00:00:10 -i input.mp4 -t 00:00:10 -c copy \
  -avoid_negative_ts make_zero \
  -movflags +faststart output.mp4
```
- Near-instant, no quality loss, minimal CPU — matters enormously on 1 vCPU.
- **Keyframe-snapped** start (may be off by up to one GOP).
- `-ss` before `-i` = fast input seek (less accurate). `-ss` after `-i` = accurate but
  decodes from the start (slower). Default to before-`-i` for copy.

**Accurate re-encode (slow — opt-in only):**
```bash
ffmpeg -ss 00:00:10 -i input.mp4 -t 00:00:10 \
  -c:v libx264 -threads 1 -preset veryfast -crf 20 -pix_fmt yuv420p \
  -c:a aac -b:a 128k -movflags +faststart output.mp4
```
- Frame-accurate, but costs CPU time and a generation of quality loss.
- On 1 vCPU, `-threads 1` and `-preset veryfast`/`ultrafast` keep it from thrashing.

**Product decision:** default to lossless. Offer a "precise cut" toggle that re-encodes,
with a clear warning that it is slower and heavier.

### 3.2 Concatenation
- Same codec/params → concat demuxer with a `file 'clip1.mp4'` list, `-c copy` (fast).
- Different sources → must re-encode, or use the concat **filter** (CPU-heavy).
- Mismatched timebases are the classic cause of A/V drift. Probe before merging.

### 3.3 Keyframe-aware seeking strategy
1. Probe source, get keyframe list (`ffprobe -select_streams v -show_frames -skip_frame nokey`).
2. Find the nearest keyframe ≤ requested start.
3. Stream-copy from that keyframe; warn the user if the cut snapped by more than a threshold.
4. Re-encode only the boundary frames if strict accuracy is required (future "smart render").

### 3.4 Progress reporting
- Use `-progress pipe:1 -nostats` — emits machine-readable `key=value` lines on stdout
  (`frame=`, `out_time_ms=`, `speed=`, `progress=continue|end`).
- Compute `% = out_time / clip_duration` (duration from ffprobe).
- Keep stderr separate and capture it for error diagnostics.
- Throttle UI updates to ~2–10/sec; don't flood SSE.

### 3.5 Audio/video desync prevention
- Add `-avoid_negative_ts make_zero` on copy cuts.
- For re-encode, keep consistent `-r` and audio `-ar` across all segments to be merged.

**Action items:**
- [ ] Wrap all FFmpeg calls behind a single `FFmpegRunner` class.
- [ ] Unit-test argv construction with table-driven tests (input → expected list).
- [ ] **Never** use `shell=True`; pass argument lists to avoid injection and spaces bugs.

---

## 4. Backend (FastAPI)

### 4.1 Process model
- Run **uvicorn with a single worker** (`--workers 1`) on 1 vCPU. Multiple workers multiply
  memory and compete for the one core.
- Use **async** routes for I/O (uploads, status), but run FFmpeg as a subprocess via
  `asyncio.create_subprocess_exec` (not blocking `subprocess.run`).

### 4.2 The job queue (no Redis, no Celery — per project rules)
- An **in-process single-worker queue** implemented with `asyncio.Queue` is sufficient.
- Constraint: **only one FFmpeg job runs at a time** (skill rule + 1 vCPU reality).
- Job states: `QUEUED → RUNNING → DONE | FAILED | CANCELLED`, plus progress (0–100).
- Jobs are lost on restart — acceptable for this scope; persist minimal job metadata to the
  DB/filesystem if users need history later.

### 4.3 Uploads (memory-safe is non-negotiable)
- **Stream request bodies to disk in chunks**; never `await file.read()` the whole file into
  RAM. A 500 MB upload must not become 500 MB of resident memory.
- Enforce limits **before and during** streaming:
  - `Content-Length` / `client_max_body_size` at Nginx.
  - max file size at the app level (abort the stream if exceeded).
- Generate **random filenames** (e.g. `uuid4().hex`), never trust the client filename.
- Store under a dedicated data dir; keep original name only as DB metadata (escaped).

### 4.4 Validation
- Validate by **probing with ffprobe**, not by extension or client MIME type.
- Reject: no video stream, duration out of range, timestamps out of range, negative/invalid
  in/out points, in ≥ out.
- Clamp/validate all numeric inputs; treat every client value as hostile.

### 4.5 Progress transport
- **SSE** (`text/event-stream`) is the simplest fit: one-way server→client, works over plain
  HTTP, no extra protocol. Use WebSockets only if you later need bidirectional control.
- Each job exposes `GET /jobs/{id}/events` streaming status/progress, and
  `GET /jobs/{id}/download` for the result.

### 4.6 API shape (sketch)
```
POST /api/uploads            # stream file to disk, return {upload_id, metadata}
GET  /api/uploads/{id}       # probe metadata (duration, fps, keyframes, thumbs)
POST /api/clips              # {upload_id, start, end, mode} -> {job_id}
GET  /api/jobs/{id}          # current status snapshot
GET  /api/jobs/{id}/events   # SSE progress stream
GET  /api/jobs/{id}/download # result file (with cleanup TTL)
DELETE /api/jobs/{id}        # cancel + cleanup
```

**Action items:**
- [ ] Endpoint that streams an upload to disk with a hard size cap.
- [ ] Pydantic models for every request; reject unknown/invalid fields.
- [ ] Job queue with cancellation that sends `q` to FFmpeg stdin, then kills on timeout.

---

## 5. Frontend (React + TypeScript + Vite)

### 5.1 Scope
- Upload with progress (XHR/`fetch` with upload progress, or tus if resumable needed later).
- Timeline with in/out handles; scrub via **thumbnail strip**, not full playback.
- Job progress via `EventSource` (SSE); download link when done.
- No heavy media libraries; keep the bundle small and the page fast to load.

### 5.2 Thumbnails
- Generate server-side once per upload:
  `ffmpeg -i in.mp4 -vf "fps=1/N,scale=160:-1" -frames:v M thumb_%03d.jpg`
  (choose N so M is ~10–30 thumbs).
- Cache them; serve statically through Nginx. Do **not** decode video in the browser.
- Timeline is a `<canvas>` or a row of images with CSS-positioned handles.

### 5.3 Preview
- For a clipper, frame-accurate scrubbing + thumbnails beats smooth playback.
- Optional: a `<video>` element streaming the *source* file for rough previewing; browser
  codec support varies (H.265 often unsupported), so don't rely on it for correctness.

### 5.4 State & UX
- Keep the in/out state simple; validate on the client *and* server.
- Show clear "cut will snap to keyframe" messaging and estimated output size.
- Handle failure states: upload too large, unsupported codec, job failed (offer logs).

### 5.5 Build & delivery
- `vite build` → static assets served by Nginx. Dev: Vite dev server proxy to FastAPI.
- Never ship source maps or debug bundles to production unless intended.

---

## 6. Storage & Disk (15 GB budget)

- **Disk is your scarcest durable resource.** Uploads + outputs + thumbnails all compete for
  15 GB.
- Layout:
  ```
  /srv/video-clipper/
    data/uploads/<uuid>.<ext>
    data/outputs/<uuid>.mp4
    data/thumbs/<uuid>/
    data/tmp/
  ```
- **Lifecycle policy:** TTL-delete uploads/outputs/thumbs after N hours/days via a periodic
  sweeper task (and/or systemd timer). Do not let orphans accumulate.
- Enforce a **global quota**: reject new uploads when free disk is below a threshold
  (check `shutil.disk_usage`), rather than failing mid-encode.
- Delete temp/partial files in `finally` — cancelled jobs leave garbage otherwise.
- Never store user media in the git repo or on tmpfs if RAM is tight.

---

## 7. Memory & CPU (1 GB / 1 vCPU)

| Concern | Guidance |
|---|---|
| Upload handling | **Stream to disk**; never buffer whole file in RAM |
| Trim | Prefer `-c copy`; ~0 CPU, minimal memory |
| Re-encode | `-threads 1`, `-preset veryfast`/`ultrafast`; one job at a time |
| Concurrency | API-enforced single job; no parallel FFmpeg |
| Python process | uvicorn `--workers 1`; measure RSS |
| React | static files, no SSR; small bundle |
| Nginx | serve statics + proxy; keep buffers sane |
| Thumbnails | generate at small scale; cache to disk |
| Database | SQLite (file-based) if needed — no separate DB server |

- Add **cgroup memory limits** in the systemd unit (`MemoryMax=700M`) so a runaway FFmpeg
  gets throttled/killed instead of taking down the box.
- Watch FFmpeg's memory with high-resolution 4K re-encodes — they can spike. Prefer copy or
  downscale (`-vf scale=-2:720`) to bound memory and time.
- Monitor with `ps_mem`/`systemd-cgtop`; log RSS periodically if needed.

---

## 8. Deployment (Debian + Nginx + systemd)

### 8.1 systemd unit (sketch)
```ini
[Unit]
Description=Video Clipper API
After=network.target

[Service]
User=videoclip
WorkingDirectory=/srv/video-clipper
ExecStart=/srv/video-clipper/.venv/bin/uvicorn app.main:app --workers 1 --host 127.0.0.1 --port 8000
Restart=on-failure
MemoryMax=700M
Nice=5
# hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/srv/video-clipper/data
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

### 8.2 Nginx (sketch)
```nginx
server {
    listen 80;
    server_name example.com;

    client_max_body_size 500m;          # align with app limit
    proxy_read_timeout 3600s;           # long encodes
    proxy_buffering off;                # required for SSE

    root /srv/video-clipper/frontend/dist;
    location / { try_files $uri /index.html; }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
    location /media/ { alias /srv/video-clipper/data/; internal; }
}
```

- `proxy_buffering off` is **required** for SSE to stream in real time.
- Set `client_max_body_size` consistent with the app's cap.
- Serve uploads/outputs with `internal;` and hand out signed/one-time URLs if you don't want
  direct access — otherwise anyone with a UUID could fetch a file.

### 8.3 Ops
- Reverse proxy + HTTPS via certbot/Let's Encrypt.
- Log rotation for app + Nginx logs (beware filling the 15 GB disk with logs).
- A disk-space and service-health check (simple cron + alert).

---

## 9. Security (server handles untrusted media — take this seriously)

- **Never `shell=True`**; always pass an argv list to ffmpeg/ffprobe.
- **Randomize filenames**; never interpolate client filenames into paths or commands.
- **Validate by probing**, not by extension/MIME.
- **Bound everything:** file size, duration, in/out range, number of jobs, request rate.
- **Path safety:** resolve and confine all paths to the data dir; reject traversal.
- **Resource protection:** single job at a time, memory cgroup, request timeouts.
- **SSRF/URL fetching:** if you ever accept URLs, this becomes a whole new threat surface —
  restrict or avoid.
- **Secrets:** keep them in env/systemd, never in the repo (see `.gitignore`).
- **Cleanup:** TTL deletion prevents disk exhaustion by malicious uploaders (also disk-fill
  DoS). Enforce quota checks.

---

## 10. Testing Strategy

- **Argv builder:** pure functions → table-driven unit tests. Highest ROI.
- **API:** `TestClient`/`httpx` against FastAPI routes; test validation and rejection paths.
- **Upload:** test size-cap and streaming behavior with generated files.
- **Media integration:** tiny fixtures (a 2-second clip) in `tests/fixtures/` (whitelisted in
  `.gitignore`). Assert output duration/streams via `ffprobe`.
- **Queue:** test single-job serialization, cancellation, and cleanup.
- **Frontend:** focused component tests (Vitest); don't over-invest in UI tests.
- **E2E smoke:** upload → clip → poll job → download, scripted.

Run `pytest` (backend) and `npm test`/`vitest` (frontend) after changes (per `AGENTS.md`).

---

## 11. Error Handling & Observability

- FFmpeg errors are opaque. Capture **full stderr** per job to a log file; surface a friendly
  message + a "view log" affordance.
- Categorize failures: unsupported codec, disk full, invalid timestamps, cancelled, timeout,
  OOM-killed.
- Logging: Python `logging` with rotation; include the exact FFmpeg argv for reproducibility.
- Don't leak stack traces or filesystem paths to clients.
- Track disk usage and job durations over time to catch regressions.

---

## 12. Legal & Licensing

- **FFmpeg license:** LGPL by default; **GPL** if built with `--enable-gpl` (needed for
  libx264). GPL FFmpeg imposes obligations on your distribution. Understand this before
  release. (Debian's `ffmpeg` package is typically GPL — check what you install.)
- **Patents:** H.264/H.265 have patent pools; AV1 is royalty-free.
- **User content:** add terms clarifying users are responsible for rights to media they
  clip. Common for YouTube/Twitch clip tools.
- **Data/privacy:** uploaded media is user data — define retention (TTL) and deletion.

---

## 13. Suggested Project Structure

```
video_clipper/
├── AGENTS.md
├── .gitignore
├── docs/
│   ├── TECHNICAL_GUIDE.md
│   └── DEPLOYMENT.md
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py            # FastAPI app, static mount
│   │   ├── config.py          # dirs, limits, ffmpeg path, env
│   │   ├── models.py          # pydantic schemas
│   │   ├── api/
│   │   │   └── routes.py      # upload, probe, clip, jobs, download
│   │   ├── core/
│   │   │   └── queue.py       # single-worker asyncio queue + progress
│   │   ├── media/
│   │   │   ├── probe.py       # ffprobe wrapper + models
│   │   │   ├── ffmpeg.py      # FFmpegRunner (create_subprocess_exec)
│   │   │   ├── command_builder.py  # pure argv builders
│   │   │   └── keyframes.py   # keyframe lookup / snap
│   │   └── maintenance/
│   │       └── cleanup.py     # TTL sweeper + quota check
│   └── tests/
│       ├── fixtures/
│       ├── test_command_builder.py
│       └── test_probe.py
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   └── src/…
└── deploy/
    ├── video-clipper.service
    ├── nginx.conf
    └── cleanup.timer / .service
```

---

## 14. Build Roadmap (incremental, always deployable)

1. **M1 — Backend skeleton:** FastAPI app, health check, static upload endpoint with size cap.
2. **M2 — Probe:** upload → ffprobe → return metadata JSON.
3. **M3 — Lossless clip:** `POST /api/clips` → single-worker queue → `-c copy` output +
   download endpoint.
4. **M4 — Progress:** `-progress` parsing → SSE stream → minimal React UI with progress bar.
5. **M5 — Timeline + thumbnails:** server thumbnails, React timeline with in/out handles.
6. **M6 — Robustness:** cancellation, TTL cleanup, disk quota, memory cgroup, precise-cut
   re-encode option.
7. **M7 — Deploy:** systemd + Nginx + HTTPS + monitoring on the VPS.

Build a vertical slice (M1→M4) end-to-end **before** polishing the timeline. A working ugly
tool beats a beautiful non-working one — especially on 1 vCPU.

---

## 15. Mentor's Top 10 Things People Get Wrong (this stack)

1. `shell=True` with user input → command injection.
2. Reading entire uploads into memory → OOM on a 1 GB box.
3. Running multiple FFmpeg jobs at once on 1 vCPU → everything crawls or dies.
4. Assuming `-c copy` cuts are frame-accurate → keyframe-snapping surprises.
5. `proxy_buffering on` → SSE progress never streams.
6. Ignoring VFR/phone recordings → A/V desync.
7. No TTL cleanup → disk fills, app breaks for everyone.
8. Trusting client filename/MIME/extension → path traversal, bad files.
9. No cancellation path → users can't stop a long encode; it hogs the core.
10. No memory cgroup → one runaway re-encode takes down the server.

---

## 16. Learning Resources

- FFmpeg docs: filters, `-ss`/`-t`, `-c copy`, concat demuxer, `-progress`.
- `ffprobe` JSON output schema (`-show_streams -show_format -show_frames`).
- FastAPI docs: streaming uploads, `StreamingResponse`, background tasks, lifespan.
- Server-Sent Events (SSE) spec and `EventSource` usage.
- Vite + React + TypeScript guide.
- systemd resource control (`MemoryMax`, `CPUQuota`) and Nginx proxy/SSE tuning.
- Keyframe / GOP / PTS-DTS primers.

---

*Living document. Update it as decisions are made.*
