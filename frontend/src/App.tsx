import { useEffect, useState } from "react";
import { ClipboardList, LayoutDashboard, Wrench, Activity } from "lucide-react";
import { useExport } from "./data/useExport";
import { useDecisions } from "./data/useDecisions";
import type { Patient } from "./types";
import { ErrorState, EmptyState, GlassCard } from "./components/ui/Primitives";
import { Overview } from "./screens/Overview";
import { ReviewQueue, TriageTableSkeleton } from "./screens/TriageTable";
import { PatientDetail } from "./screens/PatientDetail";
import { Admin } from "./screens/Admin";

type Tab = "triage" | "overview" | "admin";
const TABS: { id: Tab; label: string; Icon: typeof LayoutDashboard }[] = [
  { id: "triage", label: "Claims to review", Icon: ClipboardList },
  { id: "overview", label: "Overview", Icon: LayoutDashboard },
  { id: "admin", label: "Admin", Icon: Wrench },
];

export default function App() {
  const state = useExport();
  const runId = state.status === "ready" ? state.data.run_id : "";
  const { decisions, set, clear, counts } = useDecisions(runId);
  const [tab, setTab] = useState<Tab>("triage");
  const [selected, setSelected] = useState<Patient | null>(null);

  // hash-based deep links so a tab is shareable/restorable without a router dep.
  useEffect(() => {
    const fromHash = () => {
      const h = window.location.hash.replace("#", "") as Tab;
      if (TABS.some((t) => t.id === h)) setTab(h);
    };
    fromHash();
    window.addEventListener("hashchange", fromHash);
    return () => window.removeEventListener("hashchange", fromHash);
  }, []);
  const go = (t: Tab) => {
    setTab(t);
    window.location.hash = t;
  };

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-border bg-surface/85 backdrop-blur-md">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-5 py-3">
          <div className="flex items-center gap-2.5">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-ink text-white">
              <Activity className="h-[18px] w-[18px]" aria-hidden="true" />
            </span>
            <div className="flex items-baseline gap-2">
              <span className="text-[15px] font-bold tracking-tight text-ink">woundpipe</span>
              <span className="hidden text-xs font-medium text-ink-faint sm:inline">Billing review</span>
            </div>
          </div>
          <nav className="flex items-center gap-0.5 rounded-lg border border-border bg-surface-2 p-0.5" role="tablist" aria-label="Views">
            {TABS.map((t) => {
              const active = tab === t.id;
              return (
                <button
                  key={t.id}
                  role="tab"
                  aria-selected={active}
                  onClick={() => go(t.id)}
                  className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline-2 ${
                    active ? "bg-ink text-white shadow-sm" : "text-ink-soft hover:bg-surface hover:text-ink"
                  }`}
                >
                  <t.Icon className="h-4 w-4" aria-hidden="true" />
                  <span className="hidden sm:inline">{t.label}</span>
                </button>
              );
            })}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-5 py-6">
        {state.status === "loading" && <TriageTableSkeleton />}

        {state.status === "error" && (
          <GlassCard strong className="mx-auto max-w-lg">
            <ErrorState message={state.error} onRetry={state.reload} />
          </GlassCard>
        )}

        {state.status === "empty" && (
          <GlassCard strong className="mx-auto max-w-lg">
            <EmptyState
              title="No claims in this batch"
              hint="The latest batch published successfully but contains zero claims. Re-run it to populate the queue."
            />
          </GlassCard>
        )}

        {state.status === "ready" && (
          <>
            {tab === "triage" && (
              <ReviewQueue patients={state.data.patients} onSelect={setSelected} decisions={decisions} onDecide={set} onClear={clear} />
            )}
            {tab === "overview" && <Overview data={state.data} decided={counts.decided} onStartReviewing={() => go("triage")} />}
            {tab === "admin" && <Admin data={state.data} />}
          </>
        )}
      </main>

      <PatientDetail
        patient={selected}
        onClose={() => setSelected(null)}
        decision={selected ? decisions[selected.patient_id] : undefined}
        onDecide={set}
        onClear={clear}
      />
    </div>
  );
}
