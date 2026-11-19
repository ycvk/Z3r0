from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
import pytest_asyncio
from agents import Agent, FunctionTool, FunctionToolResult

from config import load_config
from core.agent.constants import DEFAULT_AGENT_CODE, SPECIALIST_AGENT_CODES
from core.agent.registry import AgentRegistry, SessionAgentGraph
from core.agent.tool_snapshot import AgentToolSnapshot
from core.runtime import session as agent_runtime
from tools.catalog import AGENT_SPECS


SPECIALIST_CODE = SPECIALIST_AGENT_CODES[0]


@pytest_asyncio.fixture()
async def bind() -> AsyncIterator[Any]:
    load_config()
    registry = AgentRegistry(AGENT_SPECS)
    graphs: list[SessionAgentGraph] = []

    def bind_graph(*, flow_bound: bool, sandbox: bool = True) -> SessionAgentGraph:
        graph = registry.bind(
            AgentToolSnapshot(sandbox_container_id=1 if sandbox else None, flow_bound=flow_bound)
        )
        graphs.append(graph)
        return graph

    yield bind_graph
    for graph in graphs:
        await graph.close()


def _tool_names(agent: Agent) -> set[str]:
    return {tool.name for tool in agent.tools}


async def _ends_turn(agent: Agent, tool_name: str, payload: dict[str, str]) -> bool:
    tool = next(tool for tool in agent.tools if tool.name == tool_name)
    result = FunctionToolResult(tool=cast(FunctionTool, tool), output=json.dumps(payload), run_item=cast(Any, None))
    behavior = cast(Any, agent.tool_use_behavior)
    return (await behavior(None, [result])).is_final_output


@pytest.mark.asyncio
async def test_chat_and_flow_sessions_mount_their_own_command_tools(bind: Any) -> None:
    chat_commands = {"execute_sync_command", "execute_async_command"}

    chat = _tool_names(bind(flow_bound=False).get(SPECIALIST_CODE))
    assert chat_commands <= chat
    assert "terminal" not in chat

    for code in (DEFAULT_AGENT_CODE, SPECIALIST_CODE):
        flow = _tool_names(bind(flow_bound=True).get(code))
        assert "terminal" in flow
        assert not chat_commands & flow

    without_sandbox = _tool_names(bind(flow_bound=True, sandbox=False).get(SPECIALIST_CODE))
    assert not {"terminal", "load_skill", "read_sandbox_command_output"} & without_sandbox


@pytest.mark.asyncio
async def test_only_a_successful_background_dispatch_ends_the_turn(bind: Any) -> None:
    chat = bind(flow_bound=False).get(SPECIALIST_CODE)
    assert await _ends_turn(chat, "execute_async_command", {"status": "running"})
    assert not await _ends_turn(chat, "execute_async_command", {"status": "failed"})
    assert not await _ends_turn(chat, "execute_sync_command", {"status": "running"})

    flow = bind(flow_bound=True).get(DEFAULT_AGENT_CODE)
    assert await _ends_turn(flow, "terminal", {"status": "running"})
    assert await _ends_turn(flow, "terminal", {"barrier": "ask"})
    assert not await _ends_turn(flow, "terminal", {"status": "failed"})


def test_agent_pool_must_be_installed_before_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_runtime, "_pool", None)
    with pytest.raises(RuntimeError, match="Agent pool is not installed"):
        agent_runtime.get_agent_pool()
