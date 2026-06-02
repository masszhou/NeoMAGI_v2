/* AttemptPane — right-rail detail for one attempt node. */

import type { ReactNode } from "react";

import { IconCloseX, IconDiffSm, IconFolderSm } from "../icons";
import { deltaVs, fmtBpb, fmtBytes, shortSha } from "../lib/format";
import type { Attempt, RunDetail } from "../types";
import { DeltaChip, VerdictChip } from "./primitives";

// P3 Mini Parameter Golf required submission bundle (parameter_golf_contract).
const REQUIRED_FILES = [
  "README.md",
  "submission.json",
  "manifest.json",
  "train_log.txt",
  "eval_result.json",
];

export function AttemptPane({
  attempt,
  run,
  onClose,
  onResizeStart,
}: {
  attempt: Attempt;
  run: RunDetail;
  onClose: () => void;
  onResizeStart: (e: React.MouseEvent) => void;
}) {
  const a = attempt;
  const parent = (run.attempts || []).find((x) => x.id === a.parent) || null;
  const dParent =
    parent && a.val_bpb != null && parent.val_bpb != null
      ? deltaVs(a.val_bpb, parent.val_bpb)
      : null;

  return (
    <aside className="thread apane">
      <div className="thread__resizer" title="Drag to resize" onMouseDown={onResizeStart} />
      <div className="thread__head apane__head">
        <div>
          <div className="apane__id">{a.id}</div>
          <div className="apane__uid">{a.uid}</div>
        </div>
        <button className="thread__close" onClick={onClose}>
          <IconCloseX /> close
        </button>
      </div>

      <div className="thread__body apane__body">
        <div className="apane__verdrow">
          <VerdictChip verdict={a.verdict} />
          {a.best && <span className="trow__best">★ current best</span>}
          <span className="apane__when">
            {a.created ?? (a.status === "running" ? "training" : "")}
          </span>
        </div>

        <div className="apane__metric">
          <div className="apane__metricbig">
            {fmtBpb(a.val_bpb)}
            <span className="apane__metricunit">val_bpb</span>
          </div>
          {a.val_bpb != null ? (
            <div className="apane__deltas">
              <div className="apane__dl">
                <span>vs baseline</span>
                <DeltaChip value={a.val_bpb} />
              </div>
              {dParent && (
                <div className="apane__dl">
                  <span>vs {a.parent}</span>
                  <span className={"dchip " + (dParent.improved ? "dchip--down" : "dchip--up")}>
                    {dParent.text} {dParent.arrow}
                  </span>
                </div>
              )}
            </div>
          ) : (
            <div className="apane__nometric">
              {a.status === "running" ? "evaluation pending" : "run produced no metric"}
            </div>
          )}
        </div>

        <Section label="Hypothesis">
          <p className="apane__hyp">{a.hypothesis}</p>
        </Section>

        <Section label="Change">
          <div className="apane__kv">
            <span>config</span>
            <code>{a.config ?? "—"}</code>
          </div>
          {a.codePaths.length > 0 && (
            <div className="apane__kv">
              <span>code</span>
              <code>{a.codePaths.join(", ")}</code>
            </div>
          )}
          <div className="apane__kv">
            <span>seed</span>
            <code>{a.seed ?? "—"}</code>
          </div>
          {a.trainSeconds != null && (
            <div className="apane__kv">
              <span>train</span>
              <code>{a.trainSeconds}s</code>
            </div>
          )}
        </Section>

        <Section label={"Verdict · " + a.verdict}>
          {a.reasons.length ? (
            <ul className="apane__reasons">
              {a.reasons.map((r, i) => (
                <li key={i} data-bad={a.verdict !== "accepted"}>
                  {r}
                </li>
              ))}
            </ul>
          ) : (
            <div className="apane__muted">no reasons recorded yet</div>
          )}
          <div className="apane__kv">
            <span>significance</span>
            <code>{a.significance ?? "—"}</code>
          </div>
        </Section>

        <Section label="Workspace lineage">
          <div className="apane__kv">
            <span>commit</span>
            {a.commit ? (
              <code className="apane__sha">{a.commit}</code>
            ) : (
              <code className="apane__muted">uncommitted</code>
            )}
          </div>
          <div className="apane__kv">
            <span>parent</span>
            {a.parentCommit ? (
              <code className="apane__sha">{shortSha(a.parentCommit)}…</code>
            ) : (
              <code className="apane__muted">root</code>
            )}
          </div>
          <div className="apane__kv">
            <span>branch</span>
            <code>{a.branch ?? "—"}</code>
          </div>
          <div className="apane__kv">
            <span>records</span>
            <code className="apane__records">{a.records ?? "—"}</code>
          </div>
        </Section>

        <Section label="Artifact bundle">
          <div className="apane__kv">
            <span>size</span>
            <code>
              {fmtBytes(a.artifactBytes)}
              {a.artifactBytes != null ? " / 16.00 MB cap" : ""}
            </code>
          </div>
          <div className="apane__files">
            <div className="apane__filedir">
              submission/ <span>required dir</span>
            </div>
            {REQUIRED_FILES.map((f) => (
              <div className="apane__file" key={f} data-have={a.artifactBytes != null ? "true" : "false"}>
                <span className="apane__filemark">{a.artifactBytes != null ? "✓" : "·"}</span>
                {f}
              </div>
            ))}
          </div>
        </Section>

        <div className="apane__actions">
          <button className="apane__btn" disabled>
            <IconDiffSm /> view git diff
          </button>
          <button className="apane__btn" disabled>
            <IconFolderSm /> open records/
          </button>
        </div>
      </div>
    </aside>
  );
}

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="apane__sec">
      <div className="apane__seclabel label-eyebrow">{label}</div>
      <div className="apane__secbody">{children}</div>
    </section>
  );
}
