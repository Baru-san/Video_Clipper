# Video Clipper — Technical Guide

A mentor's field guide to the concepts, pitfalls, and engineering decisions you must
understand before and while building a desktop video clipper app.

Stack assumption: **Python 3.11+ · PySide6 (Qt) · FFmpeg · PyInstaller**.

---

## 0. The Golden Rule

> **Your app is an orchestrator. FFmpeg is the engine.**

The quality of your product depends far more on how correctly you *drive FFmpeg* and
*manage long-running processes* than on any algorithmic cleverness in Python. Everything
below serves that truth.

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
  You cannot change the codec without re-encoding — slow.

### 1.2 Keyframes (I-frames) — the #1 gotcha
- Video is compressed as groups of frames (GOPs). Only **keyframes** can start playback.
- **`-c copy` cuts can only begin on a keyframe.** If a user asks to cut at 00:03.217 but
  the nearest keyframe is at 00:02.500, a stream-copy cut will be off, or start early.
- This is the single biggest source of "why is my clip wrong?" bugs.
- Two solutions (see §3.3): re-encode the cut for frame accuracy, or accept
  keyframe-snapped cuts.

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
- Always pass `-pix_fmt yuv420p` for maximum player compatibility.

**Action items for you:**
- [ ] Learn to read `ffprobe -v quiet -print_format json -show_streams -show_format file.mp4`.
- [ ] Build a helper that extracts: duration, fps, codec, resolution, keyframe interval,
      VFR/CFR, audio layout, HDR flag.
- [ ] Never assume user input is CFR H.264 MP4.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────┐
│                 UI Layer (PySide6)           │
│  Main window · Timeline · Preview · Queue    │
└───────────────┬─────────────────────────────┘
                │ signals/slots (thread-safe)
┌───────────────▼─────────────────────────────┐
│            Application Core                  │
│  Project model · Command builder · Queue     │
└───────────────┬─────────────────────────────┘
                │
┌───────────────▼─────────────────────────────┐
│         Media Engine (FFmpeg adapter)        │
│  probe() · build_command() · run() · parse() │
└───────────────┬─────────────────────────────┘
                │ subprocess
┌───────────────▼─────────────────────────────┐
│                ffmpeg / ffprobe              │
└─────────────────────────────────────────────┘
```

**Layering rule:** UI never calls `subprocess` directly. UI ↔ Core ↔ Engine. This keeps the
engine testable headlessly and lets you swap the UI (or even add a CLI) later.

---

## 3. The Media Engine — FFmpeg Deep Dive

### 3.1 The two ways to cut

**Lossless / stream copy (fast):**
```bash
ffmpeg -ss 00:00:10 -to 00:00:20 -i input.mp4 -c copy -avoid_negative_ts make_zero output.mp4
```
- Near-instant, no quality loss.
- **Keyframe-snapped** start (may be off by up to one GOP).
- Put `-ss` **before** `-i` for fast input seeking, but this is less accurate. Put `-ss`
  **after** `-i` for accurate-but-slower decode. Choose deliberately.

**Accurate re-encode (slow):**
```bash
ffmpeg -ss 00:00:10 -i input.mp4 -t 10 \
  -c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p \
  -c:a aac -b:a 192k -movflags +faststart output.mp4
```
- Frame-accurate, but costs CPU time and a generation of quality loss.
- Use `CRF` (constant quality) not bitrate unless you have a reason.

**Product decision:** offer both. Default to lossless; give a "precise cut" toggle that
re-encodes only the affected clips (smart render), not the whole timeline.

### 3.2 Concatenation
- Same codec/params → concat demuxer with `file 'clip1.mp4'` list, `-c copy` (fast).
- Different sources → must re-encode, or use the concat **filter**.
- Mismatched timebases are the classic cause of A/V drift. Probe before merging.

### 3.3 Fast/keyframe-aware seeking strategy
1. Probe source, get keyframe list (`ffprobe -select_streams v -show_frames -skip_frame nokey`).
2. Find the nearest keyframe ≤ requested start.
3. Stream-copy from that keyframe, then trim precisely with a filter if needed — or
   re-encode just that boundary segment and concat. ("Smart render.")

### 3.4 Progress reporting
- FFmpeg writes progress to stderr. Parse lines like `frame= 154 fps= 45 time=00:00:06.4`.
- Better: use `-progress pipe:1 -nostats` which emits machine-readable `key=value` lines.
- Compute `% = current_time / total_duration` (you got total from ffprobe).
- Don't over-poll: update UI ~10x/sec max.

### 3.5 Audio/video desync prevention
- Add `-avoid_negative_ts make_zero` on copy cuts.
- For re-encode, keep consistent `-r` and audio `-ar` across all segments to be merged.

**Action items:**
- [ ] Wrap all FFmpeg calls behind a single `FFmpegRunner` class.
- [ ] Unit-test command construction with table-driven tests (input → expected argv).
- [ ] Never string-concatenate shell commands; pass argument lists to avoid injection and
      path-with-spaces bugs.

---

## 4. GUI Layer (PySide6)

### 4.1 Signals & Slots
- Qt's communication primitive. Worker threads emit signals; UI slots update widgets.
- Understand `Qt.QueuedConnection` (cross-thread, thread-safe) vs `DirectConnection`.

### 4.2 The threading model (critical)
- **Never run FFmpeg on the GUI thread.** It will freeze the UI.
- Options:
  - `QProcess` — Qt-native subprocess; integrates with the event loop, emits
    `readyReadStandardOutput`, `finished`. **Recommended** for FFmpeg.
  - `QThread` + `worker` object for non-FFmpeg background work.
  - `QThreadPool` + `QRunnable` for short tasks.
- Rule: create/update widgets **only on the GUI thread**. Marshal everything via signals.

### 4.3 The video preview problem (harder than it looks)
- **Do not** try to write a video player from scratch. Options:
  1. Embed a real player via `QMediaPlayer` + `QVideoWidget` (uses OS codecs; format
     support varies, H.265 may fail).
  2. Decode frames with FFmpeg and render to a `QLabel`/`QGraphicsView` (full control,
     works with any codec, more code).
  3. Use `python-mpv` (libmpv) embedded — robust, but adds a native dependency.
- For a *clipper*, you mostly need **frame-accurate scrubbing + thumbnails**, not smooth
  playback. Prefer an FFmpeg frame-decode approach for correctness.
- **Thumbnail strips:** generate with
  `ffmpeg -i in.mp4 -vf "fps=1/5,scale=160:-1,tile=10x1" -frames:v 1 _%02d.jpg`
  or `select` filters, and cache them.

### 4.4 Timeline widget
- Custom `QWidget` with `paintEvent`, or `QGraphicsScene`. You'll need: playhead, in/out
  handles, zoom, scroll, waveform, keyframe markers.
- Keep the model (`Project`) separate from the view; the widget renders model state.

### 4.5 Dialogs, drag-and-drop, shortcuts
- Support drag-and-drop file open (`dragEnterEvent`/`dropEvent`).
- Provide keyboard shortcuts (space = play, I/O = set in/out).
- Remember last-used directory via `QSettings`.

**Action items:**
- [ ] Prototype a bare window with a QProcess-driven FFmpeg trim and a progress bar first.
- [ ] Prove UI stays responsive during a 10-minute re-encode.

---

## 5. Concurrency & Job Queue

- Model rendering as a **queue of jobs**, each with state:
  `PENDING → RUNNING → DONE / FAILED / CANCELLED`.
- Run N jobs concurrently (N ≈ CPU cores for re-encodes; 1–2 for `-c copy` since it's I/O).
- **Cancellation:** FFmpeg doesn't always stop on SIGTERM cleanly; send `q` to stdin or
  terminate, then `kill` after a timeout. Clean up partial output files.
- **Resource limits:** re-encoding is CPU/RAM-heavy. Cap concurrency; expose it as a setting.
- Consider `-threads` to control FFmpeg's internal parallelism so it doesn't starve the UI.

---

## 6. Project & State Management

- Define a serializable **Project** model (JSON): source paths, clips (in/out, effects),
  output settings, version.
- **Autosave** and crash recovery. Long edits must survive a crash.
- Version your project schema; write migrations early.
- Never store absolute paths only — handle moved/renamed source files gracefully.
- Undo/redo: use the **command pattern**, or Qt's `QUndoStack`. Retrofitting undo is painful.

---

## 7. Performance Considerations

| Concern | Guidance |
|---|---|
| Trimming | Prefer `-c copy`; it's ~100x faster than re-encode |
| Re-encoding | Use hardware encoders when available (`h264_nvenc`, `h264_videotoolbox`, `h264_qsv`) |
| Preview | Decode at low resolution for scrubbing (`scale=480:-1`) |
| Thumbnails | Generate once, cache to disk keyed by (file mtime, size, ts) |
| Memory | Stream FFmpeg output; never read whole files into RAM |
| Disk | Estimate output size before running (`ffprobe` bitrate × duration) |
| Startup | Lazy-import heavy modules; defer loading Qt modules not needed at launch |

- **Hardware acceleration:** detect capability at startup, fall back to libx264/libx265.
- **Bottleneck order is usually:** disk I/O → CPU encode → GPU encode. Profile before
  optimizing.

---

## 8. Packaging & Distribution

- **PyInstaller** (one-file or one-dir). One-dir starts faster and is easier to debug.
- **You must bundle FFmpeg binaries** — users won't have them. Ship platform builds
  (`ffmpeg.exe`, `ffmpeg` for macOS/Linux) and resolve the path at runtime via
  `sys._MEIPASS` when frozen.
- **Code signing / notarization:** unsigned macOS apps are blocked; Windows SmartScreen
  warns. Budget for this.
- **Size:** FFmpeg binaries add ~30–70MB. Accept it, or offer a trimmed build.
- **Auto-update:** decide early (e.g. `python-updater`/Sparkle-style). Hard to bolt on later.
- Keep FFmpeg **LGPL/GPL** licensing in mind: if you build with GPL components, your
  distribution obligations change. (See §11.)

---

## 9. Testing Strategy

- **Command builder:** pure functions → table-driven unit tests. Highest ROI.
- **FFmpeg integration:** use tiny sample media checked into `tests/fixtures/` (a 2-second
  clip). Assert output duration/streams via `ffprobe`.
- **Engine:** test probe output parsing against captured JSON fixtures.
- **UI:** `pytest-qt` for widget/signal tests; don't over-invest in UI tests.
- **E2E smoke:** script that opens a file, cuts, and verifies the output exists and is valid.
- **Golden files:** compare `ffprobe` JSON of output to expected, with tolerances.

---

## 10. Error Handling & Observability

- FFmpeg errors are opaque. Capture **full stderr** to a log file; surface a friendly
  message in the UI with an "open log" button.
- Categorize failures: missing file, unsupported codec, disk full, permission denied,
  cancelled, timeout.
- **Logging:** `logging` module, rotating file handler, log level configurable. Include the
  exact FFmpeg command (redacted of nothing — commands are safe) for reproducibility.
- Add a crash reporter or at least a "copy diagnostics" action.

---

## 11. Legal & Licensing (do not ignore)

- **FFmpeg license:** LGPL by default; **GPL** if built with `--enable-gpl` (needed for
  libx264). GPL FFmpeg imposes obligations on your app if you ship it. Understand this
  before commercial release.
- **Patents:** H.264/H.265 have patent licensing bodies (MPEG LA / Access Advance). AV1 is
  royalty-free.
- **User content:** add terms clarifying users are responsible for rights to the media they
  clip. Common for YouTube/Twitch clip tools.

---

## 12. Cross-Platform Gotchas

- Paths: use `pathlib`; never hardcode `/` or `\`.
- Bundled binary names differ (`ffmpeg.exe` vs `ffmpeg`).
- `subprocess` flags: use `CREATE_NO_WINDOW` on Windows to avoid console flashes.
- Signal handling differs (Windows lacks POSIX signals).
- Fonts/DPI/theming vary; high-DPI scaling needs `Qt.AA_EnableHighDpiScaling`.
- File dialogs and default codecs differ per OS.

---

## 13. Suggested Project Structure

```
video_clipper/
├── pyproject.toml
├── README.md
├── docs/
│   └── TECHNICAL_GUIDE.md
├── src/video_clipper/
│   ├── __init__.py
│   ├── __main__.py            # entry point
│   ├── app.py                 # QApplication bootstrap
│   ├── config.py              # settings, paths, ffmpeg resolution
│   ├── core/
│   │   ├── project.py         # Project/Clip models + serialization
│   │   ├── commands.py        # undo/redo command pattern
│   │   └── queue.py           # job queue + states
│   ├── media/
│   │   ├── probe.py           # ffprobe wrapper + models
│   │   ├── ffmpeg.py          # FFmpegRunner (QProcess)
│   │   ├── command_builder.py # pure argv builders
│   │   └── keyframes.py       # keyframe lookup / smart cut
│   ├── ui/
│   │   ├── main_window.py
│   │   ├── timeline.py
│   │   ├── preview.py
│   │   └── dialogs.py
│   └── bin/                   # bundled ffmpeg/ffprobe per platform
└── tests/
    ├── fixtures/
    ├── test_command_builder.py
    └── test_probe.py
```

---

## 14. Build Roadmap (incremental, always shippable)

1. **M1 — Skeleton:** window + "Open file" → `ffprobe` → show metadata.
2. **M2 — Lossless trim:** in/out spinboxes → `-c copy` cut → progress bar → done.
3. **M3 — Timeline + preview frames:** thumbnail strip, scrubbing, visual handles.
4. **M4 — Job queue:** batch multiple clips, cancellation, per-job status.
5. **M5 — Correctness pack:** keyframe-aware smart cut, concat/merge, format presets.
6. **M6 — Polish:** undo/redo, autosave, settings, drag-and-drop, shortcuts.
7. **M7 — Ship:** packaging, FFmpeg bundling, signing, auto-update, licensing review.

Build a vertical slice (M1→M2) end-to-end **before** touching the timeline. A working ugly
tool beats a beautiful non-working one.

---

## 15. Mentor's Top 10 Things People Get Wrong

1. Running FFmpeg on the GUI thread → frozen app.
2. Assuming `-c copy` cuts are frame-accurate → keyframe snapping surprises.
3. Not bundling FFmpeg → "works on my machine" only.
4. Shelling out with string concatenation → breaks on spaces/special chars, injection risk.
5. Ignoring VFR/phone recordings → A/V desync.
6. No cancellation path → users can't stop a long encode.
7. No autosave → lost work.
8. Writing a video player from scratch → months of pain.
9. Ignoring licensing → legal trouble at launch.
10. Optimizing performance before there's a working product.

---

## 16. Learning Resources to Consult

- FFmpeg docs: filters, `-ss`/`-t`, `-c copy`, concat demuxer, `-progress`.
- `ffprobe` JSON output schema.
- Qt for Python (PySide6) signals/slots, QProcess, QThread, QUndoStack.
- "Keyframe / GOP / PTS-DTS" articles — any competent video-engineering primer.
- PyInstaller docs for bundling binary data (`--add-binary`).

---

*Written as a starting contract for yourself. Revisit and update it as decisions get made —
the doc is a living artifact, not a one-time deliverable.*
