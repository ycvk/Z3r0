"""Model construction for configured agents, one implementation per wire protocol."""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace

import httpx
from agents import (
    AgentOutputSchemaBase,
    FunctionTool,
    Handoff,
    Model,
    ModelResponse,
    ModelRetryAdvice,
    ModelRetryAdviceRequest,
    ModelRetryBackoffSettings,
    ModelRetrySettings,
    ModelSettings,
    ModelTracing,
    TResponseInputItem,
    Tool,
    retry_policies,
)
from agents.models.openai_provider import OpenAIProvider
from agents.items import TResponseStreamEvent
from openai import DEFAULT_TIMEOUT, AsyncOpenAI
from openai.types.responses.response_prompt_param import ResponsePromptParam
from openai.types.shared import Reasoning

from config import OPENAI_MODEL_APIS, ModelApi, ModelConfig, ModelEndpoint
from core.agent.anthropic_messages import AnthropicMessagesModel
from core.agent.google_generative_ai import GoogleGenerativeAIModel
from core.agent.model_input import ModelInputAdapter
from core.conversation.tokens import estimate_items_tokens, estimate_text_tokens
from logger import get_logger


logger = get_logger(__name__)

_MODEL_CONNECT_TIMEOUT_SECONDS = 15.0
_MODEL_REQUEST_MAX_RETRIES = 8
_TRANSIENT_HTTP_STATUSES = (408, 409, 429, *range(500, 600))

# Room kept free when the output cap is fitted into the context window. Providers count the input
# with their own tokenizer and reject a request whose input plus output cap exceeds the window, so
# the estimate gets a 15% margin. That margin is dropped once it would leave less output than
# _MIN_OUTPUT_WITH_RATIO_MARGIN, which happens in a nearly full window and in any small one.
_CONTEXT_SAFETY_TOKENS = 4096
_CONTEXT_SAFETY_RATIO = 0.15
_MIN_OUTPUT_WITH_RATIO_MARGIN = 32768

_ModelCloser = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ModelCallFailure:
    """One failed model request. The runner may retry it; `attempt` counts consecutive failures."""

    attempt: int
    max_attempts: int
    message: str


ModelFailureListener = Callable[[ModelCallFailure], None]
_failure_listener: ContextVar[ModelFailureListener | None] = ContextVar("model_failure_listener", default=None)


@contextmanager
def listen_for_model_failures(listener: ModelFailureListener) -> Iterator[None]:
    """Report failed model requests of the runs started inside this block to `listener`."""
    token = _failure_listener.set(listener)
    try:
        yield
    finally:
        _failure_listener.reset(token)


def describe_exception(error: BaseException) -> str:
    """The error's message followed by its root cause, which names what actually went wrong.

    Provider clients wrap transport failures in a generic error such as "Connection error."; the
    TLS, DNS, or socket failure behind it is only on the exception chain.
    """
    message = str(error).strip() or type(error).__name__
    root = error
    while (cause := root.__cause__ or root.__context__) is not None:
        root = cause
    if root is error:
        return message
    detail = f"{type(root).__name__}: {root}".strip().rstrip(":")
    return message if detail in message else f"{message} ({detail})"


def build_model_retry_settings() -> ModelRetrySettings:
    """Build the provider-neutral retry policy used by every Agent model call."""
    return ModelRetrySettings(
        max_retries=_MODEL_REQUEST_MAX_RETRIES,
        backoff=ModelRetryBackoffSettings(
            initial_delay=1.0,
            max_delay=30.0,
            multiplier=2.0,
            jitter=True,
        ),
        policy=retry_policies.any(
            retry_policies.provider_suggested(),
            retry_policies.network_error(),
            retry_policies.retry_after(),
            retry_policies.http_status(_TRANSIENT_HTTP_STATUSES),
        ),
    )


def build_model_settings(model: ModelConfig, *, parallel_tool_calls: bool | None = None) -> ModelSettings:
    """Build the request parameters a configured model sends with every call."""
    return ModelSettings(
        max_tokens=model.max_tokens,
        temperature=model.temperature,
        top_p=model.top_p,
        # The Anthropic and Google models read their thinking parameters from the model config.
        reasoning=(
            Reasoning(effort=model.reasoning_effort)
            if model.reasoning_effort is not None and model.api in OPENAI_MODEL_APIS
            else None
        ),
        parallel_tool_calls=parallel_tool_calls,
        extra_headers=dict(model.extra_headers) or None,
        extra_body=dict(model.extra_body) or None,
        retry=build_model_retry_settings(),
    )


def build_model(endpoint: ModelEndpoint, model: ModelConfig) -> Z3r0Model:
    """Build the model for one configured endpoint."""
    if endpoint.api in OPENAI_MODEL_APIS:
        inner, close = _build_openai_model(endpoint, model)
    elif endpoint.api is ModelApi.ANTHROPIC_MESSAGES:
        inner = AnthropicMessagesModel(endpoint, model, connect_timeout=_MODEL_CONNECT_TIMEOUT_SECONDS)
        close = inner.close
    else:
        inner = GoogleGenerativeAIModel(endpoint, model, read_timeout=DEFAULT_TIMEOUT.read or 600.0)
        close = inner.close
    return Z3r0Model(inner, close, model)


def clamp_max_tokens(max_tokens: int, *, context_window: int, input_tokens: int) -> int:
    """Fit the output cap into what the input leaves of the context window."""
    margin = max(_CONTEXT_SAFETY_TOKENS, math.ceil(input_tokens * _CONTEXT_SAFETY_RATIO))
    room = context_window - input_tokens - margin
    if room < _MIN_OUTPUT_WITH_RATIO_MARGIN:
        room = min(_MIN_OUTPUT_WITH_RATIO_MARGIN, context_window - input_tokens - _CONTEXT_SAFETY_TOKENS)
    return min(max_tokens, max(1, room))


class Z3r0Model(Model):
    """A configured model behind the provider boundary: normalized input and a fitted output cap."""

    def __init__(self, inner: Model, close: _ModelCloser, model: ModelConfig) -> None:
        self.model = model.id
        self._inner = inner
        self._close = close
        self._context_window = model.context_window
        self._input_adapter = ModelInputAdapter()
        self._target = f"provider={model.provider} api={model.api.value} model={model.id}"
        self._failed_attempts = 0

    def get_retry_advice(self, request: ModelRetryAdviceRequest) -> ModelRetryAdvice | None:
        return self._inner.get_retry_advice(request)

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
        adapted = self._input_adapter.adapt(input)
        try:
            response = await self._inner.get_response(
                system_instructions,
                adapted,
                self._fit_output_cap(model_settings, system_instructions, adapted, tools),
                tools,
                output_schema,
                handoffs,
                tracing,
                previous_response_id=previous_response_id,
                conversation_id=conversation_id,
                prompt=prompt,
            )
        except Exception as exc:
            self._report_failure(exc)
            raise
        self._failed_attempts = 0
        return response

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
        adapted = self._input_adapter.adapt(input)
        try:
            async for event in self._inner.stream_response(
                system_instructions,
                adapted,
                self._fit_output_cap(model_settings, system_instructions, adapted, tools),
                tools,
                output_schema,
                handoffs,
                tracing,
                previous_response_id=previous_response_id,
                conversation_id=conversation_id,
                prompt=prompt,
            ):
                yield event
        except Exception as exc:
            self._report_failure(exc)
            raise
        self._failed_attempts = 0

    async def close(self) -> None:
        await self._close()

    def _report_failure(self, error: Exception) -> None:
        """Log a failed request with its cause and tell the turn that is running it.

        The runner retries transient failures on its own and says nothing until the last attempt
        fails, so without this a provider that cannot be reached looks like a model that is thinking.
        """
        self._failed_attempts += 1
        message = describe_exception(error)
        logger.warning(
            "Model request failed %s attempt=%d/%d: %s",
            self._target,
            self._failed_attempts,
            _MODEL_REQUEST_MAX_RETRIES + 1,
            message,
        )
        listener = _failure_listener.get()
        if listener is not None:
            listener(ModelCallFailure(
                attempt=self._failed_attempts,
                max_attempts=_MODEL_REQUEST_MAX_RETRIES + 1,
                message=message,
            ))

    def _fit_output_cap(
        self,
        model_settings: ModelSettings,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        tools: list[Tool],
    ) -> ModelSettings:
        if model_settings.max_tokens is None or self._context_window <= 0:
            return model_settings
        items: list[TResponseInputItem] = (
            [{"type": "message", "role": "user", "content": input}] if isinstance(input, str) else input
        )
        tool_schemas = json.dumps([
            {"name": tool.name, "description": tool.description, "parameters": tool.params_json_schema}
            for tool in tools
            if isinstance(tool, FunctionTool)
        ])
        input_tokens = (
            estimate_items_tokens(items, self.model)
            + estimate_text_tokens(system_instructions or "", self.model)
            + estimate_text_tokens(tool_schemas, self.model)
        )
        return replace(model_settings, max_tokens=clamp_max_tokens(
            model_settings.max_tokens,
            context_window=self._context_window,
            input_tokens=input_tokens,
        ))


def _build_openai_model(endpoint: ModelEndpoint, model: ModelConfig) -> tuple[Model, _ModelCloser]:
    client = AsyncOpenAI(
        api_key=endpoint.api_key or "unused",
        base_url=_normalize_openai_base_url(endpoint.base_url),
        default_headers=endpoint.headers,
        timeout=httpx.Timeout(
            connect=_MODEL_CONNECT_TIMEOUT_SECONDS,
            read=DEFAULT_TIMEOUT.read,
            write=DEFAULT_TIMEOUT.write,
            pool=DEFAULT_TIMEOUT.pool,
        ),
        # Agent runner retries are replay-aware; do not multiply them with
        # transport retries hidden inside the provider client.
        max_retries=0,
    )
    provider = OpenAIProvider(
        openai_client=client,
        use_responses=endpoint.api is ModelApi.OPENAI_RESPONSES,
    )
    inner = provider.get_model(model.id)

    async def close() -> None:
        await inner.close()
        await provider.aclose()
        await client.close()

    return inner, close


def _normalize_openai_base_url(base_url: str) -> str:
    # ponytail: auto-append /v1 for bare Ollama (:11434) URLs so local keyless setups work out of the box
    cleaned = base_url.rstrip("/")
    if cleaned.endswith(":11434"):
        return f"{cleaned}/v1"
    return cleaned
