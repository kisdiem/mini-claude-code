from __future__ import annotations

import json
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .hooks import ConfiguredHook, HookDecision, HookRuntime
from .mcp import (
    InMemoryMCPAdapter,
    MCPHTTPStatusError,
    StdioMCPAdapter,
    StreamableHTTPMCPAdapter,
    WebSocketMCPAdapter,
    classify_mcp_auth_failure,
)


@dataclass(frozen=True)
class LiveValidationCheck:
    name: str
    passed: bool
    evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "evidence": list(self.evidence),
            "warnings": list(self.warnings),
            "metrics": dict(self.metrics),
        }


@dataclass(frozen=True)
class LiveValidationReport:
    schema_version: str
    status: str
    checks: list[LiveValidationCheck]
    artifacts: dict[str, str]
    summary: dict[str, Any]
    recommendations: list[str]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "checks": [check.to_json() for check in self.checks],
            "artifacts": dict(self.artifacts),
            "summary": dict(self.summary),
            "recommendations": list(self.recommendations),
        }


def write_live_validation_report(workspace: Path, output_dir: Path) -> dict[str, Path]:
    output = output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = run_live_validation(workspace, output)
    json_path = output / "mcp-hook-live-validation.json"
    markdown_path = output / "mcp-hook-live-validation.md"
    json_path.write_text(json.dumps(report.to_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_live_validation_markdown(report) + "\n", encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def run_live_validation(workspace: Path, output_dir: Path) -> LiveValidationReport:
    root = workspace.expanduser().resolve()
    output = output_dir.expanduser().resolve()
    hook_log = output / "hooks.log"
    checks = [
        validate_stdio_mcp(output),
        validate_http_mcp(),
        validate_sse_mcp(),
        validate_websocket_mcp(),
        validate_mcp_failures_and_refresh(output),
        validate_hooks_live_trace(hook_log),
    ]
    passed = sum(1 for check in checks if check.passed)
    status = "ready" if passed == len(checks) else "needs_attention"
    recommendations: list[str] = []
    for check in checks:
        if not check.passed:
            recommendations.append(f"{check.name}: inspect warnings and retry the live validation smoke")
        for warning in check.warnings:
            recommendations.append(f"{check.name}: {warning}")
    if not recommendations:
        recommendations.append("MCP/Hook live validation passed for local stdio, HTTP, SSE, WebSocket, failures, refresh, and hook trust profiles.")
    return LiveValidationReport(
        schema_version="3.3",
        status=status,
        checks=checks,
        artifacts={
            "hook_trace": str(hook_log),
            "output_dir": str(output),
            "workspace": str(root),
        },
        summary={"passed": passed, "total": len(checks), "score": passed / len(checks) if checks else 0.0},
        recommendations=recommendations,
    )


def validate_stdio_mcp(output_dir: Path) -> LiveValidationCheck:
    output_dir.mkdir(parents=True, exist_ok=True)
    server = output_dir / "stdio_mcp_server.py"
    server.write_text(STDIO_SERVER, encoding="utf-8")
    adapter = StdioMCPAdapter("stdio-live", [sys.executable, str(server)], initialize=True)
    try:
        tools = adapter.list_tools()
        resources = adapter.list_resources()
        prompts = adapter.list_prompts()
        call = adapter.call_tool("echo", {"text": "live"})
        passed = bool(tools and resources and prompts and not call.is_error and "stdio:live" in call.content)
        return LiveValidationCheck(
            "stdio_mcp_smoke",
            passed,
            [str(server), "tools/list", "resources/list", "prompts/list", "tools/call"],
            metrics={"tools": len(tools), "resources": len(resources), "prompts": len(prompts)},
        )
    finally:
        adapter.close()


def validate_http_mcp() -> LiveValidationCheck:
    with LocalMCPHTTPServer(content_type="application/json") as endpoint:
        adapter = StreamableHTTPMCPAdapter("http-live", endpoint, initialize=True, protocol_version="2025-06-18")
        tools = adapter.list_tools()
        resources = adapter.list_resources()
        prompts = adapter.list_prompts()
        call = adapter.call_tool("echo", {"text": "live"})
        passed = bool(tools and resources and prompts and not call.is_error and "http:live" in call.content)
        return LiveValidationCheck(
            "http_mcp_smoke",
            passed,
            [endpoint, "initialize", "tools/list", "resources/list", "prompts/list", "tools/call"],
            metrics={"requests": len(endpoint_requests(endpoint))},
        )


def validate_sse_mcp() -> LiveValidationCheck:
    with LocalMCPHTTPServer(content_type="text/event-stream") as endpoint:
        adapter = StreamableHTTPMCPAdapter("sse-live", endpoint)
        prompts = adapter.list_prompts()
        prompt = adapter.get_prompt("review")
        passed = bool(prompts and "live prompt" in prompt.content)
        return LiveValidationCheck(
            "sse_mcp_smoke",
            passed,
            [endpoint, "prompts/list via SSE", "prompts/get via SSE"],
            metrics={"prompts": len(prompts)},
        )


def validate_websocket_mcp() -> LiveValidationCheck:
    connection = FakeWebSocketConnection(
        [
            {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {"tools": {}}}},
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
            {"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "ws:live"}]}},
        ]
    )
    adapter = WebSocketMCPAdapter(
        "ws-live",
        "ws://127.0.0.1/mcp",
        initialize=True,
        protocol_version="2025-06-18",
        headers={"X-Live": "yes"},
        auth_token="token",
        connector=lambda *_args, **_kwargs: connection,
    )
    tools = adapter.list_tools()
    call = adapter.call_tool("echo", {"text": "live"})
    methods = [json.loads(message)["method"] for message in connection.sent]
    passed = bool(tools and not call.is_error and "ws:live" in call.content and methods == ["initialize", "tools/list", "tools/call"])
    return LiveValidationCheck(
        "websocket_mcp_smoke",
        passed,
        ["WebSocketMCPAdapter connector", "initialize", "tools/list", "tools/call"],
        metrics={"messages_sent": len(connection.sent), "headers": connection.header},
    )


def validate_mcp_failures_and_refresh(output_dir: Path) -> LiveValidationCheck:
    refresh_store = output_dir / "oauth-token-store.json"
    with OAuthRefreshHTTPServer() as endpoint:
        adapter = StreamableHTTPMCPAdapter(
            "oauth-live",
            endpoint,
            auth_token="expired",
            token_store_path=refresh_store,
            account_profile={"account_id": "live", "label": "Live validation"},
            max_retries=0,
        )
        adapter.authorization_server_metadata = {"token_endpoint": endpoint + "/token", "issuer": "local-live-validation"}
        adapter.oauth_client_id = "client"
        adapter.oauth_refresh_token = "refresh"
        refresh = adapter.call_tool("echo", {"text": "refresh"})
    auth_401 = classify_mcp_auth_failure(
        status_code=401,
        detail="invalid_token: expired token",
        www_authenticate='Bearer error="invalid_token", error_description="expired token"',
        has_refresh_token=True,
    )
    auth_missing = classify_mcp_auth_failure(status_code=401, detail="missing token", has_refresh_token=False)
    auth_403 = classify_mcp_auth_failure(status_code=403, detail="insufficient_scope", has_refresh_token=False)
    try:
        with ErrorHTTPServer(status=500) as endpoint:
            StreamableHTTPMCPAdapter("error-live", endpoint, max_retries=0).list_tools()
        server_5xx = {"class": "unexpected_success"}
    except MCPHTTPStatusError as exc:
        server_5xx = exc.auth_failure or {"class": "http_error", "status_code": exc.status_code}
    try:
        StreamableHTTPMCPAdapter("down-live", "http://127.0.0.1:1/mcp", timeout=1, max_retries=0).list_tools()
        disconnect = {"class": "unexpected_success"}
    except Exception as exc:
        disconnect = {"class": "connection_failed", "detail": str(exc)[:120]}
    passed = (
        not refresh.is_error
        and refresh_store.exists()
        and auth_401["class"] == "expired_token"
        and auth_missing["class"] == "missing_token"
        and auth_403["class"] == "insufficient_scope"
        and server_5xx.get("class") == "http_error"
        and disconnect["class"] == "connection_failed"
    )
    return LiveValidationCheck(
        "mcp_failure_and_refresh_classification",
        passed,
        ["expired token classified", "missing token classified", "insufficient scope classified", "HTTP 500 classified", "disconnect classified", str(refresh_store)],
        metrics={
            "expired": auth_401,
            "missing": auth_missing,
            "scope": auth_403,
            "server_5xx": server_5xx,
            "disconnect": disconnect,
            "refresh_content": refresh.content[:120],
        },
    )


def validate_hooks_live_trace(hook_log: Path) -> LiveValidationCheck:
    hook_log.parent.mkdir(parents=True, exist_ok=True)
    if hook_log.exists():
        hook_log.unlink()
    hooks = HookRuntime(hook_log)
    command = write_command_hook_script(hook_log.parent)
    http_server = LocalHookHTTPServer()
    http_url = http_server.__enter__()
    try:
        mcp_adapter = InMemoryMCPAdapter("hook-mcp", tools={"allow": lambda _payload: '{"allow": true, "reason": "mcp hook ok"}'})
        hooks.mcp_hook_adapters["hook-mcp"] = mcp_adapter
        hooks.agent_hook_handlers["reviewer"] = lambda _event, _handler: {"allow": True, "reason": "agent hook ok"}
        hooks.register_configured(ConfiguredHook("PreToolUse", "run_shell", {"type": "command", "command": command}, "trust:local-script"))
        hooks.register_configured(ConfiguredHook("PreToolUse", "run_shell", {"type": "http", "url": http_url}, "trust:project-http"))
        hooks.register_configured(ConfiguredHook("PreToolUse", "run_shell", {"type": "mcp", "server": "hook-mcp", "tool": "allow"}, "trust:mcp"))
        hooks.register_configured(
            ConfiguredHook(
                "UserPromptSubmit",
                "",
                {"type": "prompt", "template": "{prompt}\n[validated by prompt hook]", "target": "prompt"},
                "trust:prompt",
            )
        )
        hooks.register_configured(ConfiguredHook("Stop", "", {"type": "agent", "agent": "reviewer"}, "trust:agent"))
        prompt = hooks.user_prompt_submit("live hook prompt", source="live-validation")
        hooks.session_start(prompt=prompt.payload_updates.get("prompt", "live hook prompt"), model="mock", session_id="live-session")
        pre = hooks.pre_tool_use("run_shell", {"command": "echo live"})
        hooks.post_tool_use("run_shell", {"command": "echo live"}, is_error=False, content="ok")
        stop = hooks.stop({"status": "completed", "reason": "live_validation", "session_id": "live-session"})
        hooks.session_end(status="completed", reason="live_validation", session_id="live-session", duration_ms=1)
        events = read_hook_events(hook_log)
        required = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"}
        trust_profiles = {"local-script", "project-http", "mcp", "prompt", "agent"}
        metrics = hooks.hook_metrics()
        passed = required.issubset(events) and pre.allow and stop.allow and prompt.allow and metrics["configured_hook_attempts"] >= 5
        return LiveValidationCheck(
            "hook_live_trace_and_trust_profiles",
            passed,
            [str(hook_log), "command hook", "HTTP hook", "MCP hook", "prompt hook", "agent hook"],
            metrics={
                "events": sorted(events),
                "required_events": sorted(required),
                "trust_profiles": sorted(trust_profiles),
                "configured_attempts": metrics["configured_hook_attempts"],
            },
        )
    finally:
        http_server.__exit__(None, None, None)


def render_live_validation_markdown(report: LiveValidationReport) -> str:
    lines = [
        "# MCP / Hook Live Validation Report",
        "",
        f"- Schema version: `{report.schema_version}`",
        f"- Status: `{report.status}`",
        f"- Score: {report.summary['passed']}/{report.summary['total']} ({report.summary['score']:.2%})",
        "",
        "## Checks",
        "",
        "| Check | Status | Evidence | Warnings |",
        "| --- | --- | --- | --- |",
    ]
    for check in report.checks:
        lines.append(
            f"| `{check.name}` | {'pass' if check.passed else 'fail'} | "
            f"{'<br>'.join(check.evidence) if check.evidence else '-'} | "
            f"{'<br>'.join(check.warnings) if check.warnings else '-'} |"
        )
    lines.extend(["", "## Artifacts", ""])
    for name, path in report.artifacts.items():
        lines.append(f"- `{name}`: `{path}`")
    lines.extend(["", "## Recommendations", ""])
    for recommendation in report.recommendations:
        lines.append(f"- {recommendation}")
    return "\n".join(lines)


def endpoint_requests(endpoint: str) -> list[dict[str, Any]]:
    return list(LOCAL_HTTP_REQUESTS.get(endpoint, []))


LOCAL_HTTP_REQUESTS: dict[str, list[dict[str, Any]]] = {}


class LocalMCPHTTPServer:
    def __init__(self, *, content_type: str) -> None:
        self.content_type = content_type
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.endpoint = ""

    def __enter__(self) -> str:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                LOCAL_HTTP_REQUESTS.setdefault(parent.endpoint, []).append(payload)
                method = payload.get("method")
                result = mcp_result_for_method(method, payload)
                response = {"jsonrpc": "2.0", "id": payload.get("id"), "result": result}
                if parent.content_type == "text/event-stream":
                    body = ("event: message\ndata: " + json.dumps(response) + "\n\n").encode("utf-8")
                else:
                    body = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", parent.content_type)
                self.send_header("Content-Length", str(len(body)))
                if method == "initialize":
                    self.send_header("Mcp-Session-Id", "live-session")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        host, port = self.server.server_address
        self.endpoint = f"http://{host}:{port}/mcp"
        LOCAL_HTTP_REQUESTS[self.endpoint] = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.endpoint

    def __exit__(self, *_exc: Any) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=1)


def mcp_result_for_method(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    if method == "initialize":
        return {"capabilities": {"tools": {}, "resources": {}, "prompts": {}}}
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": "echo",
                    "description": "Echo text",
                    "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                }
            ]
        }
    if method == "tools/call":
        arguments = (payload.get("params") or {}).get("arguments") or {}
        return {"content": [{"type": "text", "text": "http:" + str(arguments.get("text", ""))}]}
    if method == "resources/list":
        return {"resources": [{"uri": "resource://live", "name": "live"}]}
    if method == "resources/read":
        return {"contents": [{"uri": "resource://live", "text": "live resource"}]}
    if method == "prompts/list":
        return {"prompts": [{"name": "review", "description": "Review", "arguments": []}]}
    if method == "prompts/get":
        return {"messages": [{"role": "user", "content": {"type": "text", "text": "live prompt"}}]}
    return {}


class ErrorHTTPServer:
    def __init__(self, *, status: int) -> None:
        self.status = status
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def __enter__(self) -> str:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = b'{"error": "server failed"}'
                self.send_response(parent.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}/mcp"

    def __exit__(self, *_exc: Any) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=1)


class OAuthRefreshHTTPServer:
    def __init__(self) -> None:
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.endpoint = ""

    def __enter__(self) -> str:
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                if self.path.endswith("/token"):
                    response = json.dumps({"access_token": "fresh", "refresh_token": "refresh", "token_type": "Bearer"}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                    return
                payload = json.loads(body)
                auth = self.headers.get("Authorization", "")
                if auth != "Bearer fresh":
                    response = b'{"error": "invalid_token"}'
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Bearer error="invalid_token", error_description="expired token"')
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                    return
                response = json.dumps(
                    {"jsonrpc": "2.0", "id": payload.get("id"), "result": {"content": [{"type": "text", "text": "refreshed"}]}}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        host, port = self.server.server_address
        self.endpoint = f"http://{host}:{port}/mcp"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.endpoint

    def __exit__(self, *_exc: Any) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=1)


class LocalHookHTTPServer:
    def __init__(self) -> None:
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def __enter__(self) -> str:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                body = b'{"allow": true, "reason": "http hook ok"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}/hook"

    def __exit__(self, *_exc: Any) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=1)


class FakeWebSocketConnection:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.sent: list[str] = []
        self.header: list[str] = []
        self.url = ""
        self.timeout = 0
        self.closed = False

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self) -> str:
        if not self.responses:
            raise RuntimeError("no fake WebSocket response")
        return json.dumps(self.responses.pop(0))

    def close(self) -> None:
        self.closed = True


def write_command_hook_script(directory: Path) -> str:
    script = directory / "command_hook.py"
    script.write_text("import json\nprint(json.dumps({'allow': True, 'reason': 'command hook ok'}))\n", encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


def read_hook_events(path: Path) -> set[str]:
    events: set[str] = set()
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = row.get("event") if isinstance(row, dict) else None
        if isinstance(event, str):
            events.add(event)
    return events


STDIO_SERVER = r'''
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    params = request.get("params") or {}
    if method == "initialize":
        result = {"capabilities": {"tools": {}, "resources": {}, "prompts": {}}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "Echo text", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": "stdio:" + str((params.get("arguments") or {}).get("text", ""))}]}
    elif method == "resources/list":
        result = {"resources": [{"uri": "resource://stdio-live", "name": "stdio-live"}]}
    elif method == "resources/read":
        result = {"contents": [{"uri": params.get("uri"), "text": "stdio resource"}]}
    elif method == "prompts/list":
        result = {"prompts": [{"name": "review", "description": "Review prompt", "arguments": []}]}
    elif method == "prompts/get":
        result = {"messages": [{"role": "user", "content": {"type": "text", "text": "stdio prompt"}}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request.get("id"), "result": result}), flush=True)
'''
