from __future__ import annotations

import asyncio
import json

import pytest
from agents import TResponseInputItem

from config import ModelApi, get_config
from core.agent.constants import DEFAULT_AGENT_CODE
from core.conversation.compaction import _summarize_items
from core.runtime.session import get_agent_pool
from database import get_async_session
from middleware.system_user import AuthUser
from model.system_user.users import SystemUser
from schema.agent.events import AgentTextInputPart, ErrorEvent, RunStateEvent, TextCompleteEvent
from schema.system_user.users import SystemUserRole
from service.agent.runtime import submit_new_chat_turn
from service.agent.session_state import get_session_meta
from tests.model_stubs import model_stub, text_stream, use_stub_for_default_agent


@pytest.mark.asyncio
@pytest.mark.parametrize("api", list(ModelApi), ids=lambda api: api.value)
async def test_chat_turn_runs_end_to_end_against_a_stub_model(test_db: None, api: ModelApi) -> None:
    async with get_async_session() as session:
        user = SystemUser(role=SystemUserRole.USER, email="op@test.local", username="operator")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    auth = AuthUser(id=user.id or 0, role=user.role, email=user.email, username=user.username)

    events = []
    async with model_stub(api) as stub:
        stub.script(text_stream(api, "pong", input_tokens=11, output_tokens=7))
        use_stub_for_default_agent(stub)
        try:
            session_id, _ = await submit_new_chat_turn(
                content=[AgentTextInputPart(text="ping")],
                user=auth,
                sandbox_container_id=None,
                requested_agent_code=DEFAULT_AGENT_CODE,
            )
            _, queue = await get_agent_pool().subscribe(session_id)
            async with asyncio.timeout(30):
                while True:
                    event = await queue.get()
                    events.append(event)
                    if isinstance(event, RunStateEvent) and not event.running:
                        break
        finally:
            await get_agent_pool().stop()

    assert not [event for event in events if isinstance(event, ErrorEvent)]
    assert [event.text for event in events if isinstance(event, TextCompleteEvent)] == ["pong"]

    assert len(stub.requests) == 1
    request = json.dumps(stub.requests[0])
    assert "# Runtime guidance" in request
    assert "ping" in request

    meta = await get_session_meta(session_id)
    assert meta is not None
    assert not meta.is_running
    assert meta.run_error == ""
    assert (meta.llm_requests, meta.input_tokens, meta.output_tokens, meta.total_tokens) == (1, 11, 7, 18)


@pytest.mark.asyncio
async def test_context_summary_is_a_capped_request_without_thinking_or_sampling(test_db: None) -> None:
    items: list[TResponseInputItem] = [{"type": "message", "role": "user", "content": "Scan 10.0.0.5"}]
    async with model_stub(ModelApi.ANTHROPIC_MESSAGES) as stub:
        stub.script(text_stream(stub.api, "## User goals\nScan 10.0.0.5", input_tokens=30, output_tokens=12))
        use_stub_for_default_agent(stub, max_tokens=64_000, thinking_budget_tokens=16_000, temperature=0.7)
        summary = await _summarize_items(items, get_config().agents[DEFAULT_AGENT_CODE])

    assert summary == "## User goals\nScan 10.0.0.5"
    request = stub.requests[0]
    assert request["max_tokens"] == get_config().agent_runtime.context_compression_summary_max_tokens
    assert "thinking" not in request and "temperature" not in request
    assert "Scan 10.0.0.5" in json.dumps(request["messages"])
