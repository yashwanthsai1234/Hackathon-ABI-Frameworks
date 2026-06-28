import { pct } from "../../lib/route";

// Radial confidence gauge — maps cross-source corroboration to a teal arc.
// Includes an accessible text label so it never relies on the visual alone.
export function ConfidenceGauge({ value, size = 44 }: { value: number; size?: number }) {
  const r = size / 2 - 4;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(1, value));
  const dash = c * v;
  const tone = v >= 0.8 ? "var(--color-teal-500)" : v >= 0.6 ? "var(--color-flag)" : "var(--color-reject)";
  const id = `g${Math.round(value * 1000)}`;
  return (
    <div
      className="relative inline-grid place-items-center"
      style={{ width: size, height: size }}
      role="img"
      aria-label={`Confidence ${pct(v)}`}
    >
      <svg width={size} height={size} className="-rotate-90">
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor={tone} />
            <stop offset="100%" stopColor="var(--color-cyan-400)" />
          </linearGradient>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgb(15 23 42 / 0.08)" strokeWidth={4} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={`url(#${id})`}
          strokeWidth={4}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${c}`}
        />
      </svg>
      <span className="tabular absolute text-[11px] font-semibold text-ink">{Math.round(v * 100)}</span>
    </div>
  );
}

// Compact segmented variant for dense rows.
export function ConfidenceBar({ value }: { value: number }) {
  const v = Math.max(0, Math.min(1, value));
  const seg = Math.round(v * 5);
  const tone = v >= 0.8 ? "bg-teal-500" : v >= 0.6 ? "bg-amber-500" : "bg-rose-500";
  return (
    <div className="flex items-center gap-2" role="img" aria-label={`Confidence ${pct(v)}`}>
      <div className="flex gap-0.5">
        {Array.from({ length: 5 }).map((_, i) => (
          <span key={i} className={`h-3.5 w-1.5 rounded-sm ${i < seg ? tone : "bg-slate-200"}`} />
        ))}
      </div>
      <span className="tabular text-xs font-semibold text-ink-soft">{pct(v)}</span>
    </div>
  );
}
