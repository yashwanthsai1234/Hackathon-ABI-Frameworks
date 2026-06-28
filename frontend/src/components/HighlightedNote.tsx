import { useMemo } from "react";
import type { Highlight } from "../types";

// Renders note_text with extracted fields highlighted IN PLACE via char offsets.
// Overlapping/adjacent spans are flattened so we never double-wrap a character.

const FIELD_TONE: Record<string, string> = {
  type: "bg-teal-100 text-teal-800 ring-teal-500/30",
  location: "bg-cyan-100 text-cyan-800 ring-cyan-500/30",
  measure: "bg-emerald-100 text-emerald-800 ring-emerald-500/30",
  measurements: "bg-emerald-100 text-emerald-800 ring-emerald-500/30",
  stage: "bg-amber-100 text-amber-800 ring-amber-500/30",
  drainage: "bg-sky-100 text-sky-800 ring-sky-500/30",
  secondary_wound: "bg-rose-100 text-rose-800 ring-rose-500/30",
};
const FIELD_LABEL: Record<string, string> = {
  type: "Wound type",
  location: "Location",
  measure: "Size",
  measurements: "Size",
  stage: "Stage",
  drainage: "Drainage",
  secondary_wound: "2nd wound",
};

export function fieldTone(field: string) {
  return FIELD_TONE[field] ?? "bg-slate-100 text-slate-700 ring-slate-400/30";
}
export function fieldLabel(field: string) {
  return FIELD_LABEL[field] ?? field;
}

export function HighlightedNote({ text, highlights }: { text: string; highlights: Highlight[] }) {
  const segments = useMemo(() => {
    const hs = [...highlights].filter((h) => h.start < h.end && h.end <= text.length).sort((a, b) => a.start - b.start);
    const out: { text: string; field?: string; value?: string }[] = [];
    let cursor = 0;
    for (const h of hs) {
      if (h.start < cursor) continue; // skip overlap
      if (h.start > cursor) out.push({ text: text.slice(cursor, h.start) });
      out.push({ text: text.slice(h.start, h.end), field: h.field, value: h.value });
      cursor = h.end;
    }
    if (cursor < text.length) out.push({ text: text.slice(cursor) });
    return out;
  }, [text, highlights]);

  return (
    <pre className="whitespace-pre-wrap font-mono text-[13px] leading-relaxed text-ink">
      {segments.map((s, i) =>
        s.field ? (
          <mark
            key={i}
            className={`rounded px-1 py-0.5 font-medium ring-1 ${fieldTone(s.field)}`}
            title={`${fieldLabel(s.field)}: ${s.value}`}
          >
            {s.text}
          </mark>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </pre>
  );
}
