import { useCallback, useEffect, useState } from "react";
import {
  listUploads,
  subscribeJob,
  transcribe,
  uploadVideo,
  type Job,
  type UploadResult,
  type UploadSummary,
} from "../api";

function phaseLabel(phase: string | null): string {
  switch (phase) {
    case "audio":
      return "extracting audio…";
    case "transcribe":
      return "transcribing…";
    case "translate":
      return "translating…";
    case "write":
      return "writing subtitles…";
    default:
      return phase ?? "";
  }
}

export default function Transcript({
  upload,
  onUpload,
}: {
  upload: UploadResult | null;
  onUpload: (result: UploadResult) => void;
}) {
  const [mode, setMode] = useState<"current" | "previous" | "new">(
    upload ? "current" : "previous",
  );
  const [previousId, setPreviousId] = useState("");
  const [uploads, setUploads] = useState<UploadSummary[]>([]);
  const [language, setLanguage] = useState("");
  const [translateTo, setTranslateTo] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [srt, setSrt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void listUploads()
      .then((items) => {
        setUploads(items);
        if (items.length && !previousId) setPreviousId(items[0].upload_id);
      })
      .catch(() => undefined);
  }, [previousId]);

  const selectedUploadId =
    mode === "current" ? upload?.upload_id ?? "" : mode === "previous" ? previousId : "";

  const handleNewFile = useCallback(
    async (file: File) => {
      setError(null);
      setSrt(null);
      setJob(null);
      try {
        const result = await uploadVideo(file, () => undefined);
        onUpload(result);
        setMode("current");
        setUploads((current) => [
          {
            upload_id: result.upload_id,
            name: file.name,
            duration: result.metadata.duration,
            size: result.metadata.size_bytes,
            created: Date.now() / 1000,
            thumbnail_url: result.thumbnail_urls[0] ?? null,
          },
          ...current,
        ]);
      } catch (err) {
        setError(err instanceof Error ? err.message : "upload failed");
      }
    },
    [onUpload],
  );

  const handleTranscribe = useCallback(async () => {
    if (!selectedUploadId) {
      setError("choose a video first");
      return;
    }
    setError(null);
    setSrt(null);
    try {
      const created = await transcribe({
        upload_id: selectedUploadId,
        language: language || null,
        translate_to: translateTo || null,
      });
      setJob(created);
      subscribeJob(created.id, (update) => {
        setJob(update);
        if (update.status === "done" && update.subtitle_url) {
          void fetch(update.subtitle_url)
            .then((response) => response.text())
            .then(setSrt)
            .catch(() => undefined);
        }
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "transcription failed");
    }
  }, [selectedUploadId, language, translateTo]);

  return (
    <section className="panel wide">
      <div className="panel-head"><span>Transcript</span></div>
      <div className="panel-body">
        <p className="hint">
          Transcribe a video to an .srt file — pick the video already loaded, one from your
          recent uploads, or upload a new one.
        </p>

        <div className="source-modes">
          <label className={mode === "current" ? "source-opt on" : "source-opt"}>
            <input
              type="radio"
              name="source"
              checked={mode === "current"}
              disabled={!upload}
              onChange={() => setMode("current")}
            />
            Current video
            {upload ? <span className="source-note"> ({upload.metadata.duration.toFixed(0)}s)</span> : <span className="source-note"> (none loaded)</span>}
          </label>

          <label className={mode === "previous" ? "source-opt on" : "source-opt"}>
            <input
              type="radio"
              name="source"
              checked={mode === "previous"}
              onChange={() => setMode("previous")}
            />
            Previous upload
          </label>

          {mode === "previous" && (
            <select
              className="source-select"
              value={previousId}
              onChange={(e) => setPreviousId(e.target.value)}
            >
              {uploads.length === 0 && <option value="">No recent uploads</option>}
              {uploads.map((item) => (
                <option key={item.upload_id} value={item.upload_id}>
                  {item.name || item.upload_id.slice(0, 8)} · {item.duration.toFixed(0)}s
                </option>
              ))}
            </select>
          )}

          <label className={mode === "new" ? "source-opt on" : "source-opt"}>
            <input
              type="radio"
              name="source"
              checked={mode === "new"}
              onChange={() => setMode("new")}
            />
            Upload new
          </label>

          {mode === "new" && (
            <input
              className="source-select"
              type="file"
              accept="video/*"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void handleNewFile(file);
              }}
            />
          )}
        </div>

        <div className="auto-options" style={{ marginTop: 14 }}>
          <label className="field">
            <span>Spoken language</span>
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              <option value="">Auto-detect</option>
              <option value="en">English</option>
              <option value="id">Indonesian</option>
            </select>
          </label>
          <label className="field">
            <span>Translate to</span>
            <select value={translateTo} onChange={(e) => setTranslateTo(e.target.value)}>
              <option value="">None</option>
              <option value="en">English</option>
              <option value="id">Bahasa Indonesia</option>
            </select>
          </label>
          <button className="primary" onClick={() => void handleTranscribe()}>
            Transcribe
          </button>
        </div>

        {job && (job.status === "running" || job.status === "queued") && (
          <div className="job-block">
            <div className="job-line">
              <span className={`badge ${job.status}`}>{job.status}</span>
              <span>{phaseLabel(job.phase)}</span>
            </div>
            <div className="meter"><div className="meter-fill" style={{ width: "60%" }} /></div>
          </div>
        )}
        {job?.status === "failed" && <p className="error">Transcription failed: {job.error}</p>}

        {job?.status === "done" && job.subtitle_url && (
          <>
            <div className="history-head" style={{ marginTop: 14 }}>
              <h2>Transcript {job.detected_language ? `(${job.detected_language})` : ""}</h2>
              <a className="ghost small link" href={job.subtitle_url}>Download .srt</a>
            </div>
            {srt && <pre className="transcript-view">{srt}</pre>}
          </>
        )}

        {error && <p className="error">{error}</p>}
      </div>
    </section>
  );
}
