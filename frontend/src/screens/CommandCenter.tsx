import { Activity, Users, ShieldCheck, CheckCircle2, AlertTriangle, Ban } from "lucide-react";
import type { ExportData } from "../types";
import { GlassCard, CountUp, FadeIn } from "../components/ui/Primitives";
import { PipelineGraph } from "../components/flow/PipelineGraph";
import { PayerSankey } from "../components/charts/PayerSankey";

function Kpi({
  label,
  value,
  suffix,
  Icon,
  tone,
  delay,
  decimals = 0,
}: {
  label: string;
  value: number;
  suffix?: string;
  Icon: typeof Users;
  tone: string;
  delay: number;
  decimals?: number;
}) {
  return (
    <FadeIn delay={delay}>
      <GlassCard className="p-5">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold uppercase tracking-wider text-ink-faint">{label}</span>
          <span className={`grid h-8 w-8 place-items-center rounded-md text-white ${tone}`}>
            <Icon className="h-4 w-4" aria-hidden="true" />
          </span>
        </div>
        <div className="mt-3 flex items-baseline gap-1">
          <span className="tabular text-3xl font-semibold tracking-tight text-ink">
            <CountUp value={value} decimals={decimals} />
          </span>
          {suffix && <span className="text-lg font-semibold text-ink-soft">{suffix}</span>}
        </div>
      </GlassCard>
    </FadeIn>
  );
}

export function CommandCenter({ data }: { data: ExportData }) {
  const { manifest, funnel } = data;
  const extracted = manifest.stages.find((s) => s.id === "S4")?.out ?? data.patients.length;
  const mcbShare = funnel.total ? funnel.mcb_active / funnel.total : 0;

  return (
    <div className="space-y-6">
      <FadeIn>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">
              <span className="gradient-text">Command Center</span>
            </h1>
            <p className="mt-1 text-sm text-ink-soft">
              Run <span className="tabular font-medium text-ink">{manifest.run_id}</span> · {new Date(manifest.generated_at).toLocaleString()} ·{" "}
              <span className="tabular">{manifest.duration_s.toFixed(0)}s</span>
            </p>
          </div>
          <span className="inline-flex items-center gap-2 rounded-md border border-border bg-surface px-3 py-1 text-xs font-medium text-ink-soft">
            <span className="h-1.5 w-1.5 rounded-full bg-teal-500" />
            Snapshot published
          </span>
        </div>
      </FadeIn>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6">
        <Kpi label="Patients" value={funnel.total} Icon={Users} tone="gradient-teal" delay={0.02} />
        <Kpi label="Extracted" value={extracted} Icon={Activity} tone="gradient-teal" delay={0.06} />
        <Kpi label="MCB eligible" value={mcbShare * 100} suffix="%" Icon={ShieldCheck} tone="gradient-teal" delay={0.1} />
        <Kpi label="Auto-accept" value={funnel.auto_accept} Icon={CheckCircle2} tone="bg-teal-600" delay={0.14} />
        <Kpi label="Flagged" value={funnel.flag_for_review} Icon={AlertTriangle} tone="bg-amber-500" delay={0.18} />
        <Kpi label="Rejected" value={funnel.reject} Icon={Ban} tone="bg-rose-500" delay={0.22} />
      </div>

      <FadeIn delay={0.16}>
        <GlassCard strong className="p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-ink">Live pipeline</h2>
            <span className="text-xs text-ink-soft">API → fetch/retry → sniff → extract → SQLite → route</span>
          </div>
          <PipelineGraph manifest={manifest} />
          <p className="mt-2 text-center text-xs text-ink-faint">
            Animated edges show packet flow; the amber leg carries the{" "}
            <span className="tabular">{manifest.rate_limit_hits}</span> rate-limited (429) retries.
          </p>
        </GlassCard>
      </FadeIn>

      <FadeIn delay={0.2}>
        <GlassCard className="p-5">
          <div className="mb-1 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-ink">Payer → eligibility → route</h2>
            <span className="text-xs text-ink-soft">band width ∝ patient count</span>
          </div>
          <PayerSankey edges={funnel.sankey} />
        </GlassCard>
      </FadeIn>
    </div>
  );
}
