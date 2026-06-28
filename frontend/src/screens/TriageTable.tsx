import { useMemo, useState } from "react";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
  type ColumnFiltersState,
} from "@tanstack/react-table";
import { Search, ArrowUpDown, ArrowUp, ArrowDown } from "lucide-react";
import type { Patient, Route } from "../types";
import type { DecisionMap, Decision } from "../data/useDecisions";
import { GlassCard, FadeIn, EmptyState } from "../components/ui/Primitives";
import { RouteBadge } from "../components/ui/RouteBadge";
import { DecisionButtons } from "../components/ui/DecisionButtons";
import { ROUTE_META, ROUTE_ORDER, fmtMeasure, matchStrength } from "../lib/route";

const col = createColumnHelper<Patient>();

export function ReviewQueue({
  patients,
  onSelect,
  decisions,
  onDecide,
  onClear,
}: {
  patients: Patient[];
  onSelect: (p: Patient) => void;
  decisions: DecisionMap;
  onDecide: (patientId: string, decision: Decision) => void;
  onClear: (patientId: string) => void;
}) {
  const [globalFilter, setGlobalFilter] = useState("");
  // Billers land on the claims that actually need a person.
  const [sorting, setSorting] = useState<SortingState>([{ id: "status", desc: false }]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([{ id: "status", value: "flag_for_review" }]);
  const [hideDone, setHideDone] = useState(false);

  const routeFilter = (columnFilters.find((f) => f.id === "status")?.value as Route | undefined) ?? null;
  const setRoute = (r: Route | null) => setColumnFilters(r ? [{ id: "status", value: r }] : []);

  // Hide-completed is applied to the source list (decisions are reactive).
  const data = useMemo(
    () => (hideDone ? patients.filter((p) => !decisions[p.patient_id]) : patients),
    [patients, hideDone, decisions],
  );

  const columns = useMemo(
    () => [
      col.accessor("patient_id", {
        header: "Patient",
        cell: (c) => (
          <div className="flex flex-col">
            <span className="text-sm font-semibold text-ink">{c.row.original.name}</span>
            <span className="tabular text-xs text-ink-soft">{c.getValue()}</span>
          </div>
        ),
      }),
      col.accessor((r) => r.wound.type, {
        id: "wound",
        header: "Billing for",
        cell: (c) => {
          const w = c.row.original.wound;
          const m = fmtMeasure(w.L, w.W, w.D);
          return (
            <div className="flex flex-col">
              <span className="text-sm capitalize text-ink">{w.type ? w.type.replace(/_/g, " ") : "No wound recorded"}</span>
              <span className="text-xs text-ink-soft">
                {w.location ?? "Location not noted"}
                {w.stage ? ` · Stage ${w.stage}` : ""}
                {m ? <span className="tabular"> · {m}</span> : ""}
              </span>
            </div>
          );
        },
      }),
      col.accessor((r) => r.payer.code, {
        id: "payer",
        header: "Insurance",
        cell: (c) => {
          const isMcb = c.getValue() === "MCB";
          return (
            <span
              className={`inline-block whitespace-nowrap rounded-md px-2 py-0.5 text-xs font-semibold ring-1 ${
                isMcb ? "bg-teal-50 text-teal-700 ring-teal-600/20" : "bg-slate-100 text-slate-600 ring-slate-400/20"
              }`}
            >
              {c.row.original.payer.name}
            </span>
          );
        },
      }),
      col.accessor("route", {
        id: "status",
        header: "Status",
        cell: (c) => {
          const ms = matchStrength(c.row.original.confidence);
          return (
            <div className="flex flex-col items-start gap-1">
              <RouteBadge route={c.getValue()} size="sm" />
              <span className="text-[11px] text-ink-faint">{ms.label}</span>
            </div>
          );
        },
        filterFn: (row, id, val) => row.getValue(id) === val,
        sortingFn: (a, b) => ROUTE_ORDER.indexOf(a.original.route) - ROUTE_ORDER.indexOf(b.original.route),
      }),
      col.accessor("reason", {
        header: "Why",
        enableSorting: false,
        cell: (c) => <span className="line-clamp-2 max-w-xs text-xs text-ink-soft">{c.getValue()}</span>,
      }),
      col.display({
        id: "decision",
        header: "Your decision",
        cell: (c) => {
          const id = c.row.original.patient_id;
          return (
            <div onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
              <DecisionButtons
                size="sm"
                decision={decisions[id]}
                onApprove={() => onDecide(id, "approved")}
                onReject={() => onDecide(id, "rejected")}
                onClear={() => onClear(id)}
              />
            </div>
          );
        },
      }),
    ],
    [decisions, onDecide, onClear],
  );

  const table = useReactTable({
    data,
    columns,
    state: { sorting, globalFilter, columnFilters },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    onColumnFiltersChange: setColumnFilters,
    globalFilterFn: (row, _id, filter) => {
      const p = row.original;
      const hay = `${p.patient_id} ${p.name} ${p.wound.type} ${p.wound.location} ${p.payer.name} ${p.reason}`.toLowerCase();
      return hay.includes(String(filter).toLowerCase());
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  const rows = table.getRowModel().rows;
  const counts = ROUTE_ORDER.reduce<Record<string, number>>((a, r) => ((a[r] = patients.filter((p) => p.route === r).length), a), {});
  const decidedHere = rows.filter((r) => decisions[r.original.patient_id]).length;

  return (
    <div className="space-y-4">
      <FadeIn>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold tracking-tight gradient-text">Claims to review</h1>
            <p className="mt-1 text-sm text-ink-soft">
              Showing <span className="tabular font-medium text-ink">{rows.length}</span>{" "}
              {rows.length === 1 ? "claim" : "claims"}
              {decidedHere > 0 && (
                <>
                  {" "}· <span className="tabular font-medium text-teal-700">{decidedHere}</span> decided
                </>
              )}{" "}
              · click any row to see the full chart
            </p>
          </div>
          <label className="relative">
            <span className="sr-only">Search claims</span>
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-faint" aria-hidden="true" />
            <input
              value={globalFilter}
              onChange={(e) => setGlobalFilter(e.target.value)}
              placeholder="Search by name, wound, or insurance…"
              className="w-72 rounded-lg border border-border bg-surface py-2 pl-9 pr-4 text-sm text-ink shadow-glass placeholder:text-ink-faint focus-visible:outline-2"
            />
          </label>
        </div>
      </FadeIn>

      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Filter by status">
          <FilterChip active={routeFilter === null} onClick={() => setRoute(null)} label="All claims" count={patients.length} tone="bg-ink" />
          {ROUTE_ORDER.map((r) => {
            const m = ROUTE_META[r];
            return (
              <FilterChip
                key={r}
                active={routeFilter === r}
                onClick={() => setRoute(routeFilter === r ? null : r)}
                label={m.label}
                count={counts[r]}
                Icon={m.Icon}
                tone={m.dot}
              />
            );
          })}
        </div>
        <label className="ml-auto inline-flex cursor-pointer items-center gap-2 rounded-md border border-border bg-surface px-3 py-1.5 text-xs font-medium text-ink-soft">
          <input
            type="checkbox"
            checked={hideDone}
            onChange={(e) => setHideDone(e.target.checked)}
            className="h-3.5 w-3.5 accent-ink"
          />
          Hide ones I've decided
        </label>
      </div>

      <FadeIn delay={0.05}>
        <GlassCard strong className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-left">
              <thead className="sticky top-0 z-10 bg-surface-2">
                {table.getHeaderGroups().map((hg) => (
                  <tr key={hg.id} className="border-b border-border">
                    {hg.headers.map((h) => {
                      const sortable = h.column.getCanSort();
                      const dir = h.column.getIsSorted();
                      return (
                        <th key={h.id} className="px-4 py-3 text-xs font-semibold uppercase tracking-wide text-ink-soft">
                          {sortable ? (
                            <button
                              onClick={h.column.getToggleSortingHandler()}
                              className="inline-flex items-center gap-1 hover:text-ink focus-visible:outline-2"
                              aria-label={`Sort by ${String(h.column.columnDef.header)}`}
                            >
                              {flexRender(h.column.columnDef.header, h.getContext())}
                              {dir === "asc" ? <ArrowUp className="h-3 w-3" /> : dir === "desc" ? <ArrowDown className="h-3 w-3" /> : <ArrowUpDown className="h-3 w-3 opacity-40" />}
                            </button>
                          ) : (
                            flexRender(h.column.columnDef.header, h.getContext())
                          )}
                        </th>
                      );
                    })}
                  </tr>
                ))}
              </thead>
              <tbody>
                {rows.map((row) => {
                  const decided = decisions[row.original.patient_id];
                  return (
                    <tr
                      key={row.id}
                      tabIndex={0}
                      role="button"
                      aria-label={`Open ${row.original.name} — ${ROUTE_META[row.original.route].label}`}
                      onClick={() => onSelect(row.original)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onSelect(row.original);
                        }
                      }}
                      className={`cursor-pointer border-b border-border/70 transition-colors hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none ${
                        decided === "approved" ? "bg-teal-50/50" : decided === "rejected" ? "bg-rose-50/40" : ""
                      }`}
                    >
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id} className="px-4 py-3 align-middle">
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {rows.length === 0 && (
            <EmptyState
              title={hideDone ? "All caught up" : "No matching claims"}
              hint={
                hideDone
                  ? "You've made a decision on every claim in this filter. Uncheck “Hide ones I've decided” to see them again."
                  : "Try clearing the search or choosing a different status above."
              }
            />
          )}
        </GlassCard>
      </FadeIn>
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  label,
  count,
  Icon,
  tone,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
  Icon?: typeof Search;
  tone: string;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-semibold transition-colors focus-visible:outline-2 ${
        active ? `${tone} text-white` : "border border-border bg-surface text-ink-soft hover:border-border-strong hover:text-ink"
      }`}
    >
      {Icon && <Icon className="h-3.5 w-3.5" aria-hidden="true" />}
      {label}
      <span className={`tabular rounded px-1.5 text-[11px] ${active ? "bg-white/20" : "bg-surface-2 text-ink-soft"}`}>{count}</span>
    </button>
  );
}

// Skeleton shown while the run loads.
export function TriageTableSkeleton() {
  return (
    <div className="space-y-4">
      <div className="h-8 w-48 shimmer rounded-md bg-slate-200/70" />
      <GlassCard strong className="space-y-3 p-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="flex items-center gap-4">
            <div className="h-9 w-9 shimmer rounded-full bg-slate-200/70" />
            <div className="h-4 flex-1 shimmer rounded bg-slate-200/70" />
            <div className="h-4 w-24 shimmer rounded bg-slate-200/70" />
            <div className="h-6 w-20 shimmer rounded-full bg-slate-200/70" />
          </div>
        ))}
      </GlassCard>
    </div>
  );
}
