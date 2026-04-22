#!/usr/bin/env python3
"""Provider adapters for Anthropic and OpenAI."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass


def _import_anthropic():
    try:
        import anthropic
    except ModuleNotFoundError as err:
        raise RuntimeError(
            "anthropic package is required. Install scripts/requirements-runtime.txt."
        ) from err
    return anthropic


def _import_openai():
    try:
        import openai
    except ModuleNotFoundError as err:
        raise RuntimeError(
            "openai package is required. Install scripts/requirements-runtime.txt."
        ) from err
    return openai


def _extract_error_message(data) -> str:
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            message = err.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
        message = data.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        try:
            return json.dumps(data, ensure_ascii=False)
        except TypeError:
            return ""
    if isinstance(data, str) and data.strip():
        return data.strip()
    return ""


def provider_error_message(err: Exception) -> str:
    body = getattr(err, "body", None)
    message = _extract_error_message(body)
    if message:
        return message

    response = getattr(err, "response", None)
    if response is not None:
        try:
            data = response.json()
        except Exception:
            data = None
        message = _extract_error_message(data)
        if message:
            return message
        text = getattr(response, "text", None)
        message = _extract_error_message(text)
        if message:
            return message

    direct = getattr(err, "message", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    return str(err)


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class ToolResult:
    tool_use_id: str
    output: str


@dataclass
class TurnOutcome:
    final_text: str
    tool_calls: list[ToolCall]
    usage: dict | None = None


def normalize_api_base(base_url: str, version_prefix: str) -> str:
    base = base_url.rstrip("/")
    if version_prefix and base.endswith(version_prefix):
        return base[: -len(version_prefix)]
    return base


def ensure_api_base(base_url: str, version_prefix: str) -> str:
    base = base_url.rstrip("/")
    if version_prefix and not base.endswith(version_prefix):
        return f"{base}{version_prefix}"
    return base


def _as_dict(value):
    if isinstance(value, dict):
        return {key: _as_dict(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_as_dict(item) for item in value]
    if hasattr(value, "model_dump"):
        return _as_dict(value.model_dump(mode="json", exclude_none=True))
    if hasattr(value, "to_dict"):
        return _as_dict(value.to_dict())
    if hasattr(value, "__dict__"):
        return {
            key: _as_dict(item)
            for key, item in vars(value).items()
            if not key.startswith("_") and item is not None
        }
    return value


def _parse_json_object(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _provider_schema(node: dict) -> dict:
    schema = {}
    for key in ("type", "description", "enum", "format", "default"):
        if key in node:
            schema[key] = node[key]

    properties = node.get("properties")
    if isinstance(properties, dict):
        schema["properties"] = {
            name: _provider_schema(value)
            for name, value in properties.items()
            if isinstance(value, dict)
        }

    items = node.get("items")
    if isinstance(items, dict):
        schema["items"] = _provider_schema(items)

    required = node.get("required")
    if isinstance(required, list):
        schema["required"] = required

    return schema


def _schema_supports_strict(node: dict) -> bool:
    node_type = node.get("type")
    if node_type == "object":
        properties = node.get("properties")
        if not isinstance(properties, dict):
            return True
        return all(_schema_supports_strict(child) for child in properties.values() if isinstance(child, dict))

    if node_type == "array":
        items = node.get("items")
        if not isinstance(items, dict):
            return True
        # Some OpenAI-compatible endpoints reject strict schemas when array items are objects.
        if items.get("type") == "object":
            return False
        return _schema_supports_strict(items)

    return True


def _tool_supports_strict(properties: dict, required: list) -> bool:
    if set(required) != set(properties.keys()):
        return False
    return all(_schema_supports_strict(schema) for schema in properties.values() if isinstance(schema, dict))


def _anthropic_client(*, api_key: str, base_url: str):
    anthropic = _import_anthropic()
    return anthropic.Anthropic(
        api_key=api_key,
        base_url=normalize_api_base(base_url, "/v1"),
    )


def _openai_client(*, api_key: str, base_url: str):
    openai = _import_openai()
    return openai.OpenAI(
        api_key=api_key,
        base_url=ensure_api_base(base_url, "/v1"),
    )


def _anthropic_usage_dict(usage) -> dict:
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0}
    return {
        "input_tokens": (
            getattr(usage, "input_tokens", 0)
            + getattr(usage, "cache_creation_input_tokens", 0)
            + getattr(usage, "cache_read_input_tokens", 0)
        ),
        "output_tokens": getattr(usage, "output_tokens", 0),
    }


def _openai_usage_dict(usage) -> dict:
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0}
    return {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
    }


def _openai_chat_usage_dict(usage) -> dict:
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0}
    return {
        "input_tokens": getattr(usage, "prompt_tokens", 0),
        "output_tokens": getattr(usage, "completion_tokens", 0),
    }


def _openai_output_text(item) -> str:
    text_parts: list[str] = []
    for part in getattr(item, "content", []) or []:
        if getattr(part, "type", None) == "output_text":
            text = getattr(part, "text", "") or ""
            if text:
                text_parts.append(text)
    return "".join(text_parts)


def _openai_output_item_to_input(item) -> dict | None:
    item_type = getattr(item, "type", None)
    if item_type == "function_call":
        result = {
            "type": "function_call",
            "id": getattr(item, "id", "") or getattr(item, "call_id", ""),
            "call_id": getattr(item, "call_id", ""),
            "name": getattr(item, "name", ""),
            "arguments": getattr(item, "arguments", "") or "",
        }
        status = getattr(item, "status", None)
        if status:
            result["status"] = status
        return result

    if item_type == "message":
        content = []
        for part in getattr(item, "content", []) or []:
            if getattr(part, "type", None) == "output_text":
                content.append(
                    {
                        "type": "output_text",
                        "text": getattr(part, "text", "") or "",
                    }
                )
        result = {
            "type": "message",
            "role": getattr(item, "role", "assistant"),
            "content": content,
        }
        item_id = getattr(item, "id", None)
        if item_id:
            result["id"] = item_id
        status = getattr(item, "status", None)
        if status:
            result["status"] = status
        return result

    dumped = _as_dict(item)
    return dumped if isinstance(dumped, dict) else None


def kernel_to_anthropic_tools(kernel_tools: list[dict]) -> list[dict]:
    result = []
    for tool in kernel_tools:
        result.append(
            {
                "name": tool["name"],
                "description": tool["description"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        key: _provider_schema(value)
                        for key, value in tool.get("params", {}).items()
                        if isinstance(value, dict)
                    },
                    "required": tool.get("required", []),
                },
            }
        )
    return result


def kernel_to_openai_tools(kernel_tools: list[dict]) -> list[dict]:
    result = []
    for tool in kernel_tools:
        properties = {
            key: _provider_schema(value)
            for key, value in tool.get("params", {}).items()
            if isinstance(value, dict)
        }
        required = tool.get("required", [])
        result.append(
            {
                "type": "function",
                "name": tool["name"],
                "description": tool["description"],
                **({"strict": True} if _tool_supports_strict(properties, required) else {}),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            }
        )
    return result


def kernel_to_openai_chat_tools(kernel_tools: list[dict]) -> list[dict]:
    result = []
    for tool in kernel_tools:
        properties = {
            key: _provider_schema(value)
            for key, value in tool.get("params", {}).items()
            if isinstance(value, dict)
        }
        required = tool.get("required", [])
        function = {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        }
        if _tool_supports_strict(properties, required):
            function["strict"] = True
        result.append({"type": "function", "function": function})
    return result


class ProviderSession(ABC):
    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt
        self._current_resp = None

    @abstractmethod
    def add_user_text(self, text: str):
        raise NotImplementedError

    @abstractmethod
    def add_tool_results(self, results: list[ToolResult]):
        raise NotImplementedError

    @abstractmethod
    def generate(self, on_text_delta) -> TurnOutcome:
        raise NotImplementedError

    def abort(self):
        resp = self._current_resp
        if resp:
            try:
                resp.close()
            except Exception:
                pass

    def record_aborted_turn(self):
        """Keep provider state coherent after a cancelled turn."""

    def restore_history(self, entries: list[dict]):
        """Replay history entries to rebuild conversation state."""

    def needs_compact(self) -> bool:
        """Check if compaction is needed (without performing it)."""
        return False

    def compact(self, logger=None) -> str:
        """Compact conversation history if needed. Returns summary text or empty string."""
        return ""


class AnthropicSession(ProviderSession):
    def __init__(self, *, system_prompt: str, model: str, api_key: str, base_url: str, tools: list[dict]):
        super().__init__(system_prompt)
        self.model = model
        self.api_key = api_key
        self.base_url = normalize_api_base(base_url, "/v1")
        self.api_url = f"{self.base_url}/v1/messages"
        self.client = _anthropic_client(api_key=api_key, base_url=base_url)
        self.tools = kernel_to_anthropic_tools(tools)
        self.messages: list[dict] = []

    def add_user_text(self, text: str):
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[ToolResult]):
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": result.tool_use_id,
                        "content": result.output,
                    }
                    for result in results
                ],
            }
        )

    def record_aborted_turn(self):
        self.messages.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "[cancelled]"}],
            }
        )

    def restore_history(self, entries: list[dict]):
        for entry in entries:
            role = entry.get("role")
            if role == "user":
                self.messages.append({"role": "user", "content": entry["text"]})
            elif role == "assistant" and "text" in entry:
                self.messages.append({"role": "assistant", "content": [{"type": "text", "text": entry["text"]}]})
            elif role == "assistant" and "tool_use" in entry:
                tu = entry["tool_use"]
                self.messages.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": tu["id"], "name": tu["name"], "input": tu.get("input", {})}],
                    }
                )
            elif role == "tool":
                self.messages.append(
                    {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": entry["tool_use_id"], "content": entry.get("output", "")}],
                    }
                )

    def needs_compact(self) -> bool:
        from .compaction import should_compact

        return should_compact(self.messages, self.model, self.system_prompt)

    def compact(self, logger=None) -> str:
        from .compaction import compact_messages_anthropic, should_compact

        if not should_compact(self.messages, self.model, self.system_prompt):
            return ""
        new_messages, summary = compact_messages_anthropic(
            api_key=self.api_key,
            api_url=self.api_url,
            model=self.model,
            system_prompt=self.system_prompt,
            messages=self.messages,
            logger=logger,
        )
        if summary:
            self.messages = new_messages
        return summary

    def generate(self, on_text_delta) -> TurnOutcome:
        kwargs = {
            "model": self.model,
            "max_tokens": 4096,
            "system": self.system_prompt,
            "messages": self.messages,
            "timeout": 600,
        }
        if self.tools:
            kwargs["tools"] = self.tools

        text_parts: list[str] = []
        final_message = None
        try:
            stream_manager = self.client.messages.stream(**kwargs)
            with stream_manager as stream:
                self._current_resp = stream
                for event in stream:
                    if getattr(event, "type", None) == "text":
                        delta = getattr(event, "text", "") or ""
                        if delta:
                            text_parts.append(delta)
                            on_text_delta(delta)
                final_message = stream.get_final_message()
        except Exception as err:
            raise RuntimeError(provider_error_message(err)) from err
        finally:
            self._current_resp = None

        content_blocks: list[dict] = []
        tool_calls: list[ToolCall] = []
        for block in getattr(final_message, "content", []) or []:
            if getattr(block, "type", None) == "text":
                text = getattr(block, "text", "") or ""
                content_blocks.append({"type": "text", "text": text})
            elif getattr(block, "type", None) == "tool_use":
                input_data = getattr(block, "input", {})
                if not isinstance(input_data, dict):
                    input_data = {}
                content_block = {
                    "type": "tool_use",
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "input": input_data,
                }
                content_blocks.append(content_block)
                tool_calls.append(
                    ToolCall(
                        id=content_block["id"],
                        name=content_block["name"],
                        input=content_block["input"],
                    )
                )

        final_text = "".join(text_parts)
        if not final_text:
            final_text = "".join(
                block["text"]
                for block in content_blocks
                if block.get("type") == "text"
            )
        if not content_blocks and final_text:
            content_blocks.append({"type": "text", "text": final_text})

        self.messages.append({"role": "assistant", "content": content_blocks})
        return TurnOutcome(
            final_text=final_text,
            tool_calls=tool_calls,
            usage=_anthropic_usage_dict(getattr(final_message, "usage", None)),
        )


class OpenAISession(ProviderSession):
    def __init__(self, *, system_prompt: str, model: str, api_key: str, base_url: str, tools: list[dict]):
        super().__init__(system_prompt)
        self.model = model
        self.api_key = api_key
        self.base_url = ensure_api_base(base_url, "/v1")
        self.api_url = f"{self.base_url}/responses"
        self.client = _openai_client(api_key=api_key, base_url=base_url)
        self.tools = kernel_to_openai_tools(tools)
        self.previous_response_id: str | None = None
        self.pending_input: list[dict] = []
        self.last_response_output: list[dict] = []

    def add_user_text(self, text: str):
        self.last_response_output = []
        self.pending_input.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[ToolResult]):
        if self.last_response_output:
            self.pending_input.extend(self.last_response_output)
            self.last_response_output = []
        for result in results:
            self.pending_input.append(
                {
                    "type": "function_call_output",
                    "call_id": result.tool_use_id,
                    "output": result.output,
                }
            )

    def record_aborted_turn(self):
        self.pending_input = []
        self.last_response_output = []

    def restore_history(self, entries: list[dict]):
        for entry in entries:
            role = entry.get("role")
            if role == "user":
                self.pending_input.append({"role": "user", "content": entry["text"]})
            elif role == "assistant" and "text" in entry:
                self.pending_input.append({"role": "assistant", "content": entry["text"]})

    def needs_compact(self) -> bool:
        from .compaction import should_compact

        return should_compact(self.pending_input, self.model, self.system_prompt)

    def compact(self, logger=None) -> str:
        from .compaction import compact_messages_openai, should_compact

        if not should_compact(self.pending_input, self.model, self.system_prompt):
            return ""
        new_input, summary = compact_messages_openai(
            api_key=self.api_key,
            api_url=self.api_url,
            model=self.model,
            system_prompt=self.system_prompt,
            pending_input=self.pending_input,
            logger=logger,
        )
        if summary:
            self.pending_input = new_input
        return summary

    def generate(self, on_text_delta) -> TurnOutcome:
        kwargs = {
            "model": self.model,
            "instructions": self.system_prompt,
            "input": self.pending_input,
            "parallel_tool_calls": True,
            "timeout": 600,
        }
        if self.tools:
            kwargs["tools"] = self.tools
        if self.previous_response_id:
            kwargs["previous_response_id"] = self.previous_response_id

        response_id = None
        final_response = None
        text_parts: list[str] = []
        tool_state: dict[str, dict] = {}
        tool_order: list[str] = []

        try:
            stream_manager = self.client.responses.stream(**kwargs)
            with stream_manager as stream:
                self._current_resp = stream
                for event in stream:
                    event_type = getattr(event, "type", "")

                    if event_type == "response.created":
                        response = getattr(event, "response", None)
                        response_id = getattr(response, "id", response_id)

                    elif event_type == "response.output_text.delta":
                        delta = getattr(event, "delta", "") or ""
                        if delta:
                            text_parts.append(delta)
                            on_text_delta(delta)

                    elif event_type == "response.output_item.added":
                        item = getattr(event, "item", None)
                        if getattr(item, "type", None) == "function_call":
                            item_id = getattr(item, "id", None) or getattr(event, "item_id", None) or str(getattr(event, "output_index", len(tool_order)))
                            if item_id not in tool_state:
                                tool_order.append(item_id)
                                tool_state[item_id] = {"call_id": "", "name": "", "arguments": ""}
                            tool_state[item_id].update(
                                {
                                    "call_id": getattr(item, "call_id", "") or tool_state[item_id]["call_id"],
                                    "name": getattr(item, "name", "") or tool_state[item_id]["name"],
                                    "arguments": getattr(item, "arguments", "") or tool_state[item_id]["arguments"],
                                }
                            )

                    elif event_type == "response.function_call_arguments.delta":
                        item_id = getattr(event, "item_id", None)
                        if item_id in tool_state:
                            tool_state[item_id]["arguments"] += getattr(event, "delta", "") or ""

                    elif event_type == "response.function_call_arguments.done":
                        item_id = getattr(event, "item_id", None)
                        arguments = getattr(event, "arguments", None)
                        if item_id in tool_state and arguments is not None:
                            tool_state[item_id]["arguments"] = arguments

                    elif event_type == "response.output_item.done":
                        item = getattr(event, "item", None)
                        if getattr(item, "type", None) == "function_call":
                            item_id = getattr(item, "id", None) or str(getattr(event, "output_index", len(tool_order)))
                            if item_id not in tool_state:
                                tool_order.append(item_id)
                                tool_state[item_id] = {"call_id": "", "name": "", "arguments": ""}
                            tool_state[item_id].update(
                                {
                                    "call_id": getattr(item, "call_id", "") or tool_state[item_id]["call_id"],
                                    "name": getattr(item, "name", "") or tool_state[item_id]["name"],
                                    "arguments": getattr(item, "arguments", "") or tool_state[item_id]["arguments"],
                                }
                            )

                    elif event_type == "response.completed":
                        final_response = getattr(event, "response", None)
                        response_id = getattr(final_response, "id", response_id)

                    elif event_type in {"error", "response.error", "response.failed"}:
                        raise RuntimeError(provider_error_message(getattr(event, "error", event)))

                if final_response is None:
                    final_response = stream.get_final_response()
        except Exception as err:
            raise RuntimeError(provider_error_message(err)) from err
        finally:
            self._current_resp = None

        completed_output: list[dict] = []
        final_output = getattr(final_response, "output", []) or []
        for item in final_output:
            if getattr(item, "type", None) == "function_call":
                item_id = getattr(item, "id", None) or getattr(item, "call_id", None)
                if item_id and item_id not in tool_state:
                    tool_order.append(item_id)
                    tool_state[item_id] = {
                        "call_id": getattr(item, "call_id", "") or "",
                        "name": getattr(item, "name", "") or "",
                        "arguments": getattr(item, "arguments", "") or "",
                    }
            elif getattr(item, "type", None) == "message" and not text_parts:
                text = _openai_output_text(item)
                if text:
                    text_parts.append(text)

            input_item = _openai_output_item_to_input(item)
            if input_item is not None:
                completed_output.append(input_item)

        self.pending_input = []
        if response_id:
            self.previous_response_id = response_id
        if not completed_output and tool_order:
            completed_output = [
                {
                    "type": "function_call",
                    "id": item_id,
                    "call_id": tool_state[item_id].get("call_id", ""),
                    "name": tool_state[item_id].get("name", ""),
                    "arguments": tool_state[item_id].get("arguments", "") or "",
                    "status": "completed",
                }
                for item_id in tool_order
            ]
        self.last_response_output = completed_output

        tool_calls: list[ToolCall] = []
        for item_id in tool_order:
            tool = tool_state[item_id]
            tool_calls.append(
                ToolCall(
                    id=tool.get("call_id") or item_id,
                    name=tool.get("name", ""),
                    input=_parse_json_object(tool.get("arguments") or "{}"),
                )
            )

        return TurnOutcome(
            final_text="".join(text_parts),
            tool_calls=tool_calls,
            usage=_openai_usage_dict(getattr(final_response, "usage", None)),
        )


class OpenAIChatCompletionsSession(ProviderSession):
    def __init__(self, *, system_prompt: str, model: str, api_key: str, base_url: str, tools: list[dict]):
        super().__init__(system_prompt)
        self.model = model
        self.api_key = api_key
        self.base_url = ensure_api_base(base_url, "/v1")
        self.api_url = f"{self.base_url}/chat/completions"
        self.client = _openai_client(api_key=api_key, base_url=base_url)
        self.tools = kernel_to_openai_chat_tools(tools)
        self.messages: list[dict] = []

    def add_user_text(self, text: str):
        self.messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[ToolResult]):
        for result in results:
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": result.tool_use_id,
                    "content": result.output,
                }
            )

    def record_aborted_turn(self):
        self.messages.append({"role": "assistant", "content": "[cancelled]"})

    def restore_history(self, entries: list[dict]):
        for entry in entries:
            role = entry.get("role")
            if role == "user":
                self.messages.append({"role": "user", "content": entry["text"]})
            elif role == "assistant" and "text" in entry:
                self.messages.append({"role": "assistant", "content": entry["text"]})

    def needs_compact(self) -> bool:
        from .compaction import should_compact

        return should_compact(self.messages, self.model, self.system_prompt)

    def compact(self, logger=None) -> str:
        from .compaction import COMPACT_PROMPT, KEEP_LAST_MESSAGES, _extract_summary, estimate_tokens, should_compact

        if not should_compact(self.messages, self.model, self.system_prompt):
            return ""

        keep_last = KEEP_LAST_MESSAGES
        old = self.messages[:-keep_last]
        recent = self.messages[-keep_last:]
        summary_messages = [{"role": "system", "content": self.system_prompt}] + list(old) + [
            {"role": "user", "content": COMPACT_PROMPT}
        ]

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=summary_messages,
                timeout=120,
            )
            summary_text = ""
            for choice in getattr(response, "choices", []) or []:
                message = getattr(choice, "message", None)
                content = getattr(message, "content", "") if message is not None else ""
                if isinstance(content, str):
                    summary_text += content
        except Exception as err:
            if logger:
                logger(f"compaction API call failed: {provider_error_message(err)}")
            return ""

        summary_text = _extract_summary(summary_text)
        if not summary_text.strip():
            if logger:
                logger("compaction produced empty summary, skipping")
            return ""

        new_messages = [
            {"role": "user", "content": f"<conversation_summary>\n{summary_text}\n</conversation_summary>"},
            {"role": "assistant", "content": "I have the full context from our previous conversation. I'll continue from where we left off."},
        ] + recent

        if logger:
            old_tokens = estimate_tokens(self.messages)
            new_tokens = estimate_tokens(new_messages)
            logger(f"compacted: {old_tokens} -> {new_tokens} estimated tokens ({len(self.messages)} -> {len(new_messages)} messages)")

        self.messages = new_messages
        return summary_text

    def generate(self, on_text_delta) -> TurnOutcome:
        kwargs = {
            "model": self.model,
            "messages": [{"role": "system", "content": self.system_prompt}] + self.messages,
            "stream": True,
            "parallel_tool_calls": True,
            "stream_options": {"include_usage": True},
            "timeout": 600,
        }
        if self.tools:
            kwargs["tools"] = self.tools

        text_parts: list[str] = []
        usage: dict = {"input_tokens": 0, "output_tokens": 0}
        tool_state: dict[int, dict] = {}
        stream = None

        try:
            stream = self.client.chat.completions.create(**kwargs)
            self._current_resp = stream
            for chunk in stream:
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = _openai_chat_usage_dict(chunk_usage)

                for choice in getattr(chunk, "choices", []) or []:
                    delta = getattr(choice, "delta", None)
                    if delta is None:
                        continue

                    content = getattr(delta, "content", "") or ""
                    if content:
                        text_parts.append(content)
                        on_text_delta(content)

                    for tool_delta in getattr(delta, "tool_calls", []) or []:
                        index = int(getattr(tool_delta, "index", 0) or 0)
                        state = tool_state.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        tool_id = getattr(tool_delta, "id", None)
                        if tool_id:
                            state["id"] = tool_id
                        function = getattr(tool_delta, "function", None)
                        if function is not None:
                            name = getattr(function, "name", None)
                            if name:
                                state["name"] = name
                            arguments = getattr(function, "arguments", None)
                            if arguments:
                                state["arguments"] += arguments
        except Exception as err:
            raise RuntimeError(provider_error_message(err)) from err
        finally:
            self._current_resp = None
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

        tool_calls: list[ToolCall] = []
        assistant_tool_calls: list[dict] = []
        for index in sorted(tool_state):
            tool = tool_state[index]
            tool_id = tool.get("id") or f"tool_call_{index}"
            raw_arguments = tool.get("arguments", "") or "{}"
            tool_calls.append(
                ToolCall(
                    id=tool_id,
                    name=tool.get("name", ""),
                    input=_parse_json_object(raw_arguments),
                )
            )
            assistant_tool_calls.append(
                {
                    "id": tool_id,
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "arguments": raw_arguments,
                    },
                }
            )

        final_text = "".join(text_parts)
        if assistant_tool_calls:
            self.messages.append(
                {
                    "role": "assistant",
                    "content": final_text or None,
                    "tool_calls": assistant_tool_calls,
                }
            )
        else:
            self.messages.append({"role": "assistant", "content": final_text})

        return TurnOutcome(final_text=final_text, tool_calls=tool_calls, usage=usage)


# ---------------------------------------------------------------------------
# Mock provider — deterministic, no LLM calls
# ---------------------------------------------------------------------------

import enum
import os
import re
import shlex
import sys

from skills.lib.paths import testing_skills_dir


def _venv_python() -> str:
    """Return path to venv python, falling back to current interpreter."""
    tabula_home = os.environ.get("TABULA_HOME", os.path.expanduser("~/.tabula"))
    venv = os.path.join(tabula_home, ".venv", "bin", "python3")
    if os.path.isfile(venv):
        return venv
    return sys.executable


class _MockState(enum.Enum):
    IDLE = "idle"
    PROCESS_SPAWN = "process_spawn"
    TOOLS_SENT = "tools_sent"
    ENTER_COLLECTION = "enter_collection"
    FINISH = "finish"
    BUSY_REPLY = "busy_reply"


@dataclass
class MockConfig:
    subagent_count: int = 3
    mock_turns: int = 5
    mock_sleep_ms: int = 25
    default_waves: int = 1
    default_fanouts: list[int] | None = None


class MockProvider(ProviderSession):
    """Deterministic provider for testing subagent orchestration."""

    def __init__(self, *, system_prompt: str, tools: list[dict], config: MockConfig):
        super().__init__(system_prompt)
        self.config = config
        self.tools_spec = tools

        session_match = re.search(r"Your session name is `([^`]+)`", system_prompt)
        self._session = session_match.group(1) if session_match else "main"

        self._state = _MockState.IDLE
        self._turn_no = 0
        self._wave_no = 0
        self._wave_counts: list[int] = []
        self._user_text = ""
        self._output_buffer: list[str] = []
        self._all_results: dict[str, str] = {}
        self._current_wave_ids: list[str] = []
        self._active = False

    def _parse_request(self, text: str) -> tuple[str, list[int]]:
        fanouts_match = re.search(r"fanouts=([0-9,]+)", text)
        wave_match = re.search(r"waves=(\d+)", text)
        subagent_match = re.search(r"subagents=(\d+)", text)
        if fanouts_match:
            wave_counts = [max(1, int(p)) for p in fanouts_match.group(1).split(",") if p.strip()]
        else:
            waves = max(1, int(wave_match.group(1))) if wave_match else self.config.default_waves
            per_wave = max(1, int(subagent_match.group(1))) if subagent_match else self.config.subagent_count
            if self.config.default_fanouts and not wave_match and not subagent_match:
                wave_counts = list(self.config.default_fanouts)
            else:
                wave_counts = [per_wave] * waves
        cleaned = re.sub(r"\s*waves=\d+\s*", " ", text)
        cleaned = re.sub(r"\s*subagents=\d+\s*", " ", cleaned)
        cleaned = re.sub(r"\s*fanouts=[0-9,]+\s*", " ", cleaned)
        cleaned = " ".join(cleaned.split()) or text
        return cleaned, wave_counts

    def _build_wave_agent_ids(self) -> list[str]:
        count = self._wave_counts[self._wave_no - 1]
        if len(self._wave_counts) == 1:
            return [f"mock_{self._turn_no}_{i}" for i in range(1, count + 1)]
        return [f"mock_{self._turn_no}_w{self._wave_no}_{i}" for i in range(1, count + 1)]

    def _build_spawn_tool_calls(self, agent_ids: list[str]) -> list[ToolCall]:
        calls = []
        for index, agent_id in enumerate(sorted(agent_ids), start=1):
            tool_id = f"spawn_{agent_id}"
            command = " ".join([
                _venv_python(),
                str(testing_skills_dir() / "subagent-mock" / "run.py"),
                "--id",
                shlex.quote(agent_id),
                "--parent-session",
                self._session,
                "--task",
                shlex.quote(self._user_text),
                "--index",
                str(index),
                "--max-turns",
                str(self.config.mock_turns),
                "--sleep-ms",
                str(self.config.mock_sleep_ms),
            ])
            calls.append(ToolCall(id=tool_id, name="process_spawn", input={"command": command}))
        return calls

    def _build_wave_agent_ids_for(self, wave_no: int) -> list[str]:
        count = self._wave_counts[wave_no - 1]
        if len(self._wave_counts) == 1:
            return [f"mock_{self._turn_no}_{i}" for i in range(1, count + 1)]
        return [f"mock_{self._turn_no}_w{wave_no}_{i}" for i in range(1, count + 1)]

    def _build_aggregation(self) -> str:
        total_expected = sum(self._wave_counts)
        ordered_ids = sorted(self._all_results)
        lines = [
            "mock driver: aggregated subagent results",
            f"request: {self._user_text}",
            f"waves: {len(self._wave_counts)}",
            f"received: {len(self._all_results)}/{total_expected}",
        ]
        for agent_id in ordered_ids:
            lines.append(f"- {agent_id}: {self._all_results[agent_id]}")
        missing = []
        for wave_no in range(1, len(self._wave_counts) + 1):
            for agent_id in self._build_wave_agent_ids_for(wave_no):
                if agent_id not in self._all_results:
                    missing.append(agent_id)
        if missing:
            lines.append(f"missing: {', '.join(sorted(missing))}")
        return "\n".join(lines) + "\n"

    def add_user_text(self, text: str):
        if self._active and "<subagent_result " in text:
            for part in text.split("\n\n---\n\n"):
                match = re.match(r'<subagent_result id="([^"]+)">\n(.*)\n</subagent_result>', part, re.DOTALL)
                if match:
                    agent_id, result_text = match.group(1), match.group(2)
                    self._all_results[agent_id] = result_text
                    self._output_buffer.append(f"mock driver: received result from {agent_id}\n")
            if self._wave_no < len(self._wave_counts):
                self._wave_no += 1
                self._output_buffer.append(
                    f"mock driver: wave {self._wave_no - 1} complete, launching next wave\n"
                )
                self._state = _MockState.PROCESS_SPAWN
            else:
                self._state = _MockState.FINISH
            return

        if self._active:
            self._output_buffer.append("mock driver: busy, ignoring concurrent user message\n")
            self._state = _MockState.BUSY_REPLY
            return

        self._turn_no += 1
        self._active = True
        self._all_results = {}
        self._output_buffer = []
        self._user_text, self._wave_counts = self._parse_request(text)
        self._wave_no = 1
        self._state = _MockState.PROCESS_SPAWN

    def add_tool_results(self, results: list[ToolResult]):
        for result in results:
            self._output_buffer.append(f"mock driver: {result.tool_use_id} -> {result.output}\n")
        self._state = _MockState.ENTER_COLLECTION

    def generate(self, on_text_delta) -> TurnOutcome:
        if self._state == _MockState.BUSY_REPLY:
            self._state = _MockState.ENTER_COLLECTION
            return TurnOutcome(final_text="", tool_calls=[])

        if self._state == _MockState.PROCESS_SPAWN:
            agent_ids = self._build_wave_agent_ids()
            self._current_wave_ids = agent_ids
            count = self._wave_counts[self._wave_no - 1]
            header = (
                f"mock driver: spawning wave {self._wave_no}/{len(self._wave_counts)} "
                f"with {count} mock subagents for request: {self._user_text}\n"
            )
            on_text_delta(header)
            self._output_buffer.append(header)
            calls = self._build_spawn_tool_calls(agent_ids)
            self._state = _MockState.TOOLS_SENT
            return TurnOutcome(final_text=header, tool_calls=calls)

        if self._state == _MockState.ENTER_COLLECTION:
            self._state = _MockState.IDLE
            return TurnOutcome(final_text="", tool_calls=[])

        if self._state == _MockState.FINISH:
            aggregation = self._build_aggregation()
            buffered = "".join(self._output_buffer)
            self._output_buffer = []
            self._active = False
            self._wave_no = 0
            self._wave_counts = []
            full_text = buffered + "\n" + aggregation
            return TurnOutcome(final_text=full_text, tool_calls=[])

        return TurnOutcome(final_text="", tool_calls=[])
