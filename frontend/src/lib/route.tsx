import { CheckCircle2, AlertTriangle, Ban } from "lucide-react";
import type { Route } from "../types";

// Routing presentation — color is ALWAYS paired with an icon + label (never color alone).
// Labels are written in plain language for billing staff, NOT engineers:
//   auto_accept      → "Ready to bill"
//   flag_for_review  → "Needs your review"
//   reject           → "Not billable"
export const ROUTE_META: Record<
  Route,
  { label: string; short: string; desc: string; Icon: typeof CheckCircle2; color: string; bg: string; ring: string; text: string; dot: string }
> = {
  auto_accept: {
    label: "Ready to bill",
    short: "Ready",
    desc: "Meets every Medicare wound-care requirement — safe to submit.",
    Icon: CheckCircle2,
    color: "var(--color-accept)",
    bg: "bg-teal-50",
    ring: "ring-teal-600/30",
    text: "text-teal-700",
    dot: "bg-teal-600",
  },
  flag_for_review: {
    label: "Needs your review",
    short: "Review",
    desc: "A detail is missing or unclear — please check it before billing.",
    Icon: AlertTriangle,
    color: "var(--color-flag)",
    bg: "bg-amber-50",
    ring: "ring-amber-500/30",
    text: "text-amber-700",
    dot: "bg-amber-500",
  },
  reject: {
    label: "Not billable",
    short: "Not billable",
    desc: "Fails a required rule (for example, the wrong insurance).",
    Icon: Ban,
    color: "var(--color-reject)",
    bg: "bg-rose-50",
    ring: "ring-rose-500/30",
    text: "text-rose-700",
    dot: "bg-rose-500",
  },
};

// Order billers care about first: the work that needs a person comes up top.
export const ROUTE_ORDER: Route[] = ["flag_for_review", "auto_accept", "reject"];

export const fmtMeasure = (l: number | null, w: number | null, d: number | null) => {
  const part = (x: number | null) => (x == null ? "—" : x.toFixed(1));
  if (l == null && w == null && d == null) return null;
  return `${part(l)} × ${part(w)} × ${part(d)} cm`;
};

export const pct = (x: number) => `${Math.round(x * 100)}%`;

// Confidence → plain words. Billers never see the raw percentage.
export function matchStrength(value: number): { label: string; tone: string; ring: string } {
  if (value >= 0.8) return { label: "Strong match", tone: "text-teal-700 bg-teal-50", ring: "ring-teal-600/20" };
  if (value >= 0.6) return { label: "Moderate match", tone: "text-amber-700 bg-amber-50", ring: "ring-amber-500/20" };
  return { label: "Weak match", tone: "text-rose-700 bg-rose-50", ring: "ring-rose-500/20" };
}

// Insurance codes → names a biller recognizes.
export const PAYER_NAMES: Record<string, string> = {
  MCB: "Medicare Part B",
  MCA: "Medicare Advantage",
  MCD: "Medicaid",
  HMO: "HMO / Commercial",
};

export const payerName = (code: string) => PAYER_NAMES[code] ?? code;
