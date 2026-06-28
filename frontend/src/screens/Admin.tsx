import { Activity, Users, ShieldCheck, CheckCircle2, AlertTriangle, Ban, RefreshCw } from "lucide-react";
import type { ExportData } from "../types";
import { GlassCard, FadeIn, CountUp } from "../components/ui/Primitives";
import { PipelineGraph } from "../components/flow/PipelineGraph";
import { EligibilityFunnel } from "../components/charts/EligibilityFunnel";
import { PayerSankey } from "../components/charts/PayerSankey";

// One coherent system dashboard for the technical team: run summary → pipeline
// health → how patients flow to a decision. (Replaces the old Command Center +
// Pipeline Run screens, which duplicated the routing-outcome and funnel blocks.)
export function Admin({ data }: { data: ExportData }) {
  const { manifest, funnel } = data;
  const extracted = manifest.stages.find((s) => s.id === "S4")?.out ?? data.patients.length;
  const mcbShare = funnel.total ? (funnel.mcb_active / funnel.total) * 100 : 0;
  const maxOut = manifest.stages[0]?.out || 1;

  return (
    <div className="space-y-6">
      <FadeIn>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold tracking-tight gradient-text">System dashboard</h1>
            <p className="mt-1 text-sm text-ink-soft">
              Pipeline health for the technical team · run{" "}
              <span className="tabular font-medium text-ink">{manifest.run_id}</span> ·{" "}
              {new Date(manifest.generated_at).toLocaleString()} ·{" "}
              <span className="tabular">{manifest.duration_s.toFixed(0)}s</span>
            </p>
          </div>
          <span className="inline-flex items-center gap-2 rounded-md border border-border bg-surface px-3 py-1 text-xs font-medium text-ink-soft">
            <span className="h-1.5 w-1.5 rounded-full bg-teal-500" />
            Snapshot published
          </span>
        </div>
      </FadeIn>

      {/* headline metrics */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Patients" value={funnel.total} Icon={Users} delay={0.02} />
        <Kpi label="Extracted" value={extracted} Icon={Activity} delay={0.05} />
        <Kpi label="MCB eligible" value={mcbShare} suffix="%" Icon={ShieldCheck} delay={0.08} />
        <Kpi label="Ready to bill" value={funnel.auto_accept} Icon={CheckCircle2} accent="text-teal-700" delay={0.11} />
        <Kpi label="Needs review" value={funnel.flag_for_review} Icon={AlertTriangle} accent="text-amber-700" delay={0.14} />
        <Kpi label="Not billable" value={funnel.reject} Icon={Ban} accent="text-rose-700" delay={0.17} />
      </div>

      {/* pipeline: flow diagram + per-stage throughput, one place */}
      <FadeIn delay={0.12}>
        <GlassCard strong className="p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-ink">Pipeline run</h2>
            <span className="hidden text-xs text-ink-soft sm:inline">API → fetch → extract → route → publish</span>
          </div>
          <PipelineGraph manifest={manifest} />
          <div className="mt-4 space-y-1.5 border-t border-border pt-4">
            {manifest.stages.map((s) => {
              const w = maxOut ? (s.out / maxOut) * 100 : 0;
              return (
                <div key={s.id} className="flex items-center gap-3">
                  <span className="tabular w-7 shrink-0 text-xs font-semibold text-ink-faint">{s.id}</span>
                  <span className="w-28 shrink-0 truncate text-xs font-medium text-ink sm:w-36">{s.label}</span>
                  <div className="relative h-5 flex-1 overflow-hidden rounded-md bg-surface-2">
                    <div
                      className="flex h-full items-center justify-end rounded-md bg-ink pr-2 text-[11px] font-semibold text-white"
                      style={{ width: `${Math.max(w, 6)}%` }}
                    >
                      <span className="tabular">{s.out}</span>
                    </div>
                  </div>
                  {s.retried > 0 && (
                    <span className="inline-flex shrink-0 items-center gap-1 text-[10px] font-medium text-amber-700">
                      <RefreshCw className="h-3 w-3" aria-hidden="true" />
                      <span className="tabular">{s.retried}× 429</span>
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </GlassCard>
      </FadeIn>

      {/* how patients flow to a decision */}
      <div className="grid gap-6 lg:grid-cols-2">
        <FadeIn delay={0.16}>
          <GlassCard className="p-5">
            <div className="mb-2">
              <h2 className="text-sm font-semibold text-ink">Eligibility funnel</h2>
              <p className="text-xs text-ink-soft">
                <span className="tabular">{funnel.total}</span> patients → MCB → active wound → measurements → ready to bill
              </p>
            </div>
            <EligibilityFunnel funnel={funnel} />
          </GlassCard>
        </FadeIn>
        <FadeIn delay={0.2}>
          <GlassCard className="p-5">
            <div className="mb-2">
              <h2 className="text-sm font-semibold text-ink">Payer → eligibility → route</h2>
              <p className="text-xs text-ink-soft">band width ∝ patient count</p>
            </div>
            <PayerSankey edges={funnel.sankey} />
          </GlassCard>
        </FadeIn>
      </div>
    </div>
  );
}

function Kpi({
  label,
  value,
  suffix,
  Icon,
  accent = "text-ink",
  delay,
}: {
  label: string;
  value: number;
  suffix?: string;
  Icon: typeof Users;
  accent?: string;
  delay: number;
}) {
  return (
    <FadeIn delay={delay}>
      <GlassCard className="p-4">
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-ink-faint">{label}</span>
          <Icon className={`h-4 w-4 ${accent}`} aria-hidden="true" />
        </div>
        <div className="mt-2 flex items-baseline gap-0.5">
          <span className={`tabular text-2xl font-semibold tracking-tight ${accent}`}>
            <CountUp value={value} decimals={suffix === "%" ? 0 : 0} />
          </span>
          {suffix && <span className="text-sm font-semibold text-ink-soft">{suffix}</span>}
        </div>
      </GlassCard>
    </FadeIn>
  );
}
