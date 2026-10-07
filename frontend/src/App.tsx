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
import Timeline from "./components/Timeline";

export default function App() {
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
      <h1>Video Clipper</h1>

      <section className="panel">
        <input
          type="file"
          accept="video/*"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFile(file);
          }}
        />
        {uploadPercent !== null && (
          <p>Uploading… {uploadPercent.toFixed(0)}%</p>
        )}
        {upload && (
          <p className="meta">
            {upload.metadata.width}×{upload.metadata.height} ·{" "}
            {upload.metadata.duration.toFixed(2)}s · {upload.metadata.fps.toFixed(2)} fps ·{" "}
            {upload.metadata.video_codec}
            {upload.metadata.audio_codec ? ` + ${upload.metadata.audio_codec}` : ""}
          </p>
        )}
      </section>

      {upload && (
        <section className="panel">
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

          <div className="controls">
            <label>
              Start
              <input
                type="number"
                min={0}
                max={duration}
                step={0.1}
                value={start.toFixed(2)}
                onChange={(e) => setStart(Math.min(Number(e.target.value), end))}
              />
            </label>
            <label>
              End
              <input
                type="number"
                min={0}
                max={duration}
                step={0.1}
                value={end.toFixed(2)}
                onChange={(e) => setEnd(Math.max(Number(e.target.value), start))}
              />
            </label>
            <label>
              Mode
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value as "copy" | "reencode")}
              >
                <option value="copy">Lossless (fast)</option>
                <option value="reencode">Precise (slower)</option>
              </select>
            </label>
            <button onClick={handleClip} disabled={!upload || end <= start}>
              Create clip
            </button>
          </div>
        </section>
      )}

      {job && (
        <section className="panel">
          <p>
            Status: <strong>{job.status}</strong> — {job.percent.toFixed(0)}%
          </p>
          <progress value={job.percent} max={100} />
          {job.error && <p className="error">{job.error}</p>}
          {(job.status === "queued" || job.status === "running") && (
            <button onClick={handleCancel}>Cancel</button>
          )}
          {job.status === "done" && job.download_url && (
            <a className="download" href={job.download_url}>
              Download clip
            </a>
          )}
        </section>
      )}

      {error && <p className="error">{error}</p>}
    </div>
  );
}
