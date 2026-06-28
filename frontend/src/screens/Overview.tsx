import { AlertTriangle, CheckCircle2, Ban, ClipboardCheck, ArrowRight } from "lucide-react";
import type { ExportData } from "../types";
import { GlassCard, CountUp, FadeIn } from "../components/ui/Primitives";
import { ROUTE_META } from "../lib/route";

// Plain summary for billing staff — counts and what to do next. No engineering.
export function Overview({
  data,
  decided,
  onStartReviewing,
}: {
  data: ExportData;
  decided: number;
  onStartReviewing: () => void;
}) {
  const { funnel } = data;
  const total = funnel.total;

  return (
    <div className="space-y-6">
      <FadeIn>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">
              <span className="gradient-text">Today's billing</span>
            </h1>
            <p className="mt-1 text-sm text-ink-soft">
              <span className="tabular font-medium text-ink">{total}</span> claims came in from the latest patient charts. Here's where they stand.
            </p>
          </div>
          <button
            onClick={onStartReviewing}
            className="inline-flex items-center gap-2 rounded-lg bg-ink px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-ink/90 focus-visible:outline-2"
          >
            Start reviewing <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </FadeIn>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Needs your review"
          value={funnel.flag_for_review}
          help="A detail is missing or unclear"
          Icon={AlertTriangle}
          tone="bg-amber-500"
          delay={0.02}
        />
        <StatCard
          label="Ready to bill"
          value={funnel.auto_accept}
          help="Meets every requirement"
          Icon={CheckCircle2}
          tone="bg-teal-600"
          delay={0.06}
        />
        <StatCard
          label="Not billable"
          value={funnel.reject}
          help="Fails a required rule"
          Icon={Ban}
          tone="bg-rose-500"
          delay={0.1}
        />
        <StatCard
          label="Decided by you"
          value={decided}
          help="Approved or sent back today"
          Icon={ClipboardCheck}
          tone="gradient-teal"
          delay={0.14}
        />
      </div>

      <FadeIn delay={0.18}>
        <GlassCard strong className="p-5">
          <h2 className="mb-3 text-sm font-semibold text-ink">What each status means</h2>
          <div className="grid gap-3 sm:grid-cols-3">
            {(["flag_for_review", "auto_accept", "reject"] as const).map((r) => {
              const m = ROUTE_META[r];
              return (
                <div key={r} className="flex items-start gap-2.5 rounded-lg border border-border bg-surface-2 p-3">
                  <span className={`mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-md text-white ${m.dot}`}>
                    <m.Icon className="h-4 w-4" aria-hidden="true" />
                  </span>
                  <div>
                    <p className="text-sm font-semibold text-ink">{m.label}</p>
                    <p className="mt-0.5 text-xs leading-snug text-ink-soft">{m.desc}</p>
                  </div>
                </div>
              );
            })}
          </div>
          <p className="mt-4 text-xs text-ink-soft">
            Nothing is ever billed automatically — every claim waits for a person to approve it or send it back.
          </p>
        </GlassCard>
      </FadeIn>
    </div>
  );
}

function StatCard({
  label,
  value,
  help,
  Icon,
  tone,
  delay,
}: {
  label: string;
  value: number;
  help: string;
  Icon: typeof AlertTriangle;
  tone: string;
  delay: number;
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
        <div className="tabular mt-3 text-[40px] font-semibold leading-none tracking-tight text-ink">
          <CountUp value={value} />
        </div>
        <p className="mt-2 text-xs text-ink-soft">{help}</p>
      </GlassCard>
    </FadeIn>
  );
}
