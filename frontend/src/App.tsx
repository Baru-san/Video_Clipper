import { useCallback, useRef, useState } from "react";
import {
  cancelJob,
  createClip,
  getJob,
  subscribeJob,
  uploadVideo,
  type Job,
  type UploadResult,
} from "./api";
import History from "./components/History";
import Timeline from "./components/Timeline";

type View = "clipper" | "history";

function timecode(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const mm = String(Math.floor(whole / 60)).padStart(2, "0");
  const ss = String(whole % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

export default function App() {
  const [view, setView] = useState<View>("clipper");
  const [upload, setUpload] = useState<UploadResult | null>(null);
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(0);
  const [mode, setMode] = useState<"copy" | "reencode">("copy");
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  const duration = upload?.metadata.duration ?? 0;

  const handleFile = useCallback(async (file: File) => {
    setError(null);
    setJob(null);
    setUpload(null);
    setUploadPercent(0);
    try {
      const result = await uploadVideo(file, setUploadPercent);
      setUpload(result);
      setStart(0);
      setEnd(result.metadata.duration);
    } catch (err) {
      setError(err instanceof Error ? err.message : "upload failed");
    } finally {
      setUploadPercent(null);
    }
  }, []);

  const handleClip = useCallback(async () => {
    if (!upload) return;
    setError(null);
    try {
      const created = await createClip({
        upload_id: upload.upload_id,
        start,
        end,
        mode,
      });
      setJob(created);
      sourceRef.current?.close();
      sourceRef.current = subscribeJob(created.id, (update) => {
        setJob(update);
        if (update.status !== "queued" && update.status !== "running") {
          sourceRef.current?.close();
          sourceRef.current = null;
        }
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "clip failed");
    }
  }, [upload, start, end, mode]);

  const handleCancel = useCallback(async () => {
    if (!job) return;
    await cancelJob(job.id);
    const fresh = await getJob(job.id);
    setJob(fresh);
  }, [job]);

  return (
    <div className="app">
      <header className="titlebar">
        <div className="brand">
          <span className="brand-mark">◆</span>
          <span className="brand-name">Video Clipper</span>
        </div>
        <nav className="toolbar">
          <button
            className={view === "clipper" ? "tool active" : "tool"}
            onClick={() => setView("clipper")}
          >
            Cut
          </button>
          <button
            className={view === "history" ? "tool active" : "tool"}
            onClick={() => setView("history")}
          >
            Resources
          </button>
        </nav>
        <div className="titlebar-meta">FFmpeg · 1 GB box</div>
      </header>

      {view === "history" ? (
        <main className="workspace">
          <History />
        </main>
      ) : (
        <main className="workspace grid">
          <aside className="panel media-pool">
            <div className="panel-head">
              <span>Media Pool</span>
              <span className="panel-dots">•••</span>
            </div>
            <div className="panel-body">
              <label className="import-btn">
                <span>Import Media</span>
                <input
                  type="file"
                  accept="video/*"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void handleFile(file);
                  }}
                />
              </label>

              {uploadPercent !== null && (
                <div className="meter">
                  <div className="meter-fill" style={{ width: `${uploadPercent}%` }} />
                </div>
              )}

              {upload ? (
                <>
                  <div className="meta-block">
                    <div className="meta-name">
                      {upload.metadata.width}×{upload.metadata.height}
                    </div>
                    <div className="meta-line">
                      {timecode(duration)} · {upload.metadata.fps.toFixed(2)} fps
                    </div>
                    <div className="meta-line">
                      {upload.metadata.video_codec}
                      {upload.metadata.audio_codec
                        ? ` + ${upload.metadata.audio_codec}`
                        : ""}
                    </div>
                  </div>
                  <div className="thumb-grid">
                    {upload.thumbnail_urls.map((url) => (
                      <img key={url} src={url} alt="" loading="lazy" />
                    ))}
                  </div>
                </>
              ) : (
                <div className="empty">Import a video to begin</div>
              )}
            </div>
          </aside>

          <section className="panel viewer">
            <div className="panel-head">
              <span>Viewer</span>
              <span className="panel-dots">•••</span>
            </div>
            <div className="panel-body">
              {upload ? (
                <Timeline
                  duration={duration}
                  thumbnails={upload.thumbnail_urls}
                  start={start}
                  end={end}
                  onChange={(nextStart, nextEnd) => {
                    setStart(nextStart);
                    setEnd(nextEnd);
                  }}
                />
              ) : (
                <div className="viewer-empty">No clip loaded</div>
              )}

              {job && (
                <div className="job-block">
                  <div className="job-line">
                    <span className={`badge ${job.status}`}>{job.status}</span>
                    <span>{job.percent.toFixed(0)}%</span>
                  </div>
                  <div className="meter">
                    <div className="meter-fill" style={{ width: `${job.percent}%` }} />
                  </div>
                  {job.warning && <p className="warning">{job.warning}</p>}
                  {job.error && <p className="error">{job.error}</p>}
                  {(job.status === "queued" || job.status === "running") && (
                    <button className="ghost" onClick={handleCancel}>
                      Cancel
                    </button>
                  )}
                </div>
              )}

              {error && <p className="error">{error}</p>}
            </div>
          </section>

          <aside className="panel inspector">
            <div className="panel-head">
              <span>Inspector</span>
              <span className="panel-dots">•••</span>
            </div>
            <div className="panel-body">
              <label className="field">
                <span>Start</span>
                <input
                  type="number"
                  min={0}
                  max={duration}
                  step={0.1}
                  value={start.toFixed(2)}
                  onChange={(e) => setStart(Math.min(Number(e.target.value), end))}
                />
              </label>
              <label className="field">
                <span>End</span>
                <input
                  type="number"
                  min={0}
                  max={duration}
                  step={0.1}
                  value={end.toFixed(2)}
                  onChange={(e) => setEnd(Math.max(Number(e.target.value), start))}
                />
              </label>
              <div className="field">
                <span>Duration</span>
                <div className="readout">{(end - start).toFixed(2)} s</div>
              </div>
              <label className="field">
                <span>Render</span>
                <select
                  value={mode}
                  onChange={(e) => setMode(e.target.value as "copy" | "reencode")}
                >
                  <option value="copy">Lossless (fast)</option>
                  <option value="reencode">Precise (slower)</option>
                </select>
              </label>

              <button
                className="primary"
                onClick={handleClip}
                disabled={!upload || end <= start}
              >
                Render Clip
              </button>

              {job?.status === "done" && job.download_url && (
                <>
                  <a className="primary link" href={job.download_url}>
                    Download Clip
                  </a>
                  <p className="note">Saved to Resources — kept 24h.</p>
                </>
              )}
            </div>
          </aside>
        </main>
      )}
    </div>
  );
}
