from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from mini_cc.llm import MockBlock, MockProvider, MockResponse
from mini_cc.mcp import GovernedMCPAdapter, InMemoryMCPAdapter, MCPPolicy, MCPTool, StreamableHTTPMCPAdapter, WebSocketMCPAdapter
from mini_cc.s20 import S20ToolRunner
from mini_cc.subagents import PipelineDecision, PipelineStep, RestrictedToolRunner, SubagentRuntime, SubagentSpec, TaskContract, load_subagent_specs_from_payload
from mini_cc.tools import ToolResult


FAKE_MCP_SERVER = r'''
import json
import sys
request = json.loads(sys.stdin.readline())
if request.get("method") == "tools/list":
    result = {"tools": [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object", "properties": {}}}]}
elif request.get("method") == "tools/call":
    result = {"content": [{"type": "text", "text": "configured:" + str((request.get("params") or {}).get("name"))}]}
else:
    result = {"resources": []}
print(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}))
'''


class JsonPlannerProvider:
    def __init__(self, payload: dict[str, object] | str) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]], system: str) -> MockResponse:
        del tools, system
        self.prompts.append(str(messages[-1].get("content", "")))
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return MockResponse([MockBlock(type="text", text=text)])


class DelegatingProvider:
    def __init__(self, target: str = "worker", prompt: str = "list files") -> None:
        self.target = target
        self.prompt = prompt

    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]], system: str) -> MockResponse:
        del tools, system
        last = messages[-1]
        if isinstance(last.get("content"), list):
            content = str(last["content"][0].get("content", ""))  # type: ignore[index, union-attr]
            return MockResponse([MockBlock(type="text", text="Nested result:\n" + content)])
        return MockResponse(
            [
                MockBlock(type="text", text="I will delegate to a nested subagent."),
                MockBlock(
                    type="tool_use",
                    id="toolu_nested_subagent",
                    name="subagent_run",
                    input={"name": self.target, "prompt": self.prompt},
                ),
            ]
        )


class WriteFileProvider:
    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]], system: str) -> MockResponse:
        del tools, system
        last = messages[-1]
        if isinstance(last.get("content"), list):
            content = str(last["content"][0].get("content", ""))  # type: ignore[index, union-attr]
            return MockResponse([MockBlock(type="text", text="write completed:\nEVIDENCE: child-output.txt changed\nVERIFICATION: write_file returned successfully\n" + content)])
        return MockResponse(
            [
                MockBlock(type="text", text="I will write in my isolated workspace."),
                MockBlock(
                    type="tool_use",
                    id="toolu_write_file",
                    name="write_file",
                    input={"path": "child-output.txt", "content": "from isolated subagent\n"},
                ),
            ]
        )


class TargetedWriteProvider:
    def __init__(self, path: str, content: str, delay: float = 0.0) -> None:
        self.path = path
        self.content = content
        self.delay = delay

    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]], system: str) -> MockResponse:
        del tools, system
        last = messages[-1]
        if isinstance(last.get("content"), list):
            content = str(last["content"][0].get("content", ""))  # type: ignore[index, union-attr]
            return MockResponse([MockBlock(type="text", text=f"targeted write completed:\nEVIDENCE: {self.path} changed\nVERIFICATION: write_file returned successfully\n" + content)])
        if self.delay:
            time.sleep(self.delay)
        return MockResponse(
            [
                MockBlock(type="text", text=f"I will write {self.path}."),
                MockBlock(
                    type="tool_use",
                    id="toolu_targeted_write_file",
                    name="write_file",
                    input={"path": self.path, "content": self.content},
                ),
            ]
        )


class TextOnlyProvider:
    def __init__(self, text: str = "no changes made") -> None:
        self.text = text

    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]], system: str) -> MockResponse:
        del messages, tools, system
        return MockResponse([MockBlock(type="text", text=self.text)])


class QualityMCPAdapter(InMemoryMCPAdapter):
    def __init__(self, name: str, tools: list[MCPTool]) -> None:
        super().__init__(name)
        self._quality_tools = tools

    def list_tools(self) -> list[MCPTool]:
        return list(self._quality_tools)


class PeerAwareProvider:
    def __init__(self, initial_text: str, reply_text: str | None = None) -> None:
        self.initial_text = initial_text
        self.reply_text = reply_text or initial_text
        self.prompts: list[str] = []

    def complete(self, messages: list[dict[str, object]], tools: list[dict[str, object]], system: str) -> MockResponse:
        del tools, system
        prompt = str(messages[-1].get("content", ""))
        self.prompts.append(prompt)
        if "mini_cc_peer_v1" in prompt:
            return MockResponse([MockBlock(type="text", text=self.reply_text)])
        return MockResponse([MockBlock(type="text", text=self.initial_text)])


class SubagentRuntimeTests(unittest.TestCase):
    def test_restricted_tool_runner_blocks_disallowed_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = S20ToolRunner(Path(tmp), permission="auto")
            restricted = RestrictedToolRunner(base, {"list_files"})

            schemas = restricted.schemas()
            result = restricted.run("write_file", {"path": "x.txt", "content": "no"})

            self.assertEqual([schema["name"] for schema in schemas], ["list_files"])
            self.assertTrue(result.is_error)
            self.assertIn("not allowed", result.content)

    def test_subagent_run_uses_isolated_provider_and_tool_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "README.md").write_text("# Demo\n", encoding="utf-8")
            base = S20ToolRunner(Path(tmp), permission="auto")
            runtime = SubagentRuntime(
                workspace=Path(tmp),
                base_tools=base,
                provider_factory=lambda _spec: MockProvider(),
                specs=[
                    SubagentSpec(
                        name="explorer",
                        description="read only",
                        system_prompt="Explore only.",
                        allowed_tools={"list_files"},
                    )
                ],
            )
            base.set_subagents(runtime)

            result = base.run("subagent_run", {"name": "explorer", "prompt": "list files"})

            self.assertFalse(result.is_error, result.content)
            self.assertIn("[tool] list_files", result.content)
            self.assertIn("README.md", result.content)

    def test_subagent_model_override_reaches_provider_factory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seen_models: list[str | None] = []

            def factory(spec: SubagentSpec) -> MockProvider:
                seen_models.append(spec.model)
                return MockProvider()

            runtime = SubagentRuntime(
                workspace=Path(tmp),
                base_tools=S20ToolRunner(Path(tmp), permission="auto"),
                provider_factory=factory,
                specs=[
                    SubagentSpec(
                        name="verifier",
                        description="verify",
                        system_prompt="Verify.",
                        allowed_tools={"list_files"},
                        model="test-model",
                    )
                ],
            )

            result = runtime.run("verifier", "list files")

            self.assertFalse(result.is_error, result.content)
            self.assertEqual(seen_models, ["test-model"])

    def test_subagent_memories_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = SubagentRuntime(
                workspace=Path(tmp),
                base_tools=S20ToolRunner(Path(tmp), permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                specs=[
                    SubagentSpec(
                        name="a",
                        description="first",
                        system_prompt="A.",
                        allowed_tools={"list_files"},
                        memory={"role": "alpha"},
                    ),
                    SubagentSpec(
                        name="b",
                        description="second",
                        system_prompt="B.",
                        allowed_tools={"list_files"},
                        memory={"role": "beta"},
                    ),
                ],
            )

            listed = runtime.list_subagents()
            prompt_a = runtime._system_prompt(runtime.specs["a"])
            prompt_b = runtime._system_prompt(runtime.specs["b"])

            self.assertIn("a: first", listed)
            self.assertIn("b: second", listed)
            self.assertIn("role: alpha", prompt_a)
            self.assertNotIn("role: beta", prompt_a)
            self.assertIn("role: beta", prompt_b)

    def test_subagent_private_memory_tools_are_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = {"role": "alpha"}
            runner = RestrictedToolRunner(
                S20ToolRunner(Path(tmp), permission="auto"),
                {"subagent_memory_read", "subagent_memory_write"},
                memory=memory,
            )

            write = runner.run("subagent_memory_write", {"key": "finding", "value": "uses local tests"})
            read = runner.run("subagent_memory_read", {})

            self.assertFalse(write.is_error, write.content)
            self.assertEqual(memory["finding"], "uses local tests")
            self.assertIn("role: alpha", read.content)
            self.assertIn("finding: uses local tests", read.content)

    def test_subagent_mcp_adapter_exposes_allowed_tools_and_resources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = InMemoryMCPAdapter(
                "local",
                tools={"echo": lambda payload: "echo:" + str(payload.get("text", ""))},
                resources={"resource://note": "private note"},
                prompts={"review": "review prompt"},
            )
            runner = RestrictedToolRunner(
                S20ToolRunner(Path(tmp), permission="auto"),
                {
                    "mcp__local__echo",
                    "mcp_list_resources",
                    "mcp_read_resource",
                    "mcp_list_prompts",
                    "mcp_get_prompt",
                },
                mcp_adapters=[adapter],
            )

            schema_names = [schema["name"] for schema in runner.schemas()]
            call = runner.run("mcp__local__echo", {"text": "hi"})
            resources = runner.run("mcp_list_resources", {})
            resource = runner.run("mcp_read_resource", {"uri": "resource://note"})
            prompts = runner.run("mcp_list_prompts", {})
            prompt = runner.run("mcp_get_prompt", {"name": "review"})

            self.assertIn("mcp__local__echo", schema_names)
            self.assertFalse(call.is_error, call.content)
            self.assertEqual(call.content, "echo:hi")
            self.assertIn("resource://note", resources.content)
            self.assertEqual(resource.content, "private note")
            self.assertIn("review", prompts.content)
            self.assertEqual(prompt.content, "review prompt")

    def test_subagent_system_prompt_includes_mcp_capability_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = InMemoryMCPAdapter(
                "local",
                tools={"echo": lambda payload: "echo:" + str(payload.get("text", ""))},
                resources={"resource://note": "private note"},
                prompts={"review": "review prompt"},
            )
            spec = SubagentSpec(
                "reader",
                "reader",
                "Read.",
                {"mcp__local__echo", "mcp_list_resources", "mcp_list_prompts"},
                mcp_adapters=[adapter],
            )
            runtime = SubagentRuntime(
                workspace=Path(tmp),
                base_tools=S20ToolRunner(Path(tmp), permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                specs=[spec],
                load_config=False,
            )

            prompt = runtime._system_prompt(spec)

            self.assertIn("Subagent MCP capabilities", prompt)
            self.assertIn("tools: echo", prompt)
            self.assertIn("resources: resource://note", prompt)
            self.assertIn("prompts: review", prompt)

    def test_mcp_registry_writes_catalog_and_capability_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = InMemoryMCPAdapter(
                "local",
                tools={
                    "search_docs": lambda payload: "doc:" + str(payload.get("query", "")),
                    "write_note": lambda payload: "wrote:" + str(payload.get("text", "")),
                },
                resources={"resource://docs/readme": "docs"},
                prompts={"review": "review prompt"},
            )
            setattr(
                adapter,
                "_mini_cc_registry_metadata",
                {
                    "transport": "stdio",
                    "trust_level": "local",
                    "auth": {"type": "none"},
                },
            )
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=root / ".mini_cc" / "subagents",
                specs=[
                    SubagentSpec(
                        "reader",
                        "reader",
                        "Read.",
                        {"mcp__local__search_docs", "mcp_list_resources", "mcp_list_prompts"},
                        mcp_adapters=[adapter],
                    ),
                    SubagentSpec(
                        "writer",
                        "writer",
                        "Write.",
                        {"mcp__local__write_note"},
                        mcp_adapters=[adapter],
                    ),
                ],
                load_config=False,
            )

            registry = runtime.build_mcp_registry()

            registry_path = root / ".mini_cc" / "mcp-registry.json"
            self.assertTrue(registry_path.exists())
            saved = json.loads(registry_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema_version"], "2.5")
            self.assertEqual(registry["servers"][0]["name"], "local")
            self.assertEqual(registry["servers"][0]["transport"], "stdio")
            self.assertEqual(registry["servers"][0]["trust_level"], "local")
            self.assertEqual(registry["servers"][0]["health"]["status"], "healthy")
            self.assertEqual(registry["governance"]["resources"], "read policy, cache metadata, sensitive detection, read audit preview")
            self.assertIn("token store", registry["governance"]["auth"])
            qualified_tools = {tool["qualified_name"] for tool in registry["servers"][0]["tools"]}
            self.assertEqual(qualified_tools, {"mcp__local__search_docs", "mcp__local__write_note"})
            indexed_tools = {tool["qualified_name"] for tool in registry["tool_index"]}
            self.assertEqual(indexed_tools, qualified_tools)
            self.assertEqual(registry["vector_index"]["embedding_model"], "mini_cc_hashing_v1")
            self.assertIn("search", registry["capability_index"])
            self.assertIn("write", registry["capability_index"])
            subagent_visibility = {
                row["name"]: set(row["visible_tools"])
                for row in registry["servers"][0]["subagents"]
            }
            self.assertEqual(subagent_visibility["reader"], {"mcp__local__search_docs"})
            self.assertEqual(subagent_visibility["writer"], {"mcp__local__write_note"})
            self.assertEqual(registry["servers"][0]["subagents"][0]["visible_resources"], ["resource://docs/readme"])
            resource_governance = registry["servers"][0]["resources"][0]["governance"]
            prompt_governance = registry["servers"][0]["prompts"][0]["governance"]
            self.assertTrue(resource_governance["read_allowed_by_policy"])
            self.assertFalse(resource_governance["sensitive"])
            self.assertTrue(resource_governance["content_preview_available_after_read"])
            self.assertTrue(prompt_governance["get_allowed_by_policy"])
            self.assertFalse(prompt_governance["version_pinned"])
            self.assertTrue(prompt_governance["content_preview_available_after_get"])

    def test_mcp_registry_lints_tool_description_quality(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = QualityMCPAdapter(
                "quality",
                [
                    MCPTool(
                        name="search_docs",
                        description="Search project documentation by natural language query and return matching document snippets.",
                        input_schema={
                            "type": "object",
                            "properties": {"query": {"type": "string", "description": "Search query"}},
                            "required": ["query"],
                        },
                    ),
                    MCPTool(
                        name="delete_all",
                        description="MCP tool quality.delete_all",
                        input_schema={"type": "object", "properties": {}},
                    ),
                ],
            )
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                specs=[
                    SubagentSpec(
                        "reader",
                        "reader",
                        "Read.",
                        {"mcp__quality__search_docs", "mcp__quality__delete_all"},
                        mcp_adapters=[adapter],
                    )
                ],
                load_config=False,
            )

            registry = runtime.build_mcp_registry()

            tools = {tool["name"]: tool for tool in registry["servers"][0]["tools"]}
            good_quality = tools["search_docs"]["quality"]
            bad_quality = tools["delete_all"]["quality"]
            self.assertGreaterEqual(good_quality["score"], 90)
            self.assertEqual(good_quality["missing_fields"], [])
            self.assertEqual(good_quality["example_input"], {"query": "example query"})
            self.assertIn("query: string required", good_quality["input_constraints"])
            self.assertLess(bad_quality["score"], good_quality["score"])
            self.assertIn("description is generic", bad_quality["warnings"])
            self.assertIn("input_schema.properties", bad_quality["missing_fields"])
            self.assertIn("tool name looks high risk", bad_quality["warnings"])
            self.assertTrue(bad_quality["prompt_injection_warning"]["guidance"])

    def test_mcp_tool_retrieval_ranks_relevant_visible_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = QualityMCPAdapter(
                "quality",
                [
                    MCPTool(
                        name="search_docs",
                        description="Search project documentation by natural language query and return matching document snippets.",
                        input_schema={
                            "type": "object",
                            "properties": {"query": {"type": "string", "description": "Search query"}},
                            "required": ["query"],
                        },
                    ),
                    MCPTool(
                        name="create_ticket",
                        description="Create a support ticket with a title and body.",
                        input_schema={
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "body": {"type": "string"},
                            },
                            "required": ["title", "body"],
                        },
                    ),
                    MCPTool(
                        name="delete_all",
                        description="Delete all remote records for the configured account.",
                        input_schema={"type": "object", "properties": {}},
                    ),
                ],
            )
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                specs=[
                    SubagentSpec(
                        "reader",
                        "reader",
                        "Read.",
                        {"mcp__quality__search_docs", "mcp__quality__create_ticket", "mcp__quality__delete_all"},
                        mcp_adapters=[adapter],
                    )
                ],
                load_config=False,
            )

            result = runtime.retrieve_mcp_tools("find install docs", subagent="reader", top_k=1)

            self.assertEqual(result["schema_version"], "2.35")
            self.assertEqual(result["retrieval_mode"], "hybrid_vector_lexical")
            self.assertTrue(result["embedding_retrieval"]["enabled"])
            self.assertEqual(result["selected_count"], 1)
            self.assertEqual(result["selected_tools"][0]["qualified_name"], "mcp__quality__search_docs")
            self.assertIn("vector_score", result["selected_tools"][0])
            self.assertGreater(result["estimated_schema_tokens"], 0)
            self.assertGreater(result["token_savings_estimate"], 0)
            self.assertTrue(result["fallback"]["second_pass_available"])

    def test_mcp_tool_vector_index_is_written_and_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = QualityMCPAdapter(
                "quality",
                [
                    MCPTool(
                        name="search_docs",
                        description="Search installation documentation and return matching docs.",
                        input_schema={
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    ),
                    MCPTool(
                        name="create_ticket",
                        description="Create a support ticket for an incident.",
                        input_schema={
                            "type": "object",
                            "properties": {"title": {"type": "string"}},
                            "required": ["title"],
                        },
                    ),
                ],
            )
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                specs=[
                    SubagentSpec(
                        "reader",
                        "reader",
                        "Read.",
                        {"mcp__quality__search_docs", "mcp__quality__create_ticket"},
                        mcp_adapters=[adapter],
                    )
                ],
                load_config=False,
            )

            registry = runtime.build_mcp_registry()
            vector_index = runtime.build_mcp_tool_vector_index(registry=registry)
            result = runtime.retrieve_mcp_tools("installation docs", subagent="reader", top_k=1, registry=registry)

            vector_path = root / ".mini_cc" / "mcp-tool-vectors.json"
            self.assertTrue(vector_path.exists())
            self.assertEqual(vector_index["schema_version"], "2.35")
            self.assertEqual(vector_index["embedding_model"], "mini_cc_hashing_v1")
            self.assertEqual(vector_index["tool_count"], 2)
            self.assertEqual(len(vector_index["tools"][0]["vector"]), 128)
            self.assertEqual(result["embedding_retrieval"]["index_path"], str(vector_path))
            self.assertGreater(result["selected_tools"][0]["vector_score"], 0)

    def test_restricted_runner_exposes_top_k_mcp_tool_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = QualityMCPAdapter(
                "quality",
                [
                    MCPTool(
                        name="search_docs",
                        description="Search project documentation by natural language query and return matching snippets.",
                        input_schema={
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    ),
                    MCPTool(
                        name="create_ticket",
                        description="Create a support ticket with a title and body.",
                        input_schema={
                            "type": "object",
                            "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
                            "required": ["title", "body"],
                        },
                    ),
                    MCPTool(
                        name="delete_all",
                        description="Delete all remote records.",
                        input_schema={"type": "object", "properties": {}},
                    ),
                ],
            )
            runner = RestrictedToolRunner(
                S20ToolRunner(Path(tmp), permission="auto"),
                {
                    "mcp__quality__search_docs",
                    "mcp__quality__create_ticket",
                    "mcp__quality__delete_all",
                    "mcp_list_resources",
                },
                mcp_adapters=[adapter],
                schema_query="find documentation snippets",
                mcp_tool_top_k=1,
            )

            names = [schema["name"] for schema in runner.schemas()]

            self.assertIn("mcp_list_resources", names)
            self.assertIn("mcp__quality__search_docs", names)
            self.assertNotIn("mcp__quality__create_ticket", names)
            self.assertNotIn("mcp__quality__delete_all", names)

    def test_restricted_runner_adds_mcp_audit_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp, "audit.jsonl")
            adapter = GovernedMCPAdapter(
                InMemoryMCPAdapter("local", tools={"echo": lambda _payload: "ok"}),
                policy=MCPPolicy(allowed_tools={"echo"}),
                audit_log=audit,
            )
            runner = RestrictedToolRunner(
                S20ToolRunner(Path(tmp), permission="auto"),
                {"mcp__local__echo"},
                mcp_adapters=[adapter],
                audit_context={"subagent": "reader", "handoff_id": "handoff"},
            )

            result = runner.run("mcp__local__echo", {})

            self.assertFalse(result.is_error, result.content)
            row = json.loads(audit.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["subagent"], "reader")
            self.assertEqual(row["handoff_id"], "handoff")

    def test_subagent_hooks_and_sessions_are_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "README.md").write_text("# Demo\n", encoding="utf-8")
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=root / ".mini_cc" / "subagents",
                specs=[
                    SubagentSpec(
                        name="explorer",
                        description="read only",
                        system_prompt="Explore.",
                        allowed_tools={"list_files"},
                    )
                ],
            )

            result = runtime.run("explorer", "list files")

            self.assertFalse(result.is_error, result.content)
            hook_log = root / ".mini_cc" / "subagents" / "explorer" / "hooks.log"
            sessions = list((root / ".mini_cc" / "subagents" / "explorer" / "sessions").glob("*.json"))
            self.assertTrue(hook_log.exists())
            self.assertTrue(sessions)
            self.assertIn("SubagentStart", hook_log.read_text(encoding="utf-8"))
            self.assertIn("SessionStart", hook_log.read_text(encoding="utf-8"))

    def test_subagent_run_records_structured_task_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=state_dir,
                specs=[SubagentSpec("explorer", "explore", "Explore.", {"list_files"}, capabilities={"explore"})],
                load_config=False,
            )

            result = runtime.run(
                "explorer",
                "list files",
                task_contract={
                    "objective": "Inspect project files",
                    "deliverable": "A concise file list",
                    "constraints": ["read only"],
                    "allowed_tools": ["list_files", "write_file"],
                    "expected_evidence": ["listed files"],
                    "budget": {"max_turns": 2},
                    "stop_conditions": ["file list returned"],
                },
            )

            self.assertFalse(result.is_error, result.content)
            handoff = json.loads(Path(state_dir, "handoffs.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(handoff["task_contract"]["objective"], "Inspect project files")
            self.assertEqual(handoff["task_contract"]["allowed_tools"], ["list_files"])
            self.assertEqual(handoff["contract_id"], handoff["task_contract"]["id"])
            session_path = next((state_dir / "explorer" / "sessions").glob("*.json"))
            session = json.loads(session_path.read_text(encoding="utf-8"))
            contract_events = [event for event in session["events"] if event["event"] == "task_contract"]
            self.assertEqual(contract_events[0]["payload"]["id"], handoff["contract_id"])

    def test_subagent_run_records_state_machine_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=state_dir,
                specs=[SubagentSpec("explorer", "explore", "Explore.", {"list_files"}, capabilities={"explore"})],
                load_config=False,
            )

            result = runtime.run("explorer", "list files")

            self.assertFalse(result.is_error, result.content)
            events = [
                json.loads(line)
                for line in Path(state_dir, "state-events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([event["state"] for event in events], ["planned", "ready", "running", "completed"])
            self.assertEqual({event["subagent"] for event in events}, {"explorer"})
            handoff = json.loads(Path(state_dir, "handoffs.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(handoff["final_state"], "completed")
            session_path = next((state_dir / "explorer" / "sessions").glob("*.json"))
            session = json.loads(session_path.read_text(encoding="utf-8"))
            self.assertTrue(any(event["event"] == "subagent_state" for event in session["events"]))

    def test_subagent_event_history_replay_reconstructs_handoff_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=state_dir,
                specs=[SubagentSpec("explorer", "explore", "Explore.", {"list_files"}, capabilities={"explore"})],
                load_config=False,
            )

            result = runtime.run("explorer", "list files")
            replay = runtime.replay_event_history()

            self.assertFalse(result.is_error, result.content)
            self.assertTrue(Path(state_dir, "event-history.jsonl").exists())
            self.assertGreater(replay["event_count"], 0)
            self.assertEqual(replay["latest_states"]["explorer"]["state"], "completed")
            handoff = next(iter(replay["handoffs"].values()))
            self.assertEqual(handoff["subagent"], "explorer")
            self.assertEqual(handoff["final_state"], "completed")

    def test_write_capable_subagent_uses_isolated_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "README.md").write_text("# Demo\n", encoding="utf-8")
            state_dir = root / ".mini_cc" / "subagents"
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: WriteFileProvider(),
                state_dir=state_dir,
                specs=[SubagentSpec("writer", "write", "Write.", {"write_file"}, capabilities={"implement", "write"})],
                load_config=False,
            )

            result = runtime.run("writer", "write child output")

            self.assertFalse(result.is_error, result.content)
            self.assertFalse((root / "child-output.txt").exists())
            handoff = json.loads(Path(state_dir, "handoffs.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertTrue(handoff["worktree_isolated"])
            self.assertIn(handoff["worktree_backend"], {"git_worktree", "directory_copy"})
            worktree_path = Path(handoff["worktree_path"])
            self.assertTrue((worktree_path / "child-output.txt").exists())
            self.assertEqual((worktree_path / "child-output.txt").read_text(encoding="utf-8"), "from isolated subagent\n")
            replay = runtime.replay_event_history()
            self.assertEqual(replay["worktrees"][handoff["id"]]["path"], str(worktree_path))

    def test_read_only_subagent_uses_parent_workspace_without_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=state_dir,
                specs=[SubagentSpec("reader", "read", "Read.", {"list_files"}, capabilities={"explore"})],
                load_config=False,
            )

            result = runtime.run("reader", "list files")

            self.assertFalse(result.is_error, result.content)
            handoff = json.loads(Path(state_dir, "handoffs.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertFalse(handoff["worktree_isolated"])
            self.assertEqual(Path(handoff["worktree_path"]), root.resolve())
            self.assertFalse((state_dir / "worktrees").exists())

    def test_subagent_blocked_state_records_missing_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=state_dir,
                specs=[SubagentSpec("explorer", "explore", "Explore.", {"list_files"}, capabilities={"explore"})],
                load_config=False,
            )

            result = runtime.run("explorer", "list files", session_id="missing")

            self.assertTrue(result.is_error)
            events = [
                json.loads(line)
                for line in Path(state_dir, "state-events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(events[0]["state"], "blocked")
            self.assertIn("session not found", events[0]["reason"])

    def test_load_subagent_specs_from_settings_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp, "fake_mcp.py")
            server.write_text(FAKE_MCP_SERVER, encoding="utf-8")
            specs = load_subagent_specs_from_payload(
                {
                    "subagents": {
                        "reader": {
                            "description": "config reader",
                            "system_prompt": "Read only.",
                            "tools": ["list_files", "read_file", "mcp__fake__echo"],
                            "model": "small-model",
                            "memory": {"mode": "configured"},
                            "capabilities": ["read", "custom"],
                            "max_turns": 2,
                            "mcp_servers": [
                                {
                                    "name": "fake",
                                    "transport": "stdio",
                                    "command": [sys.executable, str(server)],
                                    "protocol_version": "2024-11-05",
                                    "policy": {"allowed_tools": ["echo"]},
                                    "audit_log": str(Path(tmp, "audit.jsonl")),
                                }
                            ],
                        }
                    }
                }
            )

            self.assertEqual(len(specs), 1)
            self.assertEqual(specs[0].name, "reader")
            self.assertEqual(specs[0].allowed_tools, {"list_files", "read_file", "mcp__fake__echo"})
            self.assertEqual(specs[0].model, "small-model")
            self.assertEqual(specs[0].memory["mode"], "configured")
            self.assertEqual(specs[0].capabilities, {"read", "custom"})
            self.assertEqual(specs[0].mcp_adapters[0].list_tools()[0].name, "echo")
            blocked = specs[0].mcp_adapters[0].call_tool("blocked", {})
            self.assertTrue(blocked.is_error)
            self.assertIn("not allowed", blocked.content)

    def test_load_subagent_specs_supports_streamable_http_mcp(self) -> None:
        specs = load_subagent_specs_from_payload(
            {
                "subagents": {
                    "reader": {
                        "description": "remote reader",
                        "system_prompt": "Read remote context.",
                        "tools": ["mcp__remote__echo"],
                        "mcp_servers": [
                            {
                                "name": "remote",
                                "transport": "streamable_http",
                                "url": "http://127.0.0.1:9999/mcp",
                                "protocol_version": "2025-06-18",
                                "headers": {"X-Test": "yes"},
                                "auth_token": "token",
                                "max_retries": 3,
                                "retry_backoff": 0.25,
                                "policy": {"allowed_tools": ["echo"]},
                            }
                        ],
                    }
                }
            }
        )

        self.assertEqual(len(specs), 1)
        self.assertIsInstance(specs[0].mcp_adapters[0], GovernedMCPAdapter)
        inner = specs[0].mcp_adapters[0].adapter
        self.assertIsInstance(inner, StreamableHTTPMCPAdapter)
        self.assertEqual(inner.protocol_version, "2025-06-18")
        self.assertEqual(inner.headers["X-Test"], "yes")
        self.assertEqual(inner.headers["Authorization"], "Bearer token")
        self.assertEqual(inner.max_retries, 3)
        self.assertEqual(inner.retry_backoff, 0.25)

    def test_streamable_http_mcp_auth_can_load_from_env(self) -> None:
        old_token = os.environ.get("MCP_TEST_TOKEN")
        old_header = os.environ.get("MCP_TEST_HEADER")
        os.environ["MCP_TEST_TOKEN"] = "env-token"
        os.environ["MCP_TEST_HEADER"] = "env-header"
        try:
            specs = load_subagent_specs_from_payload(
                {
                    "subagents": {
                        "reader": {
                            "description": "remote reader",
                            "system_prompt": "Read remote context.",
                            "tools": ["mcp__remote__echo"],
                            "mcp_servers": [
                                {
                                    "name": "remote",
                                    "transport": "streamable_http",
                                    "url": "http://127.0.0.1:9999/mcp",
                                    "auth_token_env": "MCP_TEST_TOKEN",
                                    "headers_env": {"X-Test": "MCP_TEST_HEADER"},
                                }
                            ],
                        }
                    }
                }
            )
        finally:
            if old_token is None:
                os.environ.pop("MCP_TEST_TOKEN", None)
            else:
                os.environ["MCP_TEST_TOKEN"] = old_token
            if old_header is None:
                os.environ.pop("MCP_TEST_HEADER", None)
            else:
                os.environ["MCP_TEST_HEADER"] = old_header

        inner = specs[0].mcp_adapters[0]
        self.assertIsInstance(inner, StreamableHTTPMCPAdapter)
        self.assertEqual(inner.headers["Authorization"], "Bearer env-token")
        self.assertEqual(inner.headers["X-Test"], "env-header")

    def test_streamable_http_mcp_auth_env_allowlist_blocks_unlisted_env_vars(self) -> None:
        old_token = os.environ.get("MCP_ALLOWED_TOKEN")
        old_header = os.environ.get("MCP_BLOCKED_HEADER")
        os.environ["MCP_ALLOWED_TOKEN"] = "allowed-token"
        os.environ["MCP_BLOCKED_HEADER"] = "blocked-header"
        try:
            specs = load_subagent_specs_from_payload(
                {
                    "subagents": {
                        "reader": {
                            "description": "remote reader",
                            "system_prompt": "Read remote context.",
                            "tools": ["mcp__remote__echo"],
                            "mcp_servers": [
                                {
                                    "name": "remote",
                                    "transport": "streamable_http",
                                    "url": "http://127.0.0.1:9999/mcp",
                                    "auth_token_env": "MCP_ALLOWED_TOKEN",
                                    "headers_env": {"X-Test": "MCP_BLOCKED_HEADER"},
                                    "env_var_allowlist": ["MCP_ALLOWED_*"],
                                }
                            ],
                        }
                    }
                }
            )
        finally:
            if old_token is None:
                os.environ.pop("MCP_ALLOWED_TOKEN", None)
            else:
                os.environ["MCP_ALLOWED_TOKEN"] = old_token
            if old_header is None:
                os.environ.pop("MCP_BLOCKED_HEADER", None)
            else:
                os.environ["MCP_BLOCKED_HEADER"] = old_header

        inner = specs[0].mcp_adapters[0]
        self.assertIsInstance(inner, StreamableHTTPMCPAdapter)
        self.assertEqual(inner.headers["Authorization"], "Bearer allowed-token")
        self.assertNotIn("X-Test", inner.headers)

    def test_streamable_http_mcp_auth_loads_token_store_and_account_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_store = str(Path(tmp, "mcp-tokens.json"))
            specs = load_subagent_specs_from_payload(
                {
                    "subagents": {
                        "reader": {
                            "description": "remote reader",
                            "system_prompt": "Read remote context.",
                            "tools": ["mcp__remote__echo"],
                            "mcp_servers": [
                                {
                                    "name": "remote",
                                    "transport": "streamable_http",
                                    "url": "http://127.0.0.1:9999/mcp",
                                    "token_store": token_store,
                                    "account_profile": {"account_id": "acct-1", "label": "Test"},
                                }
                            ],
                        }
                    }
                }
            )

            inner = specs[0].mcp_adapters[0]
            self.assertIsInstance(inner, StreamableHTTPMCPAdapter)
            self.assertEqual(inner.account_id, "acct-1")
            self.assertEqual(inner.account_profile["label"], "Test")
            self.assertEqual(inner.token_store.path, Path(token_store))

    def test_load_subagent_specs_supports_websocket_mcp(self) -> None:
        specs = load_subagent_specs_from_payload(
            {
                "subagents": {
                    "reader": {
                        "description": "remote reader",
                        "system_prompt": "Read remote context.",
                        "tools": ["mcp__remote__echo"],
                        "mcp_servers": [
                            {
                                "name": "remote",
                                "transport": "websocket",
                                "url": "wss://example.test/mcp",
                                "protocol_version": "2025-06-18",
                                "headers": {"X-Test": "yes"},
                                "auth_token": "token",
                                "policy": {"allowed_tools": ["echo"]},
                            }
                        ],
                    }
                }
            }
        )

        self.assertEqual(len(specs), 1)
        self.assertIsInstance(specs[0].mcp_adapters[0], GovernedMCPAdapter)
        inner = specs[0].mcp_adapters[0].adapter
        self.assertIsInstance(inner, WebSocketMCPAdapter)
        self.assertEqual(inner.url, "wss://example.test/mcp")
        self.assertEqual(inner.protocol_version, "2025-06-18")
        self.assertEqual(inner.headers["X-Test"], "yes")
        self.assertEqual(inner.headers["Authorization"], "Bearer token")

    def test_runtime_loads_configured_subagent_and_overrides_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings_dir = root / ".mini_cc"
            settings_dir.mkdir()
            Path(settings_dir, "settings.json").write_text(
                json.dumps(
                    {
                        "subagents": {
                            "explorer": {
                                "description": "configured explorer",
                                "system_prompt": "Configured.",
                                "tools": ["list_files"],
                                "max_turns": 1,
                            },
                            "custom": {
                                "description": "custom agent",
                                "system_prompt": "Custom.",
                                "tools": ["list_files"],
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
            )

            self.assertIn("custom", runtime.specs)
            self.assertEqual(runtime.specs["explorer"].description, "configured explorer")
            self.assertEqual(runtime.specs["explorer"].allowed_tools, {"list_files"})

    def test_subagent_local_hook_config_can_block_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "deny.py"
            script.write_text(
                "\n".join(
                    [
                        "import json",
                        "print(json.dumps({'decision': 'block', 'reason': 'blocked by subagent hook'}))",
                    ]
                ),
                encoding="utf-8",
            )
            hook_dir = root / ".mini_cc" / "subagents" / "explorer"
            hook_dir.mkdir(parents=True)
            Path(hook_dir, "hooks.json").write_text(
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "list_files",
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
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=root / ".mini_cc" / "subagents",
                specs=[
                    SubagentSpec(
                        name="explorer",
                        description="read",
                        system_prompt="Read.",
                        allowed_tools={"list_files"},
                    )
                ],
                load_config=False,
            )

            result = runtime.run("explorer", "list files")

            self.assertFalse(result.is_error, result.content)
            self.assertIn("blocked by subagent hook", result.content)

    def test_subagent_handoff_log_and_session_index_link_child_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "README.md").write_text("# Demo\n", encoding="utf-8")
            state_dir = root / ".mini_cc" / "subagents"
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=state_dir,
                specs=[
                    SubagentSpec(
                        name="explorer",
                        description="read",
                        system_prompt="Read.",
                        allowed_tools={"list_files"},
                    )
                ],
                load_config=False,
            )

            result = runtime.run("explorer", "list files")

            self.assertFalse(result.is_error, result.content)
            handoff_rows = [
                json.loads(line)
                for line in Path(state_dir, "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            index = json.loads(Path(state_dir, "session-index.json").read_text(encoding="utf-8"))
            session_id = handoff_rows[0]["session_id"]
            self.assertEqual(handoff_rows[0]["subagent"], "explorer")
            self.assertEqual(handoff_rows[0]["prompt"], "list files")
            self.assertEqual(handoff_rows[0]["status"], "completed")
            self.assertIn("README.md", handoff_rows[0]["output_preview"])
            self.assertEqual(index["handoffs"][0]["session_id"], session_id)
            self.assertTrue(Path(state_dir, "explorer", "sessions", f"{session_id}.json").exists())

    def test_subagent_run_can_resume_existing_child_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "README.md").write_text("# Demo\n", encoding="utf-8")
            state_dir = root / ".mini_cc" / "subagents"
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=state_dir,
                specs=[SubagentSpec("explorer", "explore", "Explore.", {"list_files", "read_file"})],
                load_config=False,
            )

            first = runtime.run("explorer", "list files")
            self.assertFalse(first.is_error, first.content)
            first_handoff = json.loads(Path(state_dir, "handoffs.jsonl").read_text(encoding="utf-8").splitlines()[0])
            session_id = first_handoff["session_id"]

            second = runtime.run("explorer", "read README", session_id=session_id)

            self.assertFalse(second.is_error, second.content)
            session_path = Path(state_dir, "explorer", "sessions", f"{session_id}.json")
            payload = json.loads(session_path.read_text(encoding="utf-8"))
            prompts = [
                message["content"]
                for message in payload["messages"]
                if message.get("role") == "user" and isinstance(message.get("content"), str)
            ]
            self.assertIn("list files", prompts)
            self.assertIn("read README", prompts)
            self.assertTrue(
                any(
                    isinstance(message.get("content"), list)
                    and any(item.get("type") == "tool_result" for item in message["content"])
                    for message in payload["messages"]
                )
            )
            self.assertTrue(any(event["event"] == "session_resumed" for event in payload["events"]))

    def test_subagent_resume_rejects_unknown_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=root / ".mini_cc" / "subagents",
                specs=[SubagentSpec("explorer", "explore", "Explore.", {"list_files"})],
                load_config=False,
            )

            result = runtime.run("explorer", "list files", session_id="missing")

            self.assertTrue(result.is_error)
            self.assertIn("session not found", result.content)

    def test_subagent_can_delegate_to_nested_subagent_within_depth_and_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "README.md").write_text("# Demo\n", encoding="utf-8")
            state_dir = root / ".mini_cc" / "subagents"
            runner = S20ToolRunner(root, permission="auto")

            def factory(spec: SubagentSpec):
                if spec.name == "manager":
                    return DelegatingProvider("worker", "list files")
                return MockProvider()

            runtime = SubagentRuntime(
                workspace=root,
                base_tools=runner,
                provider_factory=factory,
                state_dir=state_dir,
                specs=[
                    SubagentSpec("manager", "delegate", "Delegate.", {"subagent_run"}, capabilities={"review"}),
                    SubagentSpec("worker", "explore", "Explore.", {"list_files"}, capabilities={"explore"}),
                ],
                max_nested_depth=1,
                nested_token_budget=20,
                load_config=False,
            )
            runner.set_subagents(runtime)

            result = runtime.run("manager", "delegate once")

            self.assertFalse(result.is_error, result.content)
            self.assertIn("Nested result", result.content)
            self.assertIn("README.md", result.content)
            handoffs = [
                json.loads(line)
                for line in Path(state_dir, "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([row["subagent"] for row in handoffs], ["worker", "manager"])
            self.assertEqual(handoffs[0]["depth"], 1)
            self.assertEqual(handoffs[0]["max_depth"], 1)
            self.assertEqual(handoffs[0]["nested_token_budget"], 20)
            self.assertEqual(handoffs[0]["task_contract"]["parent_contract_id"], handoffs[1]["contract_id"])

    def test_nested_subagent_respects_max_depth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = S20ToolRunner(root, permission="auto")
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=runner,
                provider_factory=lambda spec: DelegatingProvider("worker", "list files") if spec.name == "manager" else MockProvider(),
                specs=[
                    SubagentSpec("manager", "delegate", "Delegate.", {"subagent_run"}, capabilities={"review"}),
                    SubagentSpec("worker", "explore", "Explore.", {"list_files"}, capabilities={"explore"}),
                ],
                max_nested_depth=0,
                nested_token_budget=20,
                load_config=False,
            )
            runner.set_subagents(runtime)

            result = runtime.run("manager", "delegate once")

            self.assertFalse(result.is_error, result.content)
            self.assertIn("depth limit exceeded", result.content)

    def test_nested_subagent_respects_token_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = S20ToolRunner(root, permission="auto")
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=runner,
                provider_factory=lambda spec: DelegatingProvider("worker", "list files") if spec.name == "manager" else MockProvider(),
                specs=[
                    SubagentSpec("manager", "delegate", "Delegate.", {"subagent_run"}, capabilities={"review"}),
                    SubagentSpec("worker", "explore", "Explore.", {"list_files"}, capabilities={"explore"}),
                ],
                max_nested_depth=1,
                nested_token_budget=2,
                load_config=False,
            )
            runner.set_subagents(runtime)

            result = runtime.run("manager", "delegate once")

            self.assertFalse(result.is_error, result.content)
            self.assertIn("token budget exceeded", result.content)

    def test_standard_pipeline_records_decision_and_runs_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "README.md").write_text("# Demo\n", encoding="utf-8")
            state_dir = root / ".mini_cc" / "subagents"
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=state_dir,
                specs=[
                    SubagentSpec("explorer", "explore", "Explore.", {"list_files"}),
                    SubagentSpec("implementer", "implement", "Implement.", {"list_files"}),
                    SubagentSpec("verifier", "verify", "Verify.", {"list_files"}),
                    SubagentSpec("critic", "critic", "Critic.", {"list_files"}),
                ],
                load_config=False,
            )

            result = runtime.run_pipeline("list files", mode="auto")

            self.assertFalse(result.is_error, result.content)
            self.assertIn("mode: standard", result.content)
            self.assertIn("Step 1: explorer", result.content)
            self.assertIn("phase=explore", result.content)
            self.assertIn("group=read-only-discovery", result.content)
            decision = json.loads(Path(state_dir, "pipeline-decisions.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(decision["mode"], "standard")
            self.assertEqual(decision["contract_id"], decision["task_contract"]["id"])
            self.assertEqual([step["subagent"] for step in decision["steps"]], ["explorer", "implementer", "verifier"])
            self.assertEqual(decision["steps"][0]["parallel_group"], "read-only-discovery")
            self.assertEqual(decision["steps"][0]["task_contract"]["parent_contract_id"], decision["contract_id"])
            self.assertIn("list_files", decision["steps"][0]["task_contract"]["allowed_tools"])
            self.assertIn("explorer", decision["capabilities"])
            state_events = [
                json.loads(line)
                for line in Path(state_dir, "state-events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertIn("verifying", [event["state"] for event in state_events])
            replay = runtime.replay_event_history()
            pipeline = next(iter(replay["pipelines"].values()))
            self.assertEqual(pipeline["status"], "completed")
            self.assertEqual(pipeline["mode"], "standard")
            task_graph = next(iter(replay["task_graphs"].values()))
            self.assertEqual(task_graph["node_count"], 3)
            self.assertEqual(task_graph["nodes"]["task-1"]["status"], "completed")
            self.assertEqual(task_graph["nodes"]["task-2"]["dependencies"], ["task-1"])
            self.assertEqual(task_graph["nodes"]["task-3"]["dependencies"], ["task-2"])
            self.assertEqual(task_graph["nodes"]["task-3"]["last_event"], "task_node_released")

    def test_runtime_report_includes_trace_metrics_and_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "README.md").write_text("# Demo\n", encoding="utf-8")
            state_dir = root / ".mini_cc" / "subagents"
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=state_dir,
                specs=[
                    SubagentSpec("explorer", "explore", "Explore.", {"list_files"}, capabilities={"explore"}),
                    SubagentSpec("verifier", "verify", "Verify.", {"list_files"}, capabilities={"verify"}),
                ],
                load_config=False,
            )

            result = runtime.run_pipeline("list files", mode="standard")
            report = json.loads(runtime.runtime_report())
            text_report = runtime.runtime_report(format="text")

            self.assertFalse(result.is_error, result.content)
            self.assertEqual(report["runtime"]["version"], "2.0")
            self.assertTrue(report["capabilities"]["contract"])
            self.assertTrue(report["capabilities"]["trace_metrics_evaluation"])
            self.assertGreater(report["metrics"]["event_count"], 0)
            self.assertEqual(report["metrics"]["pipeline_count"], 1)
            self.assertEqual(report["metrics"]["completed_pipeline_count"], 1)
            self.assertEqual(report["metrics"]["task_graph_count"], 1)
            self.assertGreaterEqual(report["metrics"]["quality_gate_count"], 1)
            self.assertEqual(report["evaluation"]["status"], "pass")
            self.assertTrue(report["evaluation"]["runtime_v2_ready"])
            self.assertTrue(any(item["event"] == "pipeline_completed" for item in report["trace"]))
            self.assertIn("Subagent Runtime v2 Report", text_report)

    def test_task_graph_supports_retry_and_reroute_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=state_dir,
                specs=[
                    SubagentSpec("writer", "write", "Write.", {"write_file"}, capabilities={"implement", "write"}),
                    SubagentSpec("fallback-writer", "write", "Write.", {"write_file"}, capabilities={"implement", "write"}),
                ],
                load_config=False,
            )
            root_contract = runtime.build_task_contract(
                objective="write file",
                deliverable="file written",
                allowed_tools=set(),
                expected_evidence=["file"],
                budget={},
                stop_conditions=["done"],
                source="test",
            )
            step = runtime.with_step_contract(
                PipelineStep("writer", "write file", "test retry", phase="execute"),
                root_contract,
            )
            decision = PipelineDecision(
                id="pipeline-test",
                mode="dynamic",
                task="write file",
                steps=[step],
                task_contract=root_contract,
            )
            graph = runtime.build_task_graph(decision)
            runtime.record_task_graph(graph)

            node = graph.nodes[0]
            claimed = runtime.claim_task_node(graph, node, "writer")
            retried = runtime.retry_task_node(graph, claimed, "temporary failure")
            rerouted = runtime.reroute_task_node(graph, retried, "fallback-writer", "primary writer failed")

            self.assertEqual(claimed.status, "running")
            self.assertEqual(retried.status, "failed")
            self.assertEqual(rerouted.subagent, "fallback-writer")
            self.assertEqual(rerouted.rerouted_from, "writer")
            replay = runtime.replay_event_history()
            task_graph = replay["task_graphs"][graph.id]
            self.assertEqual(task_graph["nodes"]["task-1"]["last_event"], "task_node_rerouted")
            self.assertEqual(task_graph["nodes"]["task-1"]["subagent"], "fallback-writer")
            resume_state = replay["resume_state"]
            self.assertTrue(resume_state["ready"])
            self.assertEqual(resume_state["task_graphs"][graph.id]["nodes"]["task-1"]["subagent"], "fallback-writer")
            self.assertEqual(resume_state["task_graphs"][graph.id]["nodes"]["task-1"]["status"], "failed")

    def test_change_oriented_pipeline_includes_critic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=state_dir,
                specs=[
                    SubagentSpec("explorer", "explore", "Explore.", {"list_files"}, capabilities={"explore"}),
                    SubagentSpec("implementer", "implement", "Implement.", {"list_files"}, capabilities={"implement"}),
                    SubagentSpec("verifier", "verify", "Verify.", {"list_files"}, capabilities={"verify"}),
                    SubagentSpec("critic", "critic", "Critic.", {"list_files"}, capabilities={"review"}),
                ],
                load_config=False,
            )

            result = runtime.run_pipeline("fix and edit the project", mode="standard")

            self.assertFalse(result.is_error, result.content)
            decision = json.loads(Path(state_dir, "pipeline-decisions.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual([step["subagent"] for step in decision["steps"]], ["explorer", "implementer", "verifier", "critic"])

    def test_pipeline_runs_read_only_parallel_group_concurrently(self) -> None:
        class SlowRuntime(SubagentRuntime):
            def run(self, name: str, prompt: str, **kwargs) -> ToolResult:
                del prompt, kwargs
                time.sleep(0.2)
                return ToolResult(f"{name} done")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = SlowRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                specs=[
                    SubagentSpec("explorer-a", "explore", "Explore.", {"list_files"}, capabilities={"explore"}),
                    SubagentSpec("explorer-b", "explore", "Explore.", {"read_file"}, capabilities={"explore"}),
                ],
                max_parallel_subagents=2,
                load_config=False,
            )

            started = time.perf_counter()
            result = runtime.run_pipeline("inspect project", mode="standard")
            elapsed = time.perf_counter() - started

            self.assertFalse(result.is_error, result.content)
            self.assertIn("Parallel group: read-only-discovery", result.content)
            self.assertIn("parallel=true", result.content)
            self.assertLess(elapsed, 0.39)

    def test_plan_approval_gate_blocks_unsafe_mixed_parallel_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            planner = JsonPlannerProvider(
                {
                    "steps": [
                        {
                            "subagent": "reader",
                            "prompt": "read",
                            "phase": "explore",
                            "reason": "read context",
                            "required_capabilities": ["explore"],
                            "parallel_group": "mixed",
                        },
                        {
                            "subagent": "writer",
                            "prompt": "write",
                            "phase": "execute",
                            "reason": "write file",
                            "required_capabilities": ["implement"],
                            "parallel_group": "mixed",
                        },
                    ]
                }
            )
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: TextOnlyProvider(),
                planning_provider=planner,
                state_dir=state_dir,
                specs=[
                    SubagentSpec("reader", "read", "Read.", {"list_files"}, capabilities={"explore"}),
                    SubagentSpec("writer", "write", "Write.", {"write_file"}, capabilities={"implement", "write"}),
                ],
                load_config=False,
            )

            result = runtime.run_pipeline("unsafe mixed parallel group", mode="dynamic")

            self.assertTrue(result.is_error)
            self.assertIn("plan_approval blocked", result.content)
            self.assertFalse(Path(state_dir, "handoffs.jsonl").exists())
            replay = runtime.replay_event_history()
            self.assertEqual(replay["quality_gates"][0]["gate"], "plan_approval")
            self.assertFalse(replay["quality_gates"][0]["passed"])

    def test_implementation_gate_blocks_write_step_without_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            planner = JsonPlannerProvider(
                {
                    "steps": [
                        {
                            "subagent": "writer",
                            "prompt": "claim done without writing",
                            "phase": "execute",
                            "reason": "write file",
                            "required_capabilities": ["implement"],
                        }
                    ]
                }
            )
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: TextOnlyProvider("done but no file changed"),
                planning_provider=planner,
                state_dir=state_dir,
                specs=[SubagentSpec("writer", "write", "Write.", {"write_file"}, capabilities={"implement", "write"})],
                load_config=False,
            )

            result = runtime.run_pipeline("writer must produce a diff", mode="dynamic")

            self.assertTrue(result.is_error)
            self.assertIn("quality_gate: implementation blocked", result.content)
            replay = runtime.replay_event_history()
            gates = [gate for gate in replay["quality_gates"] if gate["gate"] == "implementation"]
            self.assertEqual(len(gates), 1)
            self.assertFalse(gates[0]["passed"])
            self.assertIn("no file diff", gates[0]["reason"])

    def test_pipeline_runs_isolated_write_parallel_group_and_merges_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            planner = JsonPlannerProvider(
                {
                    "steps": [
                        {
                            "subagent": "writer-a",
                            "prompt": "write alpha",
                            "phase": "execute",
                            "reason": "write alpha file",
                            "required_capabilities": ["implement"],
                            "parallel_group": "isolated-write",
                        },
                        {
                            "subagent": "writer-b",
                            "prompt": "write beta",
                            "phase": "execute",
                            "reason": "write beta file",
                            "required_capabilities": ["implement"],
                            "parallel_group": "isolated-write",
                        },
                    ]
                }
            )

            def factory(spec: SubagentSpec) -> TargetedWriteProvider:
                if spec.name == "writer-a":
                    return TargetedWriteProvider("alpha.txt", "alpha\n", delay=0.3)
                return TargetedWriteProvider("beta.txt", "beta\n", delay=0.3)

            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=factory,
                planning_provider=planner,
                state_dir=state_dir,
                specs=[
                    SubagentSpec("writer-a", "write", "Write.", {"write_file"}, capabilities={"implement", "write"}),
                    SubagentSpec("writer-b", "write", "Write.", {"write_file"}, capabilities={"implement", "write"}),
                ],
                max_parallel_subagents=2,
                load_config=False,
            )

            started = time.perf_counter()
            result = runtime.run_pipeline("write independent files", mode="dynamic")
            elapsed = time.perf_counter() - started

            self.assertFalse(result.is_error, result.content)
            self.assertIn("kind=isolated_write", result.content)
            self.assertIn("parallel write merge completed", result.content)
            self.assertEqual((root / "alpha.txt").read_text(encoding="utf-8"), "alpha\n")
            self.assertEqual((root / "beta.txt").read_text(encoding="utf-8"), "beta\n")
            self.assertLess(elapsed, 0.55)
            handoffs = [
                json.loads(line)
                for line in Path(state_dir, "handoffs.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual({row["changed_files"][0] for row in handoffs}, {"alpha.txt", "beta.txt"})
            self.assertTrue(all(row["worktree_isolated"] for row in handoffs))
            replay = runtime.replay_event_history()
            self.assertEqual(len(replay["parallel_write_merges"]), 1)
            self.assertEqual(replay["parallel_write_conflicts"], [])

    def test_pipeline_blocks_conflicting_parallel_write_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "shared.txt").write_text("original\n", encoding="utf-8")
            state_dir = root / ".mini_cc" / "subagents"
            planner = JsonPlannerProvider(
                {
                    "steps": [
                        {
                            "subagent": "writer-a",
                            "prompt": "write shared A",
                            "phase": "execute",
                            "reason": "write shared file",
                            "required_capabilities": ["implement"],
                            "parallel_group": "isolated-write",
                        },
                        {
                            "subagent": "writer-b",
                            "prompt": "write shared B",
                            "phase": "execute",
                            "reason": "write shared file",
                            "required_capabilities": ["implement"],
                            "parallel_group": "isolated-write",
                        },
                    ]
                }
            )

            def factory(spec: SubagentSpec) -> TargetedWriteProvider:
                content = "from a\n" if spec.name == "writer-a" else "from b\n"
                return TargetedWriteProvider("shared.txt", content)

            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=factory,
                planning_provider=planner,
                state_dir=state_dir,
                specs=[
                    SubagentSpec("writer-a", "write", "Write.", {"write_file"}, capabilities={"implement", "write"}),
                    SubagentSpec("writer-b", "write", "Write.", {"write_file"}, capabilities={"implement", "write"}),
                ],
                max_parallel_subagents=2,
                load_config=False,
            )

            result = runtime.run_pipeline("write conflicting file", mode="dynamic")

            self.assertTrue(result.is_error)
            self.assertIn("parallel write merge blocked by conflicts", result.content)
            self.assertEqual((root / "shared.txt").read_text(encoding="utf-8"), "original\n")
            replay = runtime.replay_event_history()
            self.assertEqual(len(replay["parallel_write_conflicts"]), 1)
            self.assertEqual(replay["parallel_write_conflicts"][0]["conflicts"], {"shared.txt": ["writer-a", "writer-b"]})
            merge_gates = [gate for gate in replay["quality_gates"] if gate["gate"] == "merge"]
            self.assertEqual(len(merge_gates), 1)
            self.assertFalse(merge_gates[0]["passed"])

    def test_semantic_merge_conflict_detector_reports_symbol_adjacent_and_config_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                load_config=False,
            )
            patch_a = """--- a/app.py
+++ b/app.py
@@ -1,5 +1,5 @@
 def configure():
-    timeout = 10
+    timeout = 20
     retries = 3
--- a/settings.toml
+++ b/settings.toml
@@ -1,2 +1,2 @@
-timeout = 10
+timeout = 20
"""
            patch_b = """--- a/app.py
+++ b/app.py
@@ -1,5 +1,5 @@
 def configure():
     timeout = 10
-    retries = 3
+    retries = 4
--- a/settings.toml
+++ b/settings.toml
@@ -1,2 +1,2 @@
-timeout = 10
+timeout = 30
"""
            conflicts = runtime.detect_semantic_merge_conflicts(
                [
                    {"subagent": "writer-a", "diff": {"patch": patch_a}, "changed_files": ["app.py", "settings.toml"]},
                    {"subagent": "writer-b", "diff": {"patch": patch_b}, "changed_files": ["app.py", "settings.toml"]},
                ]
            )

            conflict_types = {conflict["type"] for conflict in conflicts}
            self.assertIn("same_symbol", conflict_types)
            self.assertIn("adjacent_lines", conflict_types)
            self.assertIn("same_config_key", conflict_types)

    def test_merge_gate_blocks_missing_verification_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                load_config=False,
            )

            gate = runtime.evaluate_merge_gate(
                pipeline_id="pipeline-test",
                group_name="isolated-write",
                records=[
                    {
                        "subagent": "writer",
                        "worktree": {"isolated": True},
                        "diff": {"changed_files": ["a.txt"], "patch": "--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-a\n+b\n"},
                        "changed_files": ["a.txt"],
                        "evidence": ["a.txt changed"],
                        "verification": [],
                    }
                ],
                conflicts={},
                semantic_conflicts=[],
            )

            self.assertFalse(gate.passed)
            self.assertIn("verification evidence", gate.reason)

    def test_pipeline_does_not_parallelize_write_capable_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                specs=[
                    SubagentSpec("reader", "explore", "Read.", {"list_files"}, capabilities={"explore"}),
                    SubagentSpec("writer", "explore", "Write.", {"write_file"}, capabilities={"explore"}),
                ],
                max_parallel_subagents=2,
                load_config=False,
            )
            steps = [
                PipelineStep("reader", "read", "read", parallel_group="read-only-discovery"),
                PipelineStep("writer", "write", "write", parallel_group="read-only-discovery"),
            ]

            self.assertFalse(runtime.can_run_parallel_group(steps))
            self.assertTrue(
                runtime.can_run_parallel_group(
                    [
                        PipelineStep("writer", "write a", "write", parallel_group="isolated-write"),
                        PipelineStep("writer", "write b", "write", parallel_group="isolated-write"),
                    ]
                )
            )

    def test_capability_registry_selects_read_only_subagents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = SubagentRuntime(
                workspace=Path(tmp),
                base_tools=S20ToolRunner(Path(tmp), permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                specs=[
                    SubagentSpec("reader", "read", "Read.", {"list_files"}, capabilities={"explore"}),
                    SubagentSpec("writer", "write", "Write.", {"write_file"}, capabilities={"explore"}),
                ],
                load_config=False,
            )

            self.assertEqual(runtime.capability_registry()["reader"], ["explore"])
            self.assertEqual(runtime.select_subagents_by_capability({"explore"}, read_only=True), ["reader"])

    def test_benchmark_pipeline_selects_bench_diagnoser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                state_dir=state_dir,
                specs=[
                    SubagentSpec("bench-diagnoser", "bench", "Diagnose.", {"list_files"}),
                    SubagentSpec("explorer", "explore", "Explore.", {"list_files"}),
                ],
                load_config=False,
            )

            result = runtime.run_pipeline("analyze Terminal-Bench results.json Docker failure", mode="auto")

            self.assertFalse(result.is_error, result.content)
            self.assertIn("mode: benchmark", result.content)
            self.assertIn("Step 1: bench-diagnoser", result.content)
            decision = json.loads(Path(state_dir, "pipeline-decisions.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual([step["subagent"] for step in decision["steps"]], ["bench-diagnoser"])
            self.assertEqual(decision["steps"][0]["phase"], "diagnose")

    def test_dynamic_pipeline_uses_schema_validated_model_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "README.md").write_text("# Demo\n", encoding="utf-8")
            state_dir = root / ".mini_cc" / "subagents"
            planner = JsonPlannerProvider(
                {
                    "steps": [
                        {
                            "subagent": "explorer",
                            "prompt": "list files",
                            "phase": "explore",
                            "reason": "inspect project shape",
                            "required_capabilities": ["explore"],
                            "parallel_group": "read-only-discovery",
                            "read_only": True,
                        },
                        {
                            "subagent": "verifier",
                            "prompt": "list files",
                            "phase": "verify",
                            "reason": "verify readable workspace",
                            "required_capabilities": ["verify"],
                        },
                    ]
                }
            )
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                planning_provider=planner,
                state_dir=state_dir,
                specs=[
                    SubagentSpec("explorer", "explore", "Explore.", {"list_files"}, capabilities={"explore"}),
                    SubagentSpec("verifier", "verify", "Verify.", {"list_files"}, capabilities={"verify"}),
                ],
                load_config=False,
            )

            result = runtime.run_pipeline("inspect then verify", mode="dynamic")

            self.assertFalse(result.is_error, result.content)
            self.assertIn("mode: dynamic", result.content)
            self.assertIn("Step 1: explorer", result.content)
            self.assertIn("Step 2: verifier", result.content)
            self.assertIn("available_subagents", planner.prompts[0])
            decision = json.loads(Path(state_dir, "pipeline-decisions.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(decision["planner"], "dynamic")
            self.assertEqual(decision["planning_issues"], [])
            self.assertEqual([step["subagent"] for step in decision["steps"]], ["explorer", "verifier"])

    def test_dynamic_pipeline_runs_by_dag_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            planner = JsonPlannerProvider(
                {
                    "steps": [
                        {
                            "subagent": "reader-a",
                            "prompt": "read a",
                            "phase": "explore",
                            "reason": "first independent read",
                            "required_capabilities": ["explore"],
                            "parallel_group": "readers",
                            "read_only": True,
                        },
                        {
                            "subagent": "reader-b",
                            "prompt": "read b",
                            "phase": "explore",
                            "reason": "second independent read",
                            "required_capabilities": ["explore"],
                            "parallel_group": "readers",
                            "read_only": True,
                        },
                        {
                            "subagent": "verifier",
                            "prompt": "verify both reads",
                            "phase": "verify",
                            "reason": "needs both reader outputs",
                            "required_capabilities": ["verify"],
                            "dependencies": ["task-1", "task-2"],
                        },
                    ]
                }
            )
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                planning_provider=planner,
                state_dir=state_dir,
                specs=[
                    SubagentSpec("reader-a", "explore", "Read A.", {"list_files"}, capabilities={"explore"}),
                    SubagentSpec("reader-b", "explore", "Read B.", {"list_files"}, capabilities={"explore"}),
                    SubagentSpec("verifier", "verify", "Verify.", {"list_files"}, capabilities={"verify"}),
                ],
                max_parallel_subagents=2,
                load_config=False,
            )

            result = runtime.run_pipeline("read two areas then verify", mode="dynamic")

            self.assertFalse(result.is_error, result.content)
            self.assertIn("Parallel group: readers", result.content)
            decision = json.loads(Path(state_dir, "pipeline-decisions.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(decision["steps"][2]["dependencies"], ["task-1", "task-2"])
            replay = runtime.replay_event_history()
            task_graph = next(iter(replay["task_graphs"].values()))
            self.assertEqual(task_graph["nodes"]["task-3"]["dependencies"], ["task-1", "task-2"])
            events = [
                json.loads(line)
                for line in Path(state_dir, "event-history.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            release_positions = {
                event["payload"]["node_id"]: position
                for position, event in enumerate(events)
                if event["event"] == "task_node_released"
            }
            verifier_claim = next(
                position
                for position, event in enumerate(events)
                if event["event"] == "task_node_claimed" and event["payload"]["node_id"] == "task-3"
            )
            self.assertLess(release_positions["task-1"], verifier_claim)
            self.assertLess(release_positions["task-2"], verifier_claim)

    def test_plan_approval_gate_blocks_unknown_dag_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            planner = JsonPlannerProvider(
                {
                    "steps": [
                        {
                            "subagent": "reader",
                            "prompt": "read",
                            "phase": "explore",
                            "reason": "read first",
                            "required_capabilities": ["explore"],
                            "read_only": True,
                        },
                        {
                            "subagent": "verifier",
                            "prompt": "verify",
                            "phase": "verify",
                            "reason": "bad dependency",
                            "required_capabilities": ["verify"],
                            "dependencies": ["task-99"],
                        },
                    ]
                }
            )
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                planning_provider=planner,
                state_dir=state_dir,
                specs=[
                    SubagentSpec("reader", "explore", "Read.", {"list_files"}, capabilities={"explore"}),
                    SubagentSpec("verifier", "verify", "Verify.", {"list_files"}, capabilities={"verify"}),
                ],
                load_config=False,
            )

            result = runtime.run_pipeline("bad dag", mode="dynamic")

            self.assertTrue(result.is_error)
            self.assertIn("depends on unknown task node task-99", result.content)
            replay = runtime.replay_event_history()
            plan_gates = [gate for gate in replay["quality_gates"] if gate["gate"] == "plan_approval"]
            self.assertEqual(len(plan_gates), 1)
            self.assertFalse(plan_gates[0]["passed"])

    def test_subagents_exchange_structured_questions_answers_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            planner = JsonPlannerProvider(
                {
                    "steps": [
                        {
                            "subagent": "reader",
                            "prompt": "inspect config",
                            "phase": "explore",
                            "reason": "ask a downstream question",
                            "required_capabilities": ["explore"],
                            "read_only": True,
                        },
                        {
                            "subagent": "verifier",
                            "prompt": "answer reader question",
                            "phase": "verify",
                            "reason": "answer from peer packet",
                            "required_capabilities": ["verify"],
                            "dependencies": ["task-1"],
                        },
                    ]
                }
            )
            reader_provider = PeerAwareProvider(
                "QUESTION: Is config valid?\nARTIFACT: config.json\nCLAIM: config_status=unknown"
            )
            verifier_provider = PeerAwareProvider(
                "not used",
                reply_text="ANSWER: config is valid\nCLAIM: verification_status=passed",
            )

            def factory(spec: SubagentSpec) -> PeerAwareProvider:
                return reader_provider if spec.name == "reader" else verifier_provider

            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=factory,
                planning_provider=planner,
                state_dir=state_dir,
                specs=[
                    SubagentSpec("reader", "explore", "Read.", {"list_files"}, capabilities={"explore"}),
                    SubagentSpec("verifier", "verify", "Verify.", {"list_files"}, capabilities={"verify"}),
                ],
                load_config=False,
            )

            result = runtime.run_pipeline("peer communication", mode="dynamic")

            self.assertFalse(result.is_error, result.content)
            self.assertTrue(any("mini_cc_peer_v1" in prompt for prompt in verifier_provider.prompts))
            replay = runtime.replay_event_history()
            self.assertEqual(replay["peer_questions"][0]["question"], "Is config valid?")
            self.assertEqual(replay["peer_answers"][0]["answer"], "config is valid")
            self.assertEqual(replay["peer_artifacts"][0]["artifact"], {"kind": "declared", "value": "config.json"})

    def test_subagent_claim_contradictions_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            planner = JsonPlannerProvider(
                {
                    "steps": [
                        {
                            "subagent": "reader-a",
                            "prompt": "inspect status a",
                            "phase": "explore",
                            "reason": "first claim",
                            "required_capabilities": ["explore"],
                            "parallel_group": "readers",
                            "read_only": True,
                        },
                        {
                            "subagent": "reader-b",
                            "prompt": "inspect status b",
                            "phase": "explore",
                            "reason": "conflicting claim",
                            "required_capabilities": ["explore"],
                            "parallel_group": "readers",
                            "read_only": True,
                        },
                    ]
                }
            )

            def factory(spec: SubagentSpec) -> TextOnlyProvider:
                status = "green" if spec.name == "reader-a" else "red"
                return TextOnlyProvider(f"CLAIM: build_status={status}")

            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=factory,
                planning_provider=planner,
                state_dir=state_dir,
                specs=[
                    SubagentSpec("reader-a", "explore", "Read A.", {"list_files"}, capabilities={"explore"}),
                    SubagentSpec("reader-b", "explore", "Read B.", {"list_files"}, capabilities={"explore"}),
                ],
                max_parallel_subagents=2,
                load_config=False,
            )

            result = runtime.run_pipeline("detect contradiction", mode="dynamic")

            self.assertFalse(result.is_error, result.content)
            replay = runtime.replay_event_history()
            self.assertEqual(len(replay["peer_contradictions"]), 1)
            contradiction = replay["peer_contradictions"][0]
            self.assertEqual(contradiction["claim"], "build_status")
            self.assertEqual({contradiction["left"]["value"], contradiction["right"]["value"]}, {"green", "red"})

    def test_critic_rejection_blocks_review_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            planner = JsonPlannerProvider(
                {
                    "steps": [
                        {
                            "subagent": "implementer",
                            "prompt": "write implementation",
                            "phase": "execute",
                            "reason": "produce change",
                            "required_capabilities": ["implement"],
                        },
                        {
                            "subagent": "critic",
                            "prompt": "review implementation",
                            "phase": "review",
                            "reason": "review implementation result",
                            "required_capabilities": ["review"],
                            "dependencies": ["task-1"],
                        },
                    ]
                }
            )

            def factory(spec: SubagentSpec) -> object:
                if spec.name == "implementer":
                    return TargetedWriteProvider("feature.txt", "new feature\n")
                return TextOnlyProvider("REJECT: implementation misses required verification")

            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=factory,
                planning_provider=planner,
                state_dir=state_dir,
                specs=[
                    SubagentSpec("implementer", "implement", "Implement.", {"write_file"}, capabilities={"implement", "write"}),
                    SubagentSpec("critic", "critic", "Critic.", {"list_files"}, capabilities={"review", "critic"}),
                ],
                load_config=False,
            )

            result = runtime.run_pipeline("implement then review", mode="dynamic")

            self.assertTrue(result.is_error)
            self.assertIn("quality_gate: reviewer blocked", result.content)
            replay = runtime.replay_event_history()
            reviewer_gates = [gate for gate in replay["quality_gates"] if gate["gate"] == "reviewer"]
            self.assertEqual(len(reviewer_gates), 1)
            self.assertFalse(reviewer_gates[0]["passed"])
            self.assertEqual(replay["peer_rejections"][0]["reason"], "implementation misses required verification")

    def test_dynamic_pipeline_filters_invalid_or_unsafe_model_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / ".mini_cc" / "subagents"
            planner = JsonPlannerProvider(
                {
                    "steps": [
                        {
                            "subagent": "missing",
                            "prompt": "try missing",
                            "phase": "explore",
                            "reason": "bad name",
                        },
                        {
                            "subagent": "implementer",
                            "prompt": "write files while pretending to be read-only",
                            "phase": "explore",
                            "reason": "unsafe read-only claim",
                            "read_only": True,
                        },
                        {
                            "subagent": "verifier",
                            "prompt": "implement changes",
                            "phase": "execute",
                            "reason": "wrong capability",
                            "required_capabilities": ["implement"],
                        },
                        {
                            "subagent": "explorer",
                            "prompt": "list files",
                            "phase": "explore",
                            "reason": "valid fallback step",
                            "required_capabilities": ["explore"],
                        },
                    ]
                }
            )
            runtime = SubagentRuntime(
                workspace=root,
                base_tools=S20ToolRunner(root, permission="auto"),
                provider_factory=lambda _spec: MockProvider(),
                planning_provider=planner,
                state_dir=state_dir,
                specs=[
                    SubagentSpec("explorer", "explore", "Explore.", {"list_files"}, capabilities={"explore"}),
                    SubagentSpec("implementer", "implement", "Implement.", {"write_file"}, capabilities={"implement", "write"}),
                    SubagentSpec("verifier", "verify", "Verify.", {"list_files"}, capabilities={"verify"}),
                ],
                load_config=False,
            )

            result = runtime.run_pipeline("inspect safely", mode="dynamic")

            self.assertFalse(result.is_error, result.content)
            decision = json.loads(Path(state_dir, "pipeline-decisions.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(decision["planner"], "dynamic")
            self.assertEqual([step["subagent"] for step in decision["steps"]], ["explorer"])
            self.assertTrue(any("unknown subagent" in issue for issue in decision["planning_issues"]))
            self.assertTrue(any("not capable" in issue or "not read-only" in issue for issue in decision["planning_issues"]))

    def test_s20_exposes_subagent_pipeline_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = S20ToolRunner(Path(tmp), permission="auto")
            runtime = SubagentRuntime(
                workspace=Path(tmp),
                base_tools=runner,
                provider_factory=lambda _spec: MockProvider(),
                specs=[SubagentSpec("explorer", "explore", "Explore.", {"list_files"})],
                load_config=False,
            )
            runner.set_subagents(runtime)

            names = {schema["name"] for schema in runner.schemas()}

            self.assertIn("subagent_pipeline", names)
            self.assertIn("subagent_replay_events", names)
            self.assertIn("subagent_runtime_report", names)
            self.assertIn("subagent_mcp_registry", names)
            self.assertIn("subagent_mcp_tool_retrieval", names)
            self.assertIn("subagent_mcp_vector_index", names)
            report = json.loads(runner.run("subagent_runtime_report", {}).content)
            self.assertEqual(report["runtime"]["version"], "2.0")
            registry = json.loads(runner.run("subagent_mcp_registry", {}).content)
            self.assertEqual(registry["schema_version"], "2.5")
            retrieval = json.loads(runner.run("subagent_mcp_tool_retrieval", {"query": "anything"}).content)
            self.assertEqual(retrieval["schema_version"], "2.35")
            vector_index = json.loads(runner.run("subagent_mcp_vector_index", {}).content)
            self.assertEqual(vector_index["schema_version"], "2.35")


if __name__ == "__main__":
    unittest.main()
