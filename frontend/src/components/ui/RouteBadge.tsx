import type { Route } from "../../types";
import { ROUTE_META } from "../../lib/route";

// Routing status — icon + text + color together (WCAG: never color alone).
export function RouteBadge({ route, size = "md" }: { route: Route; size?: "sm" | "md" }) {
  const m = ROUTE_META[route];
  const Icon = m.Icon;
  const pad = size === "sm" ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-1 text-xs";
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md font-semibold ring-1 ${m.bg} ${m.text} ${m.ring} ${pad}`}
    >
      <Icon className={size === "sm" ? "h-3 w-3" : "h-3.5 w-3.5"} aria-hidden="true" />
      {m.label}
    </span>
  );
}
