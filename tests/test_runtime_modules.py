from __future__ import annotations

import json
import http.server
import sys
import tempfile
import threading
import unittest
import time
from pathlib import Path

from mini_cc.agent import Agent
from mini_cc.bench import classify_terminal_bench_result
from mini_cc.cli import system_prompt_for_workspace
from mini_cc.hooks import (
    HOOK_EVENT_SPECS,
    HookDecision,
    HookRuntime,
    event_json,
    hook_event_catalog,
    load_hooks_file,
    matcher_matches,
    validate_hook_payload,
)
from mini_cc.llm import MockProvider
from mini_cc.mcp import InMemoryMCPAdapter
from mini_cc.s20 import S20ToolRunner
from mini_cc.subagents import SubagentRuntime, SubagentSpec, WorktreeHandle
from mini_cc.tools import ToolResult
from mini_cc.tools import ToolRunner


class HookHTTPHandler(http.server.BaseHTTPRequestHandler):
    response_body = "{}"
    received: list[dict] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        self.__class__.received.append(json.loads(body))
        payload = self.__class__.response_body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):  # noqa: A002
        del format, args


class RuntimeModuleTests(unittest.TestCase):
    def test_pre_tool_hook_can_block_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = S20ToolRunner(Path(tmp), permission="auto")

            def deny_shell(_event):
                return HookDecision(False, "shell disabled")

            runner.hooks.register("PreToolUse", deny_shell)
            result = runner.run("run_shell", {"command": "echo hi"})

            self.assertTrue(result.is_error)
            self.assertIn("shell disabled", result.content)

    def test_hook_runtime_writes_named_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp, "hooks.log")
            hooks = HookRuntime(log_path)

            hooks.notification("done", status="ok")

            content = log_path.read_text(encoding="utf-8")
            self.assertIn("Notification", content)
            self.assertIn("done", content)

    def test_hook_event_catalog_defines_v2_lifecycle_surface(self) -> None:
        expected = {
            "UserPromptSubmit",
            "PermissionRequest",
            "PermissionDenied",
            "PostToolUseFailure",
            "PostToolBatch",
            "SubagentStart",
            "SubagentStop",
            "TaskCreated",
            "TaskCompleted",
            "PreCompact",
            "PostCompact",
            "FileChanged",
            "SessionEnd",
            "InstructionsLoaded",
            "SessionEnd",
            "WorktreeCreate",
            "WorktreeRemove",
            "StopFailure",
            "ConfigChange",
        }

        self.assertTrue(expected.issubset(HOOK_EVENT_SPECS))
        catalog = hook_event_catalog()
        self.assertTrue(any(row["name"] == "PermissionRequest" and row["matcher_field"] == "name" for row in catalog))
        self.assertTrue(any(row["name"] == "FileChanged" and row["matcher_field"] is None for row in catalog))

    def test_hook_payload_validation_reports_missing_required_fields(self) -> None:
        errors = validate_hook_payload("PermissionRequest", {"name": "run_shell"})

        self.assertIn("missing required field: action", errors)
        self.assertIn("missing required field: risk", errors)

    def test_emit_records_payload_errors_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp, "hooks.log")
            hooks = HookRuntime(log_path)

            decision = hooks.emit("PermissionRequest", {"name": "run_shell"})

            self.assertTrue(decision.allow)
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertIn("_payload_errors", row["payload"])

    def test_structured_hook_helpers_emit_v2_json(self) -> None:
        captured = []
        hooks = HookRuntime(None)

        def capture(event):
            captured.append(event_json(event))

        hooks.register("PermissionRequest", capture)
        hooks.permission_request(name="run_shell", action="execute command", risk="workspace_write")

        self.assertEqual(captured[0]["schema_version"], 2)
        self.assertEqual(captured[0]["hook_event_name"], "PermissionRequest")
        self.assertEqual(captured[0]["tool_name"], "run_shell")
        self.assertEqual(captured[0]["risk"], "workspace_write")

    def test_hook_matchers_follow_documented_shapes(self) -> None:
        self.assertTrue(matcher_matches("", "run_shell"))
        self.assertTrue(matcher_matches("*", "write_file"))
        self.assertTrue(matcher_matches("write_file|replace_text", "replace_text"))
        self.assertFalse(matcher_matches("write_file|replace_text", "run_shell"))
        self.assertTrue(matcher_matches("mcp__.*", "mcp__server__tool"))

    def test_configured_command_hook_can_block_matched_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "deny_shell_hook.py"
            script.write_text(
                "\n".join(
                    [
                        "import json, sys",
                        "event = json.loads(sys.stdin.read())",
                        "if event.get('tool_name') == 'run_shell':",
                        "    print(json.dumps({'decision': 'block', 'reason': 'shell blocked by config'}))",
                    ]
                ),
                encoding="utf-8",
            )
            settings = root / "settings.json"
            settings.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "run_shell",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": f'"{sys.executable}" "{script}"',
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            runner = S20ToolRunner(root, permission="auto")
            self.assertTrue(load_hooks_file(runner.hooks, settings))
            result = runner.run("run_shell", {"command": "echo hi"})

            self.assertTrue(result.is_error)
            self.assertIn("shell blocked by config", result.content)

    def test_configured_http_hook_can_block_from_json_response(self) -> None:
        HookHTTPHandler.received = []
        HookHTTPHandler.response_body = json.dumps({"decision": "block", "reason": "blocked by http"})
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HookHTTPHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            hooks = HookRuntime(None)
            hooks.register_configured(
                ConfiguredHookForTest(
                    "PreToolUse",
                    "run_shell",
                    {
                        "type": "http",
                        "url": f"http://127.0.0.1:{server.server_port}/hook",
                    },
                )
            )

            decision = hooks.pre_tool_use("run_shell", {"command": "echo hi"})

            self.assertFalse(decision.allow)
            self.assertIn("blocked by http", decision.reason)
            self.assertEqual(HookHTTPHandler.received[0]["hook_event_name"], "PreToolUse")
        finally:
            server.shutdown()
            server.server_close()

    def test_configured_mcp_hook_can_update_payload(self) -> None:
        hooks = HookRuntime(None)
        hooks.register_mcp_hook_adapter(
            "local",
            InMemoryMCPAdapter(
                "local",
                tools={
                    "decide": lambda _payload: ToolResult(
                        json.dumps({"payload_updates": {"command": "echo from mcp"}})
                    )
                },
            ),
        )
        hooks.register_configured(
            ConfiguredHookForTest(
                "PreToolUse",
                "run_shell",
                {"type": "mcp", "server": "local", "tool": "decide"},
            )
        )

        decision = hooks.pre_tool_use("run_shell", {"command": "echo hi"})

        self.assertTrue(decision.allow)
        self.assertEqual(decision.payload_updates["command"], "echo from mcp")

    def test_configured_prompt_hook_renders_payload_updates(self) -> None:
        hooks = HookRuntime(None)
        hooks.register_configured(
            ConfiguredHookForTest(
                "UserPromptSubmit",
                "",
                {
                    "type": "prompt",
                    "payload_updates": {
                        "prompt": "Review this prompt from {source}: {prompt}",
                    },
                },
            )
        )

        decision = hooks.user_prompt_submit("fix tests", source="cli")

        self.assertTrue(decision.allow)
        self.assertEqual(decision.payload_updates["prompt"], "Review this prompt from cli: fix tests")

    def test_configured_agent_hook_calls_registered_handler(self) -> None:
        hooks = HookRuntime(None)
        hooks.register_agent_hook(
            "risk-checker",
            lambda event, _handler: {
                "allow": False,
                "reason": f"agent blocked {event.payload['name']}",
            },
        )
        hooks.register_configured(
            ConfiguredHookForTest(
                "PreToolUse",
                "run_shell",
                {"type": "agent", "agent": "risk-checker"},
            )
        )

        decision = hooks.pre_tool_use("run_shell", {"command": "echo hi"})

        self.assertFalse(decision.allow)
        self.assertIn("agent blocked run_shell", decision.reason)

    def test_configured_hook_fail_open_allows_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp, "bad_hook.py")
            script.write_text("import sys\nsys.exit(2)\n", encoding="utf-8")
            hooks = HookRuntime(None)
            hooks.register_configured(
                ConfiguredHookForTest(
                    "PreToolUse",
                    "run_shell",
                    {
                        "type": "command",
                        "command": f'"{sys.executable}" "{script}"',
                        "failure_mode": "fail-open",
                    },
                )
            )

            decision = hooks.pre_tool_use("run_shell", {"command": "echo hi"})

            self.assertTrue(decision.allow)
            self.assertIn("fail-open", decision.reason)
            self.assertEqual(hooks.hook_metrics()["configured_hook_failures"], 1)

    def test_configured_hook_retries_until_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp, "marker.txt")
            script = Path(tmp, "flaky_hook.py")
            script.write_text(
                "import json, pathlib, sys\n"
                f"marker = pathlib.Path({str(marker)!r})\n"
                "if not marker.exists():\n"
                "    marker.write_text('1')\n"
                "    sys.exit(1)\n"
                "print(json.dumps({'payload_updates': {'command': 'echo retried'}}))\n",
                encoding="utf-8",
            )
            hooks = HookRuntime(None)
            hooks.register_configured(
                ConfiguredHookForTest(
                    "PreToolUse",
                    "run_shell",
                    {
                        "type": "command",
                        "command": f'"{sys.executable}" "{script}"',
                        "retries": 1,
                    },
                )
            )

            decision = hooks.pre_tool_use("run_shell", {"command": "echo hi"})

            self.assertTrue(decision.allow)
            self.assertEqual(decision.payload_updates["command"], "echo retried")
            self.assertEqual(hooks.hook_metrics()["configured_hook_retries"], 1)

    def test_configured_hook_additional_context_reaches_http_payload(self) -> None:
        HookHTTPHandler.received = []
        HookHTTPHandler.response_body = json.dumps({"allow": True})
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HookHTTPHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            hooks = HookRuntime(None)
            hooks.register_configured(
                ConfiguredHookForTest(
                    "PreToolUse",
                    "run_shell",
                    {
                        "type": "http",
                        "url": f"http://127.0.0.1:{server.server_port}/hook",
                        "additionalContext": {"policy": "strict"},
                    },
                )
            )

            decision = hooks.pre_tool_use("run_shell", {"command": "echo hi"})

            self.assertTrue(decision.allow)
            self.assertEqual(HookHTTPHandler.received[0]["additionalContext"]["policy"], "strict")
        finally:
            server.shutdown()
            server.server_close()

    def test_configured_hook_spills_large_output_and_validates_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp, "large_hook.py")
            script.write_text("print('{\"allow\": \"yes\"}' + 'x' * 2000)\n", encoding="utf-8")
            spill_dir = Path(tmp, "spills")
            hooks = HookRuntime(None, spill_dir=spill_dir)
            hooks.register_configured(
                ConfiguredHookForTest(
                    "PreToolUse",
                    "run_shell",
                    {
                        "type": "command",
                        "command": f'"{sys.executable}" "{script}"',
                        "max_output_chars": 256,
                        "failure_mode": "fail-open",
                    },
                )
            )

            decision = hooks.pre_tool_use("run_shell", {"command": "echo hi"})

            self.assertTrue(decision.allow)
            self.assertIn("fail-open", decision.reason)
            self.assertEqual(hooks.hook_metrics()["configured_hook_spills"], 1)
            spills = list(spill_dir.glob("*.txt"))
            self.assertEqual(len(spills), 1)
            self.assertGreater(len(spills[0].read_text(encoding="utf-8")), 1000)

    def test_configured_hook_timeout_uses_failure_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp, "slow_hook.py")
            script.write_text("import time\ntime.sleep(1)\n", encoding="utf-8")
            hooks = HookRuntime(None)
            hooks.register_configured(
                ConfiguredHookForTest(
                    "PreToolUse",
                    "run_shell",
                    {
                        "type": "command",
                        "command": f'"{sys.executable}" "{script}"',
                        "timeout": 0,
                        "failure_mode": "fail-open",
                    },
                )
            )

            started = time.perf_counter()
            decision = hooks.pre_tool_use("run_shell", {"command": "echo hi"})

            self.assertTrue(decision.allow)
            self.assertLess(time.perf_counter() - started, 1)
            self.assertIn("timed out", decision.reason)

    def test_agent_emits_prompt_session_end_and_stop_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp, "hooks.log")
            hooks = HookRuntime(log_path)
            hooks.register("Stop", lambda _event: HookDecision(False, "stop blocked for test"))
            agent = Agent(
                MockProvider(),
                ToolRunner(Path(tmp), permission="auto"),
                max_turns=1,
                output=lambda _text: None,
                hook_runtime=hooks,
            )

            agent.run("hello")

            events = [json.loads(line)["event"] for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertIn("UserPromptSubmit", events)
            self.assertIn("SessionStart", events)
            self.assertIn("SessionEnd", events)
            self.assertIn("Stop", events)
            self.assertIn("StopFailure", events)

    def test_toolrunner_emits_file_changed_for_successful_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp, "hooks.log")
            hooks = HookRuntime(log_path)
            runner = ToolRunner(Path(tmp), permission="auto", hooks=hooks, permission_context={"session_id": "s1"})

            runner.write_file("note.txt", "hello")
            runner.replace_text("note.txt", "hello", "hello world")

            rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            file_events = [row for row in rows if row["event"] == "FileChanged"]
            self.assertEqual([row["payload"]["operation"] for row in file_events], ["write", "replace"])
            self.assertEqual(file_events[0]["payload"]["session_id"], "s1")

    def test_system_prompt_emits_instructions_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "AGENTS.md").write_text("Use project rules.\n", encoding="utf-8")
            log_path = root / "hooks.log"
            hooks = HookRuntime(log_path)

            prompt = system_prompt_for_workspace("base", root, hooks)

            self.assertIn("Use project rules.", prompt)
            row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["event"], "InstructionsLoaded")
            self.assertEqual(row["payload"]["source"], "AGENTS.md")

    def test_load_hooks_file_emits_config_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = root / "settings.json"
            settings.write_text(json.dumps({"hooks": {"PreToolUse": []}}), encoding="utf-8")
            log_path = root / "hooks.log"
            hooks = HookRuntime(log_path)

            self.assertTrue(load_hooks_file(hooks, settings))

            rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(rows[-1]["event"], "ConfigChange")
            self.assertEqual(rows[-1]["payload"]["keys"], ["PreToolUse"])

    def test_todo_write_emits_task_created_and_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = S20ToolRunner(root, permission="auto", state_dir=root / ".mini_cc")

            runner.todo_write(
                [
                    {"id": "t1", "content": "inspect files", "status": "pending"},
                    {"id": "t2", "content": "finish report", "status": "completed"},
                ]
            )

            events = [json.loads(line)["event"] for line in Path(root, ".mini_cc", "hooks.log").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(events.count("TaskCreated"), 2)
            self.assertIn("TaskCompleted", events)

    def test_subagent_worktree_create_and_remove_events_are_emitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "README.md").write_text("# Demo\n", encoding="utf-8")
            state_dir = root / ".mini_cc" / "subagents"
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto", state_dir=root / ".mini_cc"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=state_dir,
                specs=[
                    SubagentSpec(
                        name="writer",
                        description="write capable",
                        system_prompt="Write.",
                        allowed_tools={"write_file"},
                    )
                ],
                load_config=False,
            )

            result = runtime.run("writer", "say ready")

            self.assertFalse(result.is_error, result.content)
            hook_log = state_dir / "writer" / "hooks.log"
            events = [json.loads(line)["event"] for line in hook_log.read_text(encoding="utf-8").splitlines()]
            self.assertIn("WorktreeCreate", events)

            worktree_path = Path(result.metadata["worktree"]["path"])
            runtime.remove_worktree(
                WorktreeHandle(
                    path=worktree_path,
                    isolated=True,
                    backend=result.metadata["worktree"]["backend"],
                )
            )
            parent_events = [
                json.loads(line)["event"]
                for line in Path(root, ".mini_cc", "hooks.log").read_text(encoding="utf-8").splitlines()
            ]
            self.assertIn("WorktreeRemove", parent_events)

    def test_terminal_bench_docker_down_classification(self) -> None:
        result = {"task_id": "x", "failure_mode": "unknown_agent_error"}
        cls = classify_terminal_bench_result(
            result,
            run_log_tail="Docker Desktop is unable to start",
        )

        self.assertEqual(cls.category, "environment_docker_down")

    def test_terminal_bench_apt_network_classification(self) -> None:
        result = {"task_id": "x", "failure_mode": "unknown_agent_error"}
        cls = classify_terminal_bench_result(
            result,
            run_log_tail="apt-get install failed fetching deb.debian.org",
        )

        self.assertEqual(cls.category, "environment_apt_network")


if __name__ == "__main__":
    unittest.main()


def ConfiguredHookForTest(event_name: str, matcher: str, handler: dict) -> object:
    from mini_cc.hooks import ConfiguredHook

    return ConfiguredHook(event_name=event_name, matcher=matcher, handler=handler, source="test")
