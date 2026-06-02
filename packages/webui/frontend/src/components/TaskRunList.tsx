import { fmtBpb, relativeShort, shortUid } from "../lib/format";
import type { RunListItem, RunStatus } from "../types";
import { StatusChip } from "./primitives";

const ACTIVE_STATUSES: RunStatus[] = ["running", "pending", "blocked"];

function RunStatusDot({ status }: { status: RunStatus }) {
  const colors: Record<string, string> = {
    running: "var(--wave-mid)",
    pending: "var(--ink-mute)",
    blocked: "var(--fuji-coral-deep)",
    completed: "var(--wave-deep)",
    failed: "var(--fuji-coral-deep)",
    cancelled: "var(--ink-mute)",
    archived: "var(--ink-mute)",
  };
  const live = status === "running";
  return (
    <span
      className={"rdot" + (live ? " rdot--live" : "")}
      style={{ background: colors[status] || "var(--ink-mute)" }}
    />
  );
}

function RunRow({
  run,
  active,
  onPick,
}: {
  run: RunListItem;
  active: boolean;
  onPick: (id: string) => void;
}) {
  const isExp = run.kind === "experiment";
  return (
    <div
      className="ritem"
      data-active={active ? "true" : "false"}
      onClick={() => onPick(run.id)}
      title={run.goal}
    >
      <div className="ritem__top">
        <RunStatusDot status={run.status} />
        <span className="ritem__id">{shortUid(run.id)}</span>
        {isExp && <span className="ritem__kind">exp</span>}
        <span className="ritem__ago">{relativeShort(run.updatedAt)}</span>
      </div>
      <div className="ritem__goal">{run.goal}</div>
      <div className="ritem__meta">
        <StatusChip status={run.status} />
        {isExp ? (
          <span className="ritem__stat">
            {run.attemptCount} att · best <b>{fmtBpb(run.bestBpb)}</b>
          </span>
        ) : (
          <span className="ritem__stat">
            {run.stepCount} step{run.stepCount === 1 ? "" : "s"}
          </span>
        )}
      </div>
    </div>
  );
}

export function TaskRunList({
  runs,
  selected,
  onPick,
}: {
  runs: RunListItem[];
  selected: string | null;
  onPick: (id: string) => void;
}) {
  const active = runs.filter((r) => ACTIVE_STATUSES.includes(r.status));
  const closed = runs.filter((r) => !ACTIVE_STATUSES.includes(r.status));
  const running = runs.filter((r) => r.status === "running").length;
  return (
    <aside className="middle runlist">
      <div className="middle__head runlist__head">
        <div className="middle__title">Projects</div>
        <div className="runlist__sub">
          {runs.length} task run{runs.length === 1 ? "" : "s"} · {running} running
        </div>
      </div>
      <div className="middle__body">
        <div className="rgroup">Active · {active.length}</div>
        {active.map((r) => (
          <RunRow key={r.id} run={r} active={r.id === selected} onPick={onPick} />
        ))}
        <div className="rgroup">Closed · {closed.length}</div>
        {closed.map((r) => (
          <RunRow key={r.id} run={r} active={r.id === selected} onPick={onPick} />
        ))}
      </div>
    </aside>
  );
}
