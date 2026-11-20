"""Assemble Responses API stream events from a provider that speaks another protocol.

The Agents SDK consumes every model as a Responses API stream. The Anthropic and Google models feed
their own stream into this builder, which emits the event sequence the SDK expects and collects the
finished output items for the terminal response.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from agents import ModelResponse, ModelSettings, Usage
from agents.models.fake_id import FAKE_RESPONSES_ID
from agents.items import TResponseStreamEvent
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseContentPartAddedEvent,
    ResponseContentPartDoneEvent,
    ResponseCreatedEvent,
    ResponseFunctionCallArgumentsDeltaEvent,
    ResponseFunctionCallArgumentsDoneEvent,
    ResponseFunctionToolCall,
    ResponseOutputItem,
    ResponseOutputItemAddedEvent,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseOutputText,
    ResponseReasoningItem,
    ResponseTextDeltaEvent,
    ResponseTextDoneEvent,
    ResponseUsage,
)
from openai.types.responses.response_reasoning_item import Content as ReasoningContent
from openai.types.responses.response_reasoning_text_delta_event import ResponseReasoningTextDeltaEvent
from openai.types.responses.response_reasoning_text_done_event import ResponseReasoningTextDoneEvent
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails


class ResponseStreamBuilder:
    """Turn provider output into Responses API events, one output item at a time.

    Every item carries `provider_data` naming the protocol and model that produced it. The models use
    it to replay only their own reasoning, and the SDK's OpenAI models use it to drop items another
    provider produced.
    """

    def __init__(self, *, model: str, model_settings: ModelSettings, provider_data: dict[str, Any]) -> None:
        self._provider_data = provider_data
        self._response = Response(
            id=FAKE_RESPONSES_ID,
            created_at=time.time(),
            model=model,
            object="response",
            output=[],
            tool_choice="auto",
            tools=[],
            parallel_tool_calls=bool(model_settings.parallel_tool_calls),
            temperature=model_settings.temperature,
            top_p=model_settings.top_p,
        )
        self._items: list[ResponseOutputItem] = []
        self._open: ResponseOutputItem | None = None
        self._sequence_number = 0

    def start(self) -> list[TResponseStreamEvent]:
        return [ResponseCreatedEvent(response=self._response, type="response.created", sequence_number=self._next())]

    def reasoning_delta(self, text: str) -> list[TResponseStreamEvent]:
        events: list[TResponseStreamEvent] = []
        item = self._open
        if not isinstance(item, ResponseReasoningItem):
            events.extend(self.end_item())
            item = ResponseReasoningItem(
                id=FAKE_RESPONSES_ID,
                summary=[],
                content=[ReasoningContent(text="", type="reasoning_text")],
                type="reasoning",
            )
            events.append(self._open_item(item))
        if not text or not item.content:
            return events
        item.content[0].text += text
        events.append(ResponseReasoningTextDeltaEvent(
            content_index=0,
            delta=text,
            item_id=FAKE_RESPONSES_ID,
            output_index=len(self._items),
            type="response.reasoning_text.delta",
            sequence_number=self._next(),
        ))
        return events

    def reasoning_signature(self, signature: str) -> list[TResponseStreamEvent]:
        """Attach the provider's opaque proof for the open reasoning item, opening one if needed."""
        events = self.reasoning_delta("")
        item = self._open
        if isinstance(item, ResponseReasoningItem):
            item.encrypted_content = (item.encrypted_content or "") + signature
        return events

    def text_delta(self, text: str, *, item_data: dict[str, Any] | None = None) -> list[TResponseStreamEvent]:
        events: list[TResponseStreamEvent] = []
        item = self._open
        if not isinstance(item, ResponseOutputMessage):
            events.extend(self.end_item())
            item = ResponseOutputMessage(
                id=FAKE_RESPONSES_ID,
                content=[ResponseOutputText(text="", type="output_text", annotations=[], logprobs=[])],
                role="assistant",
                type="message",
                status="in_progress",
            )
            events.append(self._open_item(item))
            events.append(ResponseContentPartAddedEvent(
                content_index=0,
                item_id=FAKE_RESPONSES_ID,
                output_index=len(self._items),
                part=ResponseOutputText(text="", type="output_text", annotations=[], logprobs=[]),
                type="response.content_part.added",
                sequence_number=self._next(),
            ))
        if item_data:
            _merge_provider_data(item, item_data)
        part = item.content[0]
        if not text or not isinstance(part, ResponseOutputText):
            return events
        part.text += text
        events.append(ResponseTextDeltaEvent(
            content_index=0,
            delta=text,
            item_id=FAKE_RESPONSES_ID,
            output_index=len(self._items),
            logprobs=[],
            type="response.output_text.delta",
            sequence_number=self._next(),
        ))
        return events

    def function_call_start(
        self,
        *,
        call_id: str,
        name: str,
        item_data: dict[str, Any] | None = None,
    ) -> list[TResponseStreamEvent]:
        events = self.end_item()
        item = ResponseFunctionToolCall(
            id=FAKE_RESPONSES_ID,
            call_id=call_id,
            name=name,
            arguments="",
            type="function_call",
            status="in_progress",
        )
        events.append(self._open_item(item))
        if item_data:
            _merge_provider_data(item, item_data)
        return events

    def function_call_arguments_delta(self, delta: str) -> list[TResponseStreamEvent]:
        item = self._open
        if not isinstance(item, ResponseFunctionToolCall):
            raise RuntimeError("function call arguments arrived outside a function call")
        if not delta:
            return []
        item.arguments += delta
        return [ResponseFunctionCallArgumentsDeltaEvent(
            delta=delta,
            item_id=FAKE_RESPONSES_ID,
            output_index=len(self._items),
            type="response.function_call_arguments.delta",
            sequence_number=self._next(),
        )]

    def end_item(self) -> list[TResponseStreamEvent]:
        """Close the open output item, if any."""
        item = self._open
        if item is None:
            return []
        output_index = len(self._items)
        events: list[TResponseStreamEvent] = []
        if isinstance(item, ResponseReasoningItem):
            events.append(ResponseReasoningTextDoneEvent(
                content_index=0,
                item_id=FAKE_RESPONSES_ID,
                output_index=output_index,
                text=item.content[0].text if item.content else "",
                type="response.reasoning_text.done",
                sequence_number=self._next(),
            ))
        elif isinstance(item, ResponseOutputMessage):
            part = item.content[0]
            item.status = "completed"
            if isinstance(part, ResponseOutputText):
                events.append(ResponseTextDoneEvent(
                    content_index=0,
                    item_id=FAKE_RESPONSES_ID,
                    output_index=output_index,
                    text=part.text,
                    logprobs=[],
                    type="response.output_text.done",
                    sequence_number=self._next(),
                ))
            events.append(ResponseContentPartDoneEvent(
                content_index=0,
                item_id=FAKE_RESPONSES_ID,
                output_index=output_index,
                part=part,
                type="response.content_part.done",
                sequence_number=self._next(),
            ))
        elif isinstance(item, ResponseFunctionToolCall):
            item.status = "completed"
            # A call without arguments still has to be a JSON object for the tool runner.
            item.arguments = item.arguments or "{}"
            events.append(ResponseFunctionCallArgumentsDoneEvent(
                arguments=item.arguments,
                item_id=FAKE_RESPONSES_ID,
                name=item.name,
                output_index=output_index,
                type="response.function_call_arguments.done",
                sequence_number=self._next(),
            ))
        events.append(ResponseOutputItemDoneEvent(
            item=item,
            output_index=output_index,
            type="response.output_item.done",
            sequence_number=self._next(),
        ))
        self._items.append(item)
        self._open = None
        return events

    def complete(self, usage: ResponseUsage) -> list[TResponseStreamEvent]:
        events = self.end_item()
        final_response = self._response.model_copy()
        final_response.output = list(self._items)
        final_response.usage = usage
        events.append(ResponseCompletedEvent(
            response=final_response,
            type="response.completed",
            sequence_number=self._next(),
        ))
        return events

    def _open_item(self, item: ResponseOutputItem) -> TResponseStreamEvent:
        _merge_provider_data(item, self._provider_data)
        self._open = item
        return ResponseOutputItemAddedEvent(
            item=item,
            output_index=len(self._items),
            type="response.output_item.added",
            sequence_number=self._next(),
        )

    def _next(self) -> int:
        number = self._sequence_number
        self._sequence_number += 1
        return number


def response_usage(
    *,
    input_tokens: int,
    cached_tokens: int,
    cache_write_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
) -> ResponseUsage:
    """Build usage where input counts every prompt token, cached or not."""
    return ResponseUsage(
        input_tokens=input_tokens,
        input_tokens_details=InputTokensDetails(cached_tokens=cached_tokens, cache_write_tokens=cache_write_tokens),
        output_tokens=output_tokens,
        output_tokens_details=OutputTokensDetails(reasoning_tokens=reasoning_tokens),
        total_tokens=input_tokens + output_tokens,
    )


async def collect_model_response(events: AsyncIterator[TResponseStreamEvent]) -> ModelResponse:
    """Drain a response stream into the result of a non-streaming model call."""
    completed: Response | None = None
    async for event in events:
        if isinstance(event, ResponseCompletedEvent):
            completed = event.response
    if completed is None or completed.usage is None:
        raise RuntimeError("model stream ended without a completed response")
    usage = completed.usage
    return ModelResponse(
        output=completed.output,
        usage=Usage(
            requests=1,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            input_tokens_details=usage.input_tokens_details,
            output_tokens_details=usage.output_tokens_details,
        ),
        response_id=None,
    )


def _merge_provider_data(item: ResponseOutputItem, data: dict[str, Any]) -> None:
    current = getattr(item, "provider_data", None) or {}
    setattr(item, "provider_data", {**current, **data})  # noqa: B010
