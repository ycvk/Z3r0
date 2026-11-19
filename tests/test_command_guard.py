from __future__ import annotations

import pytest

from core.runtime.context import AgentRuntimeContext, AgentUserContext
from core.sandbox import command_guard
from core.work_project import context as wp_context
from schema.system_user.users import SystemUserRole


def test_agent_cannot_self_bypass_with_allow_guard_prefix() -> None:
    session_id = "sess-self-bypass"
    if hasattr(command_guard, "reset_guard_state"):
        command_guard.reset_guard_state()
    reason = command_guard.check_command_guard(
        "ALLOW_GUARD=1 curl -X DELETE http://target/api/users/1",
        session_id=session_id,
    )
    assert reason is not None


def test_approval_is_exact_command_and_one_shot() -> None:
    session_id = "sess-scoped"
    if hasattr(command_guard, "reset_guard_state"):
        command_guard.reset_guard_state()

    cmd_a = "curl -X DELETE http://target/api/items/1"
    cmd_b = "psql -c 'DROP TABLE users;'"

    # Initial attempt is blocked and recorded as pending
    assert command_guard.check_command_guard(cmd_a, session_id=session_id) is not None

    # Operator approves the pending command
    assert command_guard.approve_pending_command(session_id) is True

    # Different destructive command is still blocked
    assert command_guard.check_command_guard(cmd_b, session_id=session_id) is not None

    # The approved exact command runs once
    assert command_guard.check_command_guard(cmd_a, session_id=session_id) is None

    # Running the same destructive command a second time requires fresh confirmation
    assert command_guard.check_command_guard(cmd_a, session_id=session_id) is not None


@pytest.mark.asyncio
async def test_credentials_context_error_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(**_: object) -> list[object]:
        raise RuntimeError("credentials db broken")

    monkeypatch.setattr(wp_context, "list_credentials_for_context", boom)
    ctx = AgentRuntimeContext(
        session_id="sess-cred-err",
        user=AgentUserContext(id=1, username="u", email="u@test.local", role=SystemUserRole.USER),
    )
    with pytest.raises(RuntimeError, match="credentials db broken"):
        async with wp_context.activate_work_project_context(ctx):
            pass
