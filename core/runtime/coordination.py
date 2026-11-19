"""How the session pool, the subagent runtime, and sandbox command jobs reach each other.

The three import one another's callers, so they meet here. `app.py` installs the implementations
with the rest of the agent runtime.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from schema.agent.events import AgentEventSchema


@dataclass(frozen=True, slots=True)
class RuntimeCoordination:
    # Delivers an event to the live session; False when the session is not in the pool.
    publish_agent_event: Callable[[str, AgentEventSchema], bool]
    resume_target_agent_instance: Callable[[str, str], Awaitable[None]]
    cancel_sandbox_subagents: Callable[[int], Awaitable[bool]]
    cancel_session_subagents: Callable[[str], Awaitable[bool]]


_coordination: RuntimeCoordination | None = None


def install_runtime_coordination(coordination: RuntimeCoordination) -> None:
    global _coordination
    _coordination = coordination


def _installed() -> RuntimeCoordination:
    if _coordination is None:
        raise RuntimeError("Runtime coordination is not installed")
    return _coordination


def publish_agent_event(session_id: str, event: AgentEventSchema) -> bool:
    return _installed().publish_agent_event(session_id, event)


async def resume_target_agent_instance(session_id: str, agent_instance_id: str) -> None:
    await _installed().resume_target_agent_instance(session_id, agent_instance_id)


async def cancel_sandbox_subagents(container_id: int) -> bool:
    return await _installed().cancel_sandbox_subagents(container_id)


async def cancel_session_subagents(session_id: str) -> bool:
    return await _installed().cancel_session_subagents(session_id)
