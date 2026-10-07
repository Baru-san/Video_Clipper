interface TimelineProps {
  duration: number;
  thumbnails: string[];
  start: number;
  end: number;
  onChange: (start: number, end: number) => void;
}

function timecode(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) seconds = 0;
  const whole = Math.floor(seconds);
  const mm = String(Math.floor(whole / 60)).padStart(2, "0");
  const ss = String(whole % 60).padStart(2, "0");
  const ff = String(Math.floor((seconds - whole) * 25)).padStart(2, "0");
  return `${mm}:${ss}:${ff}`;
}

export default function Timeline({
  duration,
  thumbnails,
  start,
  end,
  onChange,
}: TimelineProps) {
  const safeDuration = duration > 0 ? duration : 1;
  const pct = (value: number) => `${(value / safeDuration) * 100}%`;

  const ticks = Array.from({ length: 11 }, (_, i) => {
    const value = (safeDuration * i) / 10;
    return { value, left: pct(value) };
  });

  const selectionStyle = {
    left: pct(start),
    width: pct(Math.max(end - start, 0)),
  };

  return (
    <div className="timeline">
      <div className="ruler">
        {ticks.map((tick) => (
          <div key={tick.left} className="tick" style={{ left: tick.left }}>
            <span>{timecode(tick.value)}</span>
          </div>
        ))}
      </div>

      <div className="track">
        <div className="track-clip" style={selectionStyle} />
        <div className="thumbstrip">
          {thumbnails.length === 0 ? (
            <div className="thumb-empty">No thumbnails</div>
          ) : (
            thumbnails.map((url) => <img key={url} src={url} alt="" loading="lazy" />)
          )}
        </div>
        <div className="track-selection" style={selectionStyle} />
        <div className="handle in" style={{ left: pct(start) }} />
        <div className="handle out" style={{ left: pct(end) }} />
      </div>

      <div className="scrubbers">
        <label>
          IN
          <input
            type="range"
            min={0}
            max={safeDuration}
            step={0.02}
            value={start}
            onChange={(e) => onChange(Math.min(Number(e.target.value), end), end)}
          />
        </label>
        <label>
          OUT
          <input
            type="range"
            min={0}
            max={safeDuration}
            step={0.02}
            value={end}
            onChange={(e) => onChange(start, Math.max(Number(e.target.value), start))}
          />
        </label>
      </div>
    </div>
  );
}
