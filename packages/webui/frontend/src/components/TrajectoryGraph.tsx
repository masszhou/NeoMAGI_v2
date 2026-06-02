/* TrajectoryGraph — top-to-bottom git-graph of attempt nodes.

   An absolutely-positioned SVG gutter draws lanes + branch curves; HTML rows
   to the right carry the textual attempt summary. Both share ROW_H so node
   dots line up with their row centers. */

import type { ReactElement } from "react";

import { fmtBpb, layoutTrajectory, shortSha, shortUid } from "../lib/format";
import type { Attempt } from "../types";
import { DeltaChip, VerdictChip } from "./primitives";

const TRAJ_ROW_H = 96;
const TRAJ_NODE_R = 5.5;
const TRAJ_LANE_W = 22;
const TRAJ_PAD_L = 20;
const TRAJ_PAD_R = 18;
const TRAJ_LANE_COLORS = ["#1B3A6B", "#B45E3F", "#27568F", "#6E5A86", "#3D6B57"];

const laneColor = (i: number) => TRAJ_LANE_COLORS[i % TRAJ_LANE_COLORS.length];

export function TrajectoryGraph({
  attempts,
  selectedId,
  onSelect,
}: {
  attempts: Attempt[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const { lane, laneCount } = layoutTrajectory(attempts);
  const indexOf: Record<string, number> = {};
  attempts.forEach((a, i) => {
    indexOf[a.id] = i;
  });

  const gutterW = TRAJ_PAD_L + (laneCount - 1) * TRAJ_LANE_W + TRAJ_PAD_R;
  const totalH = attempts.length * TRAJ_ROW_H;
  const cx = (id: string) => TRAJ_PAD_L + lane[id] * TRAJ_LANE_W;
  const cy = (id: string) => indexOf[id] * TRAJ_ROW_H + TRAJ_ROW_H / 2;

  const edges: ReactElement[] = [];
  attempts.forEach((a) => {
    if (!a.parent || indexOf[a.parent] === undefined) return;
    const x1 = cx(a.parent);
    const y1 = cy(a.parent);
    const x2 = cx(a.id);
    const y2 = cy(a.id);
    const col = laneColor(lane[a.id]);
    let d: string;
    if (x1 === x2) {
      d = `M ${x1} ${y1} L ${x2} ${y2}`;
    } else {
      // branch: ease horizontally within the first inter-row gap, then run
      // straight down the child's lane to the child node.
      const yb = y1 + TRAJ_ROW_H;
      d = `M ${x1} ${y1} C ${x1} ${y1 + TRAJ_ROW_H * 0.5} ${x2} ${y1 + TRAJ_ROW_H * 0.5} ${x2} ${yb} L ${x2} ${y2}`;
    }
    edges.push(<path key={"e" + a.id} d={d} fill="none" stroke={col} strokeWidth="2" />);
  });

  return (
    <div className="traj" style={{ minHeight: totalH }}>
      <svg
        className="traj__svg"
        width={gutterW}
        height={totalH}
        viewBox={`0 0 ${gutterW} ${totalH}`}
      >
        {edges}
        {attempts.map((a) => {
          const x = cx(a.id);
          const y = cy(a.id);
          const col = laneColor(lane[a.id]);
          const sel = a.id === selectedId;
          return (
            <g key={"n" + a.id}>
              {a.best && (
                <circle cx={x} cy={y} r={TRAJ_NODE_R + 3.5} fill="none" stroke="var(--fuji-coral)" strokeWidth="1.5" />
              )}
              {sel && (
                <circle cx={x} cy={y} r={TRAJ_NODE_R + 5.5} fill="none" stroke="var(--ink-black)" strokeWidth="1.5" />
              )}
              {a.status === "running" && (
                <circle className="traj__pulse" cx={x} cy={y} r={TRAJ_NODE_R + 3} fill="none" stroke={col} strokeWidth="2" />
              )}
              {a.verdict === "error" ? (
                <g>
                  <circle cx={x} cy={y} r={TRAJ_NODE_R} fill="var(--fuji-coral-deep)" stroke="var(--bg-canvas)" strokeWidth="1" />
                  <path
                    d={`M ${x - 2.2} ${y - 2.2} L ${x + 2.2} ${y + 2.2} M ${x + 2.2} ${y - 2.2} L ${x - 2.2} ${y + 2.2}`}
                    stroke="var(--foam-white)"
                    strokeWidth="1.3"
                    strokeLinecap="round"
                  />
                </g>
              ) : a.verdict === "rejected" ? (
                <circle cx={x} cy={y} r={TRAJ_NODE_R} fill="var(--bg-canvas)" stroke={col} strokeWidth="2" />
              ) : (
                <circle cx={x} cy={y} r={TRAJ_NODE_R} fill={col} stroke="var(--bg-canvas)" strokeWidth="1" />
              )}
            </g>
          );
        })}
      </svg>

      <div className="traj__rows" style={{ marginLeft: gutterW }}>
        {attempts.map((a) => (
          <TrajectoryRow key={a.id} a={a} selected={a.id === selectedId} onSelect={onSelect} />
        ))}
      </div>
    </div>
  );
}

function TrajectoryRow({
  a,
  selected,
  onSelect,
}: {
  a: Attempt;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  return (
    <div
      className="trow"
      data-selected={selected ? "true" : "false"}
      style={{ height: TRAJ_ROW_H }}
      onClick={() => onSelect(a.id)}
    >
      <div className="trow__line1">
        <span className="trow__id">{a.id}</span>
        <span className="trow__uid">{shortUid(a.uid)}</span>
        <VerdictChip verdict={a.verdict} />
        {a.best && <span className="trow__best">★ best</span>}
        <span className="trow__time">{a.status === "running" ? "running" : a.created}</span>
      </div>
      <div className="trow__line2">
        {a.val_bpb != null ? (
          <>
            <span className="trow__metric">{fmtBpb(a.val_bpb)}</span>
            <span className="trow__metricl">val_bpb</span>
            <DeltaChip value={a.val_bpb} />
          </>
        ) : a.status === "running" ? (
          <span className="trow__pending">eval pending — training</span>
        ) : (
          <span className="trow__pending trow__pending--err">
            no metric{a.reasons.length ? " — " + a.reasons[0] : ""}
          </span>
        )}
      </div>
      <div className="trow__line3">{a.hypothesis}</div>
      <div className="trow__line4">
        {a.commit ? (
          <span className="trow__sha">{shortSha(a.commit)}</span>
        ) : (
          <span className="trow__sha trow__sha--none">uncommitted</span>
        )}
        <span className="trow__records">{a.records}</span>
      </div>
    </div>
  );
}
