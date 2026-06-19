from __future__ import annotations

import json
import queue
import base64
import hashlib
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from fnmatch import fnmatchcase
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Protocol
import webbrowser

from .tools import ToolResult

DEFAULT_MCP_PROTOCOL_VERSION = "2025-06-18"
HIGH_RISK_MCP_TOOL_TOKENS = {
    "delete",
    "destroy",
    "drop",
    "exec",
    "execute",
    "mutation",
    "remove",
    "rm",
    "run",
    "shell",
    "update",
    "write",
}


@dataclass(frozen=True)
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class MCPResource:
    uri: str
    name: str
    description: str = ""


@dataclass(frozen=True)
class MCPPrompt:
    name: str
    description: str = ""
    arguments: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class MCPPolicy:
    allowed_tools: set[str] | None = None
    blocked_tools: set[str] | None = None
    allowed_resources: set[str] | None = None
    blocked_resources: set[str] | None = None
    allowed_prompts: set[str] | None = None
    blocked_prompts: set[str] | None = None
    block_high_risk_tools: bool = True

    def allows_tool(self, name: str) -> bool:
        if self.blocked_tools and pattern_set_matches(self.blocked_tools, name):
            return False
        if self.allowed_tools is not None:
            return pattern_set_matches(self.allowed_tools, name)
        if self.block_high_risk_tools and is_high_risk_mcp_tool_name(name):
            return False
        return self._allows(name, self.allowed_tools, self.blocked_tools)

    def allows_resource(self, uri: str) -> bool:
        return self._allows(uri, self.allowed_resources, self.blocked_resources)

    def allows_prompt(self, name: str) -> bool:
        return self._allows(name, self.allowed_prompts, self.blocked_prompts)

    def reason_for_tool(self, name: str) -> str:
        if self.blocked_tools and pattern_set_matches(self.blocked_tools, name):
            return f"MCP tool blocked by policy: {name}"
        if self.allowed_tools is not None and not pattern_set_matches(self.allowed_tools, name):
            return f"MCP tool not allowed by policy: {name}"
        if self.block_high_risk_tools and is_high_risk_mcp_tool_name(name):
            return f"MCP tool blocked as high risk by default: {name}"
        return self._reason(name, self.allowed_tools, self.blocked_tools, "tool")

    def reason_for_resource(self, uri: str) -> str:
        return self._reason(uri, self.allowed_resources, self.blocked_resources, "resource")

    def reason_for_prompt(self, name: str) -> str:
        return self._reason(name, self.allowed_prompts, self.blocked_prompts, "prompt")

    def _allows(self, value: str, allowed: set[str] | None, blocked: set[str] | None) -> bool:
        if blocked and pattern_set_matches(blocked, value):
            return False
        if allowed is not None and not pattern_set_matches(allowed, value):
            return False
        return True

    def _reason(self, value: str, allowed: set[str] | None, blocked: set[str] | None, kind: str) -> str:
        if blocked and pattern_set_matches(blocked, value):
            return f"MCP {kind} blocked by policy: {value}"
        if allowed is not None and not pattern_set_matches(allowed, value):
            return f"MCP {kind} not allowed by policy: {value}"
        return "allowed"


class MCPTokenStore:
    """Small JSON token store for MCP OAuth state.

    This is intentionally simple and portable. It centralizes persistence and
    redaction, but it is not an OS keychain.
    """

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": "2.5", "profiles": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": "2.5", "profiles": {}}
        if not isinstance(payload, dict):
            return {"schema_version": "2.5", "profiles": {}}
        payload.setdefault("schema_version", "2.5")
        if not isinstance(payload.get("profiles"), dict):
            payload["profiles"] = {}
        return payload

    def write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def profile(self, account_id: str) -> dict[str, Any]:
        payload = self.read()
        profiles = payload.setdefault("profiles", {})
        profile = profiles.get(account_id)
        if not isinstance(profile, dict):
            profile = {}
            profiles[account_id] = profile
        return dict(profile)

    def save_profile(self, account_id: str, profile: dict[str, Any]) -> None:
        payload = self.read()
        profiles = payload.setdefault("profiles", {})
        current = profiles.get(account_id) if isinstance(profiles.get(account_id), dict) else {}
        profiles[account_id] = {**current, **profile, "updated_at": time.time()}
        self.write(payload)

    def save_token_response(self, account_id: str, response: dict[str, Any], profile: dict[str, Any] | None = None) -> None:
        safe_response = {str(key): value for key, value in response.items() if isinstance(key, str)}
        self.save_profile(
            account_id,
            {
                **(profile or {}),
                "token_response": safe_response,
                "token_hash": content_hash(str(safe_response.get("access_token") or "")),
                "refresh_token_hash": content_hash(str(safe_response.get("refresh_token") or "")),
            },
        )

    def load_token_response(self, account_id: str) -> dict[str, Any]:
        token_response = self.profile(account_id).get("token_response", {})
        return dict(token_response) if isinstance(token_response, dict) else {}

    def save_pending_device_flow(self, account_id: str, pending: dict[str, Any]) -> None:
        self.save_profile(account_id, {"pending_device_flow": dict(pending)})

    def load_pending_device_flow(self, account_id: str) -> dict[str, Any]:
        pending = self.profile(account_id).get("pending_device_flow", {})
        return dict(pending) if isinstance(pending, dict) else {}

    def clear_pending_device_flow(self, account_id: str) -> None:
        payload = self.read()
        profile = payload.get("profiles", {}).get(account_id)
        if isinstance(profile, dict):
            profile.pop("pending_device_flow", None)
            profile["updated_at"] = time.time()
            self.write(payload)

    def redacted_profile(self, account_id: str) -> dict[str, Any]:
        profile = self.profile(account_id)
        if "token_response" in profile:
            token_response = profile.get("token_response")
            if isinstance(token_response, dict):
                profile["token_response"] = redact_secret_value(token_response)
        return redact_secret_value(profile)


class MCPAdapter(Protocol):
    name: str

    def list_tools(self) -> list[MCPTool]: ...

    def call_tool(self, name: str, tool_input: dict[str, Any]) -> ToolResult: ...

    def list_resources(self) -> list[MCPResource]: ...

    def read_resource(self, uri: str) -> ToolResult: ...

    def list_prompts(self) -> list[MCPPrompt]: ...

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult: ...


class InMemoryMCPAdapter:
    """Small MCP-like adapter for local tests and teaching integrations."""

    def __init__(
        self,
        name: str,
        *,
        tools: dict[str, Callable[[dict[str, Any]], str | ToolResult]] | None = None,
        resources: dict[str, str] | None = None,
        prompts: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self._tools = tools or {}
        self._resources = resources or {}
        self._prompts = prompts or {}

    def list_tools(self) -> list[MCPTool]:
        return [
            MCPTool(
                name=name,
                description=f"MCP tool {self.name}.{name}",
                input_schema={"type": "object", "properties": {}},
            )
            for name in sorted(self._tools)
        ]

    def call_tool(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        handler = self._tools.get(name)
        if handler is None:
            return ToolResult(f"MCP tool not found: {self.name}.{name}", is_error=True)
        try:
            result = handler(tool_input)
        except Exception as exc:
            return ToolResult(str(exc), is_error=True)
        if isinstance(result, ToolResult):
            return result
        return ToolResult(str(result))

    def list_resources(self) -> list[MCPResource]:
        return [
            MCPResource(uri=uri, name=uri.rsplit("/", 1)[-1] or uri)
            for uri in sorted(self._resources)
        ]

    def read_resource(self, uri: str) -> ToolResult:
        if uri not in self._resources:
            return ToolResult(f"MCP resource not found: {uri}", is_error=True)
        return ToolResult(self._resources[uri])

    def list_prompts(self) -> list[MCPPrompt]:
        return [MCPPrompt(name=name, description=f"MCP prompt {self.name}.{name}") for name in sorted(self._prompts)]

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        del arguments
        if name not in self._prompts:
            return ToolResult(f"MCP prompt not found: {self.name}.{name}", is_error=True)
        return ToolResult(self._prompts[name])


class GovernedMCPAdapter:
    """Policy and audit wrapper for any MCP adapter."""

    def __init__(
        self,
        adapter: MCPAdapter,
        *,
        policy: MCPPolicy | None = None,
        audit_log: Path | None = None,
        audit_context: dict[str, Any] | None = None,
        resource_cache_enabled: bool = True,
        prompt_versions: dict[str, str] | None = None,
    ) -> None:
        self.adapter = adapter
        self.name = adapter.name
        self.policy = policy or MCPPolicy()
        self.audit_log = audit_log
        self.audit_context = audit_context or {}
        self.resource_cache_enabled = resource_cache_enabled
        self.resource_cache: dict[str, ToolResult] = {}
        self.prompt_versions = dict(prompt_versions or {})

    def set_audit_context(self, context: dict[str, Any]) -> None:
        self.audit_context = {**self.audit_context, **context}

    def list_tools(self) -> list[MCPTool]:
        tools = [tool for tool in self.adapter.list_tools() if self.policy.allows_tool(tool.name)]
        self._audit("tools/list", allowed=True, is_error=False, detail={"count": len(tools)})
        return tools

    def call_tool(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        if not self.policy.allows_tool(name):
            result = ToolResult(self.policy.reason_for_tool(name), is_error=True)
            self._audit("tools/call", target=name, allowed=False, is_error=True, content=result.content)
            return result
        result = self.adapter.call_tool(name, tool_input)
        self._audit("tools/call", target=name, allowed=True, is_error=result.is_error, content=result.content)
        return result

    def list_resources(self) -> list[MCPResource]:
        resources = [
            resource for resource in self.adapter.list_resources() if self.policy.allows_resource(resource.uri)
        ]
        self._audit("resources/list", allowed=True, is_error=False, detail={"count": len(resources)})
        return resources

    def read_resource(self, uri: str) -> ToolResult:
        if not self.policy.allows_resource(uri):
            result = ToolResult(self.policy.reason_for_resource(uri), is_error=True)
            self._audit(
                "resources/read",
                target=uri,
                allowed=False,
                is_error=True,
                content=result.content,
                detail=self.resource_governance_detail(uri=uri, content=result.content, cache_hit=False),
            )
            return result
        if self.resource_cache_enabled and uri in self.resource_cache:
            result = self.resource_cache[uri]
            self._audit(
                "resources/read",
                target=uri,
                allowed=True,
                is_error=result.is_error,
                content=result.content,
                detail=self.resource_governance_detail(uri=uri, content=result.content, cache_hit=True),
            )
            return result
        result = self.adapter.read_resource(uri)
        if self.resource_cache_enabled and not result.is_error:
            self.resource_cache[uri] = result
        self._audit(
            "resources/read",
            target=uri,
            allowed=True,
            is_error=result.is_error,
            content=result.content,
            detail=self.resource_governance_detail(uri=uri, content=result.content, cache_hit=False),
        )
        return result

    def list_prompts(self) -> list[MCPPrompt]:
        prompts = [prompt for prompt in self.adapter.list_prompts() if self.policy.allows_prompt(prompt.name)]
        self._audit("prompts/list", allowed=True, is_error=False, detail={"count": len(prompts)})
        return prompts

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        if not self.policy.allows_prompt(name):
            result = ToolResult(self.policy.reason_for_prompt(name), is_error=True)
            self._audit(
                "prompts/get",
                target=name,
                allowed=False,
                is_error=True,
                content=result.content,
                detail={"version_pinned": name in self.prompt_versions},
            )
            return result
        result = self.adapter.get_prompt(name, arguments)
        detail = self.prompt_governance_detail(name, result.content, arguments)
        if not result.is_error:
            expected = self.prompt_versions.get(name)
            actual = detail["prompt_version"]
            if expected is None:
                self.prompt_versions[name] = actual
                detail["version_pinned"] = True
                detail["version_pin_created"] = True
            elif expected != actual:
                result = ToolResult(
                    f"MCP prompt version mismatch for {name}: expected {expected}, got {actual}",
                    is_error=True,
                )
                detail["version_mismatch"] = True
                detail["expected_prompt_version"] = expected
        self._audit("prompts/get", target=name, allowed=True, is_error=result.is_error, content=result.content, detail=detail)
        return result

    def close(self) -> None:
        close = getattr(self.adapter, "close", None)
        if callable(close):
            close()

    def _audit(
        self,
        action: str,
        *,
        target: str = "",
        allowed: bool,
        is_error: bool,
        content: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        if self.audit_log is None:
            return
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "request_id": uuid.uuid4().hex,
            "ts": time.time(),
            "server": self.name,
            "subagent": self.audit_context.get("subagent"),
            "session_id": self.audit_context.get("session_id"),
            "handoff_id": self.audit_context.get("handoff_id"),
            "mcp_session_id": getattr(self.adapter, "session_id", None),
            "action": action,
            "target": target,
            "allowed": allowed,
            "is_error": is_error,
            "content_preview": redact_secret_text(content[:500]),
            "detail": redact_secret_value(detail or {}),
        }
        with self.audit_log.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def resource_governance_detail(self, *, uri: str, content: str, cache_hit: bool) -> dict[str, Any]:
        sensitive = is_sensitive_mcp_resource(uri, content)
        return {
            "cache_enabled": self.resource_cache_enabled,
            "cache_hit": cache_hit,
            "sensitive": sensitive,
            "content_length": len(content or ""),
            "content_hash": content_hash(content),
            "content_preview": "[redacted sensitive content]" if sensitive else redact_secret_text((content or "")[:200]),
        }

    def prompt_governance_detail(
        self,
        name: str,
        content: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        actual = content_hash(content)
        return {
            "arguments_hash": content_hash(json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True)),
            "prompt_version": actual,
            "version_pinned": name in self.prompt_versions,
            "expected_prompt_version": self.prompt_versions.get(name),
            "content_length": len(content or ""),
            "content_preview": redact_secret_text((content or "")[:200]),
        }


class StdioMCPAdapter:
    """Minimal stdio JSON-RPC transport for external MCP-like servers."""

    def __init__(
        self,
        name: str,
        command: list[str],
        *,
        timeout: int = 10,
        initialize: bool = False,
        protocol_version: str = "2024-11-05",
    ) -> None:
        if not command:
            raise ValueError("MCP stdio command must not be empty")
        self.name = name
        self.command = command
        self.timeout = timeout
        self.initialize_on_start = initialize
        self.protocol_version = protocol_version
        self.capabilities: dict[str, Any] = {}
        self._next_id = 1
        self._process: subprocess.Popen[str] | None = None
        self._stdout_queue: queue.Queue[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._initialized = False
        self._tool_schemas: dict[str, dict[str, Any]] = {}

    def list_tools(self) -> list[MCPTool]:
        response = self._request("tools/list", {})
        tools = response.get("tools", [])
        if not isinstance(tools, list):
            return []
        parsed: list[MCPTool] = []
        for item in tools:
            if not isinstance(item, dict):
                continue
            parsed.append(
                MCPTool(
                    name=str(item.get("name") or ""),
                    description=str(item.get("description") or ""),
                    input_schema=normalize_mcp_input_schema(item),
                )
            )
        tools = [tool for tool in parsed if tool.name]
        self._tool_schemas.update({tool.name: tool.input_schema for tool in tools})
        return tools

    def call_tool(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        validation = self._validate_tool_input(name, tool_input)
        if validation:
            return ToolResult(validation, is_error=True)
        try:
            response = self._request("tools/call", {"name": name, "arguments": tool_input})
        except Exception as exc:
            return ToolResult(str(exc), is_error=True)
        if response.get("isError") is True:
            return ToolResult(render_mcp_content(response), is_error=True)
        return ToolResult(render_mcp_content(response))

    def _validate_tool_input(self, name: str, tool_input: dict[str, Any]) -> str | None:
        schema = self._tool_schemas.get(name)
        if schema is None:
            try:
                self.list_tools()
            except Exception:
                return None
            schema = self._tool_schemas.get(name)
        if schema is None:
            return None
        errors = validate_mcp_arguments(schema, tool_input)
        if errors:
            return "MCP tool input failed schema validation: " + "; ".join(errors)
        return None

    def list_resources(self) -> list[MCPResource]:
        response = self._request("resources/list", {})
        resources = response.get("resources", [])
        if not isinstance(resources, list):
            return []
        parsed: list[MCPResource] = []
        for item in resources:
            if not isinstance(item, dict):
                continue
            uri = str(item.get("uri") or "")
            if not uri:
                continue
            parsed.append(
                MCPResource(
                    uri=uri,
                    name=str(item.get("name") or uri.rsplit("/", 1)[-1] or uri),
                    description=str(item.get("description") or ""),
                )
            )
        return parsed

    def read_resource(self, uri: str) -> ToolResult:
        try:
            response = self._request("resources/read", {"uri": uri})
        except Exception as exc:
            return ToolResult(str(exc), is_error=True)
        return ToolResult(render_mcp_content(response))

    def list_prompts(self) -> list[MCPPrompt]:
        response = self._request("prompts/list", {})
        prompts = response.get("prompts", [])
        if not isinstance(prompts, list):
            return []
        parsed: list[MCPPrompt] = []
        for item in prompts:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if not name:
                continue
            arguments = item.get("arguments")
            parsed.append(
                MCPPrompt(
                    name=name,
                    description=str(item.get("description") or ""),
                    arguments=arguments if isinstance(arguments, list) else None,
                )
            )
        return parsed

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        try:
            response = self._request("prompts/get", {"name": name, "arguments": arguments or {}})
        except Exception as exc:
            return ToolResult(str(exc), is_error=True)
        return ToolResult(render_mcp_content(response))

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._request_once(method, params)
        except Exception:
            self.close()
            return self._request_once(method, params)

    def _request_once(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._ensure_process()
        request_id = self._next_id
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        self._send(payload)
        line = self._read_response_line()
        if line is None:
            raise RuntimeError(f"MCP server {self.name} returned no JSON response")
        response = json.loads(line)
        if response.get("id") != request_id:
            raise RuntimeError(f"MCP server {self.name} returned mismatched id")
        if response.get("error"):
            error = response["error"]
            if isinstance(error, dict):
                raise RuntimeError(str(error.get("message") or error))
            raise RuntimeError(str(error))
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise RuntimeError(f"MCP server {self.name} returned non-object result")
        return result

    def initialize(self) -> dict[str, Any]:
        response = self._request_once(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "mini-claude-code", "version": "0.81"},
            },
        )
        capabilities = response.get("capabilities", {})
        self.capabilities = capabilities if isinstance(capabilities, dict) else {}
        self._initialized = True
        return response

    def close(self) -> None:
        process = self._process
        self._process = None
        self._stdout_queue = None
        self._stdout_thread = None
        self._initialized = False
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
        except OSError:
            return

    def _ensure_process(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        self.close()
        self._process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._stdout_queue = queue.Queue()
        self._stdout_thread = threading.Thread(
            target=self._pump_stdout,
            args=(self._process, self._stdout_queue),
            daemon=True,
        )
        self._stdout_thread.start()
        if self.initialize_on_start and not self._initialized:
            self.initialize()

    def _send(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise RuntimeError(f"MCP server {self.name} is not running")
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def _read_response_line(self) -> str | None:
        stdout_queue = self._stdout_queue
        if stdout_queue is None:
            return None
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(f"MCP server {self.name} timed out after {self.timeout}s")
                try:
                    return stdout_queue.get(timeout=min(0.05, remaining)).strip()
                except queue.Empty:
                    process = self._process
                    if process is not None and process.poll() is not None and stdout_queue.empty():
                        raise RuntimeError(f"MCP server {self.name} exited before responding")
        except queue.Empty as exc:
            raise RuntimeError(f"MCP server {self.name} timed out after {self.timeout}s") from exc

    def _pump_stdout(self, process: subprocess.Popen[str], output: queue.Queue[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            stripped = line.strip()
            if stripped.startswith("{"):
                output.put(stripped)

    def __del__(self) -> None:
        self.close()


class WebSocketMCPAdapter:
    """Synchronous WebSocket JSON-RPC transport for remote MCP servers."""

    def __init__(
        self,
        name: str,
        url: str,
        *,
        timeout: int = 10,
        initialize: bool = False,
        protocol_version: str = DEFAULT_MCP_PROTOCOL_VERSION,
        headers: dict[str, str] | None = None,
        auth_token: str | None = None,
        connector: Callable[..., Any] | None = None,
    ) -> None:
        if not url:
            raise ValueError("MCP WebSocket URL must not be empty")
        self.name = name
        self.url = url
        self.timeout = timeout
        self.initialize_on_start = initialize
        self.protocol_version = protocol_version
        self.headers = {str(key): str(value) for key, value in (headers or {}).items()}
        if auth_token:
            self.headers.setdefault("Authorization", f"Bearer {auth_token}")
        self.capabilities: dict[str, Any] = {}
        self._next_id = 1
        self._connection: Any | None = None
        self._connector = connector
        self._tool_schemas: dict[str, dict[str, Any]] = {}
        if self.initialize_on_start:
            self.initialize()

    def list_tools(self) -> list[MCPTool]:
        response = self._request("tools/list", {})
        tools = response.get("tools", [])
        if not isinstance(tools, list):
            return []
        parsed: list[MCPTool] = []
        for item in tools:
            if not isinstance(item, dict):
                continue
            parsed.append(
                MCPTool(
                    name=str(item.get("name") or ""),
                    description=str(item.get("description") or ""),
                    input_schema=normalize_mcp_input_schema(item),
                )
            )
        tools = [tool for tool in parsed if tool.name]
        self._tool_schemas.update({tool.name: tool.input_schema for tool in tools})
        return tools

    def call_tool(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        validation = self._validate_tool_input(name, tool_input)
        if validation:
            return ToolResult(validation, is_error=True)
        try:
            response = self._request("tools/call", {"name": name, "arguments": tool_input})
        except Exception as exc:
            return ToolResult(str(exc), is_error=True)
        if response.get("isError") is True:
            return ToolResult(render_mcp_content(response), is_error=True)
        return ToolResult(render_mcp_content(response))

    def _validate_tool_input(self, name: str, tool_input: dict[str, Any]) -> str | None:
        schema = self._tool_schemas.get(name)
        if schema is None:
            try:
                self.list_tools()
            except Exception:
                return None
            schema = self._tool_schemas.get(name)
        if schema is None:
            return None
        errors = validate_mcp_arguments(schema, tool_input)
        if errors:
            return "MCP tool input failed schema validation: " + "; ".join(errors)
        return None

    def list_resources(self) -> list[MCPResource]:
        response = self._request("resources/list", {})
        resources = response.get("resources", [])
        if not isinstance(resources, list):
            return []
        parsed: list[MCPResource] = []
        for item in resources:
            if not isinstance(item, dict):
                continue
            uri = str(item.get("uri") or "")
            if not uri:
                continue
            parsed.append(
                MCPResource(
                    uri=uri,
                    name=str(item.get("name") or uri.rsplit("/", 1)[-1] or uri),
                    description=str(item.get("description") or ""),
                )
            )
        return parsed

    def read_resource(self, uri: str) -> ToolResult:
        try:
            response = self._request("resources/read", {"uri": uri})
        except Exception as exc:
            return ToolResult(str(exc), is_error=True)
        return ToolResult(render_mcp_content(response))

    def list_prompts(self) -> list[MCPPrompt]:
        response = self._request("prompts/list", {})
        prompts = response.get("prompts", [])
        if not isinstance(prompts, list):
            return []
        parsed: list[MCPPrompt] = []
        for item in prompts:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if not name:
                continue
            arguments = item.get("arguments")
            parsed.append(
                MCPPrompt(
                    name=name,
                    description=str(item.get("description") or ""),
                    arguments=arguments if isinstance(arguments, list) else None,
                )
            )
        return parsed

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        try:
            response = self._request("prompts/get", {"name": name, "arguments": arguments or {}})
        except Exception as exc:
            return ToolResult(str(exc), is_error=True)
        return ToolResult(render_mcp_content(response))

    def initialize(self) -> dict[str, Any]:
        response = self._request_once(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "mini-claude-code", "version": "1.04"},
            },
        )
        capabilities = response.get("capabilities", {})
        self.capabilities = capabilities if isinstance(capabilities, dict) else {}
        return response

    def close(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is None:
            return
        close = getattr(connection, "close", None)
        if callable(close):
            close()

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._request_once(method, params)
        except Exception:
            self.close()
            return self._request_once(method, params)

    def _request_once(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        connection = self._ensure_connection()
        request_id = self._next_id
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        connection.send(json.dumps(payload, ensure_ascii=False))
        while True:
            message = connection.recv()
            if isinstance(message, bytes):
                message = message.decode("utf-8")
            response = json.loads(str(message))
            if not isinstance(response, dict):
                continue
            if response.get("id") != request_id:
                continue
            if response.get("error"):
                error = response["error"]
                if isinstance(error, dict):
                    raise RuntimeError(str(error.get("message") or error))
                raise RuntimeError(str(error))
            result = response.get("result", {})
            if not isinstance(result, dict):
                raise RuntimeError(f"MCP WebSocket server {self.name} returned non-object result")
            return result

    def _ensure_connection(self) -> Any:
        if self._connection is not None:
            return self._connection
        connector = self._connector or self._default_connector
        self._connection = connector(
            self.url,
            timeout=self.timeout,
            header=self._header_lines(),
        )
        return self._connection

    def _default_connector(self, url: str, *, timeout: int, header: list[str]) -> Any:
        try:
            import websocket  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency for WebSocket MCP transport: "
                "run `python -m pip install websocket-client`."
            ) from exc
        return websocket.create_connection(url, timeout=timeout, header=header)

    def _header_lines(self) -> list[str]:
        headers = {"MCP-Protocol-Version": self.protocol_version, **self.headers}
        return [f"{key}: {value}" for key, value in headers.items()]

    def __del__(self) -> None:
        self.close()


class StreamableHTTPMCPAdapter:
    """Streamable HTTP JSON-RPC transport for remote MCP servers."""

    def __init__(
        self,
        name: str,
        endpoint: str,
        *,
        timeout: int = 10,
        initialize: bool = False,
        protocol_version: str = DEFAULT_MCP_PROTOCOL_VERSION,
        headers: dict[str, str] | None = None,
        auth_token: str | None = None,
        session_id: str | None = None,
        max_retries: int = 1,
        retry_backoff: float = 0.1,
        oauth_discovery: bool = False,
        oauth_metadata_url: str | None = None,
        token_store_path: Path | None = None,
        account_profile: dict[str, Any] | None = None,
    ) -> None:
        if not endpoint:
            raise ValueError("MCP HTTP endpoint must not be empty")
        self.name = name
        self.endpoint = endpoint
        self.timeout = timeout
        self.initialize_on_start = initialize
        self.protocol_version = protocol_version
        self.headers = {str(key): str(value) for key, value in (headers or {}).items()}
        if auth_token:
            self.headers.setdefault("Authorization", f"Bearer {auth_token}")
        self.session_id = session_id
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff = max(0.0, float(retry_backoff))
        self.capabilities: dict[str, Any] = {}
        self.oauth_discovery_enabled = oauth_discovery
        self.oauth_metadata_url = oauth_metadata_url
        self.protected_resource_metadata: dict[str, Any] = {}
        self.authorization_server_metadata: dict[str, Any] = {}
        self.oauth_discovery_errors: list[str] = []
        self.oauth_client_id: str | None = None
        self.oauth_refresh_token: str | None = None
        self.oauth_token_response: dict[str, Any] = {}
        self.oauth_refresh_count = 0
        self.account_profile = dict(account_profile or {})
        self.account_id = str(self.account_profile.get("account_id") or self.account_profile.get("id") or name)
        self.token_store = MCPTokenStore(token_store_path) if token_store_path is not None else None
        self.last_auth_failure: dict[str, Any] = {}
        self._next_id = 1
        self._tool_schemas: dict[str, dict[str, Any]] = {}
        self.load_persisted_oauth_token()
        if self.oauth_discovery_enabled:
            self.discover_oauth_metadata()
        if self.initialize_on_start:
            self.initialize()

    def list_tools(self) -> list[MCPTool]:
        response = self._request("tools/list", {})
        tools = response.get("tools", [])
        if not isinstance(tools, list):
            return []
        parsed: list[MCPTool] = []
        for item in tools:
            if not isinstance(item, dict):
                continue
            parsed.append(
                MCPTool(
                    name=str(item.get("name") or ""),
                    description=str(item.get("description") or ""),
                    input_schema=normalize_mcp_input_schema(item),
                )
            )
        tools = [tool for tool in parsed if tool.name]
        self._tool_schemas.update({tool.name: tool.input_schema for tool in tools})
        return tools

    def call_tool(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        validation = self._validate_tool_input(name, tool_input)
        if validation:
            return ToolResult(validation, is_error=True)
        try:
            response = self._request("tools/call", {"name": name, "arguments": tool_input})
        except Exception as exc:
            return ToolResult(str(exc), is_error=True)
        if response.get("isError") is True:
            return ToolResult(render_mcp_content(response), is_error=True)
        return ToolResult(render_mcp_content(response))

    def _validate_tool_input(self, name: str, tool_input: dict[str, Any]) -> str | None:
        schema = self._tool_schemas.get(name)
        if schema is None:
            try:
                self.list_tools()
            except Exception:
                return None
            schema = self._tool_schemas.get(name)
        if schema is None:
            return None
        errors = validate_mcp_arguments(schema, tool_input)
        if errors:
            return "MCP tool input failed schema validation: " + "; ".join(errors)
        return None

    def list_resources(self) -> list[MCPResource]:
        response = self._request("resources/list", {})
        resources = response.get("resources", [])
        if not isinstance(resources, list):
            return []
        parsed: list[MCPResource] = []
        for item in resources:
            if not isinstance(item, dict):
                continue
            uri = str(item.get("uri") or "")
            if not uri:
                continue
            parsed.append(
                MCPResource(
                    uri=uri,
                    name=str(item.get("name") or uri.rsplit("/", 1)[-1] or uri),
                    description=str(item.get("description") or ""),
                )
            )
        return parsed

    def read_resource(self, uri: str) -> ToolResult:
        try:
            response = self._request("resources/read", {"uri": uri})
        except Exception as exc:
            return ToolResult(str(exc), is_error=True)
        return ToolResult(render_mcp_content(response))

    def list_prompts(self) -> list[MCPPrompt]:
        response = self._request("prompts/list", {})
        prompts = response.get("prompts", [])
        if not isinstance(prompts, list):
            return []
        parsed: list[MCPPrompt] = []
        for item in prompts:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if not name:
                continue
            arguments = item.get("arguments")
            parsed.append(
                MCPPrompt(
                    name=name,
                    description=str(item.get("description") or ""),
                    arguments=arguments if isinstance(arguments, list) else None,
                )
            )
        return parsed

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        try:
            response = self._request("prompts/get", {"name": name, "arguments": arguments or {}})
        except Exception as exc:
            return ToolResult(str(exc), is_error=True)
        return ToolResult(render_mcp_content(response))

    def initialize(self) -> dict[str, Any]:
        response = self._request_once(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "mini-claude-code", "version": "0.83"},
            },
        )
        capabilities = response.get("capabilities", {})
        self.capabilities = capabilities if isinstance(capabilities, dict) else {}
        return response

    def discover_oauth_metadata(self) -> dict[str, Any]:
        self.oauth_discovery_errors = []
        protected = self._discover_protected_resource_metadata()
        self.protected_resource_metadata = protected
        auth_servers = protected.get("authorization_servers", [])
        if isinstance(protected.get("authorization_server"), str):
            auth_servers = [protected["authorization_server"]]
        if not isinstance(auth_servers, list):
            auth_servers = []
        for server in auth_servers:
            if not isinstance(server, str) or not server.strip():
                continue
            metadata = self._fetch_authorization_server_metadata(server.strip())
            if metadata:
                self.authorization_server_metadata = metadata
                break
        return {
            "protected_resource": self.protected_resource_metadata,
            "authorization_server": self.authorization_server_metadata,
            "errors": list(self.oauth_discovery_errors),
        }

    def _discover_protected_resource_metadata(self) -> dict[str, Any]:
        candidates = []
        if self.oauth_metadata_url:
            candidates.append(self.oauth_metadata_url)
        candidates.extend(self._protected_resource_metadata_candidates())
        seen: set[str] = set()
        for url in candidates:
            if not url or url in seen:
                continue
            seen.add(url)
            payload = self._get_json(url)
            if payload:
                return payload
        return {}

    def _protected_resource_metadata_candidates(self) -> list[str]:
        parsed = urllib.parse.urlparse(self.endpoint)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.strip("/")
        candidates = [origin + "/.well-known/oauth-protected-resource"]
        if path:
            candidates.append(origin + "/.well-known/oauth-protected-resource/" + path)
        return candidates

    def _fetch_authorization_server_metadata(self, issuer: str) -> dict[str, Any]:
        if "/.well-known/" in issuer:
            return self._get_json(issuer)
        return self._get_json(issuer.rstrip("/") + "/.well-known/oauth-authorization-server")

    def _get_json(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                text = response.read().decode("utf-8")
        except Exception as exc:
            self.oauth_discovery_errors.append(f"{url}: {exc}")
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            self.oauth_discovery_errors.append(f"{url}: invalid JSON: {exc}")
            return {}
        return payload if isinstance(payload, dict) else {}

    def start_device_authorization(self, *, client_id: str, scope: str = "") -> dict[str, Any]:
        if not self.authorization_server_metadata:
            self.discover_oauth_metadata()
        endpoint = str(self.authorization_server_metadata.get("device_authorization_endpoint") or "")
        if not endpoint:
            raise RuntimeError("OAuth device_authorization_endpoint was not discovered")
        payload = {"client_id": client_id}
        if scope:
            payload["scope"] = scope
        device = self._post_form(endpoint, payload)
        if self.token_store is not None:
            self.token_store.save_pending_device_flow(
                self.account_id,
                {
                    "client_id": client_id,
                    "scope": scope,
                    "device_code": device.get("device_code"),
                    "user_code": device.get("user_code"),
                    "verification_uri": device.get("verification_uri"),
                    "verification_uri_complete": device.get("verification_uri_complete"),
                    "interval": device.get("interval"),
                    "message": device.get("message"),
                    "expires_at": time.time() + float(device.get("expires_in") or 600),
                },
            )
        return device

    def poll_device_token(
        self,
        *,
        client_id: str,
        device_code: str,
        interval: float = 5.0,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        if not self.authorization_server_metadata:
            self.discover_oauth_metadata()
        token_endpoint = str(self.authorization_server_metadata.get("token_endpoint") or "")
        if not token_endpoint:
            raise RuntimeError("OAuth token_endpoint was not discovered")
        self.oauth_client_id = client_id
        deadline = time.monotonic() + timeout
        current_interval = max(0.1, float(interval))
        while True:
            response = self._post_form(
                token_endpoint,
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": client_id,
                },
                tolerate_oauth_error=True,
            )
            error = response.get("error")
            if not error:
                self._apply_oauth_token_response(response)
                if self.token_store is not None:
                    self.token_store.clear_pending_device_flow(self.account_id)
                return response
            if error == "authorization_pending":
                pass
            elif error == "slow_down":
                current_interval += 5
            else:
                raise RuntimeError(f"OAuth device token request failed: {error}")
            if time.monotonic() + current_interval > deadline:
                raise TimeoutError("OAuth device authorization timed out")
            time.sleep(current_interval)

    def login_with_device_code(
        self,
        *,
        client_id: str,
        scope: str = "",
        timeout: float = 600.0,
        output: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        device = self.start_device_authorization(client_id=client_id, scope=scope)
        message = device.get("message")
        if output is not None:
            if message:
                output(str(message))
            else:
                output(
                    "Open "
                    + str(device.get("verification_uri_complete") or device.get("verification_uri") or "[unknown verification URL]")
                    + " and enter code "
                    + str(device.get("user_code") or "[unknown code]")
                )
        return self.poll_device_token(
            client_id=client_id,
            device_code=str(device.get("device_code") or ""),
            interval=float(device.get("interval") or 5),
            timeout=timeout,
        )

    def resume_device_authorization(self, *, timeout: float = 600.0) -> dict[str, Any]:
        if self.token_store is None:
            raise RuntimeError("MCP token store is not configured; cannot resume device flow")
        pending = self.token_store.load_pending_device_flow(self.account_id)
        if not pending:
            raise RuntimeError("No pending MCP OAuth device flow to resume")
        expires_at = float(pending.get("expires_at") or 0)
        if expires_at and time.time() > expires_at:
            self.token_store.clear_pending_device_flow(self.account_id)
            raise RuntimeError("Pending MCP OAuth device flow expired; start login again")
        return self.poll_device_token(
            client_id=str(pending.get("client_id") or self.oauth_client_id or ""),
            device_code=str(pending.get("device_code") or ""),
            interval=float(pending.get("interval") or 5),
            timeout=timeout,
        )

    def reauth_prompt(self) -> str:
        pending = self.token_store.load_pending_device_flow(self.account_id) if self.token_store is not None else {}
        if pending:
            uri = pending.get("verification_uri_complete") or pending.get("verification_uri") or "[unknown verification URL]"
            code = pending.get("user_code") or "[unknown code]"
            return f"Resume MCP OAuth login for {self.name}: open {uri} and enter code {code}."
        if self.authorization_server_metadata.get("device_authorization_endpoint"):
            return f"MCP OAuth login is required for {self.name}. Start a device-code login and then resume it if interrupted."
        return f"MCP authentication failed for {self.name}. Check credentials, token store, or environment variables."

    def build_authorization_url(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        scope: str = "",
        state: str | None = None,
    ) -> dict[str, str]:
        if not self.authorization_server_metadata:
            self.discover_oauth_metadata()
        authorization_endpoint = str(self.authorization_server_metadata.get("authorization_endpoint") or "")
        if not authorization_endpoint:
            raise RuntimeError("OAuth authorization_endpoint was not discovered")
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii").rstrip("=")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        state = state or secrets.token_urlsafe(18)
        query = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if scope:
            query["scope"] = scope
        return {
            "url": authorization_endpoint + "?" + urllib.parse.urlencode(query),
            "state": state,
            "code_verifier": code_verifier,
        }

    def login_with_authorization_code(
        self,
        *,
        client_id: str,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
    ) -> dict[str, Any]:
        if not self.authorization_server_metadata:
            self.discover_oauth_metadata()
        token_endpoint = str(self.authorization_server_metadata.get("token_endpoint") or "")
        if not token_endpoint:
            raise RuntimeError("OAuth token_endpoint was not discovered")
        self.oauth_client_id = client_id
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
        }
        if code_verifier:
            payload["code_verifier"] = code_verifier
        response = self._post_form(token_endpoint, payload)
        self._apply_oauth_token_response(response)
        return response

    def login_with_browser(
        self,
        *,
        client_id: str,
        scope: str = "",
        host: str = "127.0.0.1",
        port: int = 0,
        timeout: float = 300.0,
        open_browser: bool = True,
        output: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        callback: dict[str, str] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                params = urllib.parse.parse_qs(parsed.query)
                if "code" in params:
                    callback["code"] = params["code"][0]
                if "state" in params:
                    callback["state"] = params["state"][0]
                body = b"OAuth login complete. You can close this window."
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        server = ThreadingHTTPServer((host, port), Handler)
        actual_host, actual_port = server.server_address
        redirect_uri = f"http://{actual_host}:{actual_port}/callback"
        auth = self.build_authorization_url(client_id=client_id, redirect_uri=redirect_uri, scope=scope)
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()
        if output is not None:
            output("Open OAuth authorization URL: " + auth["url"])
        if open_browser:
            webbrowser.open(auth["url"])
        thread.join(timeout=timeout)
        server.server_close()
        if "code" not in callback:
            raise TimeoutError("OAuth browser login timed out waiting for redirect")
        if callback.get("state") != auth["state"]:
            raise RuntimeError("OAuth browser login returned mismatched state")
        return self.login_with_authorization_code(
            client_id=client_id,
            code=callback["code"],
            redirect_uri=redirect_uri,
            code_verifier=auth["code_verifier"],
        )

    def _post_form(self, url: str, payload: dict[str, str], *, tolerate_oauth_error: bool = False) -> dict[str, Any]:
        body = urllib.parse.urlencode(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            exc.close()
            if not tolerate_oauth_error:
                raise RuntimeError(f"OAuth endpoint {url} returned HTTP {exc.code}: {text}") from exc
        payload_json = json.loads(text)
        if not isinstance(payload_json, dict):
            raise RuntimeError(f"OAuth endpoint {url} returned non-object JSON")
        return payload_json

    def _apply_oauth_token_response(self, response: dict[str, Any]) -> None:
        access_token = response.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise RuntimeError("OAuth token response did not include access_token")
        token_type = str(response.get("token_type") or "Bearer")
        self.headers["Authorization"] = f"{token_type} {access_token}"
        self.oauth_token_response = dict(response)
        refresh_token = response.get("refresh_token")
        if isinstance(refresh_token, str) and refresh_token:
            self.oauth_refresh_token = refresh_token
        if self.token_store is not None:
            self.token_store.save_token_response(self.account_id, response, profile=self.account_profile_for_store())

    def account_profile_for_store(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "server": self.name,
            "endpoint": self.endpoint,
            "label": self.account_profile.get("label"),
            "subject": self.account_profile.get("subject"),
            "issuer": self.authorization_server_metadata.get("issuer"),
            "oauth_client_id": self.oauth_client_id,
        }

    def load_persisted_oauth_token(self) -> None:
        if self.token_store is None:
            return
        token_response = self.token_store.load_token_response(self.account_id)
        if token_response:
            self._apply_oauth_token_response_without_persist(token_response)

    def _apply_oauth_token_response_without_persist(self, response: dict[str, Any]) -> None:
        access_token = response.get("access_token")
        if isinstance(access_token, str) and access_token:
            token_type = str(response.get("token_type") or "Bearer")
            self.headers["Authorization"] = f"{token_type} {access_token}"
            self.oauth_token_response = dict(response)
        refresh_token = response.get("refresh_token")
        if isinstance(refresh_token, str) and refresh_token:
            self.oauth_refresh_token = refresh_token

    def refresh_oauth_token(
        self,
        *,
        client_id: str | None = None,
        refresh_token: str | None = None,
    ) -> dict[str, Any]:
        if not self.authorization_server_metadata:
            self.discover_oauth_metadata()
        token_endpoint = str(self.authorization_server_metadata.get("token_endpoint") or "")
        if not token_endpoint:
            raise RuntimeError("OAuth token_endpoint was not discovered")
        selected_refresh_token = refresh_token or self.oauth_refresh_token
        if not selected_refresh_token:
            raise RuntimeError("OAuth refresh_token is not available")
        selected_client_id = client_id or self.oauth_client_id
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": selected_refresh_token,
        }
        if selected_client_id:
            payload["client_id"] = selected_client_id
        response = self._post_form(token_endpoint, payload)
        self._apply_oauth_token_response(response)
        self.oauth_refresh_count += 1
        return response

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        refreshed = False
        reinitialized = False
        attempt = 0
        while attempt <= self.max_retries:
            try:
                return self._request_once(method, params)
            except MCPHTTPStatusError as exc:
                last_error = exc
                self.last_auth_failure = exc.auth_failure
                if exc.status_code in {401, 403} and not refreshed and self.oauth_refresh_token:
                    try:
                        self.refresh_oauth_token()
                    except Exception as refresh_exc:
                        self.last_auth_failure = {
                            **self.last_auth_failure,
                            "class": "refresh_failed",
                            "reauth_required": True,
                            "refresh_error": redact_secret_text(str(refresh_exc)),
                            "reauth_prompt": self.reauth_prompt(),
                        }
                    else:
                        refreshed = True
                        continue
                if exc.status_code in {401, 403, 404} and not reinitialized and self.initialize_on_start and self.session_id:
                    self.session_id = None
                    self.initialize()
                    reinitialized = True
                    continue
                if exc.status_code not in {408, 409, 425, 429, 500, 502, 503, 504}:
                    raise
            except MCPTransientHTTPError as exc:
                last_error = exc
            if attempt < self.max_retries and self.retry_backoff:
                time.sleep(self.retry_backoff * (2**attempt))
            attempt += 1
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"MCP HTTP server {self.name} request failed")

    def _request_once(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        response = self._post_json(payload, method=method, name=str(params.get("name") or ""))
        if response.get("id") != request_id:
            raise RuntimeError(f"MCP HTTP server {self.name} returned mismatched id")
        if response.get("error"):
            error = response["error"]
            if isinstance(error, dict):
                raise RuntimeError(str(error.get("message") or error))
            raise RuntimeError(str(error))
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise RuntimeError(f"MCP HTTP server {self.name} returned non-object result")
        return result

    def _post_json(self, payload: dict[str, Any], *, method: str, name: str = "") -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.protocol_version,
            "Mcp-Method": method,
            **self.headers,
        }
        if name:
            headers["Mcp-Name"] = name
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self.session_id = session_id
                content_type = response.headers.get("Content-Type", "")
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            finally:
                exc.close()
            metadata_url = extract_resource_metadata_url(exc.headers.get("WWW-Authenticate", ""))
            auth_failure = classify_mcp_auth_failure(
                status_code=exc.code,
                detail=detail,
                www_authenticate=exc.headers.get("WWW-Authenticate", ""),
                has_refresh_token=bool(self.oauth_refresh_token),
            )
            if metadata_url:
                self.oauth_metadata_url = metadata_url
                discovered = self.discover_oauth_metadata()
                detail = detail + "\nOAuth discovery: " + json.dumps(discovered, ensure_ascii=False)
                auth_failure["class"] = "oauth_metadata_required"
                auth_failure["metadata_url"] = metadata_url
                auth_failure["oauth_discovery"] = {
                    "protected_resource": bool(discovered.get("protected_resource")),
                    "authorization_server": bool(discovered.get("authorization_server")),
                    "errors": discovered.get("errors", []),
                }
            if auth_failure.get("reauth_required"):
                auth_failure["reauth_prompt"] = self.reauth_prompt()
            self.last_auth_failure = auth_failure
            raise MCPHTTPStatusError(
                f"MCP HTTP server {self.name} returned HTTP {exc.code}: {detail}",
                exc.code,
                auth_failure=auth_failure,
            ) from exc
        except urllib.error.URLError as exc:
            raise MCPTransientHTTPError(f"MCP HTTP server {self.name} request failed: {exc.reason}") from exc
        if "text/event-stream" in content_type.lower():
            return parse_sse_json_rpc_response(text)
        return parse_json_rpc_response_text(text)


class MCPHTTPStatusError(RuntimeError):
    def __init__(self, message: str, status_code: int, *, auth_failure: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.auth_failure = auth_failure or {}


class MCPTransientHTTPError(RuntimeError):
    pass


def first_json_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            return stripped
    return None


def parse_json_rpc_response_text(text: str) -> dict[str, Any]:
    line = first_json_line(text)
    if line is None:
        raise RuntimeError("MCP HTTP server returned no JSON response")
    payload = json.loads(line)
    if not isinstance(payload, dict):
        raise RuntimeError("MCP HTTP server returned non-object JSON response")
    return payload


def parse_sse_json_rpc_response(text: str) -> dict[str, Any]:
    data_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())
        elif not line.strip() and data_lines:
            return parse_json_rpc_response_text("\n".join(data_lines))
    if data_lines:
        return parse_json_rpc_response_text("\n".join(data_lines))
    raise RuntimeError("MCP HTTP server returned no SSE data response")


def normalize_mcp_input_schema(item: dict[str, Any]) -> dict[str, Any]:
    schema = item.get("inputSchema")
    if not isinstance(schema, dict):
        schema = item.get("input_schema")
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    normalized = dict(schema)
    if normalized.get("type") != "object":
        normalized["type"] = "object"
    if not isinstance(normalized.get("properties"), dict):
        normalized["properties"] = {}
    if "required" in normalized and not isinstance(normalized["required"], list):
        normalized.pop("required")
    return normalized


def pattern_set_matches(patterns: set[str], value: str) -> bool:
    return any(pattern_matches(pattern, value) for pattern in patterns)


def pattern_matches(pattern: str, value: str) -> bool:
    if pattern.startswith("prefix:"):
        return value.startswith(pattern.removeprefix("prefix:"))
    if pattern.endswith("/*"):
        return value.startswith(pattern[:-1])
    if any(token in pattern for token in "*?[]"):
        return fnmatchcase(value, pattern)
    return value == pattern


def is_high_risk_mcp_tool_name(name: str) -> bool:
    normalized = name.lower().replace("-", "_")
    tokens = {token for token in normalized.replace("/", "_").split("_") if token}
    if tokens & HIGH_RISK_MCP_TOOL_TOKENS:
        return True
    return any(normalized.startswith(token + "_") or normalized.endswith("_" + token) for token in HIGH_RISK_MCP_TOOL_TOKENS)


def classify_mcp_auth_failure(
    *,
    status_code: int,
    detail: str = "",
    www_authenticate: str = "",
    has_refresh_token: bool = False,
) -> dict[str, Any]:
    lowered = f"{detail}\n{www_authenticate}".lower()
    failure_class = "auth_failed" if status_code in {401, 403} else "http_error"
    reauth_required = status_code in {401, 403}
    if "expired" in lowered or "invalid_token" in lowered:
        failure_class = "expired_token"
        reauth_required = not has_refresh_token
    elif "insufficient_scope" in lowered or status_code == 403:
        failure_class = "insufficient_scope"
    elif "resource_metadata" in lowered or "oauth-protected-resource" in lowered:
        failure_class = "oauth_metadata_required"
    elif "missing" in lowered and "token" in lowered:
        failure_class = "missing_token"
    return {
        "class": failure_class,
        "status_code": status_code,
        "reauth_required": reauth_required,
        "refresh_possible": has_refresh_token,
        "www_authenticate_present": bool(www_authenticate),
        "detail_preview": redact_secret_text(detail[:300]),
    }


def env_name_allowed(env_name: str, allowlist: set[str] | None) -> bool:
    if allowlist is None:
        return True
    return pattern_set_matches(allowlist, env_name)


def mcp_capability_summary(adapter: MCPAdapter) -> str:
    parts: list[str] = [f"{adapter.name}:"]
    try:
        tools = [tool.name for tool in adapter.list_tools()]
    except Exception as exc:
        tools = [f"[tools unavailable: {exc}]"]
    try:
        resources = [resource.uri for resource in adapter.list_resources()]
    except Exception as exc:
        resources = [f"[resources unavailable: {exc}]"]
    try:
        prompts = [prompt.name for prompt in adapter.list_prompts()]
    except Exception as exc:
        prompts = [f"[prompts unavailable: {exc}]"]
    parts.append("  tools: " + (", ".join(tools[:20]) if tools else "[none]"))
    parts.append("  resources: " + (", ".join(resources[:20]) if resources else "[none]"))
    parts.append("  prompts: " + (", ".join(prompts[:20]) if prompts else "[none]"))
    protected = getattr(adapter, "protected_resource_metadata", None)
    auth = getattr(adapter, "authorization_server_metadata", None)
    oauth_errors = getattr(adapter, "oauth_discovery_errors", None)
    if isinstance(protected, dict) and protected:
        parts.append("  oauth_resource: " + str(protected.get("resource") or protected.get("resource_name") or "[discovered]"))
        auth_servers = protected.get("authorization_servers", protected.get("authorization_server", []))
        if isinstance(auth_servers, str):
            auth_servers = [auth_servers]
        if isinstance(auth_servers, list):
            parts.append("  oauth_authorization_servers: " + ", ".join(str(item) for item in auth_servers[:5]))
    if isinstance(auth, dict) and auth:
        endpoints = [
            f"issuer={auth.get('issuer')}",
            f"authorization_endpoint={auth.get('authorization_endpoint')}",
            f"token_endpoint={auth.get('token_endpoint')}",
        ]
        parts.append("  oauth_authorization_metadata: " + "; ".join(item for item in endpoints if not item.endswith("=None")))
    if isinstance(oauth_errors, list) and oauth_errors:
        parts.append("  oauth_discovery_errors: " + " | ".join(str(item) for item in oauth_errors[:3]))
    return "\n".join(parts)


def extract_resource_metadata_url(header: str) -> str | None:
    if not header:
        return None
    for item in header.split(","):
        name, sep, value = item.partition("=")
        if sep and name.strip().lower().endswith("resource_metadata"):
            return value.strip().strip('"')
    return None


def validate_mcp_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    return validate_json_schema_value(schema, arguments, "$")


def validate_json_schema_value(schema: dict[str, Any], value: Any, path: str) -> list[str]:
    errors: list[str] = []
    for keyword in ("anyOf", "oneOf", "allOf"):
        branch_errors = validate_json_schema_composition(schema, value, path, keyword)
        if branch_errors is not None:
            errors.extend(branch_errors)
            if keyword in {"anyOf", "oneOf"} and branch_errors:
                return errors
    expected_types = json_schema_types(schema)
    if expected_types:
        if "null" in expected_types and value is None:
            return errors
        non_null_types = [item for item in expected_types if item != "null"]
        if non_null_types and not any(_matches_json_schema_type(value, item) for item in non_null_types):
            errors.append(f"{path} expected {','.join(expected_types)}")
            return errors
    if schema.get("enum") and isinstance(schema["enum"], list) and value not in schema["enum"]:
        errors.append(f"{path} expected one of {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path} expected const {schema['const']!r}")
    if schema.get("type") == "object" and isinstance(value, dict):
        return errors + validate_json_schema_object(schema, value, path)
    if schema.get("type") == "array" and isinstance(value, list):
        errors.extend(validate_json_schema_array(schema, value, path))
    if schema.get("type") == "string" and isinstance(value, str):
        errors.extend(validate_json_schema_string(schema, value, path))
    if schema.get("type") in {"number", "integer"} and isinstance(value, (int, float)) and not isinstance(value, bool):
        errors.extend(validate_json_schema_number(schema, value, path))
    return errors


def validate_json_schema_object(schema: dict[str, Any], arguments: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    if schema.get("type") != "object":
        return errors
    required = schema.get("required", [])
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in arguments:
                errors.append(f"missing required field '{path}.{key}'")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    additional = schema.get("additionalProperties", True)
    for key, value in arguments.items():
        spec = properties.get(key)
        if not isinstance(spec, dict):
            if additional is False:
                errors.append(f"{path}.{key} is not allowed by additionalProperties=false")
            elif isinstance(additional, dict):
                errors.extend(validate_json_schema_value(additional, value, f"{path}.{key}"))
            continue
        errors.extend(validate_json_schema_value(spec, value, f"{path}.{key}"))
    return errors


def validate_json_schema_array(schema: dict[str, Any], value: list[Any], path: str) -> list[str]:
    errors: list[str] = []
    min_items = schema.get("minItems")
    if isinstance(min_items, int) and len(value) < min_items:
        errors.append(f"{path} expected at least {min_items} item(s)")
    max_items = schema.get("maxItems")
    if isinstance(max_items, int) and len(value) > max_items:
        errors.append(f"{path} expected at most {max_items} item(s)")
    if schema.get("uniqueItems") is True:
        seen: set[str] = set()
        for index, item in enumerate(value):
            marker = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if marker in seen:
                errors.append(f"{path}[{index}] duplicates an earlier item")
                break
            seen.add(marker)
    items = schema.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(value):
            errors.extend(validate_json_schema_value(items, item, f"{path}[{index}]"))
    prefix_items = schema.get("prefixItems")
    if isinstance(prefix_items, list):
        for index, item_schema in enumerate(prefix_items):
            if index >= len(value):
                break
            if isinstance(item_schema, dict):
                errors.extend(validate_json_schema_value(item_schema, value[index], f"{path}[{index}]"))
    return errors


def validate_json_schema_string(schema: dict[str, Any], value: str, path: str) -> list[str]:
    errors: list[str] = []
    min_length = schema.get("minLength")
    if isinstance(min_length, int) and len(value) < min_length:
        errors.append(f"{path} expected string length >= {min_length}")
    max_length = schema.get("maxLength")
    if isinstance(max_length, int) and len(value) > max_length:
        errors.append(f"{path} expected string length <= {max_length}")
    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        import re

        try:
            if re.search(pattern, value) is None:
                errors.append(f"{path} expected string matching pattern {pattern!r}")
        except re.error as exc:
            errors.append(f"{path} has invalid schema pattern {pattern!r}: {exc}")
    return errors


def validate_json_schema_number(schema: dict[str, Any], value: int | float, path: str) -> list[str]:
    errors: list[str] = []
    minimum = schema.get("minimum")
    if isinstance(minimum, (int, float)) and value < minimum:
        errors.append(f"{path} expected >= {minimum}")
    maximum = schema.get("maximum")
    if isinstance(maximum, (int, float)) and value > maximum:
        errors.append(f"{path} expected <= {maximum}")
    exclusive_minimum = schema.get("exclusiveMinimum")
    if isinstance(exclusive_minimum, (int, float)) and value <= exclusive_minimum:
        errors.append(f"{path} expected > {exclusive_minimum}")
    exclusive_maximum = schema.get("exclusiveMaximum")
    if isinstance(exclusive_maximum, (int, float)) and value >= exclusive_maximum:
        errors.append(f"{path} expected < {exclusive_maximum}")
    multiple_of = schema.get("multipleOf")
    if isinstance(multiple_of, (int, float)) and multiple_of:
        quotient = value / multiple_of
        if abs(quotient - round(quotient)) > 1e-9:
            errors.append(f"{path} expected multipleOf {multiple_of}")
    return errors


def validate_json_schema_composition(schema: dict[str, Any], value: Any, path: str, keyword: str) -> list[str] | None:
    branches = schema.get(keyword)
    if not isinstance(branches, list):
        return None
    branch_errors = [
        validate_json_schema_value(branch, value, path)
        for branch in branches
        if isinstance(branch, dict)
    ]
    if keyword == "allOf":
        errors: list[str] = []
        for errors_for_branch in branch_errors:
            errors.extend(errors_for_branch)
        return errors
    matches = sum(1 for errors_for_branch in branch_errors if not errors_for_branch)
    if keyword == "anyOf":
        if matches >= 1:
            return []
        return [f"{path} did not match anyOf schemas"]
    if keyword == "oneOf":
        if matches == 1:
            return []
        return [f"{path} expected exactly one matching oneOf schema, got {matches}"]
    return None


def json_schema_types(schema: dict[str, Any]) -> list[str]:
    expected = schema.get("type")
    if isinstance(expected, list):
        return [str(item) for item in expected]
    if isinstance(expected, str):
        return [expected]
    return []


def _matches_json_schema_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (isinstance(value, int) or isinstance(value, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def redact_secret_text(text: str) -> str:
    if not text:
        return text
    redacted = text
    for marker in ("Bearer ", "authorization=", "Authorization:", "auth_token", "bearer_token", "api_key"):
        if marker.lower() in redacted.lower():
            return "[redacted sensitive content]"
    return redacted


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def is_sensitive_mcp_resource(uri: str, content: str = "") -> bool:
    text = f"{uri}\n{content}".lower()
    markers = [
        "secret",
        "token",
        "credential",
        "password",
        "private",
        "authorization",
        "api_key",
        "apikey",
        ".env",
    ]
    return any(marker in text for marker in markers)


def redact_secret_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ["token", "authorization", "api_key", "apikey", "secret"]):
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = redact_secret_value(item)
        return redacted
    if isinstance(value, list):
        return [redact_secret_value(item) for item in value]
    if isinstance(value, str):
        return redact_secret_text(value)
    return value


def render_mcp_content(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("content"), list):
        parts: list[str] = []
        for item in payload["content"]:
            if not isinstance(item, dict):
                continue
            if "text" in item:
                parts.append(str(item["text"]))
            elif "uri" in item and "text" in item:
                parts.append(str(item["text"]))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts) if parts else json.dumps(payload, ensure_ascii=False)
    if isinstance(payload.get("contents"), list):
        parts = []
        for item in payload["contents"]:
            if isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
            elif isinstance(item, dict):
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts) if parts else json.dumps(payload, ensure_ascii=False)
    if isinstance(payload.get("messages"), list):
        parts = []
        for item in payload["messages"]:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, dict) and "text" in content:
                role = str(item.get("role") or "message")
                parts.append(f"{role}: {content['text']}")
            elif isinstance(content, str):
                role = str(item.get("role") or "message")
                parts.append(f"{role}: {content}")
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts) if parts else json.dumps(payload, ensure_ascii=False)
    return json.dumps(payload, ensure_ascii=False)
