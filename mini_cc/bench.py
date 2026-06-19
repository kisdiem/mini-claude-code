from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class BenchClassification:
    category: str
    reason: str


@dataclass(frozen=True)
class DockerHealth:
    ok: bool
    reason: str


@dataclass(frozen=True)
class ShardPlan:
    index: int
    task_ids: list[str]
    output_dir: Path


@dataclass(frozen=True)
class ShardResult:
    index: int
    task_ids: list[str]
    status: str
    command: str
    returncode: int | None = None
    reason: str = ""
    attempt: int = 1
    completed_task_ids: list[str] | None = None


@dataclass(frozen=True)
class BenchSummary:
    total: int
    resolved: int
    categories: dict[str, int]

    @property
    def score(self) -> float:
        if self.total == 0:
            return 0.0
        return self.resolved / self.total


@dataclass(frozen=True)
class BenchmarkTaskReport:
    task_id: str
    shard: int | None
    category: str
    resolved: bool
    reason: str
    results_path: Path


@dataclass(frozen=True)
class BenchmarkShardReport:
    index: int
    status: str
    task_count: int
    resolved: int
    score: float
    categories: dict[str, int]
    results_path: Path
    command: str = ""
    reason: str = ""
    attempt: int = 1


@dataclass(frozen=True)
class BenchmarkReport:
    output_dir: Path
    total: int
    resolved: int
    score: float
    categories: dict[str, int]
    shards: list[BenchmarkShardReport]
    tasks: list[BenchmarkTaskReport]
    recommendations: list[str]
    invalid_run: bool
    invalid_reasons: list[str]


@dataclass(frozen=True)
class BenchmarkAutomationGate:
    name: str
    passed: bool
    reason: str


@dataclass(frozen=True)
class BenchmarkAutomationResult:
    output_dir: Path
    report_dir: Path
    manifest_path: Path
    aggregate_summary_path: Path
    report_json_path: Path
    report_markdown_path: Path
    automation_path: Path
    shard_results: list[ShardResult]
    report: BenchmarkReport
    gates: list[BenchmarkAutomationGate]

    @property
    def ok(self) -> bool:
        return all(gate.passed for gate in self.gates)


@dataclass(frozen=True)
class TerminalBenchPreflightCheck:
    name: str
    ok: bool
    reason: str


@dataclass(frozen=True)
class TerminalBenchPreflight:
    ok: bool
    checks: list[TerminalBenchPreflightCheck]
    command_preview: str
    task_count: int
    output_dir: Path


@dataclass(frozen=True)
class TerminalBenchRealRunResult:
    ok: bool
    preflight: TerminalBenchPreflight
    preflight_path: Path
    automation: BenchmarkAutomationResult | None = None


@dataclass(frozen=True)
class CommandRun:
    returncode: int


class DockerHealthChecker:
    def __init__(self, *, timeout: int = 10) -> None:
        self.timeout = timeout

    def check(self) -> DockerHealth:
        try:
            completed = subprocess.run(
                ["docker", "info"],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except FileNotFoundError:
            return DockerHealth(False, "docker executable was not found")
        except subprocess.TimeoutExpired:
            return DockerHealth(False, f"docker info timed out after {self.timeout}s")
        text = (completed.stdout + "\n" + completed.stderr).strip()
        if completed.returncode != 0:
            reason = text.splitlines()[0] if text else f"docker info exited {completed.returncode}"
            return DockerHealth(False, reason)
        return DockerHealth(True, "docker info succeeded")


class TerminalBenchShardRunner:
    """Run Terminal-Bench task ids in shards with a Docker health gate."""

    def __init__(
        self,
        *,
        task_ids: list[str],
        command_template: str,
        output_dir: Path,
        shard_size: int = 5,
        docker_health: DockerHealthChecker | None = None,
        dry_run: bool = False,
        resume: bool = False,
        resume_tasks: bool = True,
        max_retries: int = 0,
        retry_environment_failures: bool = True,
        run_command: Callable[[str, Path], Any] | None = None,
    ) -> None:
        if shard_size < 1:
            raise ValueError("shard_size must be >= 1")
        self.task_ids = task_ids
        self.command_template = command_template
        self.output_dir = output_dir
        self.shard_size = shard_size
        self.docker_health = docker_health or DockerHealthChecker()
        self.dry_run = dry_run
        self.resume = resume
        self.resume_tasks = resume_tasks
        self.max_retries = max(0, int(max_retries))
        self.retry_environment_failures = retry_environment_failures
        self.run_command = run_command or self._run_command
        self.manifest_path = self.output_dir / "shard-manifest.json"

    def plan(self) -> list[ShardPlan]:
        plans: list[ShardPlan] = []
        for offset in range(0, len(self.task_ids), self.shard_size):
            index = len(plans) + 1
            task_ids = self.task_ids[offset : offset + self.shard_size]
            plans.append(ShardPlan(index, task_ids, self.output_dir / f"shard-{index:03d}"))
        return plans

    def run(self) -> list[ShardResult]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        results: list[ShardResult] = []
        completed = self.load_completed_checkpoint() if self.resume else {}
        completed_tasks = self.load_completed_task_ids() if self.resume and self.resume_tasks else {}
        for shard in self.plan():
            command = self.render_command(shard)
            checkpoint = completed.get(shard.index)
            if checkpoint is not None and checkpoint.task_ids == shard.task_ids:
                results.append(
                    ShardResult(
                        shard.index,
                        shard.task_ids,
                        "resumed",
                        command,
                        returncode=checkpoint.returncode,
                        reason="already passed in checkpoint",
                    )
                )
                continue

            task_ids = [
                task_id
                for task_id in shard.task_ids
                if task_id not in completed_tasks.get(shard.index, set())
            ]
            if not task_ids:
                results.append(
                    ShardResult(
                        shard.index,
                        shard.task_ids,
                        "resumed",
                        command,
                        reason="all shard task ids already resolved in results checkpoint",
                        completed_task_ids=shard.task_ids,
                    )
                )
                continue

            active_shard = ShardPlan(shard.index, task_ids, shard.output_dir)
            command = self.render_command(active_shard)
            health = self.docker_health.check()
            if not health.ok:
                results.append(
                    ShardResult(
                        shard.index,
                        task_ids,
                        "skipped_docker_unhealthy",
                        command,
                        reason=health.reason,
                    )
                )
                break
            if self.dry_run:
                results.append(
                    ShardResult(
                        shard.index,
                        task_ids,
                        "planned",
                        command,
                        completed_task_ids=sorted(completed_tasks.get(shard.index, set())),
                    )
                )
                continue

            shard.output_dir.mkdir(parents=True, exist_ok=True)
            result = self.run_shard_with_retries(active_shard, command)
            results.append(result)
            if result.status == "failed":
                break
        self.write_manifest(results)
        self.write_aggregate_summary()
        return results

    def run_shard_with_retries(self, shard: ShardPlan, command: str) -> ShardResult:
        last_returncode: int | None = None
        for attempt in range(1, self.max_retries + 2):
            completed = self.run_command(command, self.output_dir)
            last_returncode = int(completed.returncode)
            summary = summarize_terminal_bench_results(shard.output_dir / "results.json")
            if last_returncode == 0:
                return ShardResult(
                    shard.index,
                    shard.task_ids,
                    "passed",
                    command,
                    returncode=last_returncode,
                    attempt=attempt,
                )
            if (
                attempt <= self.max_retries
                and self.retry_environment_failures
                and summary is not None
                and summary.total > 0
                and is_environment_only_summary(summary)
            ):
                health = self.docker_health.check()
                if not health.ok:
                    return ShardResult(
                        shard.index,
                        shard.task_ids,
                        "skipped_docker_unhealthy",
                        command,
                        returncode=last_returncode,
                        reason=health.reason,
                        attempt=attempt,
                    )
                continue
            reason = "terminal-bench command failed"
            if summary is not None:
                reason = json.dumps(
                    {
                        "total": summary.total,
                        "resolved": summary.resolved,
                        "score": summary.score,
                        "categories": summary.categories,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            return ShardResult(
                shard.index,
                shard.task_ids,
                "failed",
                command,
                returncode=last_returncode,
                reason=reason,
                attempt=attempt,
            )
        return ShardResult(shard.index, shard.task_ids, "failed", command, returncode=last_returncode)

    def _run_command(self, command: str, cwd: Path) -> Any:
        return subprocess.run(command, shell=True, cwd=cwd)

    def render_command(self, shard: ShardPlan) -> str:
        task_ids = ",".join(shard.task_ids)
        return self.command_template.format(
            task_ids=task_ids,
            task_args=" ".join(f"--task-id {task_id}" for task_id in shard.task_ids),
            output_dir=str(shard.output_dir),
            shard_index=shard.index,
        )

    def write_manifest(self, results: list[ShardResult]) -> None:
        payload = {
            "task_count": len(self.task_ids),
            "shard_size": self.shard_size,
            "results": [
                {
                    "index": result.index,
                    "task_ids": result.task_ids,
                    "status": result.status,
                    "command": result.command,
                    "returncode": result.returncode,
                    "reason": result.reason,
                    "attempt": result.attempt,
                    "completed_task_ids": result.completed_task_ids or [],
                }
                for result in results
            ],
        }
        self.manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def load_completed_checkpoint(self) -> dict[int, ShardResult]:
        if not self.manifest_path.exists():
            return {}
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        completed: dict[int, ShardResult] = {}
        for row in payload.get("results", []):
            if not isinstance(row, dict) or row.get("status") != "passed":
                continue
            try:
                index = int(row["index"])
                task_ids = [str(item) for item in row.get("task_ids", [])]
            except (KeyError, TypeError, ValueError):
                continue
            completed[index] = ShardResult(
                index=index,
                task_ids=task_ids,
                status="passed",
                command=str(row.get("command") or ""),
                returncode=row.get("returncode"),
                reason=str(row.get("reason") or ""),
            )
        return completed

    def load_completed_task_ids(self) -> dict[int, set[str]]:
        completed: dict[int, set[str]] = {}
        for shard in self.plan():
            summary_payload = load_terminal_bench_results(shard.output_dir / "results.json")
            if summary_payload is None:
                continue
            for result in summary_payload:
                task_id = str(result.get("task_id") or "")
                if task_id and result.get("is_resolved") is True:
                    completed.setdefault(shard.index, set()).add(task_id)
        return completed

    def aggregate_results(self) -> BenchSummary:
        aggregate = BenchSummary(0, 0, {})
        total = 0
        resolved = 0
        categories: dict[str, int] = {}
        for shard in self.plan():
            summary = summarize_terminal_bench_results(shard.output_dir / "results.json")
            if summary is None:
                continue
            total += summary.total
            resolved += summary.resolved
            for category, count in summary.categories.items():
                categories[category] = categories.get(category, 0) + count
        return BenchSummary(total, resolved, categories) if total else aggregate

    def write_aggregate_summary(self) -> BenchSummary:
        summary = self.aggregate_results()
        payload = {
            "total": summary.total,
            "resolved": summary.resolved,
            "score": summary.score,
            "categories": summary.categories,
        }
        (self.output_dir / "aggregate-summary.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return summary


def load_task_ids(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
        if isinstance(payload, list):
            return [str(item) for item in payload]
        if isinstance(payload, dict):
            for key in ("task_ids", "tasks"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [str(item) for item in value]
        raise ValueError(f"Could not find task_ids list in {path}")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def load_terminal_bench_results(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists() or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("results"), list):
        return [item for item in payload["results"] if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return None


def summarize_terminal_bench_results(path: Path) -> BenchSummary | None:
    rows = load_terminal_bench_results(path)
    if rows is None:
        return None
    categories: dict[str, int] = {}
    resolved = 0
    for row in rows:
        classification = classify_terminal_bench_result(row)
        categories[classification.category] = categories.get(classification.category, 0) + 1
        if row.get("is_resolved") is True:
            resolved += 1
    return BenchSummary(len(rows), resolved, categories)


def is_environment_only_summary(summary: BenchSummary) -> bool:
    if summary.total == 0:
        return False
    environment_categories = {
        "environment_docker_down",
        "environment_apt_network",
        "agent_install_failed",
    }
    non_resolved = summary.total - summary.resolved
    environment_failures = sum(summary.categories.get(category, 0) for category in environment_categories)
    return non_resolved > 0 and environment_failures == non_resolved


def build_benchmark_report(output_dir: Path) -> BenchmarkReport:
    root = output_dir.expanduser().resolve()
    manifest = load_shard_manifest(root / "shard-manifest.json")
    manifest_rows = {
        int(row["index"]): row
        for row in manifest.get("results", [])
        if isinstance(row, dict) and _can_int(row.get("index"))
    }
    shard_indexes = sorted(set(manifest_rows) | set(discover_result_shard_indexes(root)))
    tasks: list[BenchmarkTaskReport] = []
    shards: list[BenchmarkShardReport] = []
    categories: dict[str, int] = {}
    total = 0
    resolved = 0

    for index in shard_indexes:
        row = manifest_rows.get(index, {})
        results_path = root / f"shard-{index:03d}" / "results.json"
        rows = load_terminal_bench_results(results_path)
        shard_categories: dict[str, int] = {}
        shard_resolved = 0
        if rows is not None:
            for result in rows:
                classification = classify_terminal_bench_result(result)
                task_id = str(result.get("task_id") or "")
                is_resolved = result.get("is_resolved") is True
                shard_categories[classification.category] = shard_categories.get(classification.category, 0) + 1
                categories[classification.category] = categories.get(classification.category, 0) + 1
                shard_resolved += 1 if is_resolved else 0
                total += 1
                resolved += 1 if is_resolved else 0
                tasks.append(
                    BenchmarkTaskReport(
                        task_id=task_id or f"shard-{index:03d}-row-{len(tasks) + 1}",
                        shard=index,
                        category=classification.category,
                        resolved=is_resolved,
                        reason=classification.reason,
                        results_path=results_path,
                    )
                )
        task_count = len(rows) if rows is not None else len(row.get("task_ids", []) if isinstance(row, dict) else [])
        shard_score = shard_resolved / task_count if task_count else 0.0
        shards.append(
            BenchmarkShardReport(
                index=index,
                status=str(row.get("status") or ("read" if rows is not None else "missing_results")),
                task_count=task_count,
                resolved=shard_resolved,
                score=shard_score,
                categories=shard_categories,
                results_path=results_path,
                command=str(row.get("command") or ""),
                reason=str(row.get("reason") or ""),
                attempt=int(row.get("attempt") or 1) if _can_int(row.get("attempt") or 1) else 1,
            )
        )

    missing_result_shards = [shard.index for shard in shards if not shard.results_path.exists()]
    invalid_reasons = find_invalid_run_reasons(
        total=total,
        resolved=resolved,
        categories=categories,
        missing_result_shards=missing_result_shards,
        manifest_rows=manifest_rows,
    )
    score = resolved / total if total else 0.0
    return BenchmarkReport(
        output_dir=root,
        total=total,
        resolved=resolved,
        score=score,
        categories=categories,
        shards=shards,
        tasks=tasks,
        recommendations=build_benchmark_recommendations(
            total=total,
            resolved=resolved,
            categories=categories,
            missing_result_shards=missing_result_shards,
            invalid_reasons=invalid_reasons,
        ),
        invalid_run=bool(invalid_reasons),
        invalid_reasons=invalid_reasons,
    )


def write_benchmark_report(output_dir: Path, report_dir: Path | None = None) -> dict[str, Path]:
    report = build_benchmark_report(output_dir)
    target_dir = (report_dir or report.output_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "benchmark-report.json"
    markdown_path = target_dir / "benchmark-report.md"
    json_path.write_text(
        json.dumps(benchmark_report_to_json(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_benchmark_report_markdown(report) + "\n", encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def run_benchmark_automation(
    *,
    task_ids: list[str],
    command_template: str,
    output_dir: Path,
    report_dir: Path | None = None,
    shard_size: int = 5,
    docker_health: DockerHealthChecker | None = None,
    dry_run: bool = False,
    resume: bool = False,
    resume_tasks: bool = True,
    max_retries: int = 0,
    retry_environment_failures: bool = True,
    target_score: float | None = None,
    require_valid_run: bool = True,
    run_command: Callable[[str, Path], Any] | None = None,
) -> BenchmarkAutomationResult:
    """Run Terminal-Bench shards, parse results, write reports, and evaluate gates."""

    root = output_dir.expanduser().resolve()
    target_report_dir = (report_dir or root).expanduser().resolve()
    runner = TerminalBenchShardRunner(
        task_ids=task_ids,
        command_template=command_template,
        output_dir=root,
        shard_size=shard_size,
        docker_health=docker_health,
        dry_run=dry_run,
        resume=resume,
        resume_tasks=resume_tasks,
        max_retries=max_retries,
        retry_environment_failures=retry_environment_failures,
        run_command=run_command,
    )
    shard_results = runner.run()
    paths = write_benchmark_report(root, target_report_dir)
    report = build_benchmark_report(root)
    gates = evaluate_benchmark_gates(
        report=report,
        shard_results=shard_results,
        planned_shards=len(runner.plan()),
        target_score=target_score,
        require_valid_run=require_valid_run,
        dry_run=dry_run,
    )
    automation_path = target_report_dir / "benchmark-automation.json"
    automation_path.write_text(
        json.dumps(
            benchmark_automation_to_json(
                BenchmarkAutomationResult(
                    output_dir=root,
                    report_dir=target_report_dir,
                    manifest_path=root / "shard-manifest.json",
                    aggregate_summary_path=root / "aggregate-summary.json",
                    report_json_path=paths["json"],
                    report_markdown_path=paths["markdown"],
                    automation_path=automation_path,
                    shard_results=shard_results,
                    report=report,
                    gates=gates,
                )
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return BenchmarkAutomationResult(
        output_dir=root,
        report_dir=target_report_dir,
        manifest_path=root / "shard-manifest.json",
        aggregate_summary_path=root / "aggregate-summary.json",
        report_json_path=paths["json"],
        report_markdown_path=paths["markdown"],
        automation_path=automation_path,
        shard_results=shard_results,
        report=report,
        gates=gates,
    )


def run_terminal_bench_real_pipeline(
    *,
    task_ids: list[str],
    command_template: str,
    output_dir: Path,
    report_dir: Path | None = None,
    shard_size: int = 5,
    docker_health: DockerHealthChecker | None = None,
    dry_run: bool = False,
    resume: bool = False,
    resume_tasks: bool = True,
    max_retries: int = 0,
    retry_environment_failures: bool = True,
    target_score: float | None = None,
    require_valid_run: bool = True,
    preflight_only: bool = False,
    skip_preflight: bool = False,
    executable_exists: Callable[[str], bool] | None = None,
    run_command: Callable[[str, Path], Any] | None = None,
) -> TerminalBenchRealRunResult:
    root = output_dir.expanduser().resolve()
    target_report_dir = (report_dir or root).expanduser().resolve()
    preflight = terminal_bench_preflight(
        task_ids=task_ids,
        command_template=command_template,
        output_dir=root,
        shard_size=shard_size,
        docker_health=docker_health,
        dry_run=dry_run,
        executable_exists=executable_exists,
    )
    target_report_dir.mkdir(parents=True, exist_ok=True)
    preflight_path = target_report_dir / "terminal-bench-preflight.json"
    preflight_path.write_text(
        json.dumps(terminal_bench_preflight_to_json(preflight), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if preflight_only or (not skip_preflight and not preflight.ok):
        return TerminalBenchRealRunResult(ok=preflight.ok if preflight_only else False, preflight=preflight, preflight_path=preflight_path)

    automation = run_benchmark_automation(
        task_ids=task_ids,
        command_template=command_template,
        output_dir=root,
        report_dir=target_report_dir,
        shard_size=shard_size,
        docker_health=docker_health,
        dry_run=dry_run,
        resume=resume,
        resume_tasks=resume_tasks,
        max_retries=max_retries,
        retry_environment_failures=retry_environment_failures,
        target_score=target_score,
        require_valid_run=require_valid_run,
        run_command=run_command,
    )
    return TerminalBenchRealRunResult(
        ok=automation.ok and (preflight.ok or skip_preflight),
        preflight=preflight,
        preflight_path=preflight_path,
        automation=automation,
    )


def terminal_bench_preflight(
    *,
    task_ids: list[str],
    command_template: str,
    output_dir: Path,
    shard_size: int,
    docker_health: DockerHealthChecker | None = None,
    dry_run: bool = False,
    executable_exists: Callable[[str], bool] | None = None,
) -> TerminalBenchPreflight:
    checks: list[TerminalBenchPreflightCheck] = []
    command_preview = render_terminal_bench_command_preview(command_template, task_ids, output_dir)

    checks.append(
        TerminalBenchPreflightCheck(
            "tasks_loaded",
            bool(task_ids),
            f"{len(task_ids)} task id(s) loaded" if task_ids else "no task ids loaded",
        )
    )
    checks.append(
        TerminalBenchPreflightCheck(
            "shard_size",
            shard_size >= 1,
            f"shard_size={shard_size}" if shard_size >= 1 else "shard_size must be >= 1",
        )
    )
    template_ok = "{output_dir}" in command_template and ("{task_args}" in command_template or "{task_ids}" in command_template)
    checks.append(
        TerminalBenchPreflightCheck(
            "command_template",
            template_ok,
            "template includes output_dir and task selector fields"
            if template_ok
            else "template must include {output_dir} and one of {task_args} or {task_ids}",
        )
    )
    executable = first_command_token(command_template)
    exists = True
    if executable:
        exists = (executable_exists or default_executable_exists)(executable)
    checks.append(
        TerminalBenchPreflightCheck(
            "command_executable",
            bool(executable) and exists,
            f"found executable: {executable}" if executable and exists else f"executable not found: {executable or '[none]'}",
        )
    )
    output_parent = output_dir.parent
    checks.append(
        TerminalBenchPreflightCheck(
            "output_parent",
            output_parent.exists(),
            f"output parent exists: {output_parent}" if output_parent.exists() else f"output parent is missing: {output_parent}",
        )
    )
    if dry_run:
        checks.append(TerminalBenchPreflightCheck("docker", True, "dry run skips Docker health requirement"))
    else:
        health = (docker_health or DockerHealthChecker()).check()
        checks.append(TerminalBenchPreflightCheck("docker", health.ok, health.reason))
    return TerminalBenchPreflight(
        ok=all(check.ok for check in checks),
        checks=checks,
        command_preview=command_preview,
        task_count=len(task_ids),
        output_dir=output_dir,
    )


def terminal_bench_preflight_to_json(preflight: TerminalBenchPreflight) -> dict[str, Any]:
    return {
        "ok": preflight.ok,
        "task_count": preflight.task_count,
        "output_dir": str(preflight.output_dir),
        "command_preview": preflight.command_preview,
        "checks": [
            {"name": check.name, "ok": check.ok, "reason": check.reason}
            for check in preflight.checks
        ],
    }


def terminal_bench_real_run_to_json(result: TerminalBenchRealRunResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": result.ok,
        "preflight": terminal_bench_preflight_to_json(result.preflight),
        "preflight_path": str(result.preflight_path),
    }
    if result.automation is not None:
        payload["automation"] = benchmark_automation_to_json(result.automation)
    return payload


def render_terminal_bench_command_preview(command_template: str, task_ids: list[str], output_dir: Path) -> str:
    preview_task_ids = task_ids[: min(2, len(task_ids))]
    shard = ShardPlan(1, preview_task_ids, output_dir / "shard-001")
    try:
        return TerminalBenchShardRunner(
            task_ids=preview_task_ids,
            command_template=command_template,
            output_dir=output_dir,
            dry_run=True,
        ).render_command(shard)
    except (KeyError, ValueError) as exc:
        return f"[command template render failed] {exc}"


def first_command_token(command_template: str) -> str:
    try:
        tokens = shlex.split(command_template, posix=False)
    except ValueError:
        tokens = command_template.split()
    if not tokens:
        return ""
    return tokens[0].strip("\"'")


def default_executable_exists(executable: str) -> bool:
    path = Path(executable)
    if path.is_absolute() or any(separator in executable for separator in ("\\", "/")):
        return path.exists()
    return shutil.which(executable) is not None


def evaluate_benchmark_gates(
    *,
    report: BenchmarkReport,
    shard_results: list[ShardResult],
    planned_shards: int,
    target_score: float | None,
    require_valid_run: bool,
    dry_run: bool,
) -> list[BenchmarkAutomationGate]:
    gates: list[BenchmarkAutomationGate] = []
    complete = len(shard_results) == planned_shards and all(
        result.status in {"passed", "planned", "resumed"} for result in shard_results
    )
    gates.append(
        BenchmarkAutomationGate(
            "shards_complete",
            complete,
            "all planned shards reached a terminal ok status" if complete else "one or more shards failed, stopped, or were skipped",
        )
    )
    parsed = report.total > 0 or dry_run
    gates.append(
        BenchmarkAutomationGate(
            "results_parsed",
            parsed,
            "task results parsed" if report.total > 0 else "dry run has no task results" if dry_run else "no task results parsed",
        )
    )
    valid = not report.invalid_run
    gates.append(
        BenchmarkAutomationGate(
            "valid_run",
            valid or not require_valid_run,
            "report marked run valid"
            if valid
            else "validity not required"
            if not require_valid_run
            else "; ".join(report.invalid_reasons),
        )
    )
    if target_score is not None:
        passed = report.score >= target_score
        gates.append(
            BenchmarkAutomationGate(
                "target_score",
                passed,
                f"score {report.score:.4f} >= target {target_score:.4f}"
                if passed
                else f"score {report.score:.4f} < target {target_score:.4f}",
            )
        )
    return gates


def benchmark_automation_to_json(result: BenchmarkAutomationResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "output_dir": str(result.output_dir),
        "report_dir": str(result.report_dir),
        "artifacts": {
            "manifest": str(result.manifest_path),
            "aggregate_summary": str(result.aggregate_summary_path),
            "benchmark_report_json": str(result.report_json_path),
            "benchmark_report_markdown": str(result.report_markdown_path),
            "benchmark_automation_json": str(result.automation_path),
        },
        "score": {
            "total": result.report.total,
            "resolved": result.report.resolved,
            "score": result.report.score,
            "categories": result.report.categories,
            "invalid_run": result.report.invalid_run,
            "invalid_reasons": result.report.invalid_reasons,
        },
        "gates": [
            {"name": gate.name, "passed": gate.passed, "reason": gate.reason}
            for gate in result.gates
        ],
        "shards": [
            {
                "index": shard.index,
                "task_ids": shard.task_ids,
                "status": shard.status,
                "returncode": shard.returncode,
                "reason": shard.reason,
                "attempt": shard.attempt,
                "completed_task_ids": shard.completed_task_ids or [],
            }
            for shard in result.shard_results
        ],
    }


def benchmark_report_to_json(report: BenchmarkReport) -> dict[str, Any]:
    return {
        "output_dir": str(report.output_dir),
        "total": report.total,
        "resolved": report.resolved,
        "score": report.score,
        "categories": report.categories,
        "invalid_run": report.invalid_run,
        "invalid_reasons": report.invalid_reasons,
        "recommendations": report.recommendations,
        "shards": [
            {
                "index": shard.index,
                "status": shard.status,
                "task_count": shard.task_count,
                "resolved": shard.resolved,
                "score": shard.score,
                "categories": shard.categories,
                "results_path": str(shard.results_path),
                "command": shard.command,
                "reason": shard.reason,
                "attempt": shard.attempt,
            }
            for shard in report.shards
        ],
        "tasks": [
            {
                "task_id": task.task_id,
                "shard": task.shard,
                "category": task.category,
                "resolved": task.resolved,
                "reason": task.reason,
                "results_path": str(task.results_path),
            }
            for task in report.tasks
        ],
    }


def render_benchmark_report_markdown(report: BenchmarkReport) -> str:
    lines = [
        "# Benchmark Report",
        "",
        f"- Output dir: `{report.output_dir}`",
        f"- Total: {report.total}",
        f"- Resolved: {report.resolved}",
        f"- Score: {report.score:.2%}",
        f"- Run validity: {'invalid' if report.invalid_run else 'valid'}",
    ]
    if report.invalid_reasons:
        lines.append(f"- Invalid reasons: {'; '.join(report.invalid_reasons)}")
    lines.extend(["", "## Category Breakdown", ""])
    if report.categories:
        for category, count in sorted(report.categories.items()):
            lines.append(f"- `{category}`: {count}")
    else:
        lines.append("- No parsed task results.")
    lines.extend(["", "## Shards", "", "| Shard | Status | Resolved | Score | Results |", "| --- | --- | ---: | ---: | --- |"])
    for shard in report.shards:
        lines.append(
            f"| {shard.index} | `{shard.status}` | {shard.resolved}/{shard.task_count} | "
            f"{shard.score:.2%} | `{shard.results_path}` |"
        )
    unresolved = [task for task in report.tasks if not task.resolved]
    lines.extend(["", "## Unresolved Tasks", ""])
    if unresolved:
        for task in unresolved:
            shard = task.shard if task.shard is not None else "unknown"
            lines.append(f"- `{task.task_id}` (shard {shard}): `{task.category}` - {task.reason}")
    else:
        lines.append("- None.")
    lines.extend(["", "## Recommendations", ""])
    for recommendation in report.recommendations:
        lines.append(f"- {recommendation}")
    return "\n".join(lines)


def load_shard_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def discover_result_shard_indexes(output_dir: Path) -> list[int]:
    indexes: list[int] = []
    if not output_dir.exists():
        return indexes
    for path in output_dir.glob("shard-*/results.json"):
        name = path.parent.name
        suffix = name.removeprefix("shard-")
        if suffix.isdigit():
            indexes.append(int(suffix))
    return indexes


def find_invalid_run_reasons(
    *,
    total: int,
    resolved: int,
    categories: dict[str, int],
    missing_result_shards: list[int],
    manifest_rows: dict[int, dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    environment_categories = {
        "environment_docker_down",
        "environment_apt_network",
        "agent_install_failed",
    }
    non_resolved = total - resolved
    environment_failures = sum(categories.get(category, 0) for category in environment_categories)
    if missing_result_shards:
        reasons.append("one or more shards have no results.json")
    if total == 0:
        reasons.append("no task results were parsed")
    if non_resolved > 0 and environment_failures == non_resolved:
        reasons.append("all unresolved tasks are environment/setup failures")
    if any(str(row.get("status") or "").startswith("skipped_docker") for row in manifest_rows.values()):
        reasons.append("runner skipped at least one shard because Docker was unhealthy")
    return reasons


def build_benchmark_recommendations(
    *,
    total: int,
    resolved: int,
    categories: dict[str, int],
    missing_result_shards: list[int],
    invalid_reasons: list[str],
) -> list[str]:
    recommendations: list[str] = []
    if missing_result_shards:
        recommendations.append("Re-run missing shards with --tb-resume after confirming each shard writes results.json.")
    if categories.get("environment_docker_down"):
        recommendations.append("Fix Docker Desktop/daemon stability before interpreting the run as an agent score.")
    if categories.get("environment_apt_network"):
        recommendations.append("Stabilize container package/network access or prebuild images before comparing model behavior.")
    if categories.get("agent_install_failed"):
        recommendations.append("Treat agent installation failures as harness setup defects and retry after packaging fixes.")
    if categories.get("model_timeout"):
        recommendations.append("Inspect timeout tasks for long-horizon planning, command stalls, or missing progress checkpoints.")
    if categories.get("unknown_agent_error"):
        recommendations.append("Open the affected shard logs and classify unknown agent errors before making architecture changes.")
    if categories.get("test_failed"):
        recommendations.append("Use unresolved test_failed tasks as the primary signal for agent reasoning/tool-use improvements.")
    if total and resolved == total:
        recommendations.append("The parsed tasks are all resolved; keep the report with the exact shard manifest for reproducibility.")
    if not recommendations:
        recommendations.append("Collect shard results before making benchmark conclusions.")
    if invalid_reasons:
        recommendations.append("Mark this run invalid in comparisons until the invalid reasons are cleared.")
    return recommendations


def _can_int(value: Any) -> bool:
    try:
        int(value)
    except (TypeError, ValueError):
        return False
    return True


def classify_terminal_bench_result(result: dict[str, Any], *, run_log_tail: str = "") -> BenchClassification:
    """Classify Terminal-Bench failures into actionable buckets."""
    failure_mode = str(result.get("failure_mode") or "")
    task_id = str(result.get("task_id") or "")
    text = " ".join(
        [
            task_id,
            failure_mode,
            str(result.get("instruction") or ""),
            str(result.get("parser_results") or ""),
            run_log_tail,
        ]
    ).lower()

    if result.get("is_resolved") is True:
        return BenchClassification("resolved", "parser marked task resolved")
    if "docker desktop is unable to start" in text or "dockerdesktoplinuxengine" in text:
        return BenchClassification("environment_docker_down", "Docker daemon or Desktop engine was unavailable")
    if "deb.debian.org" in text or "apt-get install" in text or "unable to fetch some archives" in text:
        return BenchClassification("environment_apt_network", "container build failed while fetching apt packages")
    if "agent_installation_failed" in text or "install_fail_status" in text:
        return BenchClassification("agent_install_failed", "agent installation failed before model execution")
    if "agent_timeout" in text or "timed out" in text:
        return BenchClassification("model_timeout", "agent execution exceeded task timeout")
    if failure_mode in {"unknown_agent_error", "error"}:
        return BenchClassification("unknown_agent_error", "harness reported an uncategorized agent/runtime error")
    if result.get("parser_results"):
        return BenchClassification("test_failed", "tests ran but parser did not mark task resolved")
    return BenchClassification("unresolved", "task unresolved without enough diagnostic detail")
