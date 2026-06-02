import type { CSSProperties, ReactNode } from "react";

import { useBaselineMean } from "../lib/baseline";
import { deltaVs } from "../lib/format";
import type { Verdict } from "../types";

/* StatusChip — lowercase mono status. Matches dashboard's .status-chip. */
export function StatusChip({ status }: { status: string }) {
  const byStatus: Record<string, CSSProperties> = {
    running: { color: "var(--wave-mid)", background: "var(--foam-white)" },
    pending: { color: "var(--ink-mute)", background: "var(--washi-cream)" },
    blocked: { color: "var(--fuji-coral-deep)", background: "var(--sunrise-peach)" },
    completed: { color: "var(--wave-deep)", background: "var(--wave-foam)" },
    failed: { color: "#fff", background: "var(--fuji-coral-deep)" },
    cancelled: { color: "var(--ink-mute)", background: "var(--washi-sand-2)" },
    archived: { color: "var(--ink-mute)", background: "var(--washi-sand-2)" },
  };
  const s = byStatus[status] || byStatus.pending;
  return (
    <span
      style={{
        display: "inline-block",
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        lineHeight: "14px",
        padding: "1px 6px",
        textTransform: "lowercase",
        letterSpacing: "0.02em",
        border: "1px solid currentColor",
        ...s,
      }}
    >
      {status}
    </span>
  );
}

/* Tab button — bottom-border indicator with the yellow active plate. */
export function Tab({
  children,
  active,
  onClick,
  disabled,
}: {
  children: ReactNode;
  active?: boolean;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        padding: "5px 10px 4px",
        fontFamily: "var(--font-sans)",
        fontSize: 12,
        background: active ? "var(--tab-active)" : "transparent",
        color: active ? "var(--ink-black)" : "var(--ink-mute)",
        border: active ? "1px solid var(--ink-black)" : "1px solid transparent",
        fontWeight: active ? 600 : 400,
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.55 : 1,
      }}
    >
      {children}
    </button>
  );
}

/* VerdictChip — accepted / rejected / error / running. */
export function VerdictChip({ verdict }: { verdict: Verdict }) {
  const map: Record<string, CSSProperties> = {
    accepted: { color: "var(--wave-deep)", background: "var(--wave-foam)" },
    rejected: { color: "var(--fuji-coral-deep)", background: "var(--sunrise-peach)" },
    error: { color: "var(--foam-white)", background: "var(--fuji-coral-deep)" },
    running: { color: "var(--wave-mid)", background: "var(--foam-white)" },
  };
  const s = map[verdict] || map.running;
  return (
    <span className="vchip" style={{ color: s.color, background: s.background }}>
      {verdict === "running" && <span className="vchip__live" />}
      {verdict}
    </span>
  );
}

/* DeltaChip — val_bpb delta vs baseline mean (lower is better → down/green). */
export function DeltaChip({ value }: { value: number | null }) {
  const mean = useBaselineMean();
  const d = deltaVs(value, mean);
  if (!d) return null;
  return (
    <span className={"dchip " + (d.improved ? "dchip--down" : "dchip--up")}>
      {d.text} {d.arrow}
    </span>
  );
}
