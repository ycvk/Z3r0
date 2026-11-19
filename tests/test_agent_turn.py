from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from aiohttp import web

from config import get_config
from core.agent.constants import DEFAULT_AGENT_CODE
from core.runtime.session import get_agent_pool
from database import get_async_session
from middleware.system_user import AuthUser
from model.system_user.users import SystemUser
from schema.agent.events import AgentTextInputPart, ErrorEvent, RunStateEvent, TextCompleteEvent
from schema.system_user.users import SystemUserRole
from service.agent.runtime import submit_new_chat_turn
from service.agent.session_state import get_session_meta


def _chunk(**fields: Any) -> bytes:
    payload = {"id": "chatcmpl-stub", "object": "chat.completion.chunk", "created": 0, "model": "stub", **fields}
    return f"data: {json.dumps(payload)}\n\n".encode()


@pytest.mark.asyncio
async def test_chat_turn_runs_end_to_end_against_a_stub_model(test_db: None) -> None:
    model_requests: list[dict[str, Any]] = []

    async def chat_completions(request: web.Request) -> web.StreamResponse:
        model_requests.append(await request.json())
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(_chunk(choices=[{"index": 0, "delta": {"role": "assistant", "content": "pong"}}]))
        await response.write(_chunk(choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}]))
        await response.write(
            _chunk(choices=[], usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18})
        )
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    stub = web.Application()
    stub.router.add_post("/v1/chat/completions", chat_completions)
    runner = web.AppRunner(stub)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]

    agent = get_config().agents[DEFAULT_AGENT_CODE]
    agent.base_url = f"http://127.0.0.1:{port}/v1"
    agent.api_key = "stub"
    agent.model = "stub-model"
    agent.use_responses = False

    async with get_async_session() as session:
        user = SystemUser(role=SystemUserRole.USER, email="op@test.local", username="operator")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    auth = AuthUser(id=user.id or 0, role=user.role, email=user.email, username=user.username)

    events = []
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
        await runner.cleanup()

    assert not [event for event in events if isinstance(event, ErrorEvent)]
    assert [event.text for event in events if isinstance(event, TextCompleteEvent)] == ["pong"]

    assert len(model_requests) == 1
    messages = model_requests[0]["messages"]
    assert "# Runtime guidance" in json.dumps(messages[0])
    assert "ping" in json.dumps(messages[-1])

    meta = await get_session_meta(session_id)
    assert meta is not None
    assert not meta.is_running
    assert meta.run_error == ""
    assert (meta.llm_requests, meta.input_tokens, meta.output_tokens, meta.total_tokens) == (1, 11, 7, 18)
