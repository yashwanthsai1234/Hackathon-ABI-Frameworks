import { Check, CornerUpLeft, RotateCcw } from "lucide-react";
import type { Decision } from "../../data/useDecisions";

// Approve / Send-back control. When a decision is already made it collapses to a
// status chip with an Undo, so the biller can always change their mind.
export function DecisionButtons({
  decision,
  onApprove,
  onReject,
  onClear,
  size = "md",
}: {
  decision: Decision | undefined;
  onApprove: () => void;
  onReject: () => void;
  onClear: () => void;
  size?: "sm" | "md";
}) {
  const pad = size === "sm" ? "whitespace-nowrap px-2.5 py-1 text-xs" : "whitespace-nowrap px-3.5 py-2 text-sm";
  const icon = size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4";

  if (decision) {
    const approved = decision === "approved";
    return (
      <div className="inline-flex items-center gap-2">
        <span
          className={`inline-flex items-center gap-1.5 rounded-md font-semibold ${pad} ${
            approved ? "bg-teal-600 text-white" : "bg-reject text-white"
          }`}
        >
          {approved ? <Check className={icon} aria-hidden="true" /> : <CornerUpLeft className={icon} aria-hidden="true" />}
          {approved ? "Approved" : "Sent back"}
        </span>
        <button
          onClick={onClear}
          aria-label="Undo this decision"
          className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-ink-soft transition-colors hover:bg-surface-2 hover:text-ink focus-visible:outline-2"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" /> Undo
        </button>
      </div>
    );
  }

  return (
    <div className="inline-flex items-center gap-2">
      <button
        onClick={onApprove}
        className={`inline-flex items-center gap-1.5 rounded-md bg-teal-600 font-semibold text-white shadow-sm transition-colors hover:bg-teal-700 focus-visible:outline-2 ${pad}`}
      >
        <Check className={icon} aria-hidden="true" /> Approve
      </button>
      <button
        onClick={onReject}
        className={`inline-flex items-center gap-1.5 rounded-md border border-border bg-surface font-semibold text-reject transition-colors hover:bg-surface-2 focus-visible:outline-2 ${pad}`}
      >
        <CornerUpLeft className={icon} aria-hidden="true" /> Send back
      </button>
    </div>
  );
}
