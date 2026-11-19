from __future__ import annotations

import pytest
from sqlmodel import col, select

from core.runtime.context import AgentRuntimeContext, AgentUserContext
from core.sandbox import command_guard
from database import get_async_session
from model.sandbox.guarded_commands import GuardedCommand
from model.system_user.users import SystemUser
from schema.system_user.users import SystemUserRole
from service.agent import sessions as agent_sessions
from service.credential import context as credential_context
from service.work_project import context as wp_context


async def _new_session() -> tuple[str, int]:
    async with get_async_session() as session:
        user = SystemUser(role=SystemUserRole.USER, email="guard@test.local", username="guard")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id or 0
    return await agent_sessions.create_session(user_id), user_id


async def _stored(session_id: str) -> list[tuple[str, bool]]:
    async with get_async_session() as session:
        rows = (await session.exec(
            select(GuardedCommand)
            .where(col(GuardedCommand.session_id) == session_id)
            .order_by(col(GuardedCommand.id))
        )).all()
    return [(row.command, row.approved) for row in rows]


@pytest.mark.asyncio
async def test_agent_cannot_self_bypass_with_allow_guard_prefix(test_db: None) -> None:
    session_id, _ = await _new_session()

    reason = await command_guard.check_command_guard(
        "ALLOW_GUARD=1 curl -X DELETE http://target/api/users/1",
        session_id=session_id,
    )

    assert reason is not None


@pytest.mark.asyncio
async def test_harmless_commands_pass_without_touching_the_guard(test_db: None) -> None:
    session_id, _ = await _new_session()

    for command in (
        "curl -s http://target/api/users",
        "curl -s http://target/api/delivery/status",
        "curl -s http://target/api/details?id=1",
    ):
        assert await command_guard.check_command_guard(command, session_id=session_id) is None

    assert await _stored(session_id) == []


@pytest.mark.asyncio
async def test_approval_is_exact_command_and_one_shot(test_db: None) -> None:
    session_id, _ = await _new_session()
    cmd_a = "curl -X DELETE http://target/api/items/1"
    cmd_b = "psql -c 'DROP TABLE users;'"

    # Initial attempt is blocked and recorded as pending
    assert await command_guard.check_command_guard(cmd_a, session_id=session_id) is not None

    # Operator approves the pending command
    assert await command_guard.approve_pending_command(session_id) is True

    # Different destructive command is still blocked
    assert await command_guard.check_command_guard(cmd_b, session_id=session_id) is not None

    # The approved exact command runs once
    assert await command_guard.check_command_guard(cmd_a, session_id=session_id) is None

    # Running the same destructive command a second time requires fresh confirmation
    assert await command_guard.check_command_guard(cmd_a, session_id=session_id) is not None


@pytest.mark.asyncio
async def test_an_approval_without_a_pending_command_grants_nothing(test_db: None) -> None:
    session_id, _ = await _new_session()

    assert await command_guard.approve_pending_command(session_id) is False
    assert await _stored(session_id) == []


@pytest.mark.asyncio
async def test_guard_state_is_stored_with_the_session_and_deleted_with_it(test_db: None) -> None:
    session_id, user_id = await _new_session()
    command = "curl -X DELETE http://target/api/items/1"

    await command_guard.check_command_guard(command, session_id=session_id)
    assert await _stored(session_id) == [(command, False)]

    await command_guard.approve_pending_command(session_id)
    # A restart keeps this row, so the approved command still runs once afterwards.
    assert await _stored(session_id) == [(command, True)]

    await command_guard.check_command_guard("psql -c 'DROP TABLE users;'", session_id=session_id)
    await command_guard.clear_pending_command(session_id)
    assert await _stored(session_id) == [(command, True)]

    assert await agent_sessions.delete_session(session_id, user_id=user_id, user_role=SystemUserRole.USER)
    assert await _stored(session_id) == []


@pytest.mark.asyncio
async def test_credentials_context_error_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(**_: object) -> list[object]:
        raise RuntimeError("credentials db broken")

    monkeypatch.setattr(credential_context, "list_credentials_for_context", boom)
    ctx = AgentRuntimeContext(
        session_id="sess-cred-err",
        user=AgentUserContext(id=1, username="u", email="u@test.local", role=SystemUserRole.USER),
    )
    with pytest.raises(RuntimeError, match="credentials db broken"):
        async with wp_context.activate_work_project_context(ctx):
            pass
