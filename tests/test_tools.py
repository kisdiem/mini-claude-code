from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mini_cc.hooks import HookDecision, HookRuntime
from mini_cc.permission import PermissionRisk, classify_shell_command
from mini_cc.permission_ledger import PermissionLedger
from mini_cc.s20 import S20ToolRunner
from mini_cc.tools import ToolRunner


class ToolRunnerTests(unittest.TestCase):
    def test_blocks_paths_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(Path(tmp), permission="auto")
            result = runner.run("read_file", {"path": "../secret.txt"})
            self.assertTrue(result.is_error)
            self.assertIn("escapes workspace", result.content)

    def test_write_read_and_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(Path(tmp), permission="auto")
            write = runner.run(
                "write_file",
                {"path": "notes/example.txt", "content": "hello\nworld\n"},
            )
            self.assertFalse(write.is_error, write.content)
            self.assertEqual(Path(tmp, "notes/example.txt").read_bytes(), b"hello\nworld\n")

            read = runner.run(
                "read_file",
                {"path": "notes/example.txt", "start_line": 1, "max_lines": 2},
            )
            self.assertFalse(read.is_error, read.content)
            self.assertIn("1: hello", read.content)

            search = runner.run(
                "search_text",
                {"pattern": "world", "path": ".", "max_matches": 5},
            )
            self.assertFalse(search.is_error, search.content)
            self.assertIn("notes/example.txt:2", search.content)

    def test_read_only_denies_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(Path(tmp), permission="read-only")
            result = runner.run("write_file", {"path": "x.txt", "content": "nope"})
            self.assertTrue(result.is_error)
            self.assertIn("read-only", result.content)

    def test_read_only_allows_read_shell_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(Path(tmp), permission="read-only")
            result = runner.run("run_shell", {"command": "dir", "timeout": 5})
            self.assertFalse(result.is_error, result.content)

    def test_run_shell_decodes_windows_local_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(Path(tmp), permission="read-only")
            completed = subprocess.CompletedProcess(
                args="dir",
                returncode=0,
                stdout="桌面\n".encode("gbk"),
                stderr=b"",
            )
            with patch("mini_cc.tools.subprocess.run", return_value=completed):
                result = runner.run("run_shell", {"command": "dir", "timeout": 5})

            self.assertFalse(result.is_error, result.content)
            self.assertIn("桌面", result.content)

    def test_run_shell_zero_timeout_disables_subprocess_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(Path(tmp), permission="read-only", shell_timeout=0)
            completed = subprocess.CompletedProcess(args="dir", returncode=0, stdout=b"ok\n", stderr=b"")
            with patch("mini_cc.tools.subprocess.run", return_value=completed) as mocked_run:
                result = runner.run("run_shell", {"command": "dir", "timeout": 0})

            self.assertFalse(result.is_error, result.content)
            self.assertIsNone(mocked_run.call_args.kwargs["timeout"])

    def test_read_only_denies_shell_write_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(Path(tmp), permission="read-only")
            result = runner.run(
                "run_shell",
                {"command": "powershell -NoProfile -Command \"Set-Content -Path x.txt -Value nope\""},
            )
            self.assertTrue(result.is_error)
            self.assertIn("workspace_write", result.content)

    def test_auto_blocks_high_risk_shell_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = ToolRunner(Path(tmp), permission="auto")

            git_push = runner.run("run_shell", {"command": "git push origin main"})
            self.assertTrue(git_push.is_error)
            self.assertIn("git_remote_write", git_push.content)

            docker_prune = runner.run("run_shell", {"command": "docker system prune -af"})
            self.assertTrue(docker_prune.is_error)
            self.assertIn("destructive", docker_prune.content)

    def test_git_clone_is_classified_as_network_command(self) -> None:
        decision = classify_shell_command("git clone https://github.com/openvla/openvla.git")

        self.assertTrue(decision.allow)
        self.assertEqual(decision.risk, PermissionRisk.NETWORK)

    def test_read_only_denial_emits_permission_denied_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp, "hooks.log")
            ledger_path = Path(tmp, "permission-ledger.jsonl")
            hooks = HookRuntime(log_path)
            runner = ToolRunner(Path(tmp), permission="read-only", hooks=hooks)

            result = runner.run("write_file", {"path": "x.txt", "content": "nope"})

            self.assertTrue(result.is_error)
            rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            events = [row["event"] for row in rows]
            self.assertEqual(events[0], "PreToolUse")
            self.assertIn("PermissionDenied", events)
            self.assertEqual(events[-1], "PostToolUse")
            denied = next(row for row in rows if row["event"] == "PermissionDenied")
            self.assertEqual(denied["payload"]["name"], "write_file")
            self.assertEqual(denied["payload"]["risk"], "workspace_write")

    def test_permission_ledger_records_allowed_and_denied_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp, "permission-ledger.jsonl")
            runner = ToolRunner(
                Path(tmp),
                permission="auto",
                permission_ledger=PermissionLedger(ledger_path),
            )

            allowed = runner.run("write_file", {"path": "x.txt", "content": "ok"})
            denied = runner.run("run_shell", {"command": "git push origin main"})

            self.assertFalse(allowed.is_error, allowed.content)
            self.assertTrue(denied.is_error)
            rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["decision"] for row in rows], ["allowed", "denied"])
            self.assertEqual(rows[0]["name"], "write_file")
            self.assertEqual(rows[1]["risk"], "git_remote_write")
            self.assertTrue(rows[0]["request_id"])

    def test_permission_ledger_records_denial_without_hook_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp, "permission-ledger.jsonl")
            runner = ToolRunner(
                Path(tmp),
                permission="read-only",
                permission_ledger=PermissionLedger(ledger_path),
            )

            result = runner.run("write_file", {"path": "x.txt", "content": "nope"})

            self.assertTrue(result.is_error)
            row = json.loads(ledger_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["decision"], "denied")
            self.assertEqual(row["name"], "write_file")
            self.assertEqual(row["risk"], "workspace_write")

    def test_plan_scoped_permission_envelope_blocks_risk_before_auto_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp, "permission-ledger.jsonl")
            runner = ToolRunner(
                Path(tmp),
                permission="auto",
                permission_ledger=PermissionLedger(ledger_path),
            )
            runner.set_permission_envelope({PermissionRisk.READ, PermissionRisk.VERIFY}, reason="standard plan")

            result = runner.run("write_file", {"path": "x.txt", "content": "nope"})

            self.assertTrue(result.is_error)
            self.assertIn("plan-scoped", result.content)
            row = json.loads(ledger_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["decision"], "denied")
            self.assertEqual(row["risk"], "workspace_write")

    def test_permission_ledger_redacts_sensitive_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp, "permission-ledger.jsonl")
            ledger = PermissionLedger(ledger_path)

            ledger.record(
                decision="requested",
                name="run_shell",
                action="run shell command",
                risk="network",
                tool_input={"api_key": "secret", "nested": {"Authorization": "Bearer token"}},
            )

            text = ledger_path.read_text(encoding="utf-8")
            row = json.loads(text)
            self.assertNotIn("secret", text)
            self.assertNotIn("Bearer token", text)
            self.assertEqual(row["input"]["api_key"], "[redacted]")
            self.assertEqual(row["input"]["nested"]["Authorization"], "[redacted]")

    def test_auto_high_risk_shell_denial_emits_permission_denied_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp, "hooks.log")
            ledger_path = Path(tmp, "permission-ledger.jsonl")
            hooks = HookRuntime(log_path)
            runner = ToolRunner(Path(tmp), permission="auto", hooks=hooks)

            result = runner.run("run_shell", {"command": "git push origin main"})

            self.assertTrue(result.is_error)
            rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            events = [row["event"] for row in rows]
            self.assertEqual(events[0], "PreToolUse")
            self.assertIn("PermissionDenied", events)
            self.assertEqual(events[-1], "PostToolUse")
            denied = next(row for row in rows if row["event"] == "PermissionDenied")
            self.assertEqual(denied["payload"]["name"], "run_shell")
            self.assertEqual(denied["payload"]["risk"], "git_remote_write")

    def test_ask_mode_emits_permission_request_and_denied_when_hook_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp, "hooks.log")
            ledger_path = Path(tmp, "permission-ledger.jsonl")
            hooks = HookRuntime(log_path)

            def deny(_event):
                return HookDecision(False, "no writes in this test")

            hooks.register("PermissionRequest", deny)
            runner = ToolRunner(
                Path(tmp),
                permission="ask",
                hooks=hooks,
                permission_ledger=PermissionLedger(ledger_path),
            )

            result = runner.run("write_file", {"path": "x.txt", "content": "nope"})

            self.assertTrue(result.is_error)
            self.assertIn("no writes", result.content)
            rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            events = [row["event"] for row in rows]
            self.assertEqual(events[0], "PreToolUse")
            self.assertIn("PermissionRequest", events)
            self.assertIn("PermissionDenied", events)
            self.assertEqual(events[-1], "PostToolUse")
            request = next(row for row in rows if row["event"] == "PermissionRequest")
            denied = next(row for row in rows if row["event"] == "PermissionDenied")
            self.assertEqual(request["payload"]["name"], "write_file")
            self.assertEqual(denied["payload"]["reason"], "no writes in this test")
            ledger_rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["decision"] for row in ledger_rows], ["requested", "denied"])
            self.assertEqual(ledger_rows[0]["request_id"], ledger_rows[1]["request_id"])

    def test_s20_read_only_still_logs_permission_denied_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc"
            runner = S20ToolRunner(root, permission="read-only", state_dir=state_dir)

            result = runner.run("todo_write", {"items": [{"id": "1", "content": "x", "status": "pending"}]})

            self.assertTrue(result.is_error)
            rows = [json.loads(line) for line in Path(state_dir, "hooks.log").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["event"] for row in rows], ["PreToolUse", "PermissionDenied", "PostToolUse"])
            self.assertEqual(rows[1]["payload"]["name"], "todo_write")
            ledger_rows = [
                json.loads(line)
                for line in Path(state_dir, "permission-ledger.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(ledger_rows[0]["decision"], "denied")
            self.assertEqual(ledger_rows[0]["name"], "todo_write")


if __name__ == "__main__":
    unittest.main()
