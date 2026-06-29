import { useEffect, useState } from "react";
import type { ClaimLine } from "../types";

// Lazily fetch a wound's AI summary from the `woundpipe serve` backend when its row
// is opened. Generated on first request, then cached server-side AND in this module
// (so switching between a patient's wounds / reopening doesn't refetch or regenerate).

type Status = "idle" | "loading" | "ready" | "error";
const cache = new Map<string, string>();

export function useWoundSummary(line: ClaimLine | null): {
  status: Status;
  summary: string | null;
  retry: () => void;
} {
  const [status, setStatus] = useState<Status>("idle");
  const [summary, setSummary] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const lineId = line?.lineId ?? null;
  const woundKey = line?.wound.wound_key ?? null;
  const patientId = line?.patient.patient_id ?? null;

  useEffect(() => {
    if (!lineId || !patientId) {
      setStatus("idle");
      setSummary(null);
      return;
    }
    // wound-less synthetic line (a "no wound" reject) — nothing to summarize.
    if (!woundKey) {
      setStatus("ready");
      setSummary(null);
      return;
    }
    const hit = cache.get(lineId);
    if (hit) {
      setStatus("ready");
      setSummary(hit);
      return;
    }
    let alive = true;
    setStatus("loading");
    setSummary(null);
    const url = `/api/summary?patient_id=${encodeURIComponent(patientId)}&wound_key=${encodeURIComponent(woundKey)}`;
    fetch(url)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return (await r.json()) as { ai_summary: string };
      })
      .then((d) => {
        if (!alive) return;
        cache.set(lineId, d.ai_summary);
        setStatus("ready");
        setSummary(d.ai_summary);
      })
      .catch(() => {
        if (alive) setStatus("error");
      });
    return () => {
      alive = false;
    };
  }, [lineId, patientId, woundKey, tick]);

  return { status, summary, retry: () => setTick((t) => t + 1) };
}
