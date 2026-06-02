// Shapes returned by the read-only TaskRun API (see src/webui/taskrun_queries.py).

export interface Baseline {
  metric: string;
  direction: string;
  n: number;
  mean: number;
  std: number;
}

export interface Meta {
  baseline: Baseline;
  database_schema?: string;
  database_source?: string;
}

export type RunStatus =
  | "pending"
  | "running"
  | "blocked"
  | "completed"
  | "failed"
  | "cancelled"
  | "archived";

export type RunKind = "experiment" | "task";

export interface RunListItem {
  id: string;
  goal: string;
  status: RunStatus;
  kind: RunKind;
  workspaceRoot: string;
  sessionId: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  updated: string | null;
  closedAt: string | null;
  attemptCount: number;
  bestBpb: number | null;
  stepCount: number;
}

export type StepStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "blocked"
  | "cancelled";

export interface Step {
  idx: number;
  title: string;
  status: StepStatus;
  started: string | null;
  ended: string | null;
  conclusion: string;
}

export type Verdict = "accepted" | "rejected" | "error" | "running";
export type AttemptStatus = "done" | "running" | "failed";

export interface Attempt {
  id: string;
  uid: string;
  parent: string | null;
  val_bpb: number | null;
  verdict: Verdict;
  status: AttemptStatus;
  best: boolean;
  hypothesis: string;
  config: string | null;
  codePaths: string[];
  commit: string | null;
  parentCommit: string | null;
  branch: string | null;
  records: string | null;
  artifactBytes: number | null;
  significance: string | null;
  reasons: string[];
  created: string | null;
  seed: number | null;
  trainSeconds: number | null;
}

export interface NextAction {
  kind?: string;
  reason?: string;
  baseUid?: string | null;
  rationale?: string | null;
}

export interface RunDetail {
  id: string;
  goal: string;
  status: RunStatus;
  kind: RunKind;
  createdAt: string | null;
  updatedAt: string | null;
  updated: string | null;
  closedAt: string | null;
  workspaceRoot: string;
  projectionPath: string;
  sessionId: string;
  permission: string | null;
  gitStatus: string | null;
  gitTracked: number;
  nextAction: NextAction | null;
  steps: Step[];
  attempts: Attempt[] | null;
}
