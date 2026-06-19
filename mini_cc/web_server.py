from __future__ import annotations

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from . import __version__


REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPO_ROOT / "frontend"


def redact_secret(text: str, secret: str | None) -> str:
    if not secret:
        return text
    return text.replace(secret, "[redacted-api-key]")


def clean_provider(value: Any) -> str:
    provider = str(value or "mock").strip().lower()
    if provider not in {"mock", "openai", "anthropic"}:
        return "mock"
    return provider


def clean_permission(value: Any) -> str:
    permission = str(value or "auto").strip().lower()
    if permission not in {"ask", "auto", "bypass", "read-only"}:
        return "auto"
    return permission


def clean_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def resolve_workspace(value: Any) -> Path:
    raw = str(value or REPO_ROOT).strip()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    return path.resolve()


def build_cli_command(payload: dict[str, Any]) -> tuple[list[str], dict[str, str], Path, int]:
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("Prompt is required.")

    provider = clean_provider(payload.get("provider"))
    workspace = resolve_workspace(payload.get("workspace"))
    permission = clean_permission(payload.get("permissionMode"))
    max_turns = clean_int(payload.get("maxTurns"), 8, minimum=1, maximum=30)
    timeout = clean_int(payload.get("timeout"), 120, minimum=5, maximum=600)

    command = [
        sys.executable,
        "-m",
        "mini_cc",
        "run",
        "--workspace",
        str(workspace),
        "--permission-mode",
        permission,
        "--output-format",
        "json",
        "--max-turns",
        str(max_turns),
        "--prompt",
        prompt,
    ]
    if payload.get("s20", True):
        command.append("--s20")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    if provider == "mock":
        command.append("--mock")
    else:
        api_key = str(payload.get("apiKey") or "").strip()
        if not api_key:
            raise ValueError("API key is required unless provider is Mock.")
        model = str(payload.get("model") or "").strip()
        base_url = str(payload.get("baseUrl") or "").strip()
        reasoning_effort = str(payload.get("reasoningEffort") or "").strip()
        command.extend(["--provider", provider])
        if model:
            command.extend(["--model", model])
        if base_url:
            command.extend(["--base-url", base_url])
        if provider == "openai":
            env["OPENAI_API_KEY"] = api_key
            if reasoning_effort:
                command.extend(["--reasoning-effort", reasoning_effort])
        else:
            env["ANTHROPIC_API_KEY"] = api_key

    return command, env, workspace, timeout


def run_agent(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = str(payload.get("apiKey") or "")
    command, env, workspace, timeout = build_cli_command(payload)
    started_command = " ".join(command)
    try:
        completed = subprocess.run(
            command,
            cwd=str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": f"Agent run timed out after {timeout}s.",
            "command": redact_secret(started_command, api_key),
            "stdout": redact_secret(exc.stdout or "", api_key),
            "stderr": redact_secret(exc.stderr or "", api_key),
        }

    stdout = redact_secret(completed.stdout, api_key)
    stderr = redact_secret(completed.stderr, api_key)
    parsed: dict[str, Any] | None = None
    try:
        parsed_raw = json.loads(stdout) if stdout.strip() else {}
        if isinstance(parsed_raw, dict):
            parsed = parsed_raw
    except json.JSONDecodeError:
        parsed = None
    return {
        "ok": completed.returncode == 0 and (parsed.get("ok", True) if parsed else True),
        "returncode": completed.returncode,
        "workspace": str(workspace),
        "command": redact_secret(started_command, api_key),
        "stdout": stdout,
        "stderr": stderr,
        "result": parsed,
    }


class MiniCCWebHandler(BaseHTTPRequestHandler):
    server_version = "MiniCCWeb/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("[mini-cc-web] " + format % args + "\n")

    def do_GET(self) -> None:
        if self.path == "/api/status":
            self.write_json(
                {
                    "ok": True,
                    "version": __version__,
                    "repoRoot": str(REPO_ROOT),
                    "frontendRoot": str(FRONTEND_ROOT),
                    "defaultWorkspace": str(REPO_ROOT),
                }
            )
            return
        if self.path == "/":
            self.serve_file(FRONTEND_ROOT / "index.html")
            return
        rel = unquote(self.path.lstrip("/"))
        target = (FRONTEND_ROOT / rel).resolve()
        try:
            target.relative_to(FRONTEND_ROOT.resolve())
        except ValueError:
            self.send_error(403)
            return
        self.serve_file(target)

    def do_POST(self) -> None:
        if self.path != "/api/run":
            self.send_error(404)
            return
        try:
            payload = self.read_json()
            result = run_agent(payload)
            self.write_json(result, status=200 if result.get("ok") else 400)
        except ValueError as exc:
            self.write_json({"ok": False, "error": str(exc)}, status=400)
        except Exception as exc:
            self.write_json({"ok": False, "error": str(exc)}, status=500)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("Request body is too large.")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(path.suffix.lower(), "application/octet-stream")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def write_json(self, payload: dict[str, Any], *, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    host = "127.0.0.1"
    port = 8765
    if argv:
        port = clean_int(argv[0], port, minimum=1024, maximum=65535)
    server = ThreadingHTTPServer((host, port), MiniCCWebHandler)
    print(f"Mini Claude Code frontend: http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
