from __future__ import annotations

import time
from typing import Any, Callable

from .coding_loop import CodingLoopPolicy, parse_exit_code
from .hooks import HookRuntime
from .llm import Provider
from .session import AgentSession, SessionStore
from .task_state import TaskStateMachine
from .tools import ToolResult, ToolRunner
from .workflow import ExecutionRecord, StructuredWorkflow, TaskPlan


SYSTEM_PROMPT = """You are Mini Claude Code, a local coding assistant.

Work like a careful coding agent:
- Inspect the workspace before changing files.
- Use tools for file reads, searches, edits, and commands.
- Prefer apply_patch for code edits when exact string replacement is fragile.
- Keep edits minimal and explain important tradeoffs.
- Never claim a command or edit succeeded unless a tool result confirms it.
- For coding tasks, follow phases: INTAKE, EXPLORE, LOCALIZE, PLAN, EDIT, VERIFY, REPAIR, FINAL.
- Explore and localize before editing. Do not modify a file before reading it.
- Produce a minimal edit plan with planned_files before changing files.
- For code modification tasks, run a real verification command after editing; git_status, git_diff, context_snapshot, list_files, read_file, and search_text are not verification.
- If verification fails, analyze the failure output before making one minimal repair.
- If a task needs writes or shell commands, ask through the available tools and obey permission denials.
"""


def _block_to_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)

    block_type = getattr(block, "type")
    if block_type == "text":
        return {"type": "text", "text": getattr(block, "text")}
    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id"),
            "name": getattr(block, "name"),
            "input": getattr(block, "input"),
        }
    raise TypeError(f"Unsupported content block: {block!r}")


class Agent:
    def __init__(
        self,
        provider: Provider,
        tools: ToolRunner,
        *,
        max_turns: int = 8,
        system_prompt: str = SYSTEM_PROMPT,
        output: Callable[[str], None] = print,
        session_store: SessionStore | None = None,
        hook_runtime: HookRuntime | None = None,
        model_name: str | None = None,
        workflow: StructuredWorkflow | None = None,
        compaction_token_budget: int = 6000,
        compaction_keep_recent_messages: int = 6,
        model_context_token_budget: int = 8000,
        coding_loop: CodingLoopPolicy | None = None,
        task_state_machine: TaskStateMachine | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.max_turns = max_turns
        self.system_prompt = system_prompt
        self.output = output
        self.session_store = session_store
        self.hook_runtime = hook_runtime
        self.model_name = model_name
        self.workflow = workflow
        self.messages: list[dict[str, Any]] = []
        self.compaction_token_budget = max(1, int(compaction_token_budget))
        self.compaction_keep_recent_messages = max(2, int(compaction_keep_recent_messages))
        self.model_context_token_budget = max(256, int(model_context_token_budget))
        self.coding_loop = coding_loop
        self.task_state_machine = task_state_machine

    def run(self, prompt: str, *, resume_session_id: str | None = None) -> None:
        started = time.perf_counter()
        if self.coding_loop is not None:
            self.coding_loop.start(prompt)
        if self.task_state_machine is not None:
            self.task_state_machine.start(prompt)
        if self.hook_runtime is not None:
            prompt_decision = self.hook_runtime.user_prompt_submit(
                prompt,
                source="resume" if resume_session_id else "cli",
            )
            if not prompt_decision.allow:
                raise RuntimeError(prompt_decision.reason or "UserPromptSubmit hook blocked prompt")
            if isinstance(prompt_decision.payload_updates.get("prompt"), str):
                prompt = prompt_decision.payload_updates["prompt"]
        session: AgentSession | None = None
        plan: TaskPlan | None = None
        executions: list[ExecutionRecord] = []
        if self.session_store is not None:
            if resume_session_id:
                session = self.session_store.resume(resume_session_id, prompt)
                if session is None:
                    raise ValueError(f"Session not found for resume: {resume_session_id}")
                self.messages = list(session.messages)
            else:
                session = self.session_store.start(prompt, model=self.model_name)
        if self.hook_runtime is not None:
            self.hook_runtime.session_start(
                prompt=prompt,
                model=self.model_name,
                start_reason="resume" if resume_session_id else "user_prompt",
                session_id=session.id if session is not None else None,
            )
        if session is not None and hasattr(self.tools, "permission_context"):
            self.tools.permission_context = {
                **getattr(self.tools, "permission_context", {}),
                "session_id": session.id,
            }
        if self.workflow is not None:
            plan = self.workflow.planner.plan(prompt)
            set_envelope = getattr(self.tools, "set_permission_envelope", None)
            if callable(set_envelope):
                set_envelope(plan.permission_envelope, reason=f"workflow plan mode={plan.mode}")
            if self.session_store is not None and session is not None:
                self.session_store.record(session, "planner_plan", plan.to_json())
                self.session_store.record(
                    session,
                    "permission_envelope",
                    {
                        "mode": plan.mode,
                        "allowed_risks": list(plan.permission_envelope),
                    },
                )
        self.messages.append({"role": "user", "content": prompt})
        if session is not None:
            self._compact_messages_if_needed(session=session, trigger="user_prompt")
        if self.session_store is not None and session is not None:
            self.session_store.update_messages(session, self.messages)

        status = "completed"
        try:
            turn = 1
            while self.max_turns <= 0 or turn <= self.max_turns:
                if self.session_store is not None and session is not None:
                    self.session_store.record(session, "turn_start", {"turn": turn})
                tool_schemas = self.tools.schemas()
                self._prepare_model_context_for_budget(
                    session=session,
                    tools=tool_schemas,
                    trigger=f"before_model_turn_{turn}",
                )
                response = self.provider.complete(
                    self.messages,
                    tool_schemas,
                    self.system_prompt,
                )
                if self.session_store is not None and session is not None:
                    self.session_store.record(
                        session,
                        "model_response",
                        {"turn": turn, "blocks": len(response.content)},
                    )

                assistant_content: list[dict[str, Any]] = []
                tool_results: list[dict[str, Any]] = []

                for raw_block in response.content:
                    block = _block_to_dict(raw_block)
                    assistant_content.append(block)

                    if block["type"] == "text":
                        text = block.get("text", "")
                        if text:
                            self.output(text)
                            if self.task_state_machine is not None:
                                self.task_state_machine.observe_assistant_text(text)
                    elif block["type"] == "tool_use":
                        name = block["name"]
                        tool_input = block.get("input") or {}
                        self.output(f"\n[tool] {name}({tool_input})")
                        task_decision = None
                        if self.task_state_machine is not None:
                            task_decision = self.task_state_machine.before_tool(name, tool_input)
                        if task_decision is not None and not task_decision.allow:
                            result = ToolResult(
                                (
                                    "Task phase blocked: "
                                    + task_decision.reason
                                    + "\n"
                                    + task_decision.instruction
                                ),
                                is_error=True,
                                metadata={
                                    "task_phase": task_decision.next_phase.value if task_decision.next_phase else None,
                                    "task_phase_reason": task_decision.reason,
                                },
                            )
                        else:
                            result = self.tools.run(name, tool_input)
                        planned_step = self.workflow.executor.classify_tool(name) if self.workflow is not None else "tool"
                        executions.append(
                            ExecutionRecord(
                                turn=turn,
                                tool=name,
                                planned_step=planned_step,
                                is_error=result.is_error,
                                chars=len(result.content),
                                summary=self._clip(result.content, 240),
                                tool_input=dict(tool_input),
                                exit_code=parse_exit_code(result.content) if name == "run_shell" else None,
                            )
                        )
                        if self.session_store is not None and session is not None:
                            self.session_store.record(
                                session,
                                "tool_use",
                                {
                                    "turn": turn,
                                    "name": name,
                                    "is_error": result.is_error,
                                    "chars": len(result.content),
                                },
                            )
                            if self.workflow is not None:
                                self.session_store.record(
                                    session,
                                    "executor_tool_use",
                                    {
                                        "turn": turn,
                                        "name": name,
                                        "planned_step": planned_step,
                                        "is_error": result.is_error,
                                        "chars": len(result.content),
                                    },
                                )
                        if result.is_error:
                            self.output(f"[tool error] {result.content}\n")
                        else:
                            self.output(f"[tool ok] {result.content[:800]}\n")
                        if self.coding_loop is not None:
                            self.coding_loop.observe_tool_result(name, tool_input, result)
                        if self.task_state_machine is not None:
                            self.task_state_machine.observe_tool_result(name, tool_input, result)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block["id"],
                                "content": result.content,
                                "is_error": result.is_error,
                            }
                        )

                self.messages.append({"role": "assistant", "content": assistant_content})
                if self.session_store is not None and session is not None:
                    self.session_store.update_messages(session, self.messages)
                turn += 1
                if not tool_results:
                    if self.task_state_machine is not None:
                        task_decision = self.task_state_machine.finish_decision()
                        if not task_decision.allow:
                            self.output(f"\n[task-state] {task_decision.reason}\n")
                            self.messages.append({"role": "user", "content": task_decision.instruction})
                            if self.session_store is not None and session is not None:
                                self.session_store.record(
                                    session,
                                    "task_state_gate",
                                    {
                                        "reason": task_decision.reason,
                                        "next_phase": task_decision.next_phase.value if task_decision.next_phase else None,
                                        "state": self.task_state_machine.state.to_json(),
                                    },
                                )
                                self.session_store.update_messages(session, self.messages)
                            continue
                    if self.coding_loop is not None:
                        # CodingLoopPolicy is the source of truth for code task success verification.
                        decision = self.coding_loop.finish_decision()
                        if not decision.allow_finish:
                            self.output(f"\n[coding-loop] {decision.reason}\n")
                            self.messages.append({"role": "user", "content": decision.instruction})
                            if self.session_store is not None and session is not None:
                                self.session_store.record(
                                    session,
                                    "coding_loop_gate",
                                    {
                                        "reason": decision.reason,
                                        "status": decision.status,
                                        "state": self.coding_loop.state.to_json(),
                                    },
                                )
                                self.session_store.update_messages(session, self.messages)
                            continue
                        final_report = self.coding_loop.final_report(status=decision.status)
                        if final_report:
                            self.output("\n" + final_report)
                        self.coding_loop.write_artifact(status=decision.status)
                    if self.task_state_machine is not None:
                        self.task_state_machine.write_artifact(status=status)
                    if self.workflow is not None and plan is not None and self.session_store is not None and session is not None:
                        verification = self.workflow.verifier.verify(plan, executions)
                        self.session_store.record(session, "verifier_result", verification.to_json())
                        self.session_store.record(
                            session,
                            "evidence_ledger",
                            {"items": [item.to_json() for item in verification.evidence_ledger]},
                        )
                        self.session_store.record(session, "plan_repair", verification.plan_repair.to_json())
                    if self.session_store is not None and session is not None:
                        self.session_store.finish(session, status=status)
                    if self.hook_runtime is not None:
                        self._emit_session_end(
                            status=status,
                            reason="no_tool_results",
                            session=session,
                            started=started,
                        )
                        self._emit_stop(status=status, reason="no_tool_results", session=session)
                    return
                self.messages.append({"role": "user", "content": tool_results})
                if session is not None:
                    self._compact_messages_if_needed(session=session, trigger="tool_results")
                if self.session_store is not None and session is not None:
                    self.session_store.update_messages(session, self.messages)
        except Exception as exc:
            status = "failed"
            if self.coding_loop is not None:
                self.coding_loop.write_artifact(status="failed")
            if self.task_state_machine is not None:
                self.task_state_machine.write_artifact(status="failed")
            if self.session_store is not None and session is not None:
                self.session_store.record(session, "error", {"message": str(exc)})
                self.session_store.finish(session, status=status)
            if self.hook_runtime is not None:
                self._emit_session_end(
                    status=status,
                    reason="exception",
                    session=session,
                    started=started,
                )
                self._emit_stop(status=status, reason="exception", session=session, error=str(exc))
            raise

        status = "max_turns"
        if self.coding_loop is not None:
            final_report = self.coding_loop.final_report(status="max_turns_reached")
            if final_report:
                self.output("\n" + final_report)
            self.coding_loop.write_artifact(status="max_turns_reached")
        if self.task_state_machine is not None:
            self.task_state_machine.write_artifact(status="max_turns_reached")
        if self.session_store is not None and session is not None:
            if self.workflow is not None and plan is not None:
                verification = self.workflow.verifier.verify(plan, executions)
                self.session_store.record(session, "verifier_result", verification.to_json())
                self.session_store.record(
                    session,
                    "evidence_ledger",
                    {"items": [item.to_json() for item in verification.evidence_ledger]},
                )
                self.session_store.record(session, "plan_repair", verification.plan_repair.to_json())
            self.session_store.finish(session, status=status)
        if self.hook_runtime is not None:
            self._emit_session_end(status=status, reason="max_turns", session=session, started=started)
            self._emit_stop(status=status, reason="max_turns", session=session)
        self.output(f"Stopped after max_turns={self.max_turns}.")

    def _emit_session_end(
        self,
        *,
        status: str,
        reason: str,
        session: AgentSession | None,
        started: float,
    ) -> None:
        if self.hook_runtime is None:
            return
        self.hook_runtime.session_end(
            status=status,
            reason=reason,
            session_id=session.id if session is not None else None,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    def _emit_stop(
        self,
        *,
        status: str,
        reason: str,
        session: AgentSession | None,
        error: str | None = None,
    ) -> None:
        if self.hook_runtime is None:
            return
        payload: dict[str, Any] = {
            "status": status,
            "reason": reason,
            "session_id": session.id if session is not None else None,
        }
        if error:
            payload["error"] = error
        decision = self.hook_runtime.stop(payload)
        if not decision.allow:
            self.hook_runtime.stop_failure(
                error_type="hook_blocked",
                message=decision.reason or "Stop hook blocked shutdown",
                status=status,
                session_id=session.id if session is not None else None,
            )

    def _prepare_model_context_for_budget(
        self,
        *,
        session: AgentSession | None,
        tools: list[dict[str, Any]],
        trigger: str,
    ) -> None:
        before_tokens = self._estimate_model_context_tokens(tools)
        if before_tokens <= self.model_context_token_budget:
            return
        self._compact_messages_if_needed(session=session, trigger=trigger)
        after_compaction_tokens = self._estimate_model_context_tokens(tools)
        if after_compaction_tokens > self.model_context_token_budget:
            self._shrink_message_payloads_for_budget(tools)
        after_tokens = self._estimate_model_context_tokens(tools)
        if self.session_store is not None and session is not None:
            self.session_store.record(
                session,
                "model_context_budget_applied",
                {
                    "trigger": trigger,
                    "budget": self.model_context_token_budget,
                    "before_estimated_tokens": before_tokens,
                    "after_estimated_tokens": after_tokens,
                    "messages": len(self.messages),
                },
            )

    def _compact_messages_if_needed(self, *, session: AgentSession | None, trigger: str) -> None:
        estimated_tokens = self._estimate_tokens(self.messages)
        if estimated_tokens <= self.compaction_token_budget:
            return
        if len(self.messages) <= self.compaction_keep_recent_messages + 1:
            return
        source_count = len(self.messages) - self.compaction_keep_recent_messages
        if self.hook_runtime is not None:
            decision = self.hook_runtime.pre_compact(
                trigger=trigger,
                token_budget=self.compaction_token_budget,
                estimated_tokens=estimated_tokens,
                source_count=source_count,
            )
            if not decision.allow:
                return
        before_messages = len(self.messages)
        before_tokens = estimated_tokens
        summary = self._summarize_messages(self.messages[:source_count])
        self.messages = [{"role": "user", "content": summary}, *self.messages[source_count:]]
        after_tokens = self._estimate_tokens(self.messages)
        if self.session_store is not None and session is not None:
            self.session_store.record(
                session,
                "conversation_compacted",
                {
                    "trigger": trigger,
                    "before_messages": before_messages,
                    "after_messages": len(self.messages),
                    "before_estimated_tokens": before_tokens,
                    "after_estimated_tokens": after_tokens,
                    "summary_chars": len(summary),
                },
            )
        if self.hook_runtime is not None:
            self.hook_runtime.post_compact(
                trigger=trigger,
                token_budget=self.compaction_token_budget,
                estimated_tokens=after_tokens,
                compressed_sections=["conversation_messages"],
                summary_chars=len(summary),
            )

    def _summarize_messages(self, messages: list[dict[str, Any]]) -> str:
        lines = [
            "Conversation compaction summary:",
            "Older model/tool turns were compressed deterministically. Preserve these facts when continuing.",
        ]
        previous_summaries: list[str] = []
        user_prompts: list[str] = []
        assistant_texts: list[str] = []
        tool_uses: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            role = str(message.get("role", ""))
            content = message.get("content")
            if isinstance(content, str):
                if content.startswith("Conversation compaction summary:"):
                    previous_summaries.append(self._clip(content, 500))
                elif role == "user":
                    user_prompts.append(self._clip(content, 400))
                elif role == "assistant":
                    assistant_texts.append(self._clip(content, 400))
                continue
            if role == "assistant" and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and block.get("text"):
                        assistant_texts.append(self._clip(str(block.get("text")), 300))
                    elif block.get("type") == "tool_use":
                        tool_uses.append(
                            {
                                "id": block.get("id"),
                                "tool": block.get("name"),
                                "input": block.get("input") if isinstance(block.get("input"), dict) else {},
                                "is_error": None,
                                "result_summary": "",
                            }
                        )
            elif role == "user" and isinstance(content, list):
                results_by_id = {
                    item.get("tool_use_id"): item
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "tool_result"
                }
                for tool in tool_uses:
                    if tool["is_error"] is not None:
                        continue
                    result = results_by_id.get(tool.get("id"))
                    if result is None:
                        continue
                    tool["is_error"] = bool(result.get("is_error"))
                    tool["result_summary"] = self._clip(str(result.get("content", "")), 360)
            else:
                lines.append(f"- message[{index}] role={role}: {self._clip(str(content), 300)}")
        if previous_summaries:
            lines.append("\nPrevious compacted summary:")
            lines.extend(f"- {summary}" for summary in previous_summaries[-2:])
        if user_prompts:
            lines.append("\nUser prompts and handoffs:")
            lines.extend(f"- {prompt}" for prompt in user_prompts[-6:])
        if assistant_texts:
            lines.append("\nAssistant text:")
            lines.extend(f"- {text}" for text in assistant_texts[-6:])
        if tool_uses:
            lines.append("\nTool calls:")
            for tool in tool_uses[-16:]:
                status = "error" if tool["is_error"] else "ok"
                if tool["is_error"] is None:
                    status = "unknown"
                lines.append(
                    "- "
                    + f"tool={tool.get('tool')} status={status} "
                    + "input="
                    + json_dumps_compact(tool.get("input", {}))
                    + " result="
                    + json_dumps_compact(tool.get("result_summary", ""))
                )
        return "\n".join(lines)

    def _estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        return max(1, (len(json_dumps_compact(messages)) + 3) // 4)

    def _estimate_model_context_tokens(self, tools: list[dict[str, Any]]) -> int:
        payload = {
            "system": self.system_prompt,
            "tools": tools,
            "messages": self.messages,
        }
        return max(1, (len(json_dumps_compact(payload)) + 3) // 4)

    def _shrink_message_payloads_for_budget(self, tools: list[dict[str, Any]]) -> None:
        del tools
        for message in self.messages:
            content = message.get("content")
            if isinstance(content, str) and len(content) > 1200:
                message["content"] = self._clip(content, 1200)
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text" and isinstance(block.get("text"), str):
                        block["text"] = self._clip(block["text"], 600)
                    elif block.get("type") == "tool_result" and isinstance(block.get("content"), str):
                        original = block["content"]
                        block["content"] = (
                            "[tool result summarized by model context budget]\n"
                            + self._clip(original, 700)
                        )
                    elif block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                        input_text = json_dumps_compact(block["input"])
                        if len(input_text) > 1000:
                            block["input"] = {"_summary": self._clip(input_text, 1000)}

    def _clip(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 15)] + "...[truncated]"


def json_dumps_compact(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
