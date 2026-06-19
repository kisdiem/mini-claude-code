from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mini_cc.agent import Agent
from mini_cc.llm import MockBlock, MockProvider, MockResponse
from mini_cc.permission_ledger import PermissionLedger
from mini_cc.session import SessionStore
from mini_cc.s20 import S20ToolRunner
from mini_cc.tools import ToolRunner
from mini_cc.workflow import ExecutionRecord, ModelAuthoredPlanner, PlanStep, Planner, StructuredWorkflow, TaskPlan, Verifier


class DockerOnceProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, tools, system):
        del messages, tools, system
        self.calls += 1
        if self.calls == 1:
            return MockResponse(
                [
                    MockBlock(
                        type="tool_use",
                        id="toolu_docker",
                        name="run_shell",
                        input={"command": "docker ps", "timeout": 5},
                    )
                ]
            )
        return MockResponse([MockBlock(type="text", text="done")])


class PlanningTextProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[str] = []

    def complete(self, messages, tools, system):
        del tools, system
        self.prompts.append(str(messages[-1]["content"]))
        return MockResponse([MockBlock(type="text", text=self.text)])


class StructuredWorkflowTests(unittest.TestCase):
    def test_planner_marks_benchmark_mode(self) -> None:
        plan = Planner().plan("analyze Terminal-Bench results.json Docker score")

        self.assertEqual(plan.mode, "benchmark")
        self.assertEqual([step.id for step in plan.steps], ["inspect", "execute", "verify", "report"])
        self.assertIn("docker", plan.permission_envelope)

    def test_standard_plan_permission_envelope_excludes_docker(self) -> None:
        plan = Planner().plan("edit a README file")

        self.assertEqual(plan.mode, "standard")
        self.assertIn("workspace_write", plan.permission_envelope)
        self.assertNotIn("docker", plan.permission_envelope)
        self.assertEqual(plan.verification_policy, "required")

    def test_github_clone_plan_includes_network_permission(self) -> None:
        plan = Planner().plan("clone a GitHub repository into the workspace")

        self.assertEqual(plan.mode, "standard")
        self.assertIn("workspace_write", plan.permission_envelope)
        self.assertIn("network", plan.permission_envelope)

    def test_model_authored_planner_accepts_valid_structured_plan(self) -> None:
        provider = PlanningTextProvider(
            json.dumps(
                {
                    "mode": "standard",
                    "steps": [
                        {"id": "inspect", "role": "planner", "goal": "Read README first."},
                        {"id": "execute", "role": "executor", "goal": "Apply the requested edit."},
                        {"id": "verify", "role": "verifier", "goal": "Check the changed file."},
                    ],
                    "permission_envelope": ["read", "verify", "workspace_write"],
                }
            )
        )

        plan = ModelAuthoredPlanner(provider).plan("edit a README file")

        self.assertEqual([step.goal for step in plan.steps], ["Read README first.", "Apply the requested edit.", "Check the changed file."])
        self.assertEqual(plan.planning_issues, [])
        self.assertIn("allowed_permission_envelope", provider.prompts[0])

    def test_model_authored_planner_falls_back_on_invalid_json(self) -> None:
        plan = ModelAuthoredPlanner(PlanningTextProvider("not json")).plan("edit a README file")

        self.assertEqual([step.id for step in plan.steps], ["inspect", "execute", "verify"])
        self.assertTrue(any("model plan unavailable" in issue for issue in plan.planning_issues))

    def test_model_authored_planner_filters_permission_envelope_expansion(self) -> None:
        provider = PlanningTextProvider(
            json.dumps(
                {
                    "mode": "standard",
                    "steps": [{"id": "execute", "role": "executor", "goal": "Edit the file."}],
                    "permission_envelope": ["read", "verify", "workspace_write", "docker", "network"],
                }
            )
        )

        plan = ModelAuthoredPlanner(provider).plan("edit a README file")

        self.assertIn("workspace_write", plan.permission_envelope)
        self.assertNotIn("docker", plan.permission_envelope)
        self.assertNotIn("network", plan.permission_envelope)
        self.assertTrue(any("permission envelope filtered" in issue for issue in plan.planning_issues))

    def test_verifier_requires_explicit_signal_for_benchmark(self) -> None:
        plan = Planner().plan("Terminal-Bench score")
        result = Verifier().verify(plan, [])

        self.assertFalse(result.ok)
        self.assertFalse(result.verified)
        self.assertIn("requires", result.reason)
        self.assertEqual(result.verification_policy, "required")
        self.assertTrue(result.verification_required)

    def test_verifier_requires_explicit_signal_for_write_risk(self) -> None:
        plan = Planner().plan("edit a file")
        result = Verifier().verify(plan, [])

        self.assertFalse(result.ok)
        self.assertFalse(result.verified)
        self.assertIn("task risk requires", result.reason)
        self.assertEqual(result.verification_policy, "required")
        self.assertTrue(result.verification_required)
        self.assertTrue(result.plan_repair.needed)
        self.assertIn("missing_required_verification", result.plan_repair.reasons)
        self.assertIn("verify", result.plan_repair.missing_steps)

    def test_verifier_allows_low_risk_task_without_explicit_signal(self) -> None:
        plan = TaskPlan(
            mode="standard",
            steps=[PlanStep("inspect", "planner", "Read only."), PlanStep("execute", "executor", "Summarize.")],
            permission_envelope=["read", "verify"],
            verification_policy="optional",
        )

        result = Verifier().verify(plan, [ExecutionRecord(turn=1, tool="read_file", planned_step="inspect", is_error=False, chars=20)])

        self.assertTrue(result.ok)
        self.assertFalse(result.verified)
        self.assertEqual(result.verification_policy, "optional")
        self.assertFalse(result.verification_required)
        self.assertFalse(result.plan_repair.needed)

    def test_verifier_records_failed_tools(self) -> None:
        plan = Planner().plan("edit a file")
        result = Verifier().verify(
            plan,
            [
                ExecutionRecord(turn=1, tool="read_file", planned_step="inspect", is_error=False, chars=10, summary="Read target file."),
                ExecutionRecord(turn=2, tool="run_shell", planned_step="verify", is_error=True, chars=20, summary="pytest failed"),
            ],
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.failed_tools, ["run_shell"])
        self.assertEqual(result.evidence_ledger[0].kind, "tool")
        self.assertEqual(result.evidence_ledger[1].kind, "failure")
        self.assertEqual(result.evidence_ledger[1].summary, "pytest failed")
        self.assertTrue(result.plan_repair.needed)
        self.assertIn("tool_failure", result.plan_repair.reasons)

    def test_agent_records_plan_execution_and_verifier_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "README.md").write_text("# Demo\n", encoding="utf-8")
            sessions = root / ".mini_cc" / "sessions"
            output: list[str] = []
            agent = Agent(
                MockProvider(),
                S20ToolRunner(root, permission="auto"),
                max_turns=3,
                output=output.append,
                session_store=SessionStore(sessions),
                workflow=StructuredWorkflow(),
            )

            agent.run("s20 snapshot")

            session_files = list(sessions.glob("*.json"))
            self.assertEqual(len(session_files), 1)
            payload = json.loads(session_files[0].read_text(encoding="utf-8"))
            events = payload["events"]
            names = [event["event"] for event in events]
            self.assertIn("planner_plan", names)
            self.assertIn("executor_tool_use", names)
            self.assertIn("verifier_result", names)
            self.assertIn("evidence_ledger", names)
            self.assertIn("plan_repair", names)
            verifier = [event for event in events if event["event"] == "verifier_result"][0]["payload"]
            self.assertTrue(verifier["ok"])
            self.assertTrue(verifier["verified"])
            self.assertEqual(verifier["verification_policy"], "required")
            self.assertEqual(verifier["evidence_ledger"][0]["tool"], "context_snapshot")
            executor = [event for event in events if event["event"] == "executor_tool_use"][0]["payload"]
            self.assertEqual(executor["name"], "context_snapshot")
            self.assertEqual(executor["planned_step"], "verify")
            envelope = [event for event in events if event["event"] == "permission_envelope"][0]["payload"]
            self.assertEqual(envelope["mode"], "standard")
            self.assertIn("workspace_write", envelope["allowed_risks"])
            planner = [event for event in events if event["event"] == "planner_plan"][0]["payload"]
            self.assertEqual(planner["verification_policy"], "required")
            evidence = [event for event in events if event["event"] == "evidence_ledger"][0]["payload"]
            self.assertEqual(evidence["items"][0]["kind"], "verification")
            repair = [event for event in events if event["event"] == "plan_repair"][0]["payload"]
            self.assertFalse(repair["needed"])

    def test_plan_scoped_permission_envelope_blocks_unplanned_docker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sessions = root / ".mini_cc" / "sessions"
            ledger = root / ".mini_cc" / "permission-ledger.jsonl"
            output: list[str] = []
            agent = Agent(
                DockerOnceProvider(),  # type: ignore[arg-type]
                ToolRunner(root, permission="auto", permission_ledger=PermissionLedger(ledger)),
                max_turns=2,
                output=output.append,
                session_store=SessionStore(sessions),
                workflow=StructuredWorkflow(),
            )

            agent.run("edit a README file")

            rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[0]["decision"], "denied")
            self.assertEqual(rows[0]["risk"], "docker")
            self.assertIn("plan-scoped", rows[0]["reason"])
            self.assertIn("plan-scoped", "\n".join(output))


if __name__ == "__main__":
    unittest.main()
