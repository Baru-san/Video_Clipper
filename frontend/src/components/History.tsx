import { useCallback, useEffect, useState } from "react";
import { deleteResource, listResources, type Resource } from "../api";

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatWhen(epochSeconds: number): string {
  const diff = Date.now() / 1000 - epochSeconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return new Date(epochSeconds * 1000).toLocaleString();
}

function formatRange(resource: Resource): string {
  const toTC = (s: number) => {
    const whole = Math.max(0, Math.floor(s));
    const mm = String(Math.floor(whole / 60)).padStart(2, "0");
    const ss = String(whole % 60).padStart(2, "0");
    return `${mm}:${ss}`;
  };
  return `${toTC(resource.start)} → ${toTC(resource.end)}`;
}

export default function History() {
  const [items, setItems] = useState<Resource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await listResources());
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load resources");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleDelete = useCallback(
    async (resource: Resource) => {
      await deleteResource(resource.id);
      await refresh();
    },
    [refresh],
  );

  return (
    <section className="panel wide">
      <div className="panel-head">
        <span>Resources</span>
        <button className="ghost small" onClick={() => void refresh()}>
          Refresh
        </button>
      </div>
      <div className="panel-body">
        <p className="hint">
          Processed clips are kept for 24 hours, then deleted automatically.
        </p>

        {loading && <div className="empty">Loading…</div>}
        {error && <p className="error">{error}</p>}
        {!loading && !error && items.length === 0 && (
          <div className="empty">No clips yet</div>
        )}

        <div className="media-grid">
          {items.map((resource) => (
            <article key={resource.id} className="media-card">
              <div className="media-card-thumb">
                {resource.thumbnail_url ? (
                  <img src={resource.thumbnail_url} alt="" loading="lazy" />
                ) : null}
                <span className="media-card-badge">
                  {resource.mode === "reencode" ? "Precise" : "Lossless"}
                </span>
                {resource.subtitle_url && (
                  <span className="media-card-badge cc">CC</span>
                )}
              </div>
              <div className="media-card-body">
                <div className="media-card-name">{resource.name || "clip"}</div>
                <div className="media-card-sub">{formatRange(resource)}</div>
                <div className="media-card-sub">
                  {formatSize(resource.size)} · {formatWhen(resource.created)}
                  {resource.translated_to
                    ? ` · ${resource.translated_to.toUpperCase()} subs`
                    : ""}
                </div>
              </div>
              <div className="media-card-actions">
                <a className="primary small link" href={resource.download_url}>
                  Download
                </a>
                {resource.subtitle_url && (
                  <a className="ghost small link" href={resource.subtitle_url}>
                    .srt
                  </a>
                )}
                <button className="danger small" onClick={() => void handleDelete(resource)}>
                  Delete
                </button>
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
