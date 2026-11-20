"""Google Generative AI (Gemini API) model for the Agents SDK."""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
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
from google import genai
from google.genai import errors, types
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


_API = ModelApi.GOOGLE_GENERATIVE_AI.value
_COMPLETE_FINISH_REASONS = frozenset({types.FinishReason.STOP, types.FinishReason.MAX_TOKENS})
_SIGNATURE = "thought_signature"
_CALL_ID = "function_call_id"


class GoogleGenerativeAIModel(Model):
    def __init__(self, endpoint: ModelEndpoint, model: ModelConfig, *, read_timeout: float) -> None:
        self._model = model.id
        self._provider_data = {"api": _API, "model": model.id}
        self._thinking = _thinking_config(model)
        # The SDK prefers aiohttp when it is installed; its own httpx client keeps one transport
        # everywhere and applies the timeout to each read instead of to the whole stream.
        self._http = httpx.AsyncClient()
        self._client = genai.Client(
            api_key=endpoint.api_key or "unused",
            http_options=types.HttpOptions(
                # The endpoint URL already ends in the API version.
                base_url=endpoint.base_url,
                api_version="",
                headers=endpoint.headers,
                timeout=int(read_timeout * 1000),
                httpx_async_client=self._http,
            ),
        )

    def get_retry_advice(self, request: ModelRetryAdviceRequest) -> ModelRetryAdvice | None:
        error = request.error
        if not isinstance(error, errors.APIError):
            return None
        return ModelRetryAdvice(normalized=ModelRetryNormalizedError(status_code=error.code))

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
        system, contents = self._convert_input(system_instructions, input)
        config = self._build_config(
            system,
            model_settings,
            function_tools(tools, handoffs=handoffs, output_schema=output_schema, prompt=prompt, api=_API),
        )
        builder = ResponseStreamBuilder(
            model=self._model,
            model_settings=model_settings,
            provider_data=self._provider_data,
        )
        usage: types.GenerateContentResponseUsageMetadata | None = None
        call_ids: set[str] = set()
        stream = await self._client.aio.models.generate_content_stream(
            model=self._model,
            contents=contents,
            config=config,
        )
        for started in builder.start():
            yield started
        async for chunk in stream:
            if chunk.prompt_feedback is not None and chunk.prompt_feedback.block_reason is not None:
                raise ModelBehaviorError(f"The request was blocked: {chunk.prompt_feedback.block_reason.value}")
            if chunk.usage_metadata is not None:
                usage = chunk.usage_metadata
            candidate = chunk.candidates[0] if chunk.candidates else None
            if candidate is None:
                continue
            for part in (candidate.content.parts or []) if candidate.content is not None else []:
                for emitted in _part_events(builder, part, call_ids):
                    yield emitted
            if candidate.finish_reason is not None and candidate.finish_reason not in _COMPLETE_FINISH_REASONS:
                raise ModelBehaviorError(f"The model stopped with {candidate.finish_reason.value}")
        if usage is None:
            raise ModelBehaviorError("The model stream ended without usage metadata")
        thinking_tokens = usage.thoughts_token_count or 0
        for completed in builder.complete(response_usage(
            input_tokens=usage.prompt_token_count or 0,
            cached_tokens=usage.cached_content_token_count or 0,
            cache_write_tokens=0,
            output_tokens=(usage.candidates_token_count or 0) + thinking_tokens,
            reasoning_tokens=thinking_tokens,
        )):
            yield completed

    async def close(self) -> None:
        await self._client.aio.aclose()
        self._client.close()
        await self._http.aclose()

    def _build_config(
        self,
        system: list[str],
        model_settings: ModelSettings,
        tools: list[FunctionTool],
    ) -> types.GenerateContentConfig:
        config = types.GenerateContentConfig(
            system_instruction="\n\n".join(system) or None,
            max_output_tokens=model_settings.max_tokens,
            temperature=model_settings.temperature,
            top_p=model_settings.top_p,
            thinking_config=self._thinking,
        )
        if tools:
            config.tools = [types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name=tool.name,
                    description=tool.description,
                    parameters_json_schema=tool.params_json_schema,
                )
                for tool in tools
            ])]
            config.tool_config = _tool_config(model_settings)
        headers, body = extra_headers(model_settings), extra_body(model_settings)
        if headers or body:
            config.http_options = types.HttpOptions(headers=headers or None, extra_body=body or None)
        return config

    def _convert_input(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
    ) -> tuple[list[str], list[types.Content]]:
        system = [system_instructions] if system_instructions and system_instructions.strip() else []
        contents: list[types.Content] = []
        # call id -> (function name, id the model issued for the call, if any)
        calls: dict[str, tuple[str, str | None]] = {}

        def append(role: str, parts: list[types.Part]) -> None:
            if not parts:
                return
            if contents and contents[-1].role == role and contents[-1].parts is not None:
                contents[-1].parts.extend(parts)
            else:
                contents.append(types.Content(role=role, parts=parts))

        for item in input_items(input):
            kind = item_type(item)
            own = self._own_data(item)
            if kind == "message":
                role = item.get("role")
                parts = content_parts(item.get("content"), api=_API)
                if role in SYSTEM_ROLES:
                    system.extend(part.text for part in parts if isinstance(part, TextPart))
                elif role == "user":
                    append("user", [_content_part(part) for part in parts])
                elif role == "assistant":
                    model_parts = [_content_part(part) for part in parts]
                    if model_parts and _SIGNATURE in own:
                        model_parts[-1].thought_signature = _decode_signature(own[_SIGNATURE])
                    append("model", model_parts)
                else:
                    raise UserError(f"Unsupported message role for {_API}: {role!r}")
            elif kind == "reasoning":
                reasoning = own_reasoning(item, self._provider_data)
                if reasoning is not None:
                    append("model", [types.Part(
                        text=reasoning[0],
                        thought=True,
                        thought_signature=_decode_signature(reasoning[1]),
                    )])
            elif kind == "function_call":
                calls[item["call_id"]] = (item["name"], own.get(_CALL_ID))
                append("model", [types.Part(
                    function_call=types.FunctionCall(
                        id=own.get(_CALL_ID),
                        name=item["name"],
                        args=tool_arguments(item),
                    ),
                    thought_signature=_decode_signature(own[_SIGNATURE]) if _SIGNATURE in own else None,
                )])
            elif kind == "function_call_output":
                if item["call_id"] not in calls:
                    raise UserError(f"Tool output {item['call_id']!r} has no matching tool call")
                name, issued_id = calls[item["call_id"]]
                parts = content_parts(item.get("output"), api=_API)
                if not all(isinstance(part, TextPart) for part in parts):
                    raise UserError(f"Images in tool output are not supported for {_API}")
                append("user", [types.Part(function_response=types.FunctionResponse(
                    id=issued_id,
                    name=name,
                    response={"output": "".join(part.text for part in parts if isinstance(part, TextPart))},
                ))])
            else:
                raise UserError(f"Unsupported input item for {_API}: {kind!r}")
        return system, contents

    def _own_data(self, item: dict[str, Any]) -> dict[str, Any]:
        """Provider data of an item this exact protocol and model produced, else nothing."""
        data = item.get("provider_data") or {}
        if any(data.get(key) != value for key, value in self._provider_data.items()):
            return {}
        return data


def _part_events(
    builder: ResponseStreamBuilder,
    part: types.Part,
    call_ids: set[str],
) -> list[TResponseStreamEvent]:
    signature = base64.b64encode(part.thought_signature).decode("ascii") if part.thought_signature else ""
    if part.function_call is not None:
        call = part.function_call
        # Older models issue no call id, and an id may repeat within one response.
        call_id = call.id if call.id and call.id not in call_ids else f"call_{uuid.uuid4().hex}"
        call_ids.add(call_id)
        item_data = {_SIGNATURE: signature} if signature else {}
        if call.id:
            item_data[_CALL_ID] = call.id
        return [
            *builder.function_call_start(call_id=call_id, name=call.name or "", item_data=item_data),
            *builder.function_call_arguments_delta(json.dumps(call.args or {}, ensure_ascii=False)),
            *builder.end_item(),
        ]
    if part.thought:
        events = builder.reasoning_delta(part.text or "")
        if signature:
            events.extend(builder.reasoning_signature(signature))
        return events
    if part.text or signature:
        return builder.text_delta(part.text or "", item_data={_SIGNATURE: signature} if signature else None)
    return []


def _thinking_config(model: ModelConfig) -> types.ThinkingConfig | None:
    """Thinking is sized by a token budget on older models and by a level on newer ones."""
    if model.thinking_budget_tokens is not None:
        return types.ThinkingConfig(
            include_thoughts=model.thinking_budget_tokens > 0,
            thinking_budget=model.thinking_budget_tokens,
        )
    if model.reasoning_effort is None:
        return None
    if model.reasoning_effort == "none":
        return types.ThinkingConfig(thinking_budget=0)
    return types.ThinkingConfig(
        include_thoughts=True,
        thinking_level=types.ThinkingLevel(model.reasoning_effort.upper()),
    )


def _tool_config(model_settings: ModelSettings) -> types.ToolConfig | None:
    choice = model_settings.tool_choice
    if choice is None or choice == "auto":
        return None
    if choice == "none":
        calling = types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.NONE)
    elif choice == "required":
        calling = types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.ANY)
    elif isinstance(choice, str):
        calling = types.FunctionCallingConfig(
            mode=types.FunctionCallingConfigMode.ANY,
            allowed_function_names=[choice],
        )
    else:
        raise UserError(f"Unsupported tool choice for {_API}: {choice!r}")
    return types.ToolConfig(function_calling_config=calling)


def _content_part(part: ContentPart) -> types.Part:
    if isinstance(part, TextPart):
        return types.Part(text=part.text)
    if part.url:
        raise UserError(f"Image URLs are not supported for {_API}; send the image inline")
    return types.Part(inline_data=types.Blob(mime_type=part.media_type, data=base64.b64decode(part.data)))


def _decode_signature(signature: str) -> bytes:
    try:
        return base64.b64decode(signature, validate=True)
    except binascii.Error as exc:
        raise UserError("Stored thought signature is not valid base64") from exc
