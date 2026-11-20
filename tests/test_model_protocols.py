from __future__ import annotations

import base64
from typing import Any

import anthropic
import pytest
from agents import (
    Agent,
    ModelBehaviorError,
    ModelRetryAdviceRequest,
    ModelSettings,
    ModelTracing,
    RunResultStreaming,
    Runner,
    TResponseInputItem,
    function_tool,
    set_tracing_disabled,
)
from agents.stream_events import RawResponsesStreamEvent
from anthropic.types.message_create_params import MessageCreateParamsStreaming
from google.genai import errors as google_errors
from google.genai import types as google_types
from openai.types.responses.response_reasoning_text_delta_event import ResponseReasoningTextDeltaEvent
from pydantic import TypeAdapter

from config import ModelApi, ModelConfig, ProviderConfig, resolve_model_endpoint
from core.agent.models import build_model, build_model_settings, clamp_max_tokens
from tests.model_stubs import (
    STUB_MODEL_ID,
    STUB_PROVIDER_ID,
    ModelStub,
    StubError,
    anthropic_stream,
    anthropic_text_block,
    anthropic_thinking_block,
    anthropic_tool_use_block,
    google_stream,
    model_stub,
    stub_model,
    stub_provider,
    text_stream,
)


_INSTRUCTIONS = "You are a test agent."
_QUESTION = "Weather in Paris?"
_EPHEMERAL = {"type": "ephemeral"}
_ANTHROPIC_REQUEST = TypeAdapter(MessageCreateParamsStreaming)


@pytest.fixture(autouse=True)
def _no_tracing() -> None:
    set_tracing_disabled(True)


@function_tool
def lookup(city: str) -> str:
    """Look up the weather in a city."""
    return f"{city}: sunny"


async def _run(
    stub: ModelStub,
    model: ModelConfig,
    *,
    provider: ProviderConfig | None = None,
    input: str | list[TResponseInputItem] = _QUESTION,
    tools: bool = True,
) -> tuple[RunResultStreaming, list[Any]]:
    endpoint = resolve_model_endpoint({STUB_PROVIDER_ID: provider or stub_provider(stub)}, model)
    built = build_model(endpoint, model)
    agent = Agent(
        name="tester",
        instructions=_INSTRUCTIONS,
        model=built,
        model_settings=build_model_settings(model, parallel_tool_calls=False),
        tools=[lookup] if tools else [],
    )
    try:
        result = Runner.run_streamed(agent, input=input)
        events = [event async for event in result.stream_events()]
    finally:
        await built.close()
    return result, events


def _thinking_text(events: list[Any]) -> str:
    return "".join(
        event.data.delta
        for event in events
        if isinstance(event, RawResponsesStreamEvent) and isinstance(event.data, ResponseReasoningTextDeltaEvent)
    )


@pytest.mark.asyncio
async def test_anthropic_tool_loop_replays_signed_thinking_and_caches_the_prefix() -> None:
    async with model_stub(ModelApi.ANTHROPIC_MESSAGES) as stub:
        stub.script(
            anthropic_stream(
                [
                    anthropic_thinking_block("Need the weather.", "sig-abc"),
                    anthropic_tool_use_block("toolu_1", "lookup", ['{"city":', '"Paris"}']),
                ],
                stop_reason="tool_use",
                input_tokens=20,
                cache_read_tokens=100,
                cache_write_tokens=5,
                output_tokens=30,
            ),
            anthropic_stream(
                [anthropic_text_block("It is sunny in Paris.")],
                stop_reason="end_turn",
                input_tokens=40,
                output_tokens=9,
            ),
        )
        result, events = await _run(stub, stub_model(stub, reasoning_effort="high", max_tokens=4096))

    assert result.final_output == "It is sunny in Paris."
    assert _thinking_text(events) == "Need the weather."
    assert stub.paths == ["/v1/messages", "/v1/messages"]
    assert stub.headers[0]["authorization"] == "Bearer stub-key"

    first, second = stub.requests
    for request in stub.requests:
        _ANTHROPIC_REQUEST.validate_python(request, strict=True)
    assert first["model"] == STUB_MODEL_ID
    assert first["max_tokens"] == 4096
    assert first["stream"] is True
    assert first["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert first["output_config"] == {"effort": "high"}
    assert "temperature" not in first
    assert first["system"] == [{"type": "text", "text": _INSTRUCTIONS, "cache_control": _EPHEMERAL}]
    assert first["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert [tool["name"] for tool in first["tools"]] == ["lookup"]
    assert first["tools"][0]["description"] == "Look up the weather in a city."
    assert first["tools"][0]["input_schema"]["properties"]["city"]["type"] == "string"
    assert first["tools"][-1]["cache_control"] == _EPHEMERAL
    assert first["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": _QUESTION, "cache_control": _EPHEMERAL}]},
    ]

    assert second["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": _QUESTION}]},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "Need the weather.", "signature": "sig-abc"},
            {"type": "tool_use", "id": "toolu_1", "name": "lookup", "input": {"city": "Paris"}},
        ]},
        {"role": "user", "content": [{
            "type": "tool_result",
            "tool_use_id": "toolu_1",
            "content": [{"type": "text", "text": "Paris: sunny"}],
            "cache_control": _EPHEMERAL,
        }]},
    ]

    usage = result.context_wrapper.usage
    assert usage.requests == 2
    assert usage.input_tokens == (20 + 100 + 5) + 40
    assert usage.input_tokens_details.cached_tokens == 100
    assert usage.output_tokens == 30 + 9


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_fields", "thinking", "output_config"),
    [
        ({"thinking_budget_tokens": 2048}, {"type": "enabled", "budget_tokens": 2048, "display": "summarized"}, None),
        ({"reasoning_effort": "none"}, {"type": "disabled"}, None),
        ({"reasoning_effort": "max"}, {"type": "adaptive", "display": "summarized"}, {"effort": "max"}),
        ({}, None, None),
    ],
    ids=["budget", "disabled", "adaptive", "provider-default"],
)
async def test_anthropic_thinking_follows_the_model_config(
    model_fields: dict[str, Any],
    thinking: dict[str, Any] | None,
    output_config: dict[str, Any] | None,
) -> None:
    async with model_stub(ModelApi.ANTHROPIC_MESSAGES) as stub:
        stub.script(text_stream(stub.api, "ok", input_tokens=1, output_tokens=1))
        await _run(stub, stub_model(stub, **model_fields), tools=False)

    request = stub.requests[0]
    assert request.get("thinking") == thinking
    assert request.get("output_config") == output_config
    assert "tools" not in request and "tool_choice" not in request


@pytest.mark.asyncio
async def test_anthropic_replays_only_thinking_this_model_signed() -> None:
    def reasoning(model: str) -> TResponseInputItem:
        return {  # type: ignore[typeddict-unknown-key]
            "type": "reasoning",
            "id": "__fake_id__",
            "summary": [],
            "content": [{"type": "reasoning_text", "text": f"thought of {model}"}],
            "encrypted_content": f"sig-{model}",
            "provider_data": {"api": ModelApi.ANTHROPIC_MESSAGES.value, "model": model},
        }

    history: list[TResponseInputItem] = [
        {"type": "message", "role": "user", "content": _QUESTION},
        reasoning("other-model"),
        reasoning(STUB_MODEL_ID),
        {"type": "function_call", "call_id": "call:1", "name": "lookup", "arguments": '{"city": "Paris"}'},
        {"type": "function_call_output", "call_id": "call:1", "output": "Paris: sunny"},
    ]
    async with model_stub(ModelApi.ANTHROPIC_MESSAGES) as stub:
        stub.script(
            text_stream(stub.api, "ok", input_tokens=1, output_tokens=1),
            text_stream(stub.api, "ok", input_tokens=1, output_tokens=1),
        )
        await _run(stub, stub_model(stub, reasoning_effort="high"), input=history)
        await _run(stub, stub_model(stub), input=history)

    thinking_on, thinking_off = (request["messages"][1]["content"] for request in stub.requests)
    # Tool call ids are reduced to the characters Anthropic accepts.
    tool_use = {"type": "tool_use", "id": "call_1", "name": "lookup", "input": {"city": "Paris"}}
    assert thinking_on == [
        {"type": "thinking", "thinking": f"thought of {STUB_MODEL_ID}", "signature": f"sig-{STUB_MODEL_ID}"},
        tool_use,
    ]
    assert thinking_off == [tool_use]
    assert stub.requests[0]["messages"][2]["content"][0]["tool_use_id"] == "call_1"


@pytest.mark.asyncio
async def test_google_tool_loop_replays_thought_signatures() -> None:
    async with model_stub(ModelApi.GOOGLE_GENERATIVE_AI) as stub:
        stub.script(
            google_stream(
                [
                    google_types.Part(text="Checking the weather.", thought=True),
                    google_types.Part(
                        function_call=google_types.FunctionCall(id="fc_1", name="lookup", args={"city": "Paris"}),
                        thought_signature=b"sig-1",
                    ),
                ],
                input_tokens=50,
                cached_tokens=10,
                output_tokens=7,
                thinking_tokens=3,
            ),
            google_stream(
                [google_types.Part(text="It is sunny "), google_types.Part(text="in Paris.")],
                input_tokens=70,
                output_tokens=6,
            ),
        )
        result, events = await _run(stub, stub_model(stub, reasoning_effort="high", max_tokens=2048))

    assert result.final_output == "It is sunny in Paris."
    assert _thinking_text(events) == "Checking the weather."
    assert stub.paths == [f"/v1beta/models/{STUB_MODEL_ID}:streamGenerateContent?alt=sse"] * 2
    assert stub.headers[0]["x-goog-api-key"] == "stub-key"

    first, second = stub.requests
    assert first["contents"] == [{"role": "user", "parts": [{"text": _QUESTION}]}]
    assert first["systemInstruction"]["parts"] == [{"text": _INSTRUCTIONS}]
    assert first["generationConfig"]["maxOutputTokens"] == 2048
    assert first["generationConfig"]["thinkingConfig"] == {"include_thoughts": True, "thinking_level": "HIGH"}
    declaration = first["tools"][0]["functionDeclarations"][0]
    assert declaration["name"] == "lookup"
    assert declaration["description"] == "Look up the weather in a city."
    assert declaration["parameters_json_schema"]["properties"]["city"]["type"] == "string"
    assert "toolConfig" not in first

    signature = base64.b64encode(b"sig-1").decode()
    assert second["contents"] == [
        {"role": "user", "parts": [{"text": _QUESTION}]},
        {"role": "model", "parts": [{
            "functionCall": {"id": "fc_1", "name": "lookup", "args": {"city": "Paris"}},
            "thoughtSignature": signature,
        }]},
        {"role": "user", "parts": [{
            "functionResponse": {"id": "fc_1", "name": "lookup", "response": {"output": "Paris: sunny"}},
        }]},
    ]

    usage = result.context_wrapper.usage
    assert usage.requests == 2
    assert usage.input_tokens == 50 + 70
    assert usage.input_tokens_details.cached_tokens == 10
    assert usage.output_tokens == (7 + 3) + 6
    assert usage.output_tokens_details.reasoning_tokens == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model_fields", "thinking_config"),
    [
        ({"thinking_budget_tokens": 1024}, {"include_thoughts": True, "thinking_budget": 1024}),
        ({"thinking_budget_tokens": 0}, {"include_thoughts": False, "thinking_budget": 0}),
        ({"reasoning_effort": "none"}, {"thinking_budget": 0}),
        ({"reasoning_effort": "minimal"}, {"include_thoughts": True, "thinking_level": "MINIMAL"}),
        ({}, None),
    ],
    ids=["budget", "zero-budget", "disabled", "level", "provider-default"],
)
async def test_google_thinking_follows_the_model_config(
    model_fields: dict[str, Any],
    thinking_config: dict[str, Any] | None,
) -> None:
    async with model_stub(ModelApi.GOOGLE_GENERATIVE_AI) as stub:
        stub.script(text_stream(stub.api, "ok", input_tokens=1, output_tokens=1))
        await _run(stub, stub_model(stub, **model_fields), tools=False)

    request = stub.requests[0]
    assert request.get("generationConfig", {}).get("thinkingConfig") == thinking_config
    assert "tools" not in request


@pytest.mark.asyncio
async def test_google_sends_images_inline_and_generates_missing_call_ids() -> None:
    question: list[TResponseInputItem] = [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "What is this?"},
        {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8=", "detail": "auto"},
    ]}]
    call = google_types.Part(function_call=google_types.FunctionCall(name="lookup", args={"city": "Paris"}))
    async with model_stub(ModelApi.GOOGLE_GENERATIVE_AI) as stub:
        stub.script(
            google_stream([call], input_tokens=1, output_tokens=1),
            google_stream([google_types.Part(text="done")], input_tokens=1, output_tokens=1),
        )
        result, _ = await _run(stub, stub_model(stub), input=question)

    assert result.final_output == "done"
    assert stub.requests[0]["contents"][0]["parts"] == [
        {"text": "What is this?"},
        {"inlineData": {"mimeType": "image/png", "data": "aGVsbG8="}},
    ]
    # A model that issues no call id gets one locally, and nothing is sent back as its id.
    assert stub.requests[1]["contents"][1:] == [
        {"role": "model", "parts": [{"functionCall": {"name": "lookup", "args": {"city": "Paris"}}}]},
        {"role": "user", "parts": [{"functionResponse": {"name": "lookup", "response": {"output": "Paris: sunny"}}}]},
    ]


@pytest.mark.asyncio
async def test_google_blocked_response_fails_the_run() -> None:
    async with model_stub(ModelApi.GOOGLE_GENERATIVE_AI) as stub:
        stub.script(google_stream([], input_tokens=1, output_tokens=0, finish_reason=google_types.FinishReason.SAFETY))
        with pytest.raises(ModelBehaviorError, match="SAFETY"):
            await _run(stub, stub_model(stub), tools=False)


@pytest.mark.asyncio
async def test_anthropic_refusal_fails_the_run() -> None:
    async with model_stub(ModelApi.ANTHROPIC_MESSAGES) as stub:
        stub.script(anthropic_stream([], stop_reason="refusal", input_tokens=1, output_tokens=0))
        with pytest.raises(ModelBehaviorError, match="refused"):
            await _run(stub, stub_model(stub), tools=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("api", list(ModelApi), ids=lambda api: api.value)
async def test_every_protocol_sends_the_configured_request_parameters(api: ModelApi) -> None:
    effort = "low"
    async with model_stub(api) as stub:
        stub.script(text_stream(api, "ok", input_tokens=1, output_tokens=1))
        model = stub_model(
            stub,
            max_tokens=1000,
            temperature=0.2,
            top_p=0.9,
            reasoning_effort=effort,
            extra_headers={"X-Model-Header": "model"},
            extra_body={"metadata": {"trace": "t-1"}},
        )
        provider = stub_provider(stub, headers={"X-Provider-Header": "provider"})
        result, _ = await _run(stub, model, provider=provider, tools=False)

    assert result.final_output == "ok"
    request, headers = stub.requests[0], stub.headers[0]
    assert headers["x-provider-header"] == "provider"
    assert headers["x-model-header"] == "model"
    assert request["metadata"] == {"trace": "t-1"}
    if api is ModelApi.OPENAI_COMPLETIONS:
        sent = (request["max_tokens"], request["temperature"], request["top_p"], request["reasoning_effort"])
    elif api is ModelApi.OPENAI_RESPONSES:
        sent = (request["max_output_tokens"], request["temperature"], request["top_p"], request["reasoning"]["effort"])
    elif api is ModelApi.ANTHROPIC_MESSAGES:
        sent = (request["max_tokens"], request["temperature"], request["top_p"], request["output_config"]["effort"])
    else:
        config = request["generationConfig"]
        sent = (
            config["maxOutputTokens"],
            config["temperature"],
            config["topP"],
            config["thinkingConfig"]["thinking_level"].lower(),
        )
    assert sent == (1000, 0.2, 0.9, effort)


@pytest.mark.asyncio
@pytest.mark.parametrize("api", list(ModelApi), ids=lambda api: api.value)
async def test_output_cap_is_fitted_into_the_context_window(api: ModelApi) -> None:
    async with model_stub(api) as stub:
        stub.script(
            text_stream(api, "ok", input_tokens=1, output_tokens=1),
            text_stream(api, "ok", input_tokens=1, output_tokens=1),
        )
        await _run(stub, stub_model(stub, context_window=20_000, max_tokens=64_000), tools=False)
        await _run(stub, stub_model(stub, context_window=0, max_tokens=64_000), tools=False)

    def output_cap(request: dict[str, Any]) -> int:
        if api is ModelApi.OPENAI_RESPONSES:
            return request["max_output_tokens"]
        if api is ModelApi.GOOGLE_GENERATIVE_AI:
            return request["generationConfig"]["maxOutputTokens"]
        return request["max_tokens"]

    fitted, unbounded = (output_cap(request) for request in stub.requests)
    # The request holds a few dozen input tokens, so the cap is the window minus those and the margin.
    assert 15_000 < fitted < 20_000 - 4096
    assert unbounded == 64_000


def test_output_cap_keeps_a_margin_for_the_provider_counting_more_input() -> None:
    assert clamp_max_tokens(128_000, context_window=1_000_000, input_tokens=100_000) == 128_000
    # 15% of the input is kept free while that still leaves 32768 tokens of output.
    assert clamp_max_tokens(128_000, context_window=200_000, input_tokens=100_000) == 85_000
    # Past that point only the fixed margin is kept, and output stops at 32768.
    assert clamp_max_tokens(128_000, context_window=200_000, input_tokens=150_000) == 32_768
    assert clamp_max_tokens(128_000, context_window=200_000, input_tokens=180_000) == 200_000 - 180_000 - 4096
    assert clamp_max_tokens(128_000, context_window=200_000, input_tokens=199_000) == 1


@pytest.mark.asyncio
async def test_provider_errors_surface_with_the_facts_retry_policies_read() -> None:
    settings = ModelSettings(max_tokens=16)

    async def call(stub: ModelStub) -> tuple[Exception, Any]:
        model = stub_model(stub)
        built = build_model(resolve_model_endpoint({STUB_PROVIDER_ID: stub_provider(stub)}, model), model)
        try:
            with pytest.raises(Exception) as raised:  # noqa: PT011, B017
                async for _ in built.stream_response(
                    _INSTRUCTIONS,
                    _QUESTION,
                    settings,
                    [],
                    None,
                    [],
                    ModelTracing.DISABLED,
                    previous_response_id=None,
                    conversation_id=None,
                    prompt=None,
                ):
                    pass
        finally:
            await built.close()
        advice = built.get_retry_advice(ModelRetryAdviceRequest(error=raised.value, attempt=1, stream=True))
        return raised.value, advice

    async with model_stub(ModelApi.ANTHROPIC_MESSAGES) as stub:
        stub.script(StubError(
            status=429,
            body={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}},
            headers={"retry-after": "2"},
        ))
        error, advice = await call(stub)
    assert isinstance(error, anthropic.RateLimitError)
    assert advice is not None and advice.normalized is not None
    assert (advice.normalized.status_code, advice.retry_after) == (429, 2.0)

    async with model_stub(ModelApi.GOOGLE_GENERATIVE_AI) as stub:
        stub.script(StubError(
            status=503,
            body={"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}},
        ))
        error, advice = await call(stub)
    assert isinstance(error, google_errors.ServerError)
    assert advice is not None and advice.normalized is not None
    assert advice.normalized.status_code == 503
