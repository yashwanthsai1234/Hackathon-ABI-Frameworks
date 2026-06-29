import { useEffect, useRef } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { X, Check, AlertTriangle, XCircle, FileText, Users, AlertCircle, Sparkles, Loader2 } from "lucide-react";
import type { ClaimLine, CheckStatus } from "../types";
import type { Decision } from "../data/useDecisions";
import { useWoundSummary } from "../data/useWoundSummary";
import { RouteBadge } from "../components/ui/RouteBadge";
import { DecisionButtons } from "../components/ui/DecisionButtons";
import { HighlightedNote, fieldTone, fieldLabel } from "../components/HighlightedNote";
import { fmtMeasure, matchStrength, ROUTE_META } from "../lib/route";

// Plain-English wording for the requirement rows (data labels are clinical/jargon).
const CHECK_LABELS: Record<string, string> = {
  "Active Medicare Part B": "Covered by Medicare Part B",
  "Active wound": "Active wound on file",
  "Corroborating ICD-10 wound dx": "Matching wound diagnosis on record",
  "Measurements L×W×D": "Wound size recorded (length, width, depth)",
};
const CHECK_STATUS_WORD: Record<CheckStatus, string> = { pass: "Met", warn: "Needs a look", fail: "Missing" };
const CHECK_ICON: Record<CheckStatus, { Icon: typeof Check; cls: string }> = {
  pass: { Icon: Check, cls: "bg-teal-100 text-teal-700" },
  warn: { Icon: AlertTriangle, cls: "bg-amber-100 text-amber-700" },
  fail: { Icon: XCircle, cls: "bg-rose-100 text-rose-700" },
};

export function PatientDetail({
  line,
  lines,
  onSelect,
  onClose,
  decision,
  onDecide,
  onClear,
}: {
  line: ClaimLine | null;
  lines: ClaimLine[];
  onSelect: (l: ClaimLine) => void;
  onClose: () => void;
  decision: Decision | undefined;
  onDecide: (lineId: string, decision: Decision) => void;
  onClear: (lineId: string) => void;
}) {
  const reduce = useReducedMotion();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  // focus management: move focus into the panel on open, restore on close, trap Esc.
  useEffect(() => {
    if (!line) return;
    const prev = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      prev?.focus();
    };
  }, [line, onClose]);

  const p = line?.patient ?? null;
  const w = line?.wound ?? null;
  const ms = w ? matchStrength(w.confidence) : null;
  const conflicts = w ? w.evidence_graph.edges.filter((e) => e.relation === "conflict").length : 0;
  const agree = w?.evidence_graph.agreeing_sources ?? 0;
  const siblings = line && p ? lines.filter((l) => l.patient.patient_id === p.patient_id) : [];
  const aiSummary = useWoundSummary(line); // lazily generated on open via the serve backend

  return (
    <AnimatePresence>
      {line && p && w && ms && (
        <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label={`Claim for ${p.name}`}>
          <motion.div
            className="absolute inset-0 bg-ink/25"
            initial={reduce ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />
          <motion.div
            ref={panelRef}
            className="relative flex h-full w-full max-w-2xl flex-col border-l border-border bg-surface shadow-2xl"
            initial={reduce ? false : { x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "tween", duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
          >
            <div className="flex-1 overflow-y-auto p-6">
              <header className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-xl font-bold text-ink">{p.name}</h2>
                    <span className="tabular text-sm text-ink-soft">{p.patient_id}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <RouteBadge route={w.route} />
                    <span className="rounded-md bg-teal-50 px-2 py-0.5 text-xs font-semibold text-teal-700 ring-1 ring-teal-600/20">
                      {p.payer.name}
                    </span>
                    <span className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-0.5 text-xs text-ink-soft">
                      <FileText className="h-3 w-3" aria-hidden="true" /> {w.wound.format}
                    </span>
                  </div>
                </div>
                <button
                  ref={closeRef}
                  onClick={onClose}
                  aria-label="Close"
                  className="grid h-9 w-9 place-items-center rounded-full bg-white/70 text-ink-soft ring-1 ring-slate-200 transition-colors hover:bg-white hover:text-ink focus-visible:outline-2"
                >
                  <X className="h-5 w-5" aria-hidden="true" />
                </button>
              </header>

              {/* wound switcher — this patient has more than one billable wound */}
              {siblings.length > 1 && (
                <section className="mt-4">
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-ink-faint">
                    This patient has {siblings.length} wounds — pick one
                  </span>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {siblings.map((s) => {
                      const active = s.lineId === line.lineId;
                      const loc = s.wound.wound.location ?? "Location not noted";
                      return (
                        <button
                          key={s.lineId}
                          onClick={() => onSelect(s)}
                          aria-pressed={active}
                          className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-2 ${
                            active ? "bg-ink text-white" : "border border-border bg-surface text-ink-soft hover:text-ink"
                          }`}
                        >
                          <span className={`h-1.5 w-1.5 rounded-full ${ROUTE_META[s.wound.route].dot}`} />
                          {loc}
                        </button>
                      );
                    })}
                  </div>
                </section>
              )}

              {/* AI summary — the human-readable overview, shown first */}
              <section className="mt-5 rounded-xl border border-ink/15 bg-surface p-4 shadow-glass">
                <div className="mb-1.5 flex items-center gap-1.5">
                  <span className="grid h-5 w-5 place-items-center rounded-md bg-ink text-white">
                    <Sparkles className="h-3 w-3" aria-hidden="true" />
                  </span>
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-ink-soft">AI summary</span>
                </div>
                {(aiSummary.status === "loading" || aiSummary.status === "idle") && (
                  <p className="flex items-center gap-2 text-sm italic text-ink-faint">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> Generating summary…
                  </p>
                )}
                {aiSummary.status === "ready" && aiSummary.summary && (
                  <p className="text-sm leading-relaxed text-ink">{aiSummary.summary}</p>
                )}
                {aiSummary.status === "ready" && !aiSummary.summary && (
                  <p className="text-sm italic text-ink-faint">No summary for this line.</p>
                )}
                {aiSummary.status === "error" && (
                  <p className="text-sm text-ink-soft">
                    Couldn't generate a summary (is the summary service running?).{" "}
                    <button onClick={aiSummary.retry} className="font-medium text-ink underline focus-visible:outline-2">
                      Retry
                    </button>
                  </p>
                )}
              </section>

              {/* recommendation, in plain words */}
              <section className="mt-4 rounded-xl border border-border bg-surface-2 p-4">
                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-ink-faint">What the system suggests</span>
                  <span className={`rounded px-2 py-0.5 text-[11px] font-semibold ring-1 ${ms.tone} ${ms.ring}`}>{ms.label}</span>
                </div>
                <p className="mt-2 text-base font-semibold text-ink">{ROUTE_META[w.route].label}</p>
                <p className="mt-1 text-sm leading-snug text-ink-soft">{w.reason}</p>
              </section>

              {/* billing requirements */}
              <section className="mt-5">
                <h3 className="mb-2 text-sm font-semibold text-ink">Billing requirements</h3>
                <div className="grid gap-2 sm:grid-cols-2">
                  {w.eligibility_checks.map((c, i) => {
                    const { Icon, cls } = CHECK_ICON[c.status];
                    return (
                      <div key={i} className="glass flex items-start gap-2.5 rounded-xl p-3">
                        <span className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full ${cls}`}>
                          <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                        </span>
                        <div>
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className="text-xs font-semibold text-ink">{CHECK_LABELS[c.label] ?? c.label}</span>
                            <span
                              className={`rounded px-1.5 text-[10px] font-medium ${
                                c.status === "pass" ? "text-teal-700" : c.status === "warn" ? "text-amber-700" : "text-rose-700"
                              }`}
                            >
                              {CHECK_STATUS_WORD[c.status]}
                            </span>
                          </div>
                          {(c.code || c.detail) && (
                            <p className="mt-1 text-[11px] leading-snug text-ink-soft">
                              {c.code && <span className="tabular font-medium text-ink">{c.code} · </span>}
                              {c.detail}
                            </p>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </section>

              {/* wound details */}
              <section className="mt-5">
                <h3 className="mb-2 text-sm font-semibold text-ink">Wound details</h3>
                <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <Field label="Type" value={w.wound.type ? w.wound.type.replace(/_/g, " ") : null} />
                  <Field label="Location" value={w.wound.location} />
                  <Field label="Stage" value={w.wound.stage} />
                  <Field label="Size (L×W×D)" value={fmtMeasure(w.wound.L, w.wound.W, w.wound.D)} mono />
                </dl>
              </section>

              {/* where the details came from */}
              <section className="mt-5">
                {conflicts > 0 ? (
                  <div className="flex items-center gap-2 rounded-xl bg-amber-50 px-3 py-2.5 text-xs text-amber-800 ring-1 ring-amber-500/20">
                    <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
                    Some records in the chart disagree on these details — worth a closer look before deciding.
                  </div>
                ) : (
                  <div className="flex items-center gap-2 rounded-xl bg-teal-50 px-3 py-2.5 text-xs text-teal-800 ring-1 ring-teal-600/20">
                    <Users className="h-4 w-4 shrink-0" aria-hidden="true" />
                    <span className="tabular font-semibold">{agree}</span> record{agree === 1 ? "" : "s"} in the chart agree on these details.
                  </div>
                )}
              </section>

              {/* original note with highlights */}
              <section className="mt-5">
                <div className="mb-2 flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-ink">Original note — matched details highlighted</h3>
                </div>
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {Array.from(new Set(w.highlights.map((h) => h.field))).map((f) => (
                    <span key={f} className={`rounded px-1.5 py-0.5 text-[10px] font-medium ring-1 ${fieldTone(f)}`}>
                      {fieldLabel(f)}
                    </span>
                  ))}
                </div>
                <div className="glass rounded-xl p-4">
                  <HighlightedNote text={w.note_text} highlights={w.highlights} />
                </div>
              </section>
            </div>

            {/* sticky decision bar */}
            <footer className="flex items-center justify-between gap-3 border-t border-border bg-surface px-6 py-4">
              <span className="text-sm font-medium text-ink-soft">
                {decision === "approved"
                  ? "You approved this wound."
                  : decision === "rejected"
                    ? "You sent this wound back."
                    : "Approve to bill, or send it back."}
              </span>
              <DecisionButtons
                decision={decision}
                onApprove={() => onDecide(line.lineId, "approved")}
                onReject={() => onDecide(line.lineId, "rejected")}
                onClear={() => onClear(line.lineId)}
              />
            </footer>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}

function Field({ label, value, mono = false }: { label: string; value: string | null; mono?: boolean }) {
  return (
    <div className="glass rounded-xl p-2.5">
      <dt className="text-[10px] font-medium uppercase tracking-wide text-ink-faint">{label}</dt>
      <dd className={`mt-0.5 text-sm font-medium capitalize text-ink ${mono ? "tabular" : ""}`}>
        {value ?? <span className="text-ink-faint">—</span>}
      </dd>
    </div>
  );
}
