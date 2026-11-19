from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

import pytest

from core.runtime import coordination, ports
from core.runtime.context import AgentRuntimeContext, AgentUserContext, main_agent_instance_id
from core.sandbox import async_jobs as sandbox_async_jobs
from core.sandbox.command_jobs import start_async_sandbox_command, stop_async_sandbox_commands
from core.sandbox.command_output import COMMAND_TIMEOUT_ERROR
from core.sandbox.errors import SandboxContainerCommandTimeoutError
from database import get_async_session
from model.system_user.users import SystemUser
from schema.sandbox.async_jobs import SandboxAsyncJobStatus
from schema.system_user.users import SystemUserRole
from service.agent import sessions as agent_sessions


@dataclass(frozen=True)
class _CommandResult:
    output: str
    exit_code: int = 0


def test_runtime_ports_must_be_installed_before_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ports, "_ports", None)
    with pytest.raises(RuntimeError, match="Runtime ports are not installed"):
        ports.runtime_ports()


@pytest.mark.asyncio
async def test_runtime_coordination_must_be_installed_before_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coordination, "_coordination", None)
    with pytest.raises(RuntimeError, match="Runtime coordination is not installed"):
        await coordination.cancel_session_subagents("any-session")


@pytest.mark.asyncio
async def test_background_command_uses_the_installed_executor_and_resume_ports(test_db: None) -> None:
    async with get_async_session() as session:
        user = SystemUser(role=SystemUserRole.USER, email="op@test.local", username="operator")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    user_id = user.id or 0
    session_id = await agent_sessions.create_session(user_id)
    executed: list[tuple[int, str, float]] = []
    resumed: list[str] = []
    owner_resumed = asyncio.Event()

    async def execute(id: int, command: str, timeout_seconds: float) -> _CommandResult:
        executed.append((id, command, timeout_seconds))
        if command == "wrapped":
            raise SandboxContainerCommandTimeoutError(timeout_seconds)
        return _CommandResult(output="12 3")

    async def resume(resumed_session_id: str) -> None:
        resumed.append(resumed_session_id)
        owner_resumed.set()

    ports.install_runtime_ports(
        replace(ports.runtime_ports(), execute_sandbox_command=execute, resume_main_agent_session=resume)
    )
    context = AgentRuntimeContext(
        session_id=session_id,
        user=AgentUserContext(id=user_id, username=user.username, email=user.email, role=user.role),
        agent_code="cso",
        agent_instance_id=main_agent_instance_id(session_id, user_id, "cso"),
        sandbox_container_id=7,
    )

    await start_async_sandbox_command(
        run_id="run-runtime-ports",
        context=context,
        command="sleep 99",
        output_file="/tmp/shell-command-output/run-runtime-ports.log",
        wrapped_command="wrapped",
        stat_command="stat",
        timeout_seconds=5,
    )
    try:
        async with asyncio.timeout(10):
            await owner_resumed.wait()
    finally:
        await stop_async_sandbox_commands()

    job = await sandbox_async_jobs.get_async_job("run-runtime-ports", session_id=session_id)
    assert job is not None
    assert job.status == SandboxAsyncJobStatus.FAILED
    assert job.error == COMMAND_TIMEOUT_ERROR
    assert (job.output_bytes, job.output_lines) == (12, 3)
    assert executed[:2] == [(7, "wrapped", 5), (7, "stat", 30)]
    assert resumed == [session_id]
