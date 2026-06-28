import { Wrench } from "lucide-react";
import type { ExportData } from "../types";
import { GlassCard, FadeIn } from "../components/ui/Primitives";
import { CommandCenter } from "./CommandCenter";
import { PipelineFlow } from "./PipelineFlow";

// Technical dashboards (pipeline, charts, run internals) live here, away from the
// billing worklist. Intended for the engineering / operations team.
export function Admin({ data }: { data: ExportData }) {
  return (
    <div className="space-y-6">
      <FadeIn>
        <GlassCard className="flex items-start gap-3 p-4">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-ink text-white">
            <Wrench className="h-4 w-4" aria-hidden="true" />
          </span>
          <div>
            <h1 className="text-sm font-semibold text-ink">System internals</h1>
            <p className="mt-0.5 text-xs text-ink-soft">
              Pipeline health and processing stats for the technical team. Billing staff don't need anything here.
            </p>
          </div>
        </GlassCard>
      </FadeIn>

      <CommandCenter data={data} />
      <PipelineFlow data={data} />
    </div>
  );
}
