from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_cc.bench import (
    CommandRun,
    DockerHealth,
    TerminalBenchShardRunner,
    benchmark_automation_to_json,
    build_benchmark_report,
    load_task_ids,
    render_benchmark_report_markdown,
    run_benchmark_automation,
    run_terminal_bench_real_pipeline,
    terminal_bench_preflight,
    terminal_bench_real_run_to_json,
    write_benchmark_report,
)


class FakeHealth:
    def __init__(self, states: list[DockerHealth]) -> None:
        self.states = states
        self.calls = 0

    def check(self) -> DockerHealth:
        state = self.states[min(self.calls, len(self.states) - 1)]
        self.calls += 1
        return state


class TerminalBenchShardRunnerTests(unittest.TestCase):
    def test_plans_tasks_in_stable_shards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = TerminalBenchShardRunner(
                task_ids=["a", "b", "c", "d", "e"],
                command_template="tb run {task_args} --out {output_dir}",
                output_dir=Path(tmp),
                shard_size=2,
                docker_health=FakeHealth([DockerHealth(True, "ok")]),
                dry_run=True,
            )

            plans = runner.plan()

            self.assertEqual([plan.task_ids for plan in plans], [["a", "b"], ["c", "d"], ["e"]])
            self.assertEqual(plans[0].output_dir.name, "shard-001")

    def test_docker_health_gate_stops_before_unhealthy_shard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = TerminalBenchShardRunner(
                task_ids=["a", "b", "c"],
                command_template="tb run --tasks {task_ids} --out {output_dir}",
                output_dir=Path(tmp),
                shard_size=1,
                docker_health=FakeHealth(
                    [
                        DockerHealth(True, "ok"),
                        DockerHealth(False, "Docker Desktop is unable to start"),
                    ]
                ),
                dry_run=True,
            )

            results = runner.run()

            self.assertEqual([result.status for result in results], ["planned", "skipped_docker_unhealthy"])
            self.assertEqual(results[1].task_ids, ["b"])
            self.assertIn("Docker Desktop", results[1].reason)
            manifest = json.loads(Path(tmp, "shard-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["results"][1]["status"], "skipped_docker_unhealthy")

    def test_resume_skips_passed_shards_without_docker_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "shard-manifest.json").write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "index": 1,
                                "task_ids": ["a"],
                                "status": "passed",
                                "command": "old",
                                "returncode": 0,
                                "reason": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            health = FakeHealth([DockerHealth(False, "Docker is down")])
            runner = TerminalBenchShardRunner(
                task_ids=["a", "b"],
                command_template="tb run --tasks {task_ids} --out {output_dir}",
                output_dir=root,
                shard_size=1,
                docker_health=health,
                dry_run=True,
                resume=True,
            )

            results = runner.run()

            self.assertEqual([result.status for result in results], ["resumed", "skipped_docker_unhealthy"])
            self.assertEqual(health.calls, 1)
            manifest = json.loads(Path(root, "shard-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["results"][0]["status"], "resumed")

    def test_resume_does_not_skip_failed_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "shard-manifest.json").write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "index": 1,
                                "task_ids": ["a"],
                                "status": "failed",
                                "command": "old",
                                "returncode": 1,
                                "reason": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            health = FakeHealth([DockerHealth(True, "ok")])
            runner = TerminalBenchShardRunner(
                task_ids=["a"],
                command_template="tb run --tasks {task_ids} --out {output_dir}",
                output_dir=root,
                shard_size=1,
                docker_health=health,
                dry_run=True,
                resume=True,
            )

            results = runner.run()

            self.assertEqual([result.status for result in results], ["planned"])
            self.assertEqual(health.calls, 1)

    def test_task_level_resume_runs_only_unresolved_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shard_dir = root / "shard-001"
            shard_dir.mkdir()
            Path(shard_dir, "results.json").write_text(
                json.dumps({"results": [{"task_id": "a", "is_resolved": True}]}),
                encoding="utf-8",
            )
            health = FakeHealth([DockerHealth(True, "ok")])
            runner = TerminalBenchShardRunner(
                task_ids=["a", "b"],
                command_template="tb run {task_args} --out {output_dir}",
                output_dir=root,
                shard_size=2,
                docker_health=health,
                dry_run=True,
                resume=True,
            )

            results = runner.run()

            self.assertEqual(results[0].status, "planned")
            self.assertEqual(results[0].task_ids, ["b"])
            self.assertEqual(results[0].completed_task_ids, ["a"])
            self.assertIn("--task-id b", results[0].command)
            self.assertNotIn("--task-id a", results[0].command)

    def test_environment_only_failed_shard_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attempts = {"count": 0}

            def fake_run(_command: str, _cwd: Path) -> CommandRun:
                attempts["count"] += 1
                shard_dir = root / "shard-001"
                shard_dir.mkdir(exist_ok=True)
                if attempts["count"] == 1:
                    Path(shard_dir, "results.json").write_text(
                        json.dumps(
                            {
                                "results": [
                                    {
                                        "task_id": "a",
                                        "is_resolved": False,
                                        "failure_mode": "unknown_agent_error",
                                        "instruction": "Docker Desktop is unable to start",
                                    }
                                ]
                            }
                        ),
                        encoding="utf-8",
                    )
                    return CommandRun(1)
                Path(shard_dir, "results.json").write_text(
                    json.dumps({"results": [{"task_id": "a", "is_resolved": True}]}),
                    encoding="utf-8",
                )
                return CommandRun(0)

            runner = TerminalBenchShardRunner(
                task_ids=["a"],
                command_template="tb run {task_args} --out {output_dir}",
                output_dir=root,
                shard_size=1,
                docker_health=FakeHealth([DockerHealth(True, "ok"), DockerHealth(True, "ok")]),
                run_command=fake_run,
                max_retries=1,
            )

            results = runner.run()

            self.assertEqual(results[0].status, "passed")
            self.assertEqual(results[0].attempt, 2)
            self.assertEqual(attempts["count"], 2)

    def test_aggregate_summary_reads_all_shard_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, payload in {
                "shard-001": {"results": [{"task_id": "a", "is_resolved": True}]},
                "shard-002": {"results": [{"task_id": "b", "is_resolved": False, "parser_results": {"x": False}}]},
            }.items():
                shard_dir = root / name
                shard_dir.mkdir()
                Path(shard_dir, "results.json").write_text(json.dumps(payload), encoding="utf-8")
            runner = TerminalBenchShardRunner(
                task_ids=["a", "b"],
                command_template="tb run {task_args} --out {output_dir}",
                output_dir=root,
                shard_size=1,
                docker_health=FakeHealth([DockerHealth(True, "ok")]),
                dry_run=True,
            )

            summary = runner.aggregate_results()

            self.assertEqual(summary.total, 2)
            self.assertEqual(summary.resolved, 1)
            self.assertEqual(summary.score, 0.5)
            self.assertEqual(summary.categories["resolved"], 1)
            self.assertEqual(summary.categories["test_failed"], 1)

    def test_load_task_ids_from_text_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text_path = root / "tasks.txt"
            text_path.write_text("# comment\na\n\nb\n", encoding="utf-8")
            json_path = root / "tasks.json"
            json_path.write_text(json.dumps({"task_ids": ["x", "y"]}), encoding="utf-8")

            self.assertEqual(load_task_ids(text_path), ["a", "b"])
            self.assertEqual(load_task_ids(json_path), ["x", "y"])

    def test_benchmark_report_reads_shards_and_recommends_next_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "shard-manifest.json").write_text(
                json.dumps(
                    {
                        "results": [
                            {"index": 1, "task_ids": ["a", "b"], "status": "failed", "attempt": 2},
                            {
                                "index": 2,
                                "task_ids": ["c"],
                                "status": "skipped_docker_unhealthy",
                                "reason": "Docker Desktop is unable to start",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            shard_001 = root / "shard-001"
            shard_001.mkdir()
            Path(shard_001, "results.json").write_text(
                json.dumps(
                    {
                        "results": [
                            {"task_id": "a", "is_resolved": True},
                            {"task_id": "b", "is_resolved": False, "parser_results": {"ok": False}},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            shard_002 = root / "shard-002"
            shard_002.mkdir()
            Path(shard_002, "results.json").write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "task_id": "c",
                                "is_resolved": False,
                                "instruction": "Docker Desktop is unable to start",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            report = build_benchmark_report(root)
            markdown = render_benchmark_report_markdown(report)

            self.assertEqual(report.total, 3)
            self.assertEqual(report.resolved, 1)
            self.assertEqual(report.categories["resolved"], 1)
            self.assertEqual(report.categories["test_failed"], 1)
            self.assertEqual(report.categories["environment_docker_down"], 1)
            self.assertTrue(report.invalid_run)
            self.assertIn("Docker", " ".join(report.recommendations))
            self.assertIn("test_failed", markdown)
            self.assertIn("`b`", markdown)

    def test_write_benchmark_report_outputs_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shard = root / "shard-001"
            shard.mkdir()
            Path(shard, "results.json").write_text(
                json.dumps({"results": [{"task_id": "a", "is_resolved": True}]}),
                encoding="utf-8",
            )

            paths = write_benchmark_report(root)

            self.assertTrue(paths["json"].exists())
            self.assertTrue(paths["markdown"].exists())
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(payload["total"], 1)
            self.assertEqual(payload["resolved"], 1)
            self.assertIn("Benchmark Report", paths["markdown"].read_text(encoding="utf-8"))

    def test_benchmark_automation_writes_reports_and_passes_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report_dir = root / "reports"

            def fake_run(_command: str, _cwd: Path) -> CommandRun:
                shard = root / "shard-001"
                shard.mkdir(exist_ok=True)
                Path(shard, "results.json").write_text(
                    json.dumps(
                        {
                            "results": [
                                {"task_id": "a", "is_resolved": True},
                                {"task_id": "b", "is_resolved": True},
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                return CommandRun(0)

            result = run_benchmark_automation(
                task_ids=["a", "b"],
                command_template="tb run {task_args} --out {output_dir}",
                output_dir=root,
                report_dir=report_dir,
                shard_size=2,
                docker_health=FakeHealth([DockerHealth(True, "ok")]),
                run_command=fake_run,
                target_score=1.0,
            )

            self.assertTrue(result.ok)
            self.assertTrue(result.manifest_path.exists())
            self.assertTrue(result.aggregate_summary_path.exists())
            self.assertTrue(result.report_json_path.exists())
            self.assertTrue(result.report_markdown_path.exists())
            self.assertTrue(result.automation_path.exists())
            payload = json.loads(result.automation_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["score"]["score"], 1.0)
            self.assertEqual({gate["name"] for gate in payload["gates"]}, {"shards_complete", "results_parsed", "valid_run", "target_score"})

    def test_benchmark_automation_fails_target_score_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_run(_command: str, _cwd: Path) -> CommandRun:
                shard = root / "shard-001"
                shard.mkdir(exist_ok=True)
                Path(shard, "results.json").write_text(
                    json.dumps(
                        {
                            "results": [
                                {"task_id": "a", "is_resolved": True},
                                {"task_id": "b", "is_resolved": False, "parser_results": {"ok": False}},
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                return CommandRun(1)

            result = run_benchmark_automation(
                task_ids=["a", "b"],
                command_template="tb run {task_args} --out {output_dir}",
                output_dir=root,
                shard_size=2,
                docker_health=FakeHealth([DockerHealth(True, "ok")]),
                run_command=fake_run,
                target_score=0.99,
                require_valid_run=False,
            )
            payload = benchmark_automation_to_json(result)

            self.assertFalse(result.ok)
            target_gate = next(gate for gate in payload["gates"] if gate["name"] == "target_score")
            self.assertFalse(target_gate["passed"])
            self.assertIn("< target", target_gate["reason"])

    def test_terminal_bench_preflight_checks_real_run_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            preflight = terminal_bench_preflight(
                task_ids=["a", "b"],
                command_template="tb run {task_args} --output-path {output_dir}",
                output_dir=root / "out",
                shard_size=2,
                docker_health=FakeHealth([DockerHealth(True, "ok")]),
                executable_exists=lambda executable: executable == "tb",
            )

            self.assertTrue(preflight.ok)
            self.assertIn("--task-id a", preflight.command_preview)
            self.assertEqual({check.name for check in preflight.checks}, {"tasks_loaded", "shard_size", "command_template", "command_executable", "output_parent", "docker"})

    def test_real_run_pipeline_stops_when_preflight_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            calls = {"count": 0}

            def fake_run(_command: str, _cwd: Path) -> CommandRun:
                calls["count"] += 1
                return CommandRun(0)

            result = run_terminal_bench_real_pipeline(
                task_ids=["a"],
                command_template="missing-tb run {task_args} --output-path {output_dir}",
                output_dir=root / "out",
                docker_health=FakeHealth([DockerHealth(True, "ok")]),
                executable_exists=lambda _executable: False,
                run_command=fake_run,
            )
            payload = terminal_bench_real_run_to_json(result)

            self.assertFalse(result.ok)
            self.assertIsNone(result.automation)
            self.assertEqual(calls["count"], 0)
            self.assertTrue(result.preflight_path.exists())
            self.assertFalse(payload["preflight"]["ok"])

    def test_real_run_pipeline_can_skip_preflight_and_execute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_run(_command: str, _cwd: Path) -> CommandRun:
                shard = root / "out" / "shard-001"
                shard.mkdir(parents=True, exist_ok=True)
                Path(shard, "results.json").write_text(
                    json.dumps({"results": [{"task_id": "a", "is_resolved": True}]}),
                    encoding="utf-8",
                )
                return CommandRun(0)

            result = run_terminal_bench_real_pipeline(
                task_ids=["a"],
                command_template="missing-tb run {task_args} --output-path {output_dir}",
                output_dir=root / "out",
                docker_health=FakeHealth([DockerHealth(True, "ok"), DockerHealth(True, "ok")]),
                executable_exists=lambda _executable: False,
                run_command=fake_run,
                skip_preflight=True,
            )

            self.assertTrue(result.ok)
            self.assertIsNotNone(result.automation)
            self.assertFalse(result.preflight.ok)
            self.assertTrue(Path(root, "out", "benchmark-automation.json").exists())

    def test_real_run_pipeline_preflight_only_writes_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            result = run_terminal_bench_real_pipeline(
                task_ids=["a"],
                command_template="tb run {task_args} --output-path {output_dir}",
                output_dir=root / "out",
                docker_health=FakeHealth([DockerHealth(True, "ok")]),
                executable_exists=lambda executable: executable == "tb",
                preflight_only=True,
            )

            self.assertTrue(result.ok)
            self.assertIsNone(result.automation)
            payload = json.loads(result.preflight_path.read_text(encoding="utf-8"))
            self.assertTrue(payload["ok"])
            self.assertIn("command_preview", payload)


if __name__ == "__main__":
    unittest.main()
