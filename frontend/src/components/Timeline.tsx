interface TimelineProps {
  duration: number;
  thumbnails: string[];
  start: number;
  end: number;
  onChange: (start: number, end: number) => void;
}

export default function Timeline({
  duration,
  thumbnails,
  start,
  end,
  onChange,
}: TimelineProps) {
  const safeDuration = duration > 0 ? duration : 1;

  const style = (value: number) => ({
    left: `${(value / safeDuration) * 100}%`,
  });

  return (
    <div className="timeline">
      <div className="thumbstrip">
        {thumbnails.length === 0 && <div className="thumb-empty">No thumbnails</div>}
        {thumbnails.map((url) => (
          <img key={url} src={url} alt="" loading="lazy" />
        ))}
        <div className="selection" style={{
          left: `${(start / safeDuration) * 100}%`,
          width: `${((end - start) / safeDuration) * 100}%`,
        }} />
        <div className="handle" style={style(start)} />
        <div className="handle" style={style(end)} />
      </div>

      <div className="scrubbers">
        <input
          type="range"
          min={0}
          max={duration}
          step={0.1}
          value={start}
          onChange={(e) => {
            const value = Math.min(Number(e.target.value), end);
            onChange(value, end);
          }}
        />
        <input
          type="range"
          min={0}
          max={duration}
          step={0.1}
          value={end}
          onChange={(e) => {
            const value = Math.max(Number(e.target.value), start);
            onChange(start, value);
          }}
        />
      </div>
    </div>
  );
}
