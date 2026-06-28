import { ArrowRight, RefreshCw, CheckCircle2 } from "lucide-react";
import type { ExportData } from "../types";
import { GlassCard, FadeIn, CountUp } from "../components/ui/Primitives";
import { EligibilityFunnel } from "../components/charts/EligibilityFunnel";

export function PipelineFlow({ data }: { data: ExportData }) {
  const { manifest, funnel } = data;

  return (
    <div className="space-y-6">
      <FadeIn>
        <div>
          <h1 className="text-2xl font-bold tracking-tight gradient-text">Pipeline Run</h1>
          <p className="mt-1 text-sm text-ink-soft">
            7 seams · S0→S6 · <span className="tabular">{manifest.total_patients}</span> patients in{" "}
            <span className="tabular">{manifest.duration_s.toFixed(1)}s</span> despite{" "}
            <span className="tabular font-medium text-amber-600">{manifest.rate_limit_hits}</span> rate-limit (429) hits
          </p>
        </div>
      </FadeIn>

      {/* per-stage counters */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
        {manifest.stages.map((s, i) => {
          const isRetry = s.retried > 0;
          return (
            <FadeIn key={s.id} delay={i * 0.04}>
              <GlassCard className="relative h-full p-4">
                <div className="flex items-center justify-between">
                  <span className="tabular rounded-md bg-teal-600/10 px-1.5 py-0.5 text-[11px] font-bold text-teal-700">{s.id}</span>
                  {isRetry && (
                    <span className="inline-flex items-center gap-1 rounded-full bg-gradient-to-r from-amber-400 to-teal-500 px-2 py-0.5 text-[10px] font-semibold text-white">
                      <RefreshCw className="h-2.5 w-2.5" aria-hidden="true" />
                      retry
                    </span>
                  )}
                </div>
                <h3 className="mt-2 text-sm font-semibold text-ink">{s.label}</h3>
                <div className="mt-2 flex items-baseline gap-1">
                  <span className="tabular text-2xl font-bold text-ink">
                    <CountUp value={s.out} />
                  </span>
                  <span className="text-xs text-ink-soft">out</span>
                </div>
                <p className="mt-1 text-[11px] leading-snug text-ink-faint">{s.note}</p>
                {isRetry && (
                  <div className="mt-2">
                    <div className="flex items-center justify-between text-[10px] font-medium text-ink-soft">
                      <span className="text-amber-600">{s.retried} × 429</span>
                      <span className="text-teal-600">recovered</span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-amber-100">
                      <div className="h-full rounded-full bg-gradient-to-r from-amber-400 to-teal-500" style={{ width: "100%" }} />
                    </div>
                  </div>
                )}
              </GlassCard>
            </FadeIn>
          );
        })}
      </div>

      {/* stage ladder */}
      <FadeIn delay={0.1}>
        <GlassCard strong className="p-5">
          <h2 className="mb-4 text-sm font-semibold text-ink">Stage throughput</h2>
          <div className="space-y-2">
            {manifest.stages.map((s, i) => {
              const w = manifest.stages[0].out;
              const widthOut = w ? (s.out / w) * 100 : 0;
              return (
                <div key={s.id} className="flex items-center gap-3">
                  <span className="tabular w-10 text-xs font-bold text-teal-700">{s.id}</span>
                  <span className="w-32 shrink-0 truncate text-xs font-medium text-ink">{s.label}</span>
                  <div className="relative h-6 flex-1 overflow-hidden rounded-lg bg-slate-100">
                    <div
                      className="flex h-full items-center justify-end rounded-lg bg-gradient-to-r from-teal-500 to-cyan-400 pr-2 text-[11px] font-semibold text-white"
                      style={{ width: `${Math.max(widthOut, 6)}%` }}
                    >
                      <span className="tabular">{s.out}</span>
                    </div>
                  </div>
                  {i < manifest.stages.length - 1 && <ArrowRight className="h-3.5 w-3.5 text-ink-faint" aria-hidden="true" />}
                </div>
              );
            })}
          </div>
        </GlassCard>
      </FadeIn>

      {/* funnel + outcome split */}
      <div className="grid gap-6 lg:grid-cols-2">
        <FadeIn delay={0.14}>
          <GlassCard className="p-5">
            <h2 className="mb-2 text-sm font-semibold text-ink">Eligibility funnel</h2>
            <EligibilityFunnel funnel={funnel} />
          </GlassCard>
        </FadeIn>
        <FadeIn delay={0.18}>
          <GlassCard className="flex flex-col p-5">
            <h2 className="mb-3 text-sm font-semibold text-ink">Routing outcome</h2>
            <div className="grid flex-1 grid-cols-3 gap-3">
              <Outcome label="Auto-accept" value={funnel.auto_accept} tone="from-teal-600 to-emerald-500" Icon={CheckCircle2} />
              <Outcome label="Flag" value={funnel.flag_for_review} tone="from-amber-400 to-orange-400" />
              <Outcome label="Reject" value={funnel.reject} tone="from-rose-500 to-rose-600" />
            </div>
            <p className="mt-4 text-xs text-ink-soft">
              Only Medicare Part B patients with an active wound dx, complete L×W×D, cross-source agreement and confidence ≥ 0.80
              are auto-accepted. Everything else is sent to a biller, never silently dropped.
            </p>
          </GlassCard>
        </FadeIn>
      </div>
    </div>
  );
}

function Outcome({ label, value, tone, Icon }: { label: string; value: number; tone: string; Icon?: typeof CheckCircle2 }) {
  return (
    <div className={`flex flex-col items-center justify-center rounded-2xl bg-gradient-to-br ${tone} p-4 text-white shadow-lg`}>
      {Icon && <Icon className="mb-1 h-5 w-5" aria-hidden="true" />}
      <span className="tabular text-3xl font-bold">
        <CountUp value={value} />
      </span>
      <span className="mt-1 text-xs font-medium opacity-90">{label}</span>
    </div>
  );
}
