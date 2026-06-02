import { useCallback, useEffect, useState } from "react";
import type { CSSProperties } from "react";

import { getMeta, getRun, listRuns } from "./api";
import { AttemptPane } from "./components/AttemptPane";
import { LeftRail } from "./components/LeftRail";
import type { Section } from "./components/LeftRail";
import { Placeholder } from "./components/Placeholder";
import { StatusRail } from "./components/StatusRail";
import { TaskRunDetail } from "./components/TaskRunDetail";
import { TaskRunList } from "./components/TaskRunList";
import { BaselineMeanContext, DEFAULT_BASELINE_MEAN } from "./lib/baseline";
import type { Attempt, Meta, RunDetail, RunListItem } from "./types";

const SECTION_LABELS: Record<Section, string> = {
  chat: "Chat",
  tasks: "Projects",
  people: "Members",
  system: "System",
};

export function App() {
  const [section, setSection] = useState<Section>("tasks");
  const [meta, setMeta] = useState<Meta | null>(null);

  const [runs, setRuns] = useState<RunListItem[]>([]);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [runDetail, setRunDetail] = useState<RunDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [selectedAttempt, setSelectedAttempt] = useState<Attempt | null>(null);

  const [middleW, setMiddleW] = useState(232);
  const [threadW, setThreadW] = useState(380);
  const [resizing, setResizing] = useState(false);
  const [resizingMid, setResizingMid] = useState(false);

  // Initial load: baseline + run list.
  useEffect(() => {
    getMeta()
      .then(setMeta)
      .catch(() => {});
    listRuns()
      .then((rs) => {
        setRuns(rs);
        setSelectedRun((prev) => prev ?? rs[0]?.id ?? null);
      })
      .catch((e) => setRunsError(String(e)));
  }, []);

  // Load detail whenever the selected run changes.
  useEffect(() => {
    if (!selectedRun) {
      setRunDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    setSelectedAttempt(null);
    getRun(selectedRun)
      .then((d) => {
        if (!cancelled) setRunDetail(d);
      })
      .catch(() => {
        if (!cancelled) setRunDetail(null);
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedRun]);

  const refresh = useCallback(() => {
    if (selectedRun) getRun(selectedRun).then(setRunDetail).catch(() => {});
    listRuns().then(setRuns).catch(() => {});
  }, [selectedRun]);

  // Drag-to-resize the middle pane (clamped ≈ ±18 chars).
  const handleMiddleResizeStart = (e: React.MouseEvent) => {
    e.preventDefault();
    setResizingMid(true);
    const startX = e.clientX;
    const startW = middleW;
    const onMove = (ev: MouseEvent) => {
      setMiddleW(Math.min(352, Math.max(196, startW + (ev.clientX - startX))));
    };
    const onUp = () => {
      setResizingMid(false);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  // Drag-to-resize the attempt pane (clamped 280–720px).
  const handleResizeStart = (e: React.MouseEvent) => {
    e.preventDefault();
    setResizing(true);
    const startX = e.clientX;
    const startW = threadW;
    const onMove = (ev: MouseEvent) => {
      setThreadW(Math.min(720, Math.max(280, startW + (startX - ev.clientX))));
    };
    const onUp = () => {
      setResizing(false);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const inTasks = section === "tasks";
  const rightOpen = inTasks && !!selectedAttempt;
  const baselineMean = meta?.baseline?.mean ?? DEFAULT_BASELINE_MEAN;
  const runningCount = runs.filter((r) => r.status === "running").length;

  const appStyle = {
    ["--thread-w" as string]: threadW + "px",
    gridTemplateColumns: rightOpen
      ? `64px ${middleW}px 1fr ${threadW}px`
      : `64px ${middleW}px 1fr`,
    transition: resizing || resizingMid ? "none" : undefined,
  } as CSSProperties;

  return (
    <BaselineMeanContext.Provider value={baselineMean}>
      <div
        className={"app" + (rightOpen ? " app--with-thread" : "")}
        style={appStyle}
        data-resizing={resizing || resizingMid ? "true" : "false"}
      >
        <LeftRail section={section} onPick={setSection} />
        <div
          className="middle-resizer"
          style={{ left: 64 + middleW + "px" }}
          data-active={resizingMid ? "true" : "false"}
          title="Drag to resize"
          onMouseDown={handleMiddleResizeStart}
        />

        {inTasks ? (
          <TaskRunList runs={runs} selected={selectedRun} onPick={setSelectedRun} />
        ) : (
          <aside className="middle">
            <div className="middle__head">
              <div className="middle__title">{SECTION_LABELS[section]}</div>
            </div>
          </aside>
        )}

        {inTasks ? (
          runDetail ? (
            <TaskRunDetail
              run={runDetail}
              selectedAttempt={selectedAttempt}
              onOpenAttempt={setSelectedAttempt}
              onRefresh={refresh}
            />
          ) : (
            <main className="main">
              <Placeholder
                title={
                  detailLoading
                    ? "Loading…"
                    : runsError
                      ? "Could not load task runs"
                      : runs.length
                        ? "Select a task run"
                        : "No task runs yet"
                }
                sub={
                  runsError ??
                  (runs.length
                    ? "Choose a run from the list to inspect its trajectory and steps."
                    : "TaskRuns created by magipi will appear here.")
                }
              />
            </main>
          )
        ) : (
          <main className="main">
            <Placeholder
              title={`${SECTION_LABELS[section]} — not in this build`}
              sub="This pass implements the Projects (TaskRun) surface, backed by the database. Other surfaces are documented in DESIGN_DB_GAP.md."
            />
          </main>
        )}

        {rightOpen && runDetail && selectedAttempt && (
          <AttemptPane
            attempt={selectedAttempt}
            run={runDetail}
            onClose={() => setSelectedAttempt(null)}
            onResizeStart={handleResizeStart}
          />
        )}

        <StatusRail
          schema={meta?.database_schema}
          source={meta?.database_source}
          runCount={runs.length}
          runningCount={runningCount}
        />
      </div>
    </BaselineMeanContext.Provider>
  );
}
