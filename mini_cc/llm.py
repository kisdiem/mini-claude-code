from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol


class Provider(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> Any:
        ...


class AnthropicProvider:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        max_tokens: int,
        base_url: str | None = None,
    ) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency: run `python -m pip install -r requirements.txt`."
            ) from exc

        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set. Copy .env.example to .env first.")

        self.client = Anthropic(api_key=api_key, base_url=base_url)
        self.model = model
        self.max_tokens = max_tokens

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> Any:
        return self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )


class OpenAIProvider:
    """OpenAI Responses API provider that emits Anthropic-like content blocks."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        max_tokens: int,
        base_url: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency: run `python -m pip install -r requirements.txt`."
            ) from exc

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> Any:
        input_payload: Any = self.messages_to_responses_input(messages)

        request: dict[str, Any] = {
            "model": self.model,
            "instructions": system,
            "input": input_payload,
            "tools": [self._tool_schema(tool) for tool in tools],
            "max_output_tokens": self.max_tokens,
            "store": False,
        }
        if self.reasoning_effort:
            request["reasoning"] = {"effort": self.reasoning_effort}
        response = self.client.responses.create(**request)
        return self._to_blocks(response)

    @staticmethod
    def messages_to_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        input_items: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role", "user"))
            content = message.get("content", "")
            if isinstance(content, str):
                input_items.append({"role": role, "content": content})
                continue
            if role == "assistant" and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type")
                    if block_type == "text" and block.get("text"):
                        input_items.append({"role": "assistant", "content": str(block.get("text", ""))})
                    elif block_type == "tool_use":
                        input_items.append(
                            {
                                "type": "function_call",
                                "call_id": str(block.get("id", "")),
                                "name": str(block.get("name", "")),
                                "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                            }
                        )
                continue
            if role == "user" and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": str(block.get("tool_use_id", "")),
                            "output": str(block.get("content", "")),
                        }
                    )
                continue
            input_items.append({"role": role, "content": str(content)})
        return input_items

    def _tool_schema(self, tool: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "function",
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        }

    def _to_blocks(self, response: Any) -> "MockResponse":
        blocks: list[MockBlock] = []
        output_text = getattr(response, "output_text", None)
        if output_text:
            blocks.append(MockBlock(type="text", text=output_text))

        for item in getattr(response, "output", []) or []:
            item_type = getattr(item, "type", None)
            if item_type == "message" and not output_text:
                text = self._message_text(item)
                if text:
                    blocks.append(MockBlock(type="text", text=text))
            elif item_type == "function_call":
                arguments = getattr(item, "arguments", "{}") or "{}"
                try:
                    parsed_args = json.loads(arguments)
                except json.JSONDecodeError:
                    parsed_args = {}
                blocks.append(
                    MockBlock(
                        type="tool_use",
                        id=getattr(item, "call_id"),
                        name=getattr(item, "name"),
                        input=parsed_args,
                    )
                )

        if not blocks:
            blocks.append(MockBlock(type="text", text="OpenAI provider returned no output."))
        return MockResponse(blocks)

    def _message_text(self, item: Any) -> str:
        parts: list[str] = []
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                parts.append(str(text))
        return "\n".join(parts)


@dataclass
class MockBlock:
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None

    def model_dump(self, exclude_none: bool = True) -> dict[str, Any]:
        data = {
            "type": self.type,
            "text": self.text,
            "id": self.id,
            "name": self.name,
            "input": self.input,
        }
        if exclude_none:
            return {key: value for key, value in data.items() if value is not None}
        return data


@dataclass
class MockResponse:
    content: list[MockBlock]


class MockProvider:
    """Deterministic provider for local testing without network or API keys."""

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> MockResponse:
        del tools, system

        last = messages[-1]
        if last["role"] == "user" and isinstance(last["content"], list):
            tool_result = last["content"][0]
            content = str(tool_result.get("content", ""))
            return MockResponse(
                [
                    MockBlock(
                        type="text",
                        text="Mock provider received the tool result:\n\n" + content[:1200],
                    )
                ]
            )

        prompt = str(last.get("content", "")).lower()
        if "s20" in prompt or "snapshot" in prompt or "comprehensive" in prompt:
            return MockResponse(
                [
                    MockBlock(type="text", text="I will take an S20 context snapshot."),
                    MockBlock(
                        type="tool_use",
                        id="toolu_mock_snapshot",
                        name="context_snapshot",
                        input={},
                    ),
                ]
            )

        if "todo" in prompt:
            return MockResponse(
                [
                    MockBlock(type="text", text="I will read the current todo state."),
                    MockBlock(
                        type="tool_use",
                        id="toolu_mock_todo",
                        name="todo_read",
                        input={},
                    ),
                ]
            )

        if "search" in prompt:
            return MockResponse(
                [
                    MockBlock(type="text", text="I will search the workspace."),
                    MockBlock(
                        type="tool_use",
                        id="toolu_mock_search",
                        name="search_text",
                        input={"pattern": "class|def|README", "path": ".", "max_matches": 20},
                    ),
                ]
            )

        if "read" in prompt and "readme" in prompt:
            return MockResponse(
                [
                    MockBlock(type="text", text="I will read the README."),
                    MockBlock(
                        type="tool_use",
                        id="toolu_mock_read",
                        name="read_file",
                        input={"path": "README.md", "start_line": 1, "max_lines": 120},
                    ),
                ]
            )

        if "list" in prompt or "files" in prompt or "structure" in prompt or "结构" in prompt:
            return MockResponse(
                [
                    MockBlock(type="text", text="I will list the workspace files."),
                    MockBlock(
                        type="tool_use",
                        id="toolu_mock_list",
                        name="list_files",
                        input={"path": ".", "recursive": False, "max_entries": 80},
                    ),
                ]
            )

        return MockResponse(
            [
                MockBlock(
                    type="text",
                    text=(
                        "Mock mode is ready. Try prompts like `list files`, "
                        "`read README`, or `search project`."
                    ),
                )
            ]
        )
