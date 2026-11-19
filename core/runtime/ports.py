"""What the agent runtime needs from the layers above it.

`app.py` installs the implementations at startup.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from core.runtime.context import AgentRuntimeContext


class SessionRunState(Protocol):
    """Persists the run state and the usage counters of a session."""

    async def mark_session_running(
        self,
        session_id: str,
        *,
        agent_code: str,
        user_id: int,
        sandbox_container_id: int | None,
        sandbox_container_generation: int,
    ) -> None: ...

    async def mark_session_stopped(self, session_id: str, *, error: str = "") -> None: ...

    async def mark_sessions_stopped(self, session_ids: list[str], *, error: str = "") -> None: ...

    async def force_mark_session_stopped(self, session_id: str, *, error: str = "") -> None: ...

    async def record_session_usage(self, session_id: str, usage: Any) -> None: ...


class SandboxCommandResult(Protocol):
    @property
    def output(self) -> str: ...

    @property
    def exit_code(self) -> int: ...


class ExecuteSandboxCommand(Protocol):
    """Runs a command in a sandbox container.

    Raises `SandboxContainerCommandTimeoutError` when the command outlives its timeout.
    """

    async def __call__(self, id: int, command: str, timeout_seconds: float) -> SandboxCommandResult: ...


@dataclass(frozen=True, slots=True)
class RuntimePorts:
    session_runs: SessionRunState
    # Fills the work project and credentials context of a turn and clears it when the turn ends.
    activate_turn_context: Callable[[AgentRuntimeContext], AbstractAsyncContextManager[None]]
    execute_sandbox_command: ExecuteSandboxCommand
    # Starts the next turn of the main agent when a notification is pending for it.
    resume_main_agent_session: Callable[[str], Awaitable[None]]


_ports: RuntimePorts | None = None


def install_runtime_ports(ports: RuntimePorts) -> None:
    global _ports
    _ports = ports


def runtime_ports() -> RuntimePorts:
    if _ports is None:
        raise RuntimeError("Runtime ports are not installed")
    return _ports
