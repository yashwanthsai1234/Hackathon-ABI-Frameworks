import { useCallback, useEffect, useMemo, useState } from "react";

// A biller's Approve / Send-back decisions, saved in the browser so the queue
// remembers what they've already worked through (even after a refresh).
// Keyed by run_id, so publishing a fresh run starts with a clean slate.

export type Decision = "approved" | "rejected";
export type DecisionMap = Record<string, Decision>;

const keyFor = (runId: string) => `woundpipe.decisions.${runId}`;

function load(runId: string): DecisionMap {
  try {
    const raw = localStorage.getItem(keyFor(runId));
    return raw ? (JSON.parse(raw) as DecisionMap) : {};
  } catch {
    return {};
  }
}

export interface DecisionsApi {
  decisions: DecisionMap;
  /** Set a decision; choosing the same one again clears it (acts as undo). */
  set: (patientId: string, decision: Decision) => void;
  clear: (patientId: string) => void;
  counts: { approved: number; rejected: number; decided: number };
}

export function useDecisions(runId: string): DecisionsApi {
  const [decisions, setDecisions] = useState<DecisionMap>(() => load(runId));

  // Reload (and reset) when the run changes.
  useEffect(() => {
    setDecisions(load(runId));
  }, [runId]);

  // Persist on every change.
  useEffect(() => {
    try {
      localStorage.setItem(keyFor(runId), JSON.stringify(decisions));
    } catch {
      /* storage unavailable — keep working in-memory */
    }
  }, [runId, decisions]);

  const set = useCallback((patientId: string, decision: Decision) => {
    setDecisions((prev) => {
      if (prev[patientId] === decision) {
        const { [patientId]: _drop, ...rest } = prev; // same choice → undo
        return rest;
      }
      return { ...prev, [patientId]: decision };
    });
  }, []);

  const clear = useCallback((patientId: string) => {
    setDecisions((prev) => {
      if (!(patientId in prev)) return prev;
      const { [patientId]: _drop, ...rest } = prev;
      return rest;
    });
  }, []);

  const counts = useMemo(() => {
    const values = Object.values(decisions);
    const approved = values.filter((d) => d === "approved").length;
    const rejected = values.filter((d) => d === "rejected").length;
    return { approved, rejected, decided: approved + rejected };
  }, [decisions]);

  return { decisions, set, clear, counts };
}
