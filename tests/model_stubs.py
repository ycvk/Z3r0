"""Scripted model endpoints for the four wire protocols."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web
from anthropic import types as anthropic_types
from google.genai import types as google_types
from openai.types import responses as openai_responses
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails
from pydantic import BaseModel

from config import ModelApi, ModelConfig, ProviderConfig, get_config
from core.agent.constants import DEFAULT_AGENT_CODE


STUB_PROVIDER_ID = "stub"
STUB_MODEL_ID = "stub-model"

_ROUTES = {
    ModelApi.OPENAI_COMPLETIONS: "/v1/chat/completions",
    ModelApi.OPENAI_RESPONSES: "/v1/responses",
    ModelApi.ANTHROPIC_MESSAGES: "/v1/messages",
    ModelApi.GOOGLE_GENERATIVE_AI: "/v1beta/models/{model}",
}
# Base URL suffix a provider endpoint is configured with for each protocol.
_BASE_PATHS = {
    ModelApi.OPENAI_COMPLETIONS: "/v1",
    ModelApi.OPENAI_RESPONSES: "/v1",
    ModelApi.ANTHROPIC_MESSAGES: "",
    ModelApi.GOOGLE_GENERATIVE_AI: "/v1beta",
}


@dataclass(frozen=True)
class StubError:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class ModelStub:
    """A model endpoint that answers each request with the next scripted stream."""

    api: ModelApi
    base_url: str = ""
    requests: list[dict[str, Any]] = field(default_factory=list)
    headers: list[dict[str, str]] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    _responses: list[bytes | StubError] = field(default_factory=list)

    def script(self, *responses: bytes | StubError) -> None:
        """Queue responses: bytes are sent as an event stream, a StubError as a JSON error."""
        self._responses.extend(responses)

    async def handle(self, request: web.Request) -> web.StreamResponse:
        self.requests.append(await request.json())
        self.headers.append({name.lower(): value for name, value in request.headers.items()})
        self.paths.append(request.path_qs)
        if not self._responses:
            return web.json_response({"error": {"message": "no scripted response left"}}, status=500)
        scripted = self._responses.pop(0)
        if isinstance(scripted, StubError):
            return web.json_response(scripted.body, status=scripted.status, headers=scripted.headers)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(scripted)
        await response.write_eof()
        return response


@asynccontextmanager
async def model_stub(api: ModelApi) -> AsyncIterator[ModelStub]:
    stub = ModelStub(api=api)
    app = web.Application()
    app.router.add_post(_ROUTES[api], stub.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    stub.base_url = f"http://127.0.0.1:{runner.addresses[0][1]}{_BASE_PATHS[api]}"
    try:
        yield stub
    finally:
        await runner.cleanup()


def stub_provider(stub: ModelStub, **fields: Any) -> ProviderConfig:
    return ProviderConfig(name="Stub", api_key="stub-key", endpoints={stub.api: stub.base_url}, **fields)


def stub_model(stub: ModelStub, **fields: Any) -> ModelConfig:
    defaults: dict[str, Any] = {"context_window": 200_000}
    if stub.api is ModelApi.ANTHROPIC_MESSAGES:
        defaults["max_tokens"] = 4096
    return ModelConfig(provider=STUB_PROVIDER_ID, api=stub.api, id=STUB_MODEL_ID, **{**defaults, **fields})


def use_stub_for_default_agent(stub: ModelStub, **model_fields: Any) -> None:
    """Point the default agent of the shared config at the stub."""
    cfg = get_config()
    cfg.providers = {**cfg.providers, STUB_PROVIDER_ID: stub_provider(stub)}
    cfg.agents[DEFAULT_AGENT_CODE].model = stub_model(stub, **model_fields)


def text_stream(api: ModelApi, text: str, *, input_tokens: int, output_tokens: int) -> bytes:
    """A complete response of plain text in the protocol's own streaming format."""
    if api is ModelApi.OPENAI_COMPLETIONS:
        return openai_chat_stream(
            {"choices": [{"index": 0, "delta": {"role": "assistant", "content": text}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            }},
        )
    if api is ModelApi.OPENAI_RESPONSES:
        return openai_responses_stream(text, input_tokens=input_tokens, output_tokens=output_tokens)
    if api is ModelApi.ANTHROPIC_MESSAGES:
        return anthropic_stream(
            [anthropic_text_block(text)],
            stop_reason="end_turn",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    return google_stream(
        [google_types.Part(text=text)],
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def openai_chat_stream(*chunks: dict[str, Any]) -> bytes:
    lines = [
        _sse({"id": "chatcmpl-stub", "object": "chat.completion.chunk", "created": 0, "model": STUB_MODEL_ID, **chunk})
        for chunk in chunks
    ]
    return b"".join([*lines, b"data: [DONE]\n\n"])


def openai_responses_stream(text: str, *, input_tokens: int, output_tokens: int) -> bytes:
    message = openai_responses.ResponseOutputMessage(
        id="msg_stub",
        type="message",
        role="assistant",
        status="completed",
        content=[openai_responses.ResponseOutputText(type="output_text", text=text, annotations=[])],
    )
    response = openai_responses.Response(
        id="resp_stub",
        created_at=0,
        model=STUB_MODEL_ID,
        object="response",
        output=[],
        tool_choice="auto",
        tools=[],
        parallel_tool_calls=False,
    )
    completed = response.model_copy(update={
        "output": [message],
        "usage": openai_responses.ResponseUsage(
            input_tokens=input_tokens,
            input_tokens_details=InputTokensDetails(cached_tokens=0, cache_write_tokens=0),
            output_tokens=output_tokens,
            output_tokens_details=OutputTokensDetails(reasoning_tokens=0),
            total_tokens=input_tokens + output_tokens,
        ),
    })
    events: list[BaseModel] = [
        openai_responses.ResponseCreatedEvent(type="response.created", sequence_number=0, response=response),
        openai_responses.ResponseTextDeltaEvent(
            type="response.output_text.delta",
            sequence_number=1,
            item_id="msg_stub",
            output_index=0,
            content_index=0,
            delta=text,
            logprobs=[],
        ),
        openai_responses.ResponseTextDoneEvent(
            type="response.output_text.done",
            sequence_number=2,
            item_id="msg_stub",
            output_index=0,
            content_index=0,
            text=text,
            logprobs=[],
        ),
        openai_responses.ResponseOutputItemDoneEvent(
            type="response.output_item.done",
            sequence_number=3,
            output_index=0,
            item=message,
        ),
        openai_responses.ResponseCompletedEvent(type="response.completed", sequence_number=4, response=completed),
    ]
    return b"".join(_sse(event.model_dump(mode="json"), event=getattr(event, "type", "")) for event in events)


def anthropic_text_block(text: str) -> list[BaseModel]:
    return [
        anthropic_types.RawContentBlockStartEvent(
            type="content_block_start",
            index=0,
            content_block=anthropic_types.TextBlock(type="text", text="", citations=None),
        ),
        anthropic_types.RawContentBlockDeltaEvent(
            type="content_block_delta",
            index=0,
            delta=anthropic_types.TextDelta(type="text_delta", text=text),
        ),
        anthropic_types.RawContentBlockStopEvent(type="content_block_stop", index=0),
    ]


def anthropic_thinking_block(thinking: str, signature: str) -> list[BaseModel]:
    return [
        anthropic_types.RawContentBlockStartEvent(
            type="content_block_start",
            index=0,
            content_block=anthropic_types.ThinkingBlock(type="thinking", thinking="", signature=""),
        ),
        anthropic_types.RawContentBlockDeltaEvent(
            type="content_block_delta",
            index=0,
            delta=anthropic_types.ThinkingDelta(type="thinking_delta", thinking=thinking),
        ),
        anthropic_types.RawContentBlockDeltaEvent(
            type="content_block_delta",
            index=0,
            delta=anthropic_types.SignatureDelta(type="signature_delta", signature=signature),
        ),
        anthropic_types.RawContentBlockStopEvent(type="content_block_stop", index=0),
    ]


def anthropic_tool_use_block(call_id: str, name: str, argument_fragments: Sequence[str]) -> list[BaseModel]:
    return [
        anthropic_types.RawContentBlockStartEvent(
            type="content_block_start",
            index=0,
            content_block=anthropic_types.ToolUseBlock(type="tool_use", id=call_id, name=name, input={}),
        ),
        *(
            anthropic_types.RawContentBlockDeltaEvent(
                type="content_block_delta",
                index=0,
                delta=anthropic_types.InputJSONDelta(type="input_json_delta", partial_json=fragment),
            )
            for fragment in argument_fragments
        ),
        anthropic_types.RawContentBlockStopEvent(type="content_block_stop", index=0),
    ]


def anthropic_stream(
    blocks: Sequence[Sequence[BaseModel]],
    *,
    stop_reason: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> bytes:
    events: list[BaseModel] = [anthropic_types.RawMessageStartEvent(
        type="message_start",
        message=anthropic_types.Message(
            id="msg_stub",
            type="message",
            role="assistant",
            model=STUB_MODEL_ID,
            content=[],
            stop_reason=None,
            stop_sequence=None,
            usage=anthropic_types.Usage(
                input_tokens=input_tokens,
                output_tokens=1,
                cache_read_input_tokens=cache_read_tokens,
                cache_creation_input_tokens=cache_write_tokens,
            ),
        ),
    )]
    for index, block in enumerate(blocks):
        events.extend(event.model_copy(update={"index": index}) for event in block)
    events.append(anthropic_types.RawMessageDeltaEvent.model_validate({
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": output_tokens},
    }))
    events.append(anthropic_types.RawMessageStopEvent(type="message_stop"))
    return b"".join(
        _sse(event.model_dump(mode="json", exclude_none=True), event=getattr(event, "type", ""))
        for event in events
    )


def google_stream(
    parts: Sequence[google_types.Part],
    *,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    thinking_tokens: int = 0,
    finish_reason: google_types.FinishReason = google_types.FinishReason.STOP,
) -> bytes:
    """One chunk per part; the last chunk carries the finish reason and the usage."""
    chunks = [
        google_types.GenerateContentResponse(candidates=[google_types.Candidate(
            index=0,
            content=google_types.Content(role="model", parts=[part]),
        )])
        for part in parts
    ]
    chunks.append(google_types.GenerateContentResponse(
        candidates=[google_types.Candidate(
            index=0,
            content=google_types.Content(role="model", parts=[]),
            finish_reason=finish_reason,
        )],
        usage_metadata=google_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=input_tokens,
            cached_content_token_count=cached_tokens,
            candidates_token_count=output_tokens,
            thoughts_token_count=thinking_tokens,
            total_token_count=input_tokens + output_tokens + thinking_tokens,
        ),
    ))
    return b"".join(_sse(chunk.model_dump(mode="json", by_alias=True, exclude_none=True)) for chunk in chunks)


def _sse(payload: dict[str, Any], *, event: str = "") -> bytes:
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {json.dumps(payload)}\n\n".encode()
