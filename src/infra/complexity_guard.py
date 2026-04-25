"""
Complexity governance guard for tracked code files.
看 CLI 帮助
uv run python -m infra.complexity_guard --help
"""

from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

CODE_GLOBS = ("*.py", "*.ts", "*.tsx", "*.js", "*.jsx")
CODE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx"})
BRANCH_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.Match)
DEFAULT_BASELINE_PATH = ".complexity-baseline.json"
DEFAULT_OVERRIDES_PATH = ".complexity-overrides.json"
TEST_FILE_SUFFIXES = (
    ".test.ts",
    ".test.tsx",
    ".test.js",
    ".test.jsx",
    ".spec.ts",
    ".spec.tsx",
    ".spec.js",
    ".spec.jsx",
)


@dataclass(frozen=True)
class GroupThresholds:
    file_target: int | None
    file_block: int | None
    function_target: int | None
    function_block: int | None
    branches_target: int | None
    branches_block: int | None
    nesting_block: int | None


@dataclass(frozen=True)
class Finding:
    severity: str
    group: str
    metric: str
    path: str
    actual: int
    limit: int
    symbol: str | None = None
    line: int | None = None

    @property
    def fingerprint(self) -> str:
        parts = [self.metric, self.path]
        if self.symbol:
            parts.append(self.symbol)
        if self.line is not None:
            parts.append(str(self.line))
        return "::".join(parts)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "severity": self.severity,
            "group": self.group,
            "metric": self.metric,
            "path": self.path,
            "actual": self.actual,
            "limit": self.limit,
            "fingerprint": self.fingerprint,
        }
        if self.symbol:
            payload["symbol"] = self.symbol
        if self.line is not None:
            payload["line"] = self.line
        return payload


@dataclass(frozen=True)
class FunctionMetric:
    qualname: str
    line: int
    length: int
    branches: int
    nesting: int


GROUP_THRESHOLDS = {
    "prod": GroupThresholds(
        file_target=500,
        file_block=800,
        function_target=30,
        function_block=50,
        branches_target=3,
        branches_block=6,
        nesting_block=3,
    ),
    "scripts": GroupThresholds(
        file_target=500,
        file_block=800,
        function_target=30,
        function_block=50,
        branches_target=3,
        branches_block=6,
        nesting_block=3,
    ),
    "tests": GroupThresholds(
        file_target=1200,
        file_block=1200,
        function_target=30,
        function_block=50,
        branches_target=3,
        branches_block=6,
        nesting_block=3,
    ),
}


def classify_path(path: Path) -> str | None:
    posix_path = path.as_posix()
    name = path.name
    if _is_ignored_path(posix_path):
        return None
    if _is_test_path(posix_path, name):
        return "tests"
    if posix_path.startswith("scripts/"):
        return "scripts"
    if posix_path.startswith("src/"):
        return "prod"
    return None


def _is_ignored_path(posix_path: str) -> bool:
    return posix_path.startswith("alembic/versions/")


def _is_test_path(posix_path: str, name: str) -> bool:
    return (
        posix_path.startswith("tests/")
        or "/__tests__/" in f"/{posix_path}/"
        or name.startswith("test_")
        or name.endswith(TEST_FILE_SUFFIXES)
    )


def tracked_code_paths(workspace_root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", *CODE_GLOBS],
        cwd=workspace_root,
        capture_output=True,
        check=True,
        text=True,
    )
    paths: list[Path] = []
    for line in result.stdout.splitlines():
        relpath = Path(line.strip())
        if relpath.suffix not in CODE_SUFFIXES:
            continue
        if classify_path(relpath) is None:
            continue
        paths.append(relpath)
    return sorted(paths)


def load_file_line_overrides(workspace_root: Path) -> tuple[str, ...]:
    overrides_path = workspace_root / DEFAULT_OVERRIDES_PATH
    if not overrides_path.is_file():
        return ()

    payload = json.loads(overrides_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{DEFAULT_OVERRIDES_PATH} must contain a JSON object")
    patterns = payload.get("skip_file_lines", [])
    if not isinstance(patterns, list) or not all(isinstance(item, str) for item in patterns):
        raise ValueError("skip_file_lines must be an array of strings")
    return tuple(patterns)


def analyze_paths(
    paths: Sequence[Path],
    workspace_root: Path,
    *,
    skip_file_lines: Sequence[str] = (),
) -> dict[str, Any]:
    findings: list[Finding] = []
    files_by_group = {group: 0 for group in GROUP_THRESHOLDS}

    for relpath in sorted(paths):
        group = classify_path(relpath)
        if group is None:
            continue
        thresholds = GROUP_THRESHOLDS[group]
        files_by_group[group] += 1
        findings.extend(
            _file_findings(
                relpath,
                workspace_root,
                group,
                thresholds,
                skip_file_lines=skip_file_lines,
            )
        )
        if relpath.suffix == ".py":
            findings.extend(_python_findings(relpath, workspace_root, group, thresholds))

    target_findings = [finding for finding in findings if finding.severity == "target"]
    block_findings = [finding for finding in findings if finding.severity == "block"]
    return {
        "generated_at": _utc_now(),
        "policy_version": 1,
        "files_scanned": sum(files_by_group.values()),
        "files_by_group": files_by_group,
        "thresholds": _thresholds_payload(),
        "target_findings": [finding.to_dict() for finding in _sort_findings(target_findings)],
        "block_findings": [finding.to_dict() for finding in _sort_findings(block_findings)],
    }


def build_baseline_payload(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_at": report["generated_at"],
        "policy_version": report["policy_version"],
        "thresholds": report["thresholds"],
        "block_findings": report["block_findings"],
    }


def detect_regressions(
    current_block_findings: Sequence[dict[str, Any]],
    baseline_block_findings: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    baseline_by_fingerprint = {
        finding["fingerprint"]: int(finding["actual"]) for finding in baseline_block_findings
    }
    regressions: list[dict[str, Any]] = []
    for finding in current_block_findings:
        fingerprint = finding["fingerprint"]
        actual = int(finding["actual"])
        baseline_actual = baseline_by_fingerprint.get(fingerprint)
        if baseline_actual is None or actual > baseline_actual:
            regressions.append(finding)
    regressions.sort(
        key=lambda finding: (
            finding["path"],
            finding["metric"],
            finding.get("line") or 0,
            finding.get("symbol") or "",
        )
    )
    return regressions


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    workspace_root = Path(args.workspace_root).resolve()
    paths = tracked_code_paths(workspace_root)
    skip_file_lines = load_file_line_overrides(workspace_root)
    report = analyze_paths(paths, workspace_root, skip_file_lines=skip_file_lines)

    if args.command == "report":
        _emit_report(report, as_json=args.json)
        return 0

    if args.command == "write-baseline":
        baseline_path = workspace_root / args.baseline
        baseline_path.write_text(
            json.dumps(build_baseline_payload(report), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote complexity baseline to {baseline_path}")
        return 0

    if args.command != "check":
        raise ValueError(f"unsupported command: {args.command}")

    baseline_path = workspace_root / args.baseline
    if not baseline_path.is_file():
        print(
            f"Complexity baseline missing: {baseline_path}. "
            "Run `just complexity-baseline` first.",
            file=sys.stderr,
        )
        return 2
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    regressions = detect_regressions(report["block_findings"], baseline.get("block_findings", []))
    _emit_check_result(report, regressions, as_json=args.json)
    return 1 if regressions else 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NeoMAGI complexity governance guard")
    parser.add_argument(
        "--workspace-root",
        default=".",
        help="Workspace root used for git ls-files and baseline lookup",
    )
    parser.add_argument(
        "--baseline",
        default=DEFAULT_BASELINE_PATH,
        help=f"Baseline file path relative to workspace root (default: {DEFAULT_BASELINE_PATH})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    report_parser = subparsers.add_parser("report", help="Print current complexity snapshot")
    report_parser.add_argument("--json", action="store_true", help="Emit JSON only")

    baseline_parser = subparsers.add_parser(
        "write-baseline", help="Write current hard-threshold findings as baseline"
    )
    baseline_parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    check_parser = subparsers.add_parser(
        "check", help="Fail on new or worsened hard-threshold complexity findings"
    )
    check_parser.add_argument("--json", action="store_true", help="Emit JSON only")

    return parser


def _file_findings(
    relpath: Path,
    workspace_root: Path,
    group: str,
    thresholds: GroupThresholds,
    *,
    skip_file_lines: Sequence[str] = (),
) -> list[Finding]:
    if _matches_path_pattern(relpath, skip_file_lines):
        return []
    with (workspace_root / relpath).open(encoding="utf-8") as handle:
        line_count = sum(1 for _ in handle)
    finding = _metric_finding(
        group=group,
        metric="file_lines",
        path=relpath.as_posix(),
        actual=line_count,
        target_limit=thresholds.file_target,
        block_limit=thresholds.file_block,
    )
    return [finding] if finding is not None else []


def _matches_path_pattern(relpath: Path, patterns: Sequence[str]) -> bool:
    path = relpath.as_posix()
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def _python_findings(
    relpath: Path,
    workspace_root: Path,
    group: str,
    thresholds: GroupThresholds,
) -> list[Finding]:
    module = ast.parse((workspace_root / relpath).read_text(encoding="utf-8"))
    findings: list[Finding] = []
    for metric in _collect_function_metrics(module):
        findings.extend(
            _function_findings(
                relpath=relpath,
                group=group,
                thresholds=thresholds,
                metric=metric,
            )
        )
    return findings


def _function_findings(
    *,
    relpath: Path,
    group: str,
    thresholds: GroupThresholds,
    metric: FunctionMetric,
) -> list[Finding]:
    findings: list[Finding] = []
    metrics = [
        (
            "function_lines",
            metric.length,
            thresholds.function_target,
            thresholds.function_block,
        ),
        (
            "function_branches",
            metric.branches,
            thresholds.branches_target,
            thresholds.branches_block,
        ),
        ("function_nesting", metric.nesting, None, thresholds.nesting_block),
    ]
    for name, actual, target_limit, block_limit in metrics:
        finding = _metric_finding(
            group=group,
            metric=name,
            path=relpath.as_posix(),
            actual=actual,
            target_limit=target_limit,
            block_limit=block_limit,
            symbol=metric.qualname,
            line=metric.line,
        )
        if finding is not None:
            findings.append(finding)
    return findings


def _metric_finding(
    *,
    group: str,
    metric: str,
    path: str,
    actual: int,
    target_limit: int | None,
    block_limit: int | None,
    symbol: str | None = None,
    line: int | None = None,
) -> Finding | None:
    if block_limit is not None and actual > block_limit:
        return Finding(
            severity="block",
            group=group,
            metric=metric,
            path=path,
            actual=actual,
            limit=block_limit,
            symbol=symbol,
            line=line,
        )
    if target_limit is not None and actual > target_limit:
        return Finding(
            severity="target",
            group=group,
            metric=metric,
            path=path,
            actual=actual,
            limit=target_limit,
            symbol=symbol,
            line=line,
        )
    return None


def _collect_function_metrics(module: ast.AST) -> list[FunctionMetric]:
    collector = _FunctionCollector()
    collector.visit(module)
    return collector.metrics


class _FunctionCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.metrics: list[FunctionMetric] = []
        self._stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = ".".join([*self._stack, node.name]) if self._stack else node.name
        branches, nesting = _body_metrics(node.body)
        end_line = getattr(node, "end_lineno", node.lineno)
        self.metrics.append(
            FunctionMetric(
                qualname=qualname,
                line=node.lineno,
                length=end_line - node.lineno + 1,
                branches=branches,
                nesting=nesting,
            )
        )
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()


def _body_metrics(statements: Sequence[ast.stmt]) -> tuple[int, int]:
    branches = 0
    max_depth = 0
    for statement in statements:
        child_branches, child_depth = _branch_metrics(statement)
        branches += child_branches
        max_depth = max(max_depth, child_depth)
    return branches, max_depth


def _branch_metrics(node: ast.AST, depth: int = 0) -> tuple[int, int]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return 0, depth

    branches = 0
    max_depth = depth
    next_depth = depth
    if isinstance(node, BRANCH_NODES):
        branches += 1
        next_depth = depth + 1
        max_depth = max(max_depth, next_depth)
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        child_branches, child_depth = _branch_metrics(child, next_depth)
        branches += child_branches
        max_depth = max(max_depth, child_depth)
    return branches, max_depth


def _emit_report(report: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    print("Complexity snapshot")
    print(f"- Files scanned: {report['files_scanned']}")
    print(f"- Target findings: {len(report['target_findings'])}")
    print(f"- Block findings: {len(report['block_findings'])}")
    print(f"- Files by group: {report['files_by_group']}")
    _print_findings("Block findings", report["block_findings"])
    _print_findings("Target findings", report["target_findings"], limit=20)


def _emit_check_result(
    report: dict[str, Any],
    regressions: Sequence[dict[str, Any]],
    *,
    as_json: bool,
) -> None:
    payload = {
        "ok": not regressions,
        "regressions": list(regressions),
        "target_findings": report["target_findings"],
        "block_findings": report["block_findings"],
    }
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    print("Complexity check")
    print(f"- Current target findings: {len(report['target_findings'])}")
    print(f"- Current block findings: {len(report['block_findings'])}")
    if regressions:
        _print_findings("Regressions", regressions)
    else:
        print("- Regressions: 0")


def _print_findings(
    title: str,
    findings: Sequence[dict[str, Any]],
    *,
    limit: int | None = None,
) -> None:
    print(title)
    if not findings:
        print("- none")
        return
    for finding in list(findings)[: limit or len(findings)]:
        location = f":{finding['line']}" if finding.get("line") else ""
        symbol = f" {finding['symbol']}" if finding.get("symbol") else ""
        print(
            f"- [{finding['group']}] {finding['metric']} {finding['path']}{location}"
            f"{symbol} actual={finding['actual']} limit={finding['limit']}"
        )
    if limit is not None and len(findings) > limit:
        print(f"- ... {len(findings) - limit} more")


def _sort_findings(findings: Sequence[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda finding: (
            finding.path,
            finding.metric,
            finding.line or 0,
            finding.symbol or "",
            finding.severity,
        ),
    )


def _thresholds_payload() -> dict[str, dict[str, int | None]]:
    payload: dict[str, dict[str, int | None]] = {}
    for group, thresholds in GROUP_THRESHOLDS.items():
        payload[group] = {
            "file_target": thresholds.file_target,
            "file_block": thresholds.file_block,
            "function_target": thresholds.function_target,
            "function_block": thresholds.function_block,
            "branches_target": thresholds.branches_target,
            "branches_block": thresholds.branches_block,
            "nesting_block": thresholds.nesting_block,
        }
    return payload


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
