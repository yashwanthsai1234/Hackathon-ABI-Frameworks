import { useEffect, useState } from "react";
import type { ExportData, Patient, Payer } from "../types";
import { payerName } from "../lib/route";

// The export contract says payer is an object, but some runs emit just the code
// string ("MCB"). Normalize both shapes to a full Payer so the whole UI can rely
// on payer.code / payer.name without rendering "undefined".
function normalizePayer(raw: Payer | string): Payer {
  if (typeof raw === "string") {
    return { code: raw, name: payerName(raw), type: raw.startsWith("MC") ? "Medicare" : "Commercial" };
  }
  return { ...raw, name: raw.name ?? payerName(raw.code) };
}

function normalize(data: ExportData): ExportData {
  return {
    ...data,
    patients: data.patients.map((p): Patient => ({ ...p, payer: normalizePayer(p.payer as Payer | string) })),
  };
}

type State =
  | { status: "loading"; data: null; error: null }
  | { status: "error"; data: null; error: string }
  | { status: "empty"; data: ExportData; error: null }
  | { status: "ready"; data: ExportData; error: null };

// Loads the static export.json. Race-safe: a stale response after unmount/refetch
// is ignored via the `alive` guard. Server state only — no global spinner.
export function useExport(reloadKey = 0): State & { reload: () => void } {
  const [state, setState] = useState<State>({ status: "loading", data: null, error: null });
  const [tick, setTick] = useState(reloadKey);

  useEffect(() => {
    let alive = true;
    setState({ status: "loading", data: null, error: null });
    const url = `${import.meta.env.BASE_URL}export.json`;
    fetch(url)
      .then(async (r) => {
        if (!r.ok) throw new Error(`Could not load run data (HTTP ${r.status}).`);
        return (await r.json()) as ExportData;
      })
      .then((raw) => {
        if (!alive) return; // ignore out-of-order / stale response
        if (!raw?.patients || raw.patients.length === 0) {
          setState({ status: "empty", data: raw, error: null });
        } else {
          setState({ status: "ready", data: normalize(raw), error: null });
        }
      })
      .catch((e: unknown) => {
        if (!alive) return;
        const msg = e instanceof Error ? e.message : "Unexpected error loading run data.";
        setState({ status: "error", data: null, error: msg });
      });
    return () => {
      alive = false;
    };
  }, [tick]);

  return { ...state, reload: () => setTick((t) => t + 1) };
}
