/* TaskRunDetail — main pane: header, tabs, trajectory git-graph, steps. */

import { useEffect, useState } from "react";

import {
  IconArtifactsTab,
  IconFlask,
  IconFolderTab,
  IconRefreshSm,
  IconRunGlyph,
} from "../icons";
import { layoutTrajectory } from "../lib/format";
import type { Attempt, RunDetail, Step } from "../types";
import { Placeholder } from "./Placeholder";
import { StatusChip, Tab } from "./primitives";
import { TrajectoryGraph } from "./TrajectoryGraph";

type DetailTab = "trajectory" | "artifacts" | "workspace";

export function TaskRunDetail({
  run,
  selectedAttempt,
  onOpenAttempt,
  onRefresh,
}: {
  run: RunDetail;
  selectedAttempt: Attempt | null;
  onOpenAttempt: (attempt: Attempt | null) => void;
  onRefresh: () => void;
}) {
  const [tab, setTab] = useState<DetailTab>("trajectory");
  useEffect(() => {
    setTab("trajectory");
  }, [run.id]);

  const attempts = run.attempts ?? [];
  const isExp = run.kind === "experiment" && attempts.length > 0;
  const laneCount = isExp ? layoutTrajectory(attempts).laneCount : 0;

  return (
    <main className="main">
      <header className="chead trun__head">
        <div className="chead__row">
          <div className="chead__sealrow">
            <div className="chead__seal trun__seal">
              {isExp ? <IconFlask /> : <IconRunGlyph />}
            </div>
            <div>
              <div className="chead__title trun__title">{run.goal}</div>
              <div className="trun__subrow">
                <span className="trun__uid">{run.id}</span>
                <span className="trun__sep">·</span>
                <span className="trun__status">{run.status}</span>
                <span className="trun__sep">·</span>
                <span className="trun__updated">updated {run.updated}</span>
              </div>
            </div>
          </div>
          <div className="chead__actions">
            <button className="chead__members" title="Refresh read model" onClick={onRefresh}>
              <IconRefreshSm /> refresh
            </button>
          </div>
        </div>
        <div className="chead__tabs">
          <Tab active={tab === "trajectory"} onClick={() => setTab("trajectory")}>
            Trajectory
          </Tab>
          <Tab active={tab === "artifacts"} onClick={() => setTab("artifacts")}>
            <IconArtifactsTab /> Artifacts
          </Tab>
          <Tab active={tab === "workspace"} onClick={() => setTab("workspace")}>
            <IconFolderTab /> Workspace
          </Tab>
        </div>
      </header>

      {tab === "trajectory" ? (
        <div className="cbody trun__body">
          <div className="trun__sectionhead">
            <span className="label-eyebrow">Trajectory</span>
            {isExp && (
              <span className="trun__sectionmeta">
                {attempts.length} attempts · {laneCount} lane{laneCount === 1 ? "" : "s"} ·
                parent_experiment_id tree
              </span>
            )}
            {isExp && <TrajLegend />}
          </div>

          {isExp ? (
            <TrajectoryGraph
              attempts={attempts}
              selectedId={selectedAttempt ? selectedAttempt.id : null}
              onSelect={(id) => onOpenAttempt(attempts.find((a) => a.id === id) ?? null)}
            />
          ) : (
            <div className="traj__empty">
              <div className="traj__empty-seal">
                <IconFlask />
              </div>
              <div className="traj__empty-title">No experiment trajectory</div>
              <div className="traj__empty-sub">
                This TaskRun is a standard task, not a P3 experiment loop. Attempt trees are
                reserved for experiment-session runs.
              </div>
            </div>
          )}

          <div className="trun__sectionhead trun__sectionhead--steps">
            <span className="label-eyebrow">Completed steps</span>
            <span className="trun__sectionmeta">
              {run.steps.length} step{run.steps.length === 1 ? "" : "s"}
            </span>
          </div>
          <StepTimeline steps={run.steps} />
        </div>
      ) : (
        <div className="cbody">
          <Placeholder
            seal={tab === "artifacts" ? <IconArtifactsTab size={30} /> : <IconFolderTab size={30} />}
            title={
              tab === "artifacts"
                ? "Artifacts browser not in this build"
                : "Workspace browser not in this build"
            }
            sub="This pass implements the DB-backed Projects trajectory. Filesystem-backed Artifacts / Workspace browsing is deferred — see DESIGN_DB_GAP.md."
          />
        </div>
      )}
    </main>
  );
}

function TrajLegend() {
  return (
    <div className="tleg">
      <span className="tleg__i">
        <span className="tleg__dot tleg__dot--acc" />
        accepted
      </span>
      <span className="tleg__i">
        <span className="tleg__dot tleg__dot--rej" />
        rejected
      </span>
      <span className="tleg__i">
        <span className="tleg__dot tleg__dot--err" />
        error
      </span>
      <span className="tleg__i">
        <span className="tleg__dot tleg__dot--run" />
        running
      </span>
      <span className="tleg__i">
        <span className="tleg__dot tleg__dot--best" />
        best
      </span>
    </div>
  );
}

function StepTimeline({ steps }: { steps: Step[] }) {
  if (steps.length === 0) {
    return <div className="board__empty" style={{ padding: "4px 0" }}>no steps recorded</div>;
  }
  return (
    <div className="steps">
      {steps.map((s, i) => (
        <div className="step" key={s.idx}>
          <div className="step__rail">
            <span className={"step__dot step__dot--" + s.status} />
            {i < steps.length - 1 && <span className="step__line" />}
          </div>
          <div className="step__body">
            <div className="step__top">
              <span className="step__title">{s.title}</span>
              <StatusChip status={s.status === "done" ? "completed" : s.status} />
              <span className="step__time">
                {s.started ?? "—"}
                {s.ended ? " → " + s.ended : " → …"}
              </span>
            </div>
            <div className="step__concl">{s.conclusion}</div>
          </div>
        </div>
      ))}
    </div>
  );
}
