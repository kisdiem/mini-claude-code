from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from pathlib import Path
from typing import Callable

from .agent import Agent
from .bench import (
    TerminalBenchShardRunner,
    benchmark_automation_to_json,
    classify_terminal_bench_result,
    load_task_ids,
    run_benchmark_automation,
    run_terminal_bench_real_pipeline,
    terminal_bench_real_run_to_json,
    write_benchmark_report,
)
from .config import DEFAULT_PERMISSION, build_config, load_env_file
from .coding_loop import CodingLoopPolicy
from .governance import load_governance_config
from .hooks import HookRuntime, load_configured_hooks
from .llm import AnthropicProvider, MockProvider, OpenAIProvider
from .mcp_live import write_live_validation_report
from .permission import PermissionPolicy
from .session import SessionStore
from .s20 import S20ToolRunner
from .subagents import SubagentRuntime
from .task_state import TaskStateMachine
from .task_runtime import TaskRuntime
from .tool_eval import run_real_tool_use_eval
from .tool_runtime import write_tool_runtime_evidence_smoke, write_tool_runtime_report
from .tools import ToolRunner
from .workflow import ModelAuthoredPlanner, StructuredWorkflow


def system_prompt_for_workspace(base_prompt: str, workspace: Path, hooks: HookRuntime | None = None) -> str:
    agents_md = workspace / "AGENTS.md"
    if not agents_md.exists() or not agents_md.is_file():
        return base_prompt
    try:
        content = agents_md.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return base_prompt
    if not content:
        return base_prompt
    if hooks is not None:
        hooks.instructions_loaded(
            reason="workspace",
            source="AGENTS.md",
            chars=len(content),
            path=str(agents_md),
        )
    return base_prompt + "\n\nWorkspace AGENTS.md instructions:\n" + content


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    if argv is None:
        argv = sys.argv[1:]
    evidence_mode = bool(argv and argv[0] == "evidence")
    if evidence_mode:
        argv = [
            "--s20",
            "--coding-loop",
            "--permission-mode",
            "bypass",
            "--output-format",
            "json",
            *argv[1:],
        ]
    if argv and argv[0] == "run":
        argv = argv[1:]

    parser = argparse.ArgumentParser(
        description=(
            "Mini Claude Code is an evidence-first local coding-agent runtime. "
            "Recommended reviewer path: `mini_cc evidence --workspace . --prompt \"fix the failing test\"`."
        ),
        epilog=(
            "`evidence` is a golden-path alias for run mode with S20, coding-loop, "
            "permission bypass, JSON output, and Evidence Report generation. "
            "S20/MCP/subagents/benchmark tooling are optional or experimental extensions."
        ),
    )
    parser.add_argument(
        "--classify-terminal-bench",
        metavar="RESULTS_JSON",
        help="Classify a Terminal-Bench results.json file into failure buckets.",
    )
    parser.add_argument(
        "--diagnose-config",
        action="store_true",
        help="Print merged governance config, loaded settings files, and validation issues.",
    )
    parser.add_argument(
        "--terminal-bench-shards",
        metavar="TASKS_FILE",
        help="Run Terminal-Bench task ids in Docker-gated shards. The file may be JSON or newline-delimited text.",
    )
    parser.add_argument(
        "--benchmark-automation",
        metavar="TASKS_FILE",
        help="Run Terminal-Bench shards, write reports, and evaluate automation gates in one command.",
    )
    parser.add_argument(
        "--terminal-bench-real-run",
        metavar="TASKS_FILE",
        help="Preflight a real Terminal-Bench run, then execute automation if checks pass.",
    )
    parser.add_argument(
        "--benchmark-report",
        metavar="OUTPUT_DIR",
        help="Build benchmark-report.json and benchmark-report.md from a Terminal-Bench shard output directory.",
    )
    parser.add_argument(
        "--benchmark-report-output",
        metavar="REPORT_DIR",
        help="Optional directory for benchmark report files. Defaults to --benchmark-report.",
    )
    parser.add_argument(
        "--tool-use-eval",
        metavar="OUTPUT_DIR",
        help="Run the real tool-use trace evaluation harness and write JSON/Markdown reports.",
    )
    parser.add_argument(
        "--tool-use-eval-input",
        metavar="OBSERVATIONS_JSON",
        help="Optional observations JSON for --tool-use-eval. Omit this to run real local traces.",
    )
    parser.add_argument(
        "--tool-runtime-report",
        metavar="OUTPUT_DIR",
        help="Write the Tool-Use Runtime v3.15 JSON/Markdown evidence report.",
    )
    parser.add_argument(
        "--tool-runtime-evidence-smoke",
        action="store_true",
        help="Materialize local evidence artifacts for the Tool-Use Runtime report.",
    )
    parser.add_argument(
        "--mcp-hook-live-validation",
        metavar="OUTPUT_DIR",
        help="Run local MCP transport, failure, auth-refresh, and hook trust-profile validation.",
    )
    parser.add_argument(
        "--tb-command-template",
        help=(
            "Command template for one Terminal-Bench shard. Available fields: "
            "{task_ids}, {task_args}, {output_dir}, {shard_index}."
        ),
    )
    parser.add_argument("--tb-shard-size", type=int, default=5, help="Task count per Terminal-Bench shard.")
    parser.add_argument("--tb-output-dir", default="terminal-bench-shards", help="Directory for shard outputs and manifest.")
    parser.add_argument("--tb-dry-run", action="store_true", help="Plan shards without running Terminal-Bench commands.")
    parser.add_argument("--tb-preflight-only", action="store_true", help="Run Terminal-Bench real-run preflight without executing shards.")
    parser.add_argument("--tb-skip-preflight", action="store_true", help="Write preflight diagnostics but continue even if checks fail.")
    parser.add_argument("--tb-resume", action="store_true", help="Resume from shard-manifest.json and skip passed shards.")
    parser.add_argument("--tb-no-task-resume", action="store_true", help="Disable per-task resume from shard results.json files.")
    parser.add_argument("--tb-max-retries", type=int, default=0, help="Retry count for environment-only failed shards.")
    parser.add_argument(
        "--benchmark-target-score",
        type=float,
        help="Optional minimum score gate for --benchmark-automation, for example 0.99.",
    )
    parser.add_argument(
        "--benchmark-allow-invalid",
        action="store_true",
        help="Do not fail --benchmark-automation solely because the generated report is marked invalid.",
    )
    parser.add_argument(
        "--tb-no-env-retry",
        action="store_true",
        help="Disable retries when a shard failure is classified as environment-only.",
    )
    parser.add_argument("prompt", nargs="*", help="Prompt to run once. Omit for REPL mode.")
    parser.add_argument("--prompt", dest="prompt_flag", help="Prompt to run once.")
    parser.add_argument("--workspace", default=".", help="Workspace root the tools can access.")
    parser.add_argument("--env-file", default=".env", help="Optional .env file path.")
    parser.add_argument("--model", help="Model name for real provider runs.")
    parser.add_argument("--base-url", help="Optional provider-compatible API base URL.")
    parser.add_argument(
        "--reasoning-effort",
        help="Optional OpenAI Responses reasoning effort, for example high or xhigh.",
    )
    parser.add_argument(
        "--openai-api-mode",
        choices=["auto", "responses", "chat"],
        help="OpenAI API mode. auto uses Responses for api.openai.com and Chat Completions for custom base URLs.",
    )
    parser.add_argument(
        "--provider",
        choices=["anthropic", "openai"],
        help="Real model provider to use when --mock is not set.",
    )
    parser.add_argument("--mock", action="store_true", help="Use deterministic mock provider.")
    parser.add_argument("--s20", action="store_true", help="Enable the comprehensive S20 toolset. Optional/experimental beyond the core evidence loop.")
    parser.add_argument("--max-turns", type=int, help="Maximum model/tool loop turns.")
    parser.add_argument("--coding-loop", action="store_true", help="Enable the evidence-first verification gate for code modification tasks.")
    parser.add_argument("--no-coding-loop", action="store_true", help="Disable Coding Task Success Loop, including the S20 default.")
    parser.add_argument("--test-command", help="Explicit verification command for Coding Task Success Loop.")
    parser.add_argument("--max-repair-attempts", type=int, default=3, help="Maximum repair attempts after failed verification.")
    parser.add_argument(
        "--require-verification",
        action="store_true",
        help="Force verification after any write-file task when Coding Task Success Loop is enabled.",
    )
    parser.add_argument(
        "--max-nested-subagent-depth",
        type=int,
        help="Maximum nested subagent delegation depth inside S20 subagents.",
    )
    parser.add_argument(
        "--nested-subagent-token-budget",
        type=int,
        help="Approximate token budget for each nested subagent prompt/task.",
    )
    parser.add_argument(
        "--conversation-compaction-token-budget",
        type=int,
        help="Approximate token budget before old model/tool turns are compacted.",
    )
    parser.add_argument(
        "--conversation-compaction-keep-recent",
        type=int,
        help="Recent message count to preserve verbatim during conversation compaction.",
    )
    parser.add_argument(
        "--model-context-token-budget",
        type=int,
        help="Approximate end-to-end budget for system prompt, tool schemas, and messages sent to the model.",
    )
    parser.add_argument("--timeout", type=int, help="Harness timeout hint in seconds.")
    parser.add_argument("--shell-timeout", type=int, help="Shell timeout in seconds.")
    parser.add_argument(
        "--state-dir",
        default=".mini_cc",
        help="S20 state directory relative to workspace, or 'none' for in-memory state.",
    )
    parser.add_argument(
        "--benchmark-hints",
        action="store_true",
        help="Prepend mechanical hints extracted from obfuscated benchmark prompts.",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "json"],
        default="text",
        help="Output format for harness integrations.",
    )
    parser.add_argument(
        "--permission-mode",
        choices=["ask", "auto", "bypass", "read-only"],
        help="Harness-compatible permission mode. bypass maps to auto.",
    )
    parser.add_argument(
        "--permission",
        choices=["ask", "auto", "read-only"],
        help="Permission mode for write_file, replace_text, and run_shell.",
    )
    args = parser.parse_args(argv)
    args.evidence_mode = evidence_mode
    return args


def permission_mode(args: argparse.Namespace) -> str:
    selected = args.permission_mode or args.permission or DEFAULT_PERMISSION
    if selected == "bypass":
        return "auto"
    return selected


def prompt_text(args: argparse.Namespace) -> str:
    text = args.prompt_flag.strip() if args.prompt_flag else " ".join(args.prompt).strip()
    if args.benchmark_hints and text:
        hints = extract_benchmark_hints(text)
        if hints:
            return hints + "\n\nOriginal task:\n" + text
    return text


def extract_benchmark_hints(text: str) -> str:
    """Extract visible code anchors from multilingual or obfuscated prompts."""
    hints: list[str] = []
    reserved_words = {
        "and",
        "as",
        "class",
        "def",
        "else",
        "for",
        "from",
        "if",
        "import",
        "in",
        "or",
        "return",
        "while",
        "with",
    }
    tokens = []
    for token in re.findall(r"\.?[A-Za-z_][A-Za-z0-9_./-]*", text):
        cleaned = token.rstrip(".,:;!?()[]{}")
        if cleaned.startswith("./"):
            cleaned = cleaned[2:]
        tokens.append(cleaned)
    files = {
        token
        for token in tokens
        if re.search(r"\.(?:py|json|txt|csv|md|toml|yaml|yml|ini|log|xml|html|js|ts|sql)$", token)
    }
    for directory in ("src", "tests", "docs", "logs", "config"):
        if directory in tokens:
            for file_name in tuple(files):
                if (
                    "/" not in file_name
                    and file_name != directory
                    and re.search(
                        rf"\b{re.escape(directory)}\b[\s\S]{{0,100}}\b{re.escape(file_name)}\b",
                        text,
                    )
                ):
                    files.add(f"{directory}/{file_name}")
    signatures = sorted(set(re.findall(r"[A-Za-z_]\w*\([^)]*\)\s*->\s*[A-Za-z_][A-Za-z0-9_\[\], ]*", text)))
    calls = sorted(set(re.findall(r"[A-Za-z_]\w*\([^)]*\)", text)))
    identifiers = sorted(
        {
            token
            for token in tokens
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token)
            and token.lower() not in reserved_words
            and token not in {"src", "tests", "docs", "logs", "config"}
        }
    )
    quoted = sorted({left or right for left, right in re.findall(r"'([^']*)'|\"([^\"]*)\"", text) if left or right})
    assignments = sorted(
        set(
            re.findall(
                r"\b[A-Z][A-Z0-9_]*\s*=\s*(?:\"[^\"]*\"|'[^']*'|\d+|True|False|true|false)",
                text,
            )
        )
    )
    code_fragments = sorted(
        {
            fragment.strip()
            for fragment in re.findall(r"\(([^()\n]*(?:return|def|import|=|:)[^()\n]*)\)", text)
            if len(fragment.strip()) <= 120
        }
    )
    structured_blocks = extract_structured_blocks(text)
    ascii_phrases = sorted(
        {
            value.strip()
            for value in re.findall(r"(?<![A-Za-z0-9_])([A-Z][A-Za-z0-9 _.,:;!?-]{4,}[.!?])", text)
            if any(mark in value for mark in ",.!?:;")
        }
    )
    key_values: list[str] = []
    value_atom = r"(?:\"([^\"]+)\"|'([^']+)'|(\d+(?:\.\d+)?|True|False|true|false))"
    for key, double_quoted, single_quoted, number in re.findall(
        rf"\b([A-Za-z_][A-Za-z0-9_-]*)\b\s*(?:=|:)\s*{value_atom}",
        text,
    ):
        value = double_quoted or single_quoted or number
        if key.lower() not in reserved_words and value and not value.startswith(".") and "(" not in value and ")" not in value:
            key_values.append(f"{key}={value}")
    for key, double_quoted, single_quoted, number in re.findall(
        rf"\b([A-Za-z_][A-Za-z0-9_-]*)\b\s+{value_atom}",
        text,
    ):
        value = double_quoted or single_quoted or number
        if key.lower() not in reserved_words and value and not value.startswith(".") and "(" not in value and ")" not in value:
            key_values.append(f"{key}={value}")
    key_values = sorted(set(key_values))

    if files:
        hints.append(
            "Visible file paths/names mentioned by the task (target or context; do not edit tests unless explicitly requested): "
            + ", ".join(sorted(files))
        )
    if signatures:
        hints.append("Required function signatures to implement: " + ", ".join(signatures))
    elif calls:
        hints.append("Visible function calls: " + ", ".join(calls[:8]))
    if identifiers:
        hints.append("Visible code identifiers/type tokens: " + ", ".join(identifiers[:20]))
    if assignments:
        hints.append("Visible exact assignment lines to preserve or create: " + ", ".join(repr(value) for value in assignments[:12]))
    if code_fragments:
        hints.append("Visible code fragments to preserve exactly when relevant: " + ", ".join(repr(value) for value in code_fragments[:12]))
    if structured_blocks:
        rendered_blocks = "\n\n".join(f"```\n{block}\n```" for block in structured_blocks[:3])
        hints.append("Visible structured block(s), dedented by common prose indentation, to copy exactly when relevant:\n" + rendered_blocks)
    if quoted:
        hints.append(
            "Visible quoted literals/examples to copy exactly, without translating or normalizing: "
            + ", ".join(repr(value) for value in quoted[:16])
        )
    if "docstring" in text.lower() and quoted:
        hints.append(
            "Required exact docstring line(s) to insert: "
            + ", ".join(repr(f'"""{value}"""') for value in quoted[:4])
        )
    if ascii_phrases:
        hints.append("Visible exact ASCII phrases/output candidates: " + ", ".join(repr(value) for value in ascii_phrases[:12]))
    if key_values:
        hints.append("Visible key/value anchors: " + ", ".join(key_values[:12]))
    task_contract = build_task_contract_hint(
        text=text,
        files=sorted(files),
        quoted=quoted,
        assignments=assignments,
        signatures=signatures,
        calls=calls,
        identifiers=identifiers,
    )
    if task_contract:
        hints.append(task_contract)
    if hints:
        hints.insert(
            0,
            (
                "Benchmark hint: the original prompt may be obfuscated. Complete only this task. "
                "First classify the anchors below as target files, context files, exact literals, "
                "semantic user facts, or verification clues. Copy exact literals character-for-character "
                "only when the task asks for that literal, output, code, or docstring. For semantic "
                "facts, infer the canonical fact and store it using the workspace's stated memory format; "
                "do not copy whole prose sentences as keys. "
                "For existing files, read the target file first and prefer replace_text so unrelated "
                "content stays unchanged. You must call write_file or replace_text to create/update the required target "
                "files before final response. Do not create unrelated files. Do not ask for clarification."
            ),
        )
    return "\n".join(hints)


def extract_structured_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if re.fullmatch(r"\s+[A-Za-z0-9_./:@#'\" -]+", line):
            current.append(line.rstrip())
            continue
        if len(current) >= 2:
            blocks.append(textwrap.dedent("\n".join(current)).strip("\n"))
        current = []
    if len(current) >= 2:
        blocks.append(textwrap.dedent("\n".join(current)).strip("\n"))
    return blocks


def build_task_contract_hint(
    *,
    text: str,
    files: list[str],
    quoted: list[str],
    assignments: list[str],
    signatures: list[str],
    calls: list[str],
    identifiers: list[str],
) -> str:
    """Build a generic interpretation contract without task-specific answers."""
    del text
    contract = [
        "Task contract guidance:",
        "- Separate target files from context files. Test files are verification context unless the task explicitly asks to edit tests.",
        "- Treat quoted/code-block/output/docstring values as exact literals when they are the requested artifact.",
        "- Treat user profile details, preferences, contact facts, dates, locations, and tools as semantic facts; normalize them into the memory format required by AGENTS.md or existing MEMORY.md.",
        "- For semantic facts, choose the shortest stable schema/category key from AGENTS.md or existing memory; keep context such as current/work/preferred in the value only when it changes the fact type.",
        "- For edits/refactors, preserve unrelated lines, assignments, imports, constants, functions, and file formatting.",
        "- For deterministic text-derived reports, manifests, and hashes, treat common text files as logical text: read/decode text and normalize CRLF/CR to LF before calculating. Use raw bytes only when the task explicitly says binary, byte-for-byte, or raw bytes.",
        "- Verify with the most local deterministic check available after editing.",
    ]
    if signatures:
        contract.append("- Implement the visible function signature(s) exactly and keep unrelated function bodies unchanged.")
    elif calls:
        contract.append("- Function calls are behavior examples or named symbols; inspect existing files/tests before deciding what to edit.")
    elif identifiers:
        contract.append("- Code identifiers and type tokens are likely named symbols or constraints; inspect files/tests to infer their role before editing.")
    if assignments:
        contract.append("- Visible assignments may be required final lines or preservation constraints; do not delete them unless explicitly instructed.")
    if quoted:
        contract.append("- Quoted prose can be either an exact literal or a semantic fact; decide from the requested artifact and AGENTS.md/MEMORY.md rules.")
    if files:
        contract.append("- If several files are mentioned, inspect existing files to decide which are targets and which are read-only context.")
    return "\n".join(contract)


def build_agent(args: argparse.Namespace, output: Callable[[str], None] = print) -> Agent:
    load_env_file(Path(args.env_file))
    shell_timeout = args.timeout if args.timeout is not None else args.shell_timeout
    config = build_config(
        workspace=args.workspace,
        permission=permission_mode(args),
        max_turns=args.max_turns,
        shell_timeout=shell_timeout,
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        reasoning_effort=args.reasoning_effort,
        openai_api_mode=args.openai_api_mode,
        nested_subagent_depth=args.max_nested_subagent_depth,
        nested_subagent_token_budget=args.nested_subagent_token_budget,
        compaction_token_budget=args.conversation_compaction_token_budget,
        compaction_keep_recent_messages=args.conversation_compaction_keep_recent,
        model_context_token_budget=args.model_context_token_budget,
    )
    governance = load_governance_config(config.workspace)
    permission_policy = PermissionPolicy.from_config(governance.merged.get("permission_policy"))
    if args.s20:
        state_dir = None if args.state_dir == "none" else config.workspace / args.state_dir
        tools = S20ToolRunner(
            config.workspace,
            permission=config.permission,
            shell_timeout=config.shell_timeout,
            state_dir=state_dir,
            permission_policy=permission_policy,
        )
        load_configured_hooks(tools.hooks, config.workspace)
    else:
        tools = ToolRunner(
            config.workspace,
            permission=config.permission,
            shell_timeout=config.shell_timeout,
            permission_policy=permission_policy,
        )
    def make_provider(model_override: str | None = None):
        if args.mock:
            return MockProvider()
        if config.provider == "openai":
            return OpenAIProvider(
                api_key=config.openai_api_key,
                model=model_override or config.openai_model,
                max_tokens=config.max_tokens,
                base_url=config.base_url,
                reasoning_effort=config.openai_reasoning_effort,
                api_mode=config.openai_api_mode,
            )
        return AnthropicProvider(
            api_key=config.api_key,
            model=model_override or config.model,
            max_tokens=config.max_tokens,
            base_url=config.base_url,
        )

    provider = make_provider()
    selected_model = config.openai_model if config.provider == "openai" else config.model
    coding_loop_enabled = (args.coding_loop or args.require_verification or args.s20) and not args.no_coding_loop
    coding_loop = (
        CodingLoopPolicy(
            config.workspace,
            enabled=True,
            test_command=args.test_command,
            max_repair_attempts=args.max_repair_attempts,
            require_verification=args.require_verification,
        )
        if coding_loop_enabled
        else None
    )
    task_state_machine = TaskStateMachine(
        config.workspace,
        max_repair_attempts=args.max_repair_attempts,
        enabled=True,
    )
    task_runtime = TaskRuntime(
        config.workspace,
        task_state_machine=task_state_machine,
        coding_loop=coding_loop,
    )
    system_prompt = system_prompt_for_workspace(
        config.s20_system_prompt if args.s20 else config.system_prompt,
        config.workspace,
        tools.hooks if args.s20 else None,
    )
    if args.s20:
        tools.set_subagents(
            SubagentRuntime(
                workspace=config.workspace,
                base_tools=tools,
                provider_factory=lambda spec: make_provider(spec.model),
                planning_provider=make_provider(),
                max_nested_depth=config.nested_subagent_depth,
                nested_token_budget=config.nested_subagent_token_budget,
                compaction_token_budget=config.compaction_token_budget,
                compaction_keep_recent_messages=config.compaction_keep_recent_messages,
                model_context_token_budget=config.model_context_token_budget,
                state_dir=None if state_dir is None else state_dir / "subagents",
            )
        )
        return Agent(
            provider,
            tools,
            max_turns=config.max_turns,
            system_prompt=system_prompt,
            output=output,
            session_store=SessionStore(None if state_dir is None else state_dir / "sessions"),
            hook_runtime=tools.hooks,
            model_name=selected_model,
            workflow=StructuredWorkflow(planner=ModelAuthoredPlanner(make_provider())),
            compaction_token_budget=config.compaction_token_budget,
            compaction_keep_recent_messages=config.compaction_keep_recent_messages,
            model_context_token_budget=config.model_context_token_budget,
            coding_loop=coding_loop,
            task_state_machine=task_state_machine,
            task_runtime=task_runtime,
        )
    return Agent(
        provider,
        tools,
        max_turns=config.max_turns,
        system_prompt=system_prompt,
        output=output,
        compaction_token_budget=config.compaction_token_budget,
        compaction_keep_recent_messages=config.compaction_keep_recent_messages,
        model_context_token_budget=config.model_context_token_budget,
        coding_loop=coding_loop,
        task_state_machine=task_state_machine,
        task_runtime=task_runtime,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.diagnose_config:
        workspace = Path(args.workspace).expanduser().resolve()
        print(json.dumps(load_governance_config(workspace).to_json(), ensure_ascii=False, indent=2))
        return 0
    if args.classify_terminal_bench:
        payload = json.loads(Path(args.classify_terminal_bench).read_text(encoding="utf-8"))
        rows = []
        for result in payload.get("results", []):
            classification = classify_terminal_bench_result(result)
            rows.append(
                {
                    "task_id": result.get("task_id"),
                    "is_resolved": result.get("is_resolved"),
                    "failure_mode": result.get("failure_mode"),
                    "category": classification.category,
                    "reason": classification.reason,
                }
            )
        print(json.dumps({"results": rows}, ensure_ascii=False, indent=2))
        return 0
    if args.benchmark_report:
        paths = write_benchmark_report(
            Path(args.benchmark_report),
            Path(args.benchmark_report_output) if args.benchmark_report_output else None,
        )
        print(
            json.dumps(
                {
                    "benchmark_report_json": str(paths["json"]),
                    "benchmark_report_markdown": str(paths["markdown"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.tool_use_eval:
        paths = run_real_tool_use_eval(
            Path(args.tool_use_eval),
            Path(args.workspace).expanduser().resolve(),
            Path(args.tool_use_eval_input) if args.tool_use_eval_input else None,
        )
        print(
            json.dumps(
                {
                    "tool_use_eval_json": str(paths["json"]),
                    "tool_use_eval_markdown": str(paths["markdown"]),
                    "tool_use_scenarios": str(paths["scenarios"]),
                    "tool_use_trace": str(paths["trace"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.tool_runtime_evidence_smoke:
        workspace = Path(args.workspace).expanduser().resolve()
        smoke_paths = write_tool_runtime_evidence_smoke(workspace)
        if not args.tool_runtime_report:
            print(json.dumps({key: str(value) for key, value in smoke_paths.items()}, ensure_ascii=False, indent=2))
            return 0
    if args.tool_runtime_report:
        workspace = Path(args.workspace).expanduser().resolve()
        paths = write_tool_runtime_report(workspace, Path(args.tool_runtime_report))
        payload = {
            "tool_runtime_report_json": str(paths["json"]),
            "tool_runtime_report_markdown": str(paths["markdown"]),
        }
        if args.tool_runtime_evidence_smoke:
            payload["tool_runtime_evidence_smoke"] = {key: str(value) for key, value in smoke_paths.items()}
        print(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.mcp_hook_live_validation:
        workspace = Path(args.workspace).expanduser().resolve()
        paths = write_live_validation_report(workspace, Path(args.mcp_hook_live_validation))
        print(
            json.dumps(
                {
                    "mcp_hook_live_validation_json": str(paths["json"]),
                    "mcp_hook_live_validation_markdown": str(paths["markdown"]),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.terminal_bench_real_run:
        if not args.tb_command_template:
            print("--tb-command-template is required with --terminal-bench-real-run", file=sys.stderr)
            return 2
        task_ids = load_task_ids(Path(args.terminal_bench_real_run))
        result = run_terminal_bench_real_pipeline(
            task_ids=task_ids,
            command_template=args.tb_command_template,
            output_dir=Path(args.tb_output_dir).expanduser().resolve(),
            report_dir=Path(args.benchmark_report_output).expanduser().resolve()
            if args.benchmark_report_output
            else None,
            shard_size=args.tb_shard_size,
            dry_run=args.tb_dry_run,
            resume=args.tb_resume,
            resume_tasks=not args.tb_no_task_resume,
            max_retries=args.tb_max_retries,
            retry_environment_failures=not args.tb_no_env_retry,
            target_score=args.benchmark_target_score,
            require_valid_run=not args.benchmark_allow_invalid,
            preflight_only=args.tb_preflight_only,
            skip_preflight=args.tb_skip_preflight,
        )
        print(json.dumps(terminal_bench_real_run_to_json(result), ensure_ascii=False, indent=2))
        return 0 if result.ok else 1
    if args.benchmark_automation:
        if not args.tb_command_template:
            print("--tb-command-template is required with --benchmark-automation", file=sys.stderr)
            return 2
        task_ids = load_task_ids(Path(args.benchmark_automation))
        result = run_benchmark_automation(
            task_ids=task_ids,
            command_template=args.tb_command_template,
            output_dir=Path(args.tb_output_dir).expanduser().resolve(),
            report_dir=Path(args.benchmark_report_output).expanduser().resolve()
            if args.benchmark_report_output
            else None,
            shard_size=args.tb_shard_size,
            dry_run=args.tb_dry_run,
            resume=args.tb_resume,
            resume_tasks=not args.tb_no_task_resume,
            max_retries=args.tb_max_retries,
            retry_environment_failures=not args.tb_no_env_retry,
            target_score=args.benchmark_target_score,
            require_valid_run=not args.benchmark_allow_invalid,
        )
        print(json.dumps(benchmark_automation_to_json(result), ensure_ascii=False, indent=2))
        return 0 if result.ok else 1
    if args.terminal_bench_shards:
        if not args.tb_command_template:
            print("--tb-command-template is required with --terminal-bench-shards", file=sys.stderr)
            return 2
        task_ids = load_task_ids(Path(args.terminal_bench_shards))
        runner = TerminalBenchShardRunner(
            task_ids=task_ids,
            command_template=args.tb_command_template,
            output_dir=Path(args.tb_output_dir).expanduser().resolve(),
            shard_size=args.tb_shard_size,
            dry_run=args.tb_dry_run,
            resume=args.tb_resume,
            resume_tasks=not args.tb_no_task_resume,
            max_retries=args.tb_max_retries,
            retry_environment_failures=not args.tb_no_env_retry,
        )
        results = runner.run()
        summary = runner.aggregate_results()
        ok_statuses = {"passed", "planned", "resumed"}
        print(
            json.dumps(
                {
                    "ok": all(result.status in ok_statuses for result in results)
                    and len(results) == len(runner.plan()),
                    "manifest": str(runner.output_dir / "shard-manifest.json"),
                    "aggregate_summary": str(runner.output_dir / "aggregate-summary.json"),
                    "score": {
                        "total": summary.total,
                        "resolved": summary.resolved,
                        "score": summary.score,
                        "categories": summary.categories,
                    },
                    "results": [
                        {
                            "index": result.index,
                            "task_ids": result.task_ids,
                            "status": result.status,
                            "command": result.command,
                            "returncode": result.returncode,
                            "reason": result.reason,
                        }
                        for result in results
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if all(result.status in ok_statuses for result in results) else 1
    lines: list[str] = []
    output = lines.append if args.output_format == "json" else print
    agent = build_agent(args, output=output)
    prompt = prompt_text(args)
    if prompt:
        try:
            agent.run(prompt)
        except Exception as exc:
            if args.output_format == "json":
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "error": str(exc),
                            "prompt": prompt,
                            "workspace": str(Path(args.workspace).resolve()),
                            "model": args.model,
                            "max_turns": args.max_turns,
                            "trace": lines,
                        },
                        ensure_ascii=False,
                    )
                )
            else:
                raise
            return 1
        if args.output_format == "json":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "prompt": prompt,
                        "workspace": str(Path(args.workspace).resolve()),
                        "model": args.model,
                        "max_turns": args.max_turns,
                        "trace": lines,
                    },
                    ensure_ascii=False,
                )
            )
        return 0

    print("Mini Claude Code REPL. Type /exit to quit.")
    while True:
        try:
            user_input = input("mini-cc> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not user_input:
            continue
        if user_input in {"/exit", "/quit"}:
            return 0
        agent.run(user_input)

