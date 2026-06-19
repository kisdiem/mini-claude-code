from __future__ import annotations

import json
import threading
import sys
import tempfile
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from mini_cc.mcp import (
    GovernedMCPAdapter,
    InMemoryMCPAdapter,
    MCPPolicy,
    MCPTokenStore,
    StdioMCPAdapter,
    StreamableHTTPMCPAdapter,
    WebSocketMCPAdapter,
    mcp_capability_summary,
)


FAKE_SERVER = r'''
import json
import sys

request = json.loads(sys.stdin.readline())
method = request.get("method")
params = request.get("params") or {}
result = {}
if method == "tools/list":
    result = {
        "tools": [
            {
                "name": "echo",
                "description": "Echo text",
                "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
            }
        ]
    }
elif method == "tools/call":
    result = {"content": [{"type": "text", "text": "echo:" + str((params.get("arguments") or {}).get("text", ""))}]}
elif method == "resources/list":
    result = {"resources": [{"uri": "resource://note", "name": "note"}]}
elif method == "resources/read":
    result = {"contents": [{"uri": params.get("uri"), "text": "resource text"}]}
elif method == "prompts/list":
    result = {"prompts": [{"name": "review", "description": "Review prompt", "arguments": []}]}
elif method == "prompts/get":
    result = {"messages": [{"role": "user", "content": {"type": "text", "text": "review prompt"}}]}
else:
    print(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "error": {"message": "unknown method"}}))
    sys.exit(0)
print(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}))
'''

LONG_LIVED_SERVER = r'''
import json
import sys

count = 0
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    params = request.get("params") or {}
    count += 1
    if method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object", "properties": {}}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": f"count:{count}:{(params.get('arguments') or {}).get('text', '')}"}]}
    else:
        result = {"resources": []}
    print(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}), flush=True)
'''

INITIALIZE_SERVER = r'''
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        result = {"capabilities": {"tools": {}, "resources": {}}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object", "properties": {}}}]}
    else:
        result = {"content": [{"type": "text", "text": "ok"}]}
    print(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}), flush=True)
'''


class StdioMCPAdapterTests(unittest.TestCase):
    def test_stdio_mcp_adapter_lists_calls_and_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp, "fake_mcp.py")
            server.write_text(FAKE_SERVER, encoding="utf-8")
            adapter = StdioMCPAdapter("fake", [sys.executable, str(server)])

            tools = adapter.list_tools()
            call = adapter.call_tool("echo", {"text": "hi"})
            resources = adapter.list_resources()
            resource = adapter.read_resource("resource://note")
            prompts = adapter.list_prompts()
            prompt = adapter.get_prompt("review")

            self.assertEqual(tools[0].name, "echo")
            self.assertEqual(tools[0].input_schema["type"], "object")
            self.assertEqual(call.content, "echo:hi")
            self.assertEqual(resources[0].uri, "resource://note")
            self.assertEqual(resource.content, "resource text")
            self.assertEqual(prompts[0].name, "review")
            self.assertIn("review prompt", prompt.content)

    def test_stdio_mcp_adapter_reuses_long_lived_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp, "long_lived_mcp.py")
            server.write_text(LONG_LIVED_SERVER, encoding="utf-8")
            adapter = StdioMCPAdapter("fake", [sys.executable, str(server)])

            tools = adapter.list_tools()
            call = adapter.call_tool("echo", {"text": "hi"})
            adapter.close()

            self.assertEqual(tools[0].name, "echo")
            self.assertEqual(call.content, "count:2:hi")

    def test_stdio_mcp_adapter_initializes_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp, "initialize_mcp.py")
            server.write_text(INITIALIZE_SERVER, encoding="utf-8")
            adapter = StdioMCPAdapter("fake", [sys.executable, str(server)], initialize=True)

            tools = adapter.list_tools()
            adapter.close()

            self.assertEqual(tools[0].name, "echo")
            self.assertIn("tools", adapter.capabilities)

    def test_governed_mcp_adapter_filters_blocks_and_audits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp, "mcp-audit.jsonl")
            adapter = GovernedMCPAdapter(
                InMemoryMCPAdapter(
                    "local",
                    tools={
                        "echo": lambda payload: "echo:" + str(payload.get("text", "")),
                        "secret": lambda _payload: "secret",
                    },
                    resources={"resource://public": "public", "resource://secret": "secret"},
                    prompts={"safe": "safe prompt", "secret": "secret prompt"},
                ),
                policy=MCPPolicy(
                    allowed_tools={"echo"},
                    allowed_resources={"resource://public"},
                    blocked_prompts={"secret"},
                ),
                audit_log=audit,
            )

            tools = adapter.list_tools()
            allowed = adapter.call_tool("echo", {"text": "hi"})
            blocked_tool = adapter.call_tool("secret", {})
            resources = adapter.list_resources()
            blocked_resource = adapter.read_resource("resource://secret")
            prompts = adapter.list_prompts()
            blocked_prompt = adapter.get_prompt("secret")

            self.assertEqual([tool.name for tool in tools], ["echo"])
            self.assertEqual(allowed.content, "echo:hi")
            self.assertTrue(blocked_tool.is_error)
            self.assertIn("not allowed", blocked_tool.content)
            self.assertEqual([resource.uri for resource in resources], ["resource://public"])
            self.assertTrue(blocked_resource.is_error)
            self.assertIn("not allowed", blocked_resource.content)
            self.assertEqual([prompt.name for prompt in prompts], ["safe"])
            self.assertTrue(blocked_prompt.is_error)
            self.assertIn("blocked", blocked_prompt.content)
            rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(row["action"] == "tools/call" and row["allowed"] is False for row in rows))
            self.assertTrue(all(row.get("request_id") for row in rows))

    def test_mcp_policy_supports_patterns_and_blocks_high_risk_tools(self) -> None:
        adapter = GovernedMCPAdapter(
            InMemoryMCPAdapter(
                "local",
                tools={"read": lambda _payload: "ok", "write_file": lambda _payload: "bad"},
                resources={
                    "resource://public/a": "a",
                    "resource://private/a": "private",
                },
                prompts={"safe-review": "safe", "unsafe-review": "unsafe"},
            ),
            policy=MCPPolicy(
                allowed_resources={"resource://public/*"},
                blocked_prompts={"unsafe-*"},
            ),
        )

        tools = adapter.list_tools()
        resources = adapter.list_resources()
        prompts = adapter.list_prompts()
        blocked_tool = adapter.call_tool("write_file", {})
        blocked_resource = adapter.read_resource("resource://private/a")
        blocked_prompt = adapter.get_prompt("unsafe-review")

        self.assertEqual([tool.name for tool in tools], ["read"])
        self.assertEqual([resource.uri for resource in resources], ["resource://public/a"])
        self.assertEqual([prompt.name for prompt in prompts], ["safe-review"])
        self.assertTrue(blocked_tool.is_error)
        self.assertIn("high risk", blocked_tool.content)
        self.assertTrue(blocked_resource.is_error)
        self.assertIn("not allowed", blocked_resource.content)
        self.assertTrue(blocked_prompt.is_error)
        self.assertIn("blocked", blocked_prompt.content)

    def test_mcp_audit_includes_context_and_mcp_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp, "mcp-audit.jsonl")
            inner = InMemoryMCPAdapter("local", tools={"echo": lambda _payload: "ok"})
            inner.session_id = "mcp-session-1"
            adapter = GovernedMCPAdapter(
                inner,
                policy=MCPPolicy(allowed_tools={"echo"}),
                audit_log=audit,
                audit_context={"subagent": "reader", "session_id": "agent-session", "handoff_id": "handoff"},
            )

            adapter.call_tool("echo", {})

            row = json.loads(audit.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(row["subagent"], "reader")
            self.assertEqual(row["session_id"], "agent-session")
            self.assertEqual(row["handoff_id"], "handoff")
            self.assertEqual(row["mcp_session_id"], "mcp-session-1")

    def test_mcp_audit_redacts_sensitive_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp, "mcp-audit.jsonl")
            adapter = GovernedMCPAdapter(
                InMemoryMCPAdapter("local", tools={"echo": lambda _payload: "Authorization: Bearer secret-token"}),
                policy=MCPPolicy(allowed_tools={"echo"}),
                audit_log=audit,
            )

            adapter.call_tool("echo", {})
            adapter._audit(
                "test",
                allowed=True,
                is_error=False,
                detail={"auth_token": "secret-token", "nested": {"Authorization": "Bearer secret-token"}},
            )

            text = audit.read_text(encoding="utf-8")
            rows = [json.loads(line) for line in text.splitlines()]
            self.assertNotIn("secret-token", text)
            self.assertEqual(rows[0]["content_preview"], "[redacted sensitive content]")
            self.assertEqual(rows[1]["detail"]["auth_token"], "[redacted]")
            self.assertEqual(rows[1]["detail"]["nested"]["Authorization"], "[redacted]")

    def test_mcp_resource_reads_are_cached_and_audited_with_sensitivity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp, "mcp-audit.jsonl")
            inner = InMemoryMCPAdapter(
                "local",
                resources={"resource://private/secret-note": "secret value"},
            )
            adapter = GovernedMCPAdapter(
                inner,
                policy=MCPPolicy(allowed_resources={"resource://private/*"}),
                audit_log=audit,
                resource_cache_enabled=True,
            )

            first = adapter.read_resource("resource://private/secret-note")
            inner._resources["resource://private/secret-note"] = "changed value"
            second = adapter.read_resource("resource://private/secret-note")

            self.assertEqual(first.content, "secret value")
            self.assertEqual(second.content, "secret value")
            rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
            reads = [row for row in rows if row["action"] == "resources/read"]
            self.assertEqual(len(reads), 2)
            self.assertFalse(reads[0]["detail"]["cache_hit"])
            self.assertTrue(reads[1]["detail"]["cache_hit"])
            self.assertTrue(reads[0]["detail"]["sensitive"])
            self.assertEqual(reads[0]["detail"]["content_preview"], "[redacted sensitive content]")

    def test_mcp_prompt_get_pins_version_and_blocks_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp, "mcp-audit.jsonl")
            inner = InMemoryMCPAdapter("local", prompts={"review": "review prompt v1"})
            adapter = GovernedMCPAdapter(
                inner,
                policy=MCPPolicy(allowed_prompts={"review"}),
                audit_log=audit,
            )

            first = adapter.get_prompt("review")
            inner._prompts["review"] = "review prompt v2"
            second = adapter.get_prompt("review")

            self.assertFalse(first.is_error, first.content)
            self.assertTrue(second.is_error)
            self.assertIn("version mismatch", second.content)
            rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
            gets = [row for row in rows if row["action"] == "prompts/get"]
            self.assertTrue(gets[0]["detail"]["version_pin_created"])
            self.assertTrue(gets[1]["detail"]["version_mismatch"])
            self.assertEqual(adapter.prompt_versions["review"], gets[0]["detail"]["prompt_version"])

    def test_streamable_http_adapter_uses_protocol_session_and_json(self) -> None:
        seen: list[dict[str, str]] = []

        def handler(request: dict, headers: dict[str, str]) -> tuple[dict, dict[str, str], str]:
            seen.append(headers)
            method = request.get("method")
            if method == "initialize":
                return (
                    {"capabilities": {"tools": {}, "resources": {}, "prompts": {}}},
                    {"Mcp-Session-Id": "session-1"},
                    "application/json",
                )
            if method == "tools/list":
                return (
                    {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "Echo",
                                "inputSchema": {"type": "string", "properties": []},
                            }
                        ]
                    },
                    {},
                    "application/json",
                )
            if method == "tools/call":
                arguments = (request.get("params") or {}).get("arguments") or {}
                return (
                    {"content": [{"type": "text", "text": "http:" + str(arguments.get("text", ""))}]},
                    {},
                    "application/json",
                )
            return ({"resources": []}, {}, "application/json")

        with run_http_mcp_server(handler) as endpoint:
            adapter = StreamableHTTPMCPAdapter(
                "remote",
                endpoint,
                initialize=True,
                protocol_version="2025-06-18",
                headers={"X-Test": "yes"},
            )

            tools = adapter.list_tools()
            call = adapter.call_tool("echo", {"text": "hi"})

            self.assertEqual(adapter.session_id, "session-1")
            self.assertEqual(tools[0].name, "echo")
            self.assertEqual(tools[0].input_schema["type"], "object")
            self.assertEqual(call.content, "http:hi")
            self.assertEqual(header_value(seen[0], "MCP-Protocol-Version"), "2025-06-18")
            self.assertEqual(header_value(seen[1], "Mcp-Session-Id"), "session-1")
            self.assertEqual(header_value(seen[1], "X-Test"), "yes")

    def test_streamable_http_adapter_parses_sse_response(self) -> None:
        def handler(request: dict, _headers: dict[str, str]) -> tuple[dict, dict[str, str], str]:
            method = request.get("method")
            if method == "prompts/list":
                return ({"prompts": [{"name": "review", "description": "Review"}]}, {}, "text/event-stream")
            if method == "prompts/get":
                return (
                    {"messages": [{"role": "user", "content": {"type": "text", "text": "sse prompt"}}]},
                    {},
                    "text/event-stream",
                )
            return ({"tools": []}, {}, "text/event-stream")

        with run_http_mcp_server(handler) as endpoint:
            adapter = StreamableHTTPMCPAdapter("remote", endpoint)

            prompts = adapter.list_prompts()
            prompt = adapter.get_prompt("review")

            self.assertEqual(prompts[0].name, "review")
            self.assertIn("sse prompt", prompt.content)

    def test_mcp_tool_call_validates_required_and_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = Path(tmp, "fake_mcp.py")
            server.write_text(FAKE_SERVER, encoding="utf-8")
            adapter = StdioMCPAdapter("fake", [sys.executable, str(server)])

            adapter.list_tools()
            missing = adapter.call_tool("echo", {})
            wrong_type = adapter.call_tool("echo", {"text": 123})

            self.assertFalse(missing.is_error, missing.content)
            self.assertTrue(wrong_type.is_error)
            self.assertIn("schema validation", wrong_type.content)
            self.assertIn("text", wrong_type.content)

    def test_mcp_tool_call_validates_nested_schema_paths(self) -> None:
        calls = {"count": 0}

        def handler(request: dict, _headers: dict[str, str]) -> tuple[dict, dict[str, str], str]:
            method = request.get("method")
            if method == "tools/list":
                return (
                    {
                        "tools": [
                            {
                                "name": "nested",
                                "inputSchema": {
                                    "type": "object",
                                    "required": ["config"],
                                    "properties": {
                                        "config": {
                                            "type": "object",
                                            "required": ["limit"],
                                            "properties": {
                                                "limit": {"type": "integer"},
                                                "tags": {"type": "array", "items": {"type": "string"}},
                                            },
                                        }
                                    },
                                },
                            }
                        ]
                    },
                    {},
                    "application/json",
                )
            calls["count"] += 1
            return ({"content": [{"type": "text", "text": "sent"}]}, {}, "application/json")

        with run_http_mcp_server(handler) as endpoint:
            adapter = StreamableHTTPMCPAdapter("remote", endpoint)

            result = adapter.call_tool("nested", {"config": {"limit": "many", "tags": ["a", 2]}})

            self.assertTrue(result.is_error)
            self.assertIn("$.config.limit", result.content)
            self.assertIn("$.config.tags[1]", result.content)
            self.assertEqual(calls["count"], 0)

    def test_mcp_tool_call_validates_schema_constraints(self) -> None:
        calls = {"count": 0}

        def handler(request: dict, _headers: dict[str, str]) -> tuple[dict, dict[str, str], str]:
            method = request.get("method")
            if method == "tools/list":
                return (
                    {
                        "tools": [
                            {
                                "name": "strict",
                                "inputSchema": {
                                    "type": "object",
                                    "required": ["mode", "count", "tags", "choice"],
                                    "additionalProperties": False,
                                    "properties": {
                                        "mode": {
                                            "type": "string",
                                            "enum": ["read", "write"],
                                            "minLength": 3,
                                            "maxLength": 5,
                                            "pattern": "^[a-z]+$",
                                        },
                                        "count": {
                                            "type": "integer",
                                            "minimum": 1,
                                            "maximum": 5,
                                            "multipleOf": 1,
                                        },
                                        "tags": {
                                            "type": "array",
                                            "minItems": 1,
                                            "maxItems": 2,
                                            "uniqueItems": True,
                                            "items": {"type": "string"},
                                        },
                                        "choice": {
                                            "oneOf": [
                                                {"type": "string", "const": "alpha"},
                                                {"type": "integer", "minimum": 10},
                                            ]
                                        },
                                    },
                                },
                            }
                        ]
                    },
                    {},
                    "application/json",
                )
            calls["count"] += 1
            return ({"content": [{"type": "text", "text": "sent"}]}, {}, "application/json")

        with run_http_mcp_server(handler) as endpoint:
            adapter = StreamableHTTPMCPAdapter("remote", endpoint)

            result = adapter.call_tool(
                "strict",
                {
                    "mode": "READ",
                    "count": 7,
                    "tags": ["a", "a", 3],
                    "choice": 5,
                    "extra": True,
                },
            )
            ok = adapter.call_tool(
                "strict",
                {"mode": "read", "count": 3, "tags": ["a", "b"], "choice": "alpha"},
            )

            self.assertTrue(result.is_error)
            self.assertIn("$.extra", result.content)
            self.assertIn("$.mode", result.content)
            self.assertIn("$.count", result.content)
            self.assertIn("$.tags", result.content)
            self.assertIn("$.tags[2]", result.content)
            self.assertIn("$.choice", result.content)
            self.assertFalse(ok.is_error, ok.content)
            self.assertEqual(calls["count"], 1)

    def test_streamable_http_adapter_retries_transient_status(self) -> None:
        calls = {"count": 0}

        def handler(request: dict, _headers: dict[str, str]) -> tuple[dict, dict[str, str], str, int]:
            calls["count"] += 1
            if calls["count"] == 1:
                return ({"error": "temporary"}, {}, "application/json", 500)
            return ({"tools": [{"name": "echo", "inputSchema": {"type": "object", "properties": {}}}]}, {}, "application/json", 200)

        with run_http_mcp_server(handler) as endpoint:
            adapter = StreamableHTTPMCPAdapter("remote", endpoint, max_retries=1, retry_backoff=0)

            tools = adapter.list_tools()

            self.assertEqual(calls["count"], 2)
            self.assertEqual(tools[0].name, "echo")

    def test_streamable_http_adapter_reinitializes_after_session_expiry(self) -> None:
        seen_sessions: list[str | None] = []
        initialized = {"count": 0}

        def handler(request: dict, headers: dict[str, str]) -> tuple[dict, dict[str, str], str, int]:
            method = request.get("method")
            if method == "initialize":
                initialized["count"] += 1
                return (
                    {"capabilities": {"tools": {}}},
                    {"Mcp-Session-Id": f"session-{initialized['count']}"},
                    "application/json",
                    200,
                )
            seen_sessions.append(header_value(headers, "Mcp-Session-Id"))
            if seen_sessions[-1] == "session-1":
                return ({"error": "expired"}, {}, "application/json", 404)
            return ({"tools": [{"name": "echo", "inputSchema": {"type": "object", "properties": {}}}]}, {}, "application/json", 200)

        with run_http_mcp_server(handler) as endpoint:
            adapter = StreamableHTTPMCPAdapter("remote", endpoint, initialize=True, max_retries=1, retry_backoff=0)

            tools = adapter.list_tools()

            self.assertEqual(initialized["count"], 2)
            self.assertEqual(seen_sessions, ["session-1", "session-2"])
            self.assertEqual(tools[0].name, "echo")

    def test_streamable_http_adapter_discovers_oauth_metadata(self) -> None:
        with run_oauth_mcp_server() as endpoint:
            origin = endpoint.removesuffix("/mcp")
            adapter = StreamableHTTPMCPAdapter("remote", endpoint, oauth_discovery=True)

            self.assertEqual(adapter.protected_resource_metadata["resource"], endpoint)
            self.assertEqual(adapter.protected_resource_metadata["authorization_servers"], [origin + "/issuer"])
            self.assertEqual(adapter.authorization_server_metadata["issuer"], origin + "/issuer")
            self.assertIn("authorization_endpoint", adapter.authorization_server_metadata)
            summary = mcp_capability_summary(adapter)
            self.assertIn("oauth_resource", summary)
            self.assertIn("oauth_authorization_metadata", summary)

    def test_streamable_http_adapter_discovers_oauth_metadata_from_401_header(self) -> None:
        with run_oauth_mcp_server(always_unauthorized=True) as endpoint:
            adapter = StreamableHTTPMCPAdapter("remote", endpoint)

            with self.assertRaises(Exception):
                adapter.list_tools()

            self.assertEqual(adapter.protected_resource_metadata["resource"], endpoint)
            self.assertIn("issuer", adapter.authorization_server_metadata)

    def test_streamable_http_adapter_device_code_login_sets_bearer_token(self) -> None:
        with run_oauth_mcp_server() as endpoint:
            adapter = StreamableHTTPMCPAdapter("remote", endpoint, oauth_discovery=True)
            messages: list[str] = []

            token = adapter.login_with_device_code(client_id="mini-client", scope="mcp:read", timeout=2, output=messages.append)

            self.assertEqual(token["access_token"], "device-access-token")
            self.assertEqual(adapter.headers["Authorization"], "Bearer device-access-token")
            self.assertEqual(adapter.oauth_refresh_token, "refresh-token-1")
            self.assertTrue(any("CODE-123" in message for message in messages))

    def test_streamable_http_adapter_authorization_code_login_sets_bearer_token(self) -> None:
        with run_oauth_mcp_server() as endpoint:
            adapter = StreamableHTTPMCPAdapter("remote", endpoint, oauth_discovery=True)

            auth = adapter.build_authorization_url(
                client_id="mini-client",
                redirect_uri="http://127.0.0.1/callback",
                scope="mcp:read",
                state="state-1",
            )
            token = adapter.login_with_authorization_code(
                client_id="mini-client",
                code="browser-code",
                redirect_uri="http://127.0.0.1/callback",
                code_verifier=auth["code_verifier"],
            )

            self.assertIn("response_type=code", auth["url"])
            self.assertEqual(token["access_token"], "browser-access-token")
            self.assertEqual(adapter.headers["Authorization"], "Bearer browser-access-token")
            self.assertEqual(adapter.oauth_refresh_token, "refresh-token-1")

    def test_streamable_http_adapter_refreshes_oauth_token_after_401(self) -> None:
        with run_oauth_mcp_server(require_refreshed_token=True) as endpoint:
            adapter = StreamableHTTPMCPAdapter("remote", endpoint, oauth_discovery=True)

            adapter.login_with_device_code(client_id="mini-client", scope="mcp:read", timeout=2)
            tools = adapter.list_tools()

            self.assertEqual(tools[0].name, "echo")
            self.assertEqual(adapter.headers["Authorization"], "Bearer refreshed-access-token")
            self.assertEqual(adapter.oauth_refresh_token, "refresh-token-2")
            self.assertEqual(adapter.oauth_refresh_count, 1)

    def test_oauth_refresh_retry_does_not_require_transient_retry_budget(self) -> None:
        with run_oauth_mcp_server(require_refreshed_token=True) as endpoint:
            adapter = StreamableHTTPMCPAdapter("remote", endpoint, oauth_discovery=True, max_retries=0)

            adapter.login_with_device_code(client_id="mini-client", scope="mcp:read", timeout=2)
            tools = adapter.list_tools()

            self.assertEqual(tools[0].name, "echo")
            self.assertEqual(adapter.oauth_refresh_count, 1)

    def test_mcp_token_store_persists_oauth_tokens_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_store = Path(tmp, "tokens.json")
            with run_oauth_mcp_server() as endpoint:
                adapter = StreamableHTTPMCPAdapter(
                    "remote",
                    endpoint,
                    oauth_discovery=True,
                    token_store_path=token_store,
                    account_profile={"account_id": "acct-1", "label": "Test Account"},
                )

                adapter.login_with_device_code(client_id="mini-client", scope="mcp:read", timeout=2)
                resumed = StreamableHTTPMCPAdapter(
                    "remote",
                    endpoint,
                    oauth_discovery=True,
                    token_store_path=token_store,
                    account_profile={"account_id": "acct-1"},
                )

            stored = MCPTokenStore(token_store).redacted_profile("acct-1")
            self.assertEqual(resumed.headers["Authorization"], "Bearer device-access-token")
            self.assertEqual(resumed.oauth_refresh_token, "refresh-token-1")
            self.assertEqual(stored["account_id"], "acct-1")
            self.assertEqual(stored["label"], "Test Account")
            self.assertEqual(stored["token_response"], "[redacted]")

    def test_mcp_device_flow_can_resume_from_token_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_store = Path(tmp, "tokens.json")
            with run_oauth_mcp_server() as endpoint:
                first = StreamableHTTPMCPAdapter(
                    "remote",
                    endpoint,
                    oauth_discovery=True,
                    token_store_path=token_store,
                    account_profile={"account_id": "acct-1"},
                )
                device = first.start_device_authorization(client_id="mini-client", scope="mcp:read")
                second = StreamableHTTPMCPAdapter(
                    "remote",
                    endpoint,
                    oauth_discovery=True,
                    token_store_path=token_store,
                    account_profile={"account_id": "acct-1"},
                )

                token = second.resume_device_authorization(timeout=2)

            self.assertEqual(device["user_code"], "CODE-123")
            self.assertEqual(token["access_token"], "device-access-token")
            self.assertEqual(second.headers["Authorization"], "Bearer device-access-token")
            self.assertEqual(MCPTokenStore(token_store).load_pending_device_flow("acct-1"), {})

    def test_streamable_http_auth_failure_is_classified(self) -> None:
        with run_oauth_mcp_server(always_unauthorized=True) as endpoint:
            adapter = StreamableHTTPMCPAdapter("remote", endpoint)

            with self.assertRaises(Exception):
                adapter.list_tools()

            self.assertEqual(adapter.last_auth_failure["class"], "oauth_metadata_required")
            self.assertEqual(adapter.last_auth_failure["status_code"], 401)
            self.assertTrue(adapter.last_auth_failure["reauth_required"])
            self.assertIn("OAuth", adapter.last_auth_failure["reauth_prompt"])

    def test_websocket_mcp_adapter_lists_tools_and_calls_tool(self) -> None:
        connections: list[FakeWebSocketConnection] = []
        responses = [
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {"tools": {}}}},
            {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"message": "working"}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo text",
                            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                        }
                    ]
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "result": {"content": [{"type": "text", "text": "ws:hi"}]},
            },
        ]

        def connector(url: str, *, timeout: int, header: list[str]) -> "FakeWebSocketConnection":
            connection = FakeWebSocketConnection(responses)
            connection.url = url
            connection.timeout = timeout
            connection.header = header
            connections.append(connection)
            return connection

        adapter = WebSocketMCPAdapter(
            "remote",
            "ws://127.0.0.1/mcp",
            initialize=True,
            protocol_version="2025-06-18",
            headers={"X-Test": "yes"},
            auth_token="token",
            connector=connector,
        )

        tools = adapter.list_tools()
        call = adapter.call_tool("echo", {"text": "hi"})

        self.assertEqual(tools[0].name, "echo")
        self.assertEqual(call.content, "ws:hi")
        self.assertEqual(connections[0].url, "ws://127.0.0.1/mcp")
        self.assertIn("MCP-Protocol-Version: 2025-06-18", connections[0].header)
        self.assertIn("X-Test: yes", connections[0].header)
        self.assertIn("Authorization: Bearer token", connections[0].header)
        methods = [json.loads(message)["method"] for message in connections[0].sent]
        self.assertEqual(methods, ["initialize", "tools/list", "tools/call"])

    def test_websocket_mcp_adapter_validates_tool_input_before_send(self) -> None:
        responses = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                        }
                    ]
                },
            }
        ]
        connection = FakeWebSocketConnection(responses)
        adapter = WebSocketMCPAdapter("remote", "ws://127.0.0.1/mcp", connector=lambda *_args, **_kwargs: connection)

        result = adapter.call_tool("echo", {"text": 123})

        self.assertTrue(result.is_error)
        self.assertIn("schema validation", result.content)
        self.assertEqual(len(connection.sent), 1)


class run_http_mcp_server:
    def __init__(self, handler):
        self.handler = handler
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def __enter__(self) -> str:
        handler_func = self.handler

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                row = handler_func(payload, dict(self.headers.items()))
                if len(row) == 4:
                    result, headers, content_type, status = row
                else:
                    result, headers, content_type = row
                    status = 200
                response = {"jsonrpc": "2.0", "id": payload.get("id"), "result": result}
                if content_type == "text/event-stream":
                    body = ("event: message\ndata: " + json.dumps(response) + "\n\n").encode("utf-8")
                else:
                    body = json.dumps(response).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}/mcp"

    def __exit__(self, *_exc) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=1)


class FakeWebSocketConnection:
    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = list(responses)
        self.sent: list[str] = []
        self.closed = False
        self.url = ""
        self.timeout = 0
        self.header: list[str] = []

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self) -> str:
        if not self.responses:
            raise RuntimeError("no fake WebSocket response")
        return json.dumps(self.responses.pop(0))

    def close(self) -> None:
        self.closed = True


def header_value(headers: dict[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


class run_oauth_mcp_server:
    def __init__(self, always_unauthorized: bool = False, require_refreshed_token: bool = False):
        self.always_unauthorized = always_unauthorized
        self.require_refreshed_token = require_refreshed_token
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def __enter__(self) -> str:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                host, port = self.server.server_address  # type: ignore[attr-defined]
                origin = f"http://{host}:{port}"
                endpoint = origin + "/mcp"
                if self.path == "/.well-known/oauth-protected-resource" or self.path == "/.well-known/oauth-protected-resource/mcp":
                    body = json.dumps(
                        {
                            "resource": endpoint,
                            "authorization_servers": [origin + "/issuer"],
                            "scopes_supported": ["mcp:read"],
                        }
                    ).encode("utf-8")
                elif self.path == "/issuer/.well-known/oauth-authorization-server":
                    body = json.dumps(
                        {
                            "issuer": origin + "/issuer",
                            "authorization_endpoint": origin + "/issuer/authorize",
                            "token_endpoint": origin + "/issuer/token",
                            "device_authorization_endpoint": origin + "/issuer/device",
                        }
                    ).encode("utf-8")
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                host, port = self.server.server_address  # type: ignore[attr-defined]
                origin = f"http://{host}:{port}"
                if parent.always_unauthorized:
                    body = b'{"error":"unauthorized"}'
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.send_header(
                        "WWW-Authenticate",
                        f'Bearer resource_metadata="{origin}/.well-known/oauth-protected-resource"',
                    )
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/issuer/device":
                    length = int(self.headers.get("Content-Length", "0"))
                    form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                    if form.get("client_id") != ["mini-client"]:
                        raise AssertionError("unexpected client_id")
                    body = json.dumps(
                        {
                            "device_code": "device-code-123",
                            "user_code": "CODE-123",
                            "verification_uri": origin + "/activate",
                            "verification_uri_complete": origin + "/activate?user_code=CODE-123",
                            "interval": 0.1,
                            "message": "Open activation page and enter CODE-123",
                        }
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/issuer/token":
                    length = int(self.headers.get("Content-Length", "0"))
                    form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                    grant = (form.get("grant_type") or [""])[0]
                    if grant == "urn:ietf:params:oauth:grant-type:device_code":
                        token = "device-access-token"
                        refresh_token = "refresh-token-1"
                    elif grant == "authorization_code":
                        token = "browser-access-token"
                        refresh_token = "refresh-token-1"
                    elif grant == "refresh_token":
                        if form.get("refresh_token") != ["refresh-token-1"]:
                            raise AssertionError("unexpected refresh_token")
                        token = "refreshed-access-token"
                        refresh_token = "refresh-token-2"
                    else:
                        token = "unknown-token"
                        refresh_token = "refresh-token-unknown"
                    body = json.dumps(
                        {"access_token": token, "refresh_token": refresh_token, "token_type": "Bearer"}
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if parent.require_refreshed_token and self.headers.get("Authorization") != "Bearer refreshed-access-token":
                    body = b'{"error":"expired_token"}'
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                response = {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {"tools": [{"name": "echo", "inputSchema": {"type": "object", "properties": {}}}]},
                }
                body = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}/mcp"

    def __exit__(self, *_exc) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
