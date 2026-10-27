"""Thin OpenAI-compatible client.

Only chat completions with optional function tools are used, so DeepSeek and
other compatible providers work without provider-specific SDKs.  Both buffered
and streaming completions return the same :class:`LLMResponse` shape.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI, OpenAIError

from code_reader.config import Settings


class LLMError(RuntimeError):
    """A user-facing model/API failure."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""
    parse_error: str | None = None


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_content: str = ""
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.api_key:
            raise LLMError("缺少模型 API Key")
        self.settings = settings
        self.thinking = settings.thinking
        self._is_deepseek = "deepseek" in settings.base_url.lower() or settings.model.lower().startswith(
            "deepseek"
        )
        self._client = OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
            max_retries=1,
        )

    # ------------------------------------------------------------------ requests

    def _request_kwargs(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
        max_tokens: int | None,
        temperature: float | None,
    ) -> dict[str, Any]:
        effective_max_tokens = max_tokens or self.settings.max_tokens
        if self.thinking == "enabled":
            # Thinking tokens share the completion budget.  A 4K cap often
            # yields finish_reason=length with empty content.
            effective_max_tokens = max(effective_max_tokens, 8192)
        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "max_tokens": effective_max_tokens,
            "temperature": self.settings.temperature if temperature is None else temperature,
        }
        if self._is_deepseek:
            kwargs["extra_body"] = {"thinking": {"type": self.thinking}}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return kwargs

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        kwargs = self._request_kwargs(
            messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        try:
            response = self._client.chat.completions.create(**kwargs)
        except OpenAIError as exc:
            raise LLMError(self._friendly_error(exc)) from exc
        except Exception as exc:  # network/proxy/SSL and other transport failures
            raise LLMError(f"模型调用失败：{type(exc).__name__}: {exc}") from exc

        if not response.choices:
            raise LLMError("模型没有返回候选结果")
        choice = response.choices[0]
        return self._message_response(
            choice.message,
            finish_reason=choice.finish_reason,
            usage=self._usage(response.usage),
        )

    def stream_complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        """Stream content deltas through ``on_text`` and return the full response."""

        kwargs = self._request_kwargs(
            messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        kwargs["stream"] = True
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_buffers: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage: dict[str, int] = {}

        try:
            stream = self._client.chat.completions.create(**kwargs)
            for chunk in stream:
                if getattr(chunk, "usage", None) is not None:
                    usage = self._usage(chunk.usage)
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                text = getattr(delta, "content", None)
                if isinstance(text, str) and text:
                    content_parts.append(text)
                    if on_text is not None:
                        on_text(text)
                reasoning = getattr(delta, "reasoning_content", None)
                if isinstance(reasoning, str) and reasoning:
                    reasoning_parts.append(reasoning)
                for raw_call in getattr(delta, "tool_calls", None) or []:
                    index = getattr(raw_call, "index", 0) or 0
                    buffer = tool_buffers.setdefault(
                        index,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    call_id = getattr(raw_call, "id", None)
                    if call_id and not buffer["id"]:
                        buffer["id"] = call_id
                    function = getattr(raw_call, "function", None)
                    if function is not None:
                        name = getattr(function, "name", None)
                        arguments = getattr(function, "arguments", None)
                        if isinstance(name, str) and name:
                            buffer["name"] += name
                        if isinstance(arguments, str) and arguments:
                            buffer["arguments"] += arguments
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
        except OpenAIError as exc:
            raise LLMError(self._friendly_error(exc)) from exc
        except Exception as exc:
            raise LLMError(f"模型调用失败：{type(exc).__name__}: {exc}") from exc

        tool_calls = [
            self._parse_tool_call(
                call_id=buffer["id"],
                name=buffer["name"],
                raw_arguments=buffer["arguments"] or "{}",
            )
            for _, buffer in sorted(tool_buffers.items())
        ]
        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            reasoning_content="".join(reasoning_parts),
            finish_reason=finish_reason,
            usage=usage,
        )

    # ------------------------------------------------------------------ parsing

    def _message_response(
        self,
        message: Any,
        *,
        finish_reason: str | None,
        usage: dict[str, int],
    ) -> LLMResponse:
        tool_calls: list[ToolCall] = []
        for raw_call in message.tool_calls or []:
            function = raw_call.function
            tool_calls.append(
                self._parse_tool_call(
                    call_id=raw_call.id,
                    name=function.name,
                    raw_arguments=function.arguments or "{}",
                )
            )
        return LLMResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            reasoning_content=getattr(message, "reasoning_content", "") or "",
            finish_reason=finish_reason,
            usage=usage,
        )

    @staticmethod
    def _parse_tool_call(call_id: str, name: str, raw_arguments: str) -> ToolCall:
        arguments: dict[str, Any] = {}
        parse_error: str | None = None
        try:
            parsed = json.loads(raw_arguments)
            if isinstance(parsed, dict):
                arguments = parsed
            else:
                parse_error = "工具参数不是 JSON object"
        except json.JSONDecodeError as exc:
            parse_error = f"工具参数 JSON 解析失败: {exc.msg}"
        return ToolCall(
            id=call_id,
            name=name,
            arguments=arguments,
            raw_arguments=raw_arguments,
            parse_error=parse_error,
        )

    @staticmethod
    def _usage(raw_usage: Any) -> dict[str, int]:
        if raw_usage is None:
            return {}
        return {
            "prompt_tokens": int(getattr(raw_usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(raw_usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(raw_usage, "total_tokens", 0) or 0),
        }

    # ------------------------------------------------------------------ mode

    @property
    def thinking_enabled(self) -> bool:
        return self.thinking == "enabled"

    def set_thinking(self, enabled: bool) -> None:
        """Toggle for the current session only; config remains the default."""

        self.thinking = "enabled" if enabled else "disabled"

    def _friendly_error(self, exc: OpenAIError) -> str:
        text = str(exc)
        lowered = text.lower()
        if "authentication" in lowered or "401" in text or "invalid api key" in lowered:
            return (
                "模型鉴权失败（401）。请检查 CODE_READER_API_KEY 是否正确，"
                f"以及 base_url={self.settings.base_url} 是否匹配该 Key。"
            )
        if "model" in lowered and ("not found" in lowered or "does not exist" in lowered):
            return (
                f"模型 `{self.settings.model}` 不可用。请检查 CODE_READER_MODEL，"
                "或换用该服务商支持的模型名。"
            )
        if "rate limit" in lowered or "429" in text:
            return "模型限流（429）。稍后重试，或换一个模型/提高额度。"
        if "timeout" in lowered:
            return "模型请求超时。检查网络/代理，或调大 CODE_READER_TIMEOUT。"
        return f"模型调用失败：{text[:800]}"


def assistant_message(response: LLMResponse) -> dict[str, Any]:
    """Convert a response into the assistant message expected by the API."""

    message: dict[str, Any] = {
        "role": "assistant",
        "content": response.content or None,
    }
    if response.reasoning_content:
        message["reasoning_content"] = response.reasoning_content
    if response.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.raw_arguments or json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in response.tool_calls
        ]
    return message
