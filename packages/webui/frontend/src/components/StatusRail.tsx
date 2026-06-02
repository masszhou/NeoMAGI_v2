export function StatusRail({
  schema,
  source,
  runCount,
  runningCount,
}: {
  schema?: string;
  source?: string;
  runCount: number;
  runningCount: number;
}) {
  return (
    <div className="statusrail">
      <span>
        <span className="dot" /> postgres · {schema ?? "neomagi"} · read-only
      </span>
      <span>
        {runCount} task run{runCount === 1 ? "" : "s"} · {runningCount} running
      </span>
      <span className="right">
        {source ? source + " · " : ""}projects v1.0
      </span>
    </div>
  );
}
