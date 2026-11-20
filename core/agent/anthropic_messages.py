"""Anthropic Messages API model for the Agents SDK."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

import anthropic
from agents import (
    AgentOutputSchemaBase,
    FunctionTool,
    Handoff,
    Model,
    ModelBehaviorError,
    ModelResponse,
    ModelRetryAdvice,
    ModelRetryAdviceRequest,
    ModelRetryNormalizedError,
    ModelSettings,
    ModelTracing,
    TResponseInputItem,
    Tool,
    UserError,
)
from agents.items import TResponseStreamEvent
from openai.types.responses.response_prompt_param import ResponsePromptParam

from config import ModelApi, ModelConfig, ModelEndpoint
from core.agent.model_items import (
    SYSTEM_ROLES,
    ContentPart,
    TextPart,
    content_parts,
    extra_body,
    extra_headers,
    function_tools,
    input_items,
    item_type,
    own_reasoning,
    tool_arguments,
)
from core.agent.response_stream import ResponseStreamBuilder, collect_model_response, response_usage


_API = ModelApi.ANTHROPIC_MESSAGES.value
_CACHE_CONTROL = {"type": "ephemeral"}
_INVALID_TOOL_ID_CHARACTERS = re.compile(r"[^a-zA-Z0-9_-]")
_MAX_TOOL_ID_LENGTH = 64


class AnthropicMessagesModel(Model):
    def __init__(self, endpoint: ModelEndpoint, model: ModelConfig, *, connect_timeout: float) -> None:
        self._model = model.id
        self._provider_data = {"api": _API, "model": model.id}
        self._thinking = _thinking_params(model)
        self._client = anthropic.AsyncAnthropic(
            api_key=endpoint.api_key or "unused",
            base_url=endpoint.base_url,
            default_headers=endpoint.headers,
            timeout=anthropic.Timeout(
                connect=connect_timeout,
                read=anthropic.DEFAULT_TIMEOUT.read,
                write=anthropic.DEFAULT_TIMEOUT.write,
                pool=anthropic.DEFAULT_TIMEOUT.pool,
            ),
            # Agent runner retries are replay-aware; do not multiply them with
            # transport retries hidden inside the provider client.
            max_retries=0,
        )

    def get_retry_advice(self, request: ModelRetryAdviceRequest) -> ModelRetryAdvice | None:
        error = request.error
        if isinstance(error, anthropic.APITimeoutError):
            return ModelRetryAdvice(normalized=ModelRetryNormalizedError(is_network_error=True, is_timeout=True))
        if isinstance(error, anthropic.APIConnectionError):
            return ModelRetryAdvice(normalized=ModelRetryNormalizedError(is_network_error=True))
        if isinstance(error, anthropic.APIStatusError):
            return ModelRetryAdvice(
                retry_after=_retry_after_seconds(error.response.headers.get("retry-after")),
                normalized=ModelRetryNormalizedError(status_code=error.status_code),
            )
        return None

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        return await collect_model_response(self.stream_response(
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        ))

    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        request = self._build_request(
            system_instructions,
            input,
            model_settings,
            function_tools(tools, handoffs=handoffs, output_schema=output_schema, prompt=prompt, api=_API),
        )
        builder = ResponseStreamBuilder(
            model=self._model,
            model_settings=model_settings,
            provider_data=self._provider_data,
        )
        input_tokens = cached_tokens = cache_write_tokens = output_tokens = thinking_tokens = 0
        stream = await self._client.messages.create(
            **request,
            stream=True,
            extra_headers=extra_headers(model_settings),
            extra_body={**_sampling_params(model_settings), **extra_body(model_settings)},
        )
        async with stream:
            for started in builder.start():
                yield started
            async for event in stream:
                events: list[TResponseStreamEvent] = []
                if event.type == "message_start":
                    usage = event.message.usage
                    cached_tokens = usage.cache_read_input_tokens or 0
                    cache_write_tokens = usage.cache_creation_input_tokens or 0
                    input_tokens = usage.input_tokens + cached_tokens + cache_write_tokens
                    output_tokens = usage.output_tokens
                elif event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "text":
                        events = builder.text_delta(block.text)
                    elif block.type == "thinking":
                        events = builder.reasoning_delta(block.thinking)
                        if block.signature:
                            events.extend(builder.reasoning_signature(block.signature))
                    elif block.type == "tool_use":
                        events = builder.function_call_start(call_id=block.id, name=block.name)
                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        events = builder.text_delta(delta.text)
                    elif delta.type == "thinking_delta":
                        events = builder.reasoning_delta(delta.thinking)
                    elif delta.type == "signature_delta":
                        events = builder.reasoning_signature(delta.signature)
                    elif delta.type == "input_json_delta":
                        events = builder.function_call_arguments_delta(delta.partial_json)
                elif event.type == "content_block_stop":
                    events = builder.end_item()
                elif event.type == "message_delta":
                    if event.delta.stop_reason == "refusal":
                        raise ModelBehaviorError("The model refused to complete the request")
                    output_tokens = event.usage.output_tokens
                    if event.usage.output_tokens_details is not None:
                        thinking_tokens = event.usage.output_tokens_details.thinking_tokens or 0
                for emitted in events:
                    yield emitted
        for completed in builder.complete(response_usage(
            input_tokens=input_tokens,
            cached_tokens=cached_tokens,
            cache_write_tokens=cache_write_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=thinking_tokens,
        )):
            yield completed

    async def close(self) -> None:
        await self._client.close()

    def _build_request(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[FunctionTool],
    ) -> dict[str, Any]:
        if model_settings.max_tokens is None:
            raise UserError(f"{_API} requires max_tokens")
        system, messages = self._convert_input(system_instructions, input)
        request: dict[str, Any] = {
            "model": self._model,
            "max_tokens": model_settings.max_tokens,
            "messages": messages,
            **self._thinking,
        }
        if system:
            request["system"] = system
        if tools:
            request["tools"] = [
                {"name": tool.name, "description": tool.description, "input_schema": tool.params_json_schema}
                for tool in tools
            ]
            request["tools"][-1]["cache_control"] = _CACHE_CONTROL
            request["tool_choice"] = _tool_choice(model_settings)
        return request

    def _convert_input(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        # The instructions are the stable prefix of every request, so they end a cache segment.
        system: list[dict[str, Any]] = []
        if system_instructions and system_instructions.strip():
            system.append({"type": "text", "text": system_instructions, "cache_control": _CACHE_CONTROL})
        messages: list[dict[str, Any]] = []
        replay_thinking = self._thinking.get("thinking", {}).get("type") in ("adaptive", "enabled")

        def append(role: str, blocks: list[dict[str, Any]]) -> None:
            if not blocks:
                return
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"].extend(blocks)
            else:
                messages.append({"role": role, "content": blocks})

        for item in input_items(input):
            kind = item_type(item)
            if kind == "message":
                role = item.get("role")
                blocks = [_content_block(part) for part in content_parts(item.get("content"), api=_API)]
                if role in SYSTEM_ROLES:
                    system.extend(blocks)
                elif role in ("user", "assistant"):
                    append(role, blocks)
                else:
                    raise UserError(f"Unsupported message role for {_API}: {role!r}")
            elif kind == "reasoning":
                reasoning = own_reasoning(item, self._provider_data) if replay_thinking else None
                if reasoning is not None:
                    append("assistant", [{"type": "thinking", "thinking": reasoning[0], "signature": reasoning[1]}])
            elif kind == "function_call":
                append("assistant", [{
                    "type": "tool_use",
                    "id": _tool_id(item["call_id"]),
                    "name": item["name"],
                    "input": tool_arguments(item),
                }])
            elif kind == "function_call_output":
                result: dict[str, Any] = {"type": "tool_result", "tool_use_id": _tool_id(item["call_id"])}
                output = [_content_block(part) for part in content_parts(item.get("output"), api=_API)]
                if output:
                    result["content"] = output
                append("user", [result])
            else:
                raise UserError(f"Unsupported input item for {_API}: {kind!r}")

        # Marking the last block caches the whole conversation so far for the next request.
        if messages and messages[-1]["role"] == "user":
            messages[-1]["content"][-1]["cache_control"] = _CACHE_CONTROL
        return system, messages


def _thinking_params(model: ModelConfig) -> dict[str, Any]:
    """Thinking is sized by a token budget on older models and by an effort level on adaptive ones."""
    if model.thinking_budget_tokens is not None:
        return {"thinking": {
            "type": "enabled",
            "budget_tokens": model.thinking_budget_tokens,
            "display": "summarized",
        }}
    if model.reasoning_effort is None:
        return {}
    if model.reasoning_effort == "none":
        return {"thinking": {"type": "disabled"}}
    return {
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": model.reasoning_effort},
    }


def _sampling_params(model_settings: ModelSettings) -> dict[str, Any]:
    """Sampling parameters go in the raw body: the SDK no longer names them, endpoints still take them."""
    params: dict[str, Any] = {}
    if model_settings.temperature is not None:
        params["temperature"] = model_settings.temperature
    if model_settings.top_p is not None:
        params["top_p"] = model_settings.top_p
    return params


def _tool_choice(model_settings: ModelSettings) -> dict[str, Any]:
    choice = model_settings.tool_choice
    if choice == "none":
        return {"type": "none"}
    if choice is None or choice == "auto":
        tool_choice: dict[str, Any] = {"type": "auto"}
    elif choice == "required":
        tool_choice = {"type": "any"}
    elif isinstance(choice, str):
        tool_choice = {"type": "tool", "name": choice}
    else:
        raise UserError(f"Unsupported tool choice for {_API}: {choice!r}")
    if model_settings.parallel_tool_calls is False:
        tool_choice["disable_parallel_tool_use"] = True
    return tool_choice


def _content_block(part: ContentPart) -> dict[str, Any]:
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if part.url:
        return {"type": "image", "source": {"type": "url", "url": part.url}}
    return {"type": "image", "source": {"type": "base64", "media_type": part.media_type, "data": part.data}}


def _tool_id(call_id: str) -> str:
    """Anthropic accepts only word characters and hyphens in tool call ids."""
    return _INVALID_TOOL_ID_CHARACTERS.sub("_", call_id)[:_MAX_TOOL_ID_LENGTH]


def _retry_after_seconds(value: str | None) -> float | None:
    try:
        seconds = float(value) if value is not None else None
    except ValueError:
        return None
    return seconds if seconds is not None and seconds >= 0 else None
