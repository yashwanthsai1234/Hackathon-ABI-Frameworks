import { useEffect, useRef, useState, type ReactNode } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { AlertCircle, Inbox, RefreshCw } from "lucide-react";

export function GlassCard({
  children,
  className = "",
  strong = false,
  as = "div",
  ...rest
}: {
  children: ReactNode;
  className?: string;
  strong?: boolean;
  as?: "div" | "section" | "article";
} & React.HTMLAttributes<HTMLElement>) {
  const Tag = as;
  return (
    <Tag className={`${strong ? "glass-strong" : "glass"} ${className}`} {...rest}>
      {children}
    </Tag>
  );
}

// Count-up that respects prefers-reduced-motion (jumps straight to value).
export function CountUp({ value, duration = 1.1, decimals = 0 }: { value: number; duration?: number; decimals?: number }) {
  const reduce = useReducedMotion();
  const [n, setN] = useState(reduce ? value : 0);
  const raf = useRef<number>(0);
  useEffect(() => {
    if (reduce) {
      setN(value);
      return;
    }
    const start = performance.now();
    const from = 0;
    const ease = (t: number) => 1 - Math.pow(1 - t, 3);
    const step = (now: number) => {
      const t = Math.min(1, (now - start) / (duration * 1000));
      setN(from + (value - from) * ease(t));
      if (t < 1) raf.current = requestAnimationFrame(step);
    };
    raf.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf.current);
  }, [value, duration, reduce]);
  return <span className="tabular">{n.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}</span>;
}

export function Pill({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${className}`}>{children}</span>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`shimmer rounded-md bg-slate-200/70 ${className}`} aria-hidden="true" />;
}

export function EmptyState({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center" role="status">
      <div className="grid h-14 w-14 place-items-center rounded-xl border border-border bg-surface-2 text-ink-soft">
        <Inbox className="h-7 w-7" aria-hidden="true" />
      </div>
      <h3 className="text-lg font-semibold text-ink">{title}</h3>
      <p className="max-w-sm text-sm text-ink-soft">{hint}</p>
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center" role="alert">
      <div className="grid h-14 w-14 place-items-center rounded-2xl bg-rose-100 text-rose-600">
        <AlertCircle className="h-7 w-7" aria-hidden="true" />
      </div>
      <h3 className="text-lg font-semibold text-ink">We couldn’t load the run</h3>
      <p className="max-w-sm text-sm text-ink-soft">{message}</p>
      <p className="max-w-sm text-xs text-ink-faint">
        The dashboard reads a static <code className="tabular">export.json</code>. Confirm it’s present, then retry.
      </p>
      <button
        onClick={onRetry}
        className="mt-1 inline-flex items-center gap-2 rounded-lg bg-ink px-4 py-2 text-sm font-medium text-white shadow-sm transition-colors hover:bg-ink/90 focus-visible:outline-2"
      >
        <RefreshCw className="h-4 w-4" aria-hidden="true" /> Retry
      </button>
    </div>
  );
}

// Subtle enter animation wrapper, reduced-motion safe.
export function FadeIn({ children, delay = 0, className = "" }: { children: ReactNode; delay?: number; className?: string }) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      className={className}
      initial={reduce ? false : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.32, delay, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </motion.div>
  );
}
