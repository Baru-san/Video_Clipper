import { useCallback, useRef, useState } from "react";
import {
  analyzeVideo,
  createClipsBatch,
  subscribeJob,
  type Candidate,
  type Job,
  type UploadResult,
} from "../api";

function tc(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const mm = String(Math.floor(whole / 60)).padStart(2, "0");
  const ss = String(whole % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

function phaseLabel(phase: string | null): string {
  switch (phase) {
    case "audio":
      return "extracting audio…";
    case "transcribe":
      return "transcribing…";
    case "analyze":
      return "finding highlights…";
    default:
      return phase ?? "";
  }
}

export default function AutoClip({ upload }: { upload: UploadResult | null }) {
  const [minLen, setMinLen] = useState(15);
  const [maxLen, setMaxLen] = useState(60);
  const [maxClips, setMaxClips] = useState(8);
  const [aspect, setAspect] = useState<"original" | "vertical">("vertical");
  const [subtitles, setSubtitles] = useState<"none" | "srt">("srt");
  const [translateTo, setTranslateTo] = useState("");
  const [analyzing, setAnalyzing] = useState<Job | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [renderJobs, setRenderJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const analyzeRef = useRef<EventSource | null>(null);

  const handleAnalyze = useCallback(async () => {
    if (!upload) return;
    setError(null);
    setCandidates([]);
    setSelected(new Set());
    setRenderJobs([]);
    try {
      const job = await analyzeVideo({
        upload_id: upload.upload_id,
        min_length: minLen,
        max_length: maxLen,
        max_clips: maxClips,
      });
      setAnalyzing(job);
      analyzeRef.current?.close();
      analyzeRef.current = subscribeJob(job.id, (update) => {
        setAnalyzing(update);
        if (update.status === "done") {
          const found = update.candidates ?? [];
          setCandidates(found);
          setSelected(new Set(found.map((_, i) => i)));
        }
        if (update.status !== "queued" && update.status !== "running") {
          analyzeRef.current?.close();
          analyzeRef.current = null;
        }
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "analysis failed");
    }
  }, [upload, minLen, maxLen, maxClips]);

  const handleRender = useCallback(async () => {
    if (!upload) return;
    setError(null);
    const clips = candidates
      .filter((_, i) => selected.has(i))
      .map((c) => ({ start: c.start, end: c.end, title: c.title }));
    if (clips.length === 0) {
      setError("select at least one clip");
      return;
    }
    try {
      const jobs = await createClipsBatch({
        upload_id: upload.upload_id,
        clips,
        mode: "copy",
        subtitles,
        translate_to: subtitles === "srt" && translateTo ? translateTo : null,
        aspect,
      });
      setRenderJobs(jobs);
      jobs.forEach((job) => {
        subscribeJob(job.id, (update) => {
          setRenderJobs((current) =>
            current.map((item) => (item.id === update.id ? update : item)),
          );
        });
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "render failed");
    }
  }, [upload, candidates, selected, subtitles, translateTo, aspect]);

  const toggle = (index: number) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  if (!upload) {
    return <section className="panel wide"><div className="panel-body"><div className="empty">Import a video on the Cut tab first</div></div></section>;
  }

  return (
    <section className="panel wide">
      <div className="panel-head"><span>Auto Clip — AI highlights</span></div>
      <div className="panel-body">
        <div className="auto-options">
          <label className="field">
            <span>Min length (s)</span>
            <input type="number" min={3} max={600} value={minLen}
              onChange={(e) => setMinLen(Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Max length (s)</span>
            <input type="number" min={5} max={1800} value={maxLen}
              onChange={(e) => setMaxLen(Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Max clips</span>
            <input type="number" min={1} max={30} value={maxClips}
              onChange={(e) => setMaxClips(Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Format</span>
            <select value={aspect} onChange={(e) => setAspect(e.target.value as "original" | "vertical")}>
              <option value="vertical">Vertical 9:16</option>
              <option value="original">Original</option>
            </select>
          </label>
          <label className="field">
            <span>Subtitles</span>
            <select value={subtitles} onChange={(e) => setSubtitles(e.target.value as "none" | "srt")}>
              <option value="srt">Generate (SRT)</option>
              <option value="none">Off</option>
            </select>
          </label>
          {subtitles === "srt" && (
            <label className="field">
              <span>Translate to</span>
              <select value={translateTo} onChange={(e) => setTranslateTo(e.target.value)}>
                <option value="">None</option>
                <option value="en">English</option>
                <option value="id">Bahasa Indonesia</option>
              </select>
            </label>
          )}
          <button className="primary" onClick={() => void handleAnalyze()}
            disabled={analyzing?.status === "running" || analyzing?.status === "queued"}>
            Analyze
          </button>
        </div>

        {analyzing && (analyzing.status === "running" || analyzing.status === "queued") && (
          <div className="job-block">
            <div className="job-line">
              <span className={`badge ${analyzing.status}`}>{analyzing.status}</span>
              <span>{phaseLabel(analyzing.phase)}</span>
            </div>
            <div className="meter"><div className="meter-fill" style={{ width: "60%" }} /></div>
          </div>
        )}
        {analyzing?.status === "failed" && <p className="error">Analysis failed: {analyzing.error}</p>}

        {candidates.length > 0 && (
          <>
            <div className="history-head" style={{ marginTop: 16 }}>
              <h2>{candidates.length} suggested clips</h2>
              <button className="primary small" onClick={() => void handleRender()}>
                Render selected ({selected.size})
              </button>
            </div>
            <ul className="candidate-list">
              {candidates.map((c, i) => (
                <li key={`${c.start}-${i}`} className={selected.has(i) ? "candidate on" : "candidate"}>
                  <input type="checkbox" checked={selected.has(i)} onChange={() => toggle(i)} />
                  <div className="candidate-body">
                    <div className="candidate-title">{c.title || "Untitled clip"}</div>
                    <div className="candidate-sub">
                      {tc(c.start)} → {tc(c.end)} · {(c.end - c.start).toFixed(0)}s
                      {c.score ? ` · score ${c.score.toFixed(2)}` : ""}
                    </div>
                    {c.reason && <div className="candidate-reason">{c.reason}</div>}
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}

        {renderJobs.length > 0 && (
          <div className="render-list">
            <h2>Rendering</h2>
            {renderJobs.map((job) => (
              <div key={job.id} className="render-row">
                <span className={`badge ${job.status}`}>{job.status}</span>
                <span className="render-title">{job.title || "clip"}</span>
                {job.status === "done" && job.download_url ? (
                  <a className="ghost small link" href={job.download_url}>Download</a>
                ) : (
                  <span className="candidate-sub">{job.percent.toFixed(0)}%</span>
                )}
              </div>
            ))}
          </div>
        )}

        {error && <p className="error">{error}</p>}
      </div>
    </section>
  );
}
