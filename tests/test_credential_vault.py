from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import text
from sqlmodel import col, select

from core.errors import NotFoundError
from database import alembic_config, get_async_session, get_engine
from model.credential.credentials import SessionCredential
from model.system_user.users import SystemUser
from model.work_project.projects import WorkProject, WorkProjectOwner
from schema.agent.sessions import SessionType
from schema.system_user.users import SystemUserRole
from service.agent import sessions as agent_sessions
from service.credential import credentials as credential_service
from service.system_user.users import delete_system_user
from service.work_project.projects import delete_work_project


async def _seed() -> tuple[int, int, int]:
    """An admin, alice, and a work project owned by both."""
    async with get_async_session() as session:
        admin = SystemUser(role=SystemUserRole.ADMIN, email="admin@vault.local", username="admin")
        alice = SystemUser(role=SystemUserRole.USER, email="alice@vault.local", username="alice")
        project = WorkProject(name="Vault project")
        session.add_all([admin, alice, project])
        await session.commit()
        for row in (admin, alice, project):
            await session.refresh(row)
        admin_id, alice_id, project_id = admin.id or 0, alice.id or 0, project.id or 0
        session.add_all([
            WorkProjectOwner(project_id=project_id, user_id=admin_id, position=0),
            WorkProjectOwner(project_id=project_id, user_id=alice_id, position=1),
        ])
        await session.commit()
    return admin_id, alice_id, project_id


async def _stored() -> list[SessionCredential]:
    async with get_async_session() as session:
        return list((await session.exec(select(SessionCredential).order_by(col(SessionCredential.id)))).all())


async def _save(owner_id: int, *, session_id: str = "", project_id: int | None = None, target: str = "t") -> int:
    saved = await credential_service.upsert_credential(
        owner_id=owner_id,
        session_id=session_id,
        project_id=project_id,
        target=target,
        secret="s3cret",
    )
    return saved.id


@pytest.mark.asyncio
async def test_a_work_project_shares_one_vault_across_its_sessions(test_db: None) -> None:
    _, alice_id, project_id = await _seed()
    first = await agent_sessions.create_session(alice_id, session_type=SessionType.PROJECT, project_id=project_id)
    second = await agent_sessions.create_session(alice_id, session_type=SessionType.PROJECT, project_id=project_id)
    chat = await agent_sessions.create_session(alice_id)

    await _save(alice_id, session_id=first, project_id=project_id, target="https://portal.local")
    await _save(alice_id, session_id=chat, target="https://chat-only.local")
    # Saving the same target again from another session of the project updates the one record.
    await _save(alice_id, session_id=second, project_id=project_id, target="https://portal.local")

    def targets(items: list) -> list[str]:
        return sorted(item.target for item in items)

    listed = credential_service.list_credentials_for_context
    assert targets(await listed(session_id=second, project_id=project_id)) == ["https://portal.local"]
    assert targets(await listed(session_id=chat, project_id=None)) == ["https://chat-only.local"]
    assert targets((await credential_service.query_project_credentials(project_id)).items) == ["https://portal.local"]

    # The record belongs to the project, so it stays when the session that recorded it is deleted.
    assert await agent_sessions.delete_session(
        first, user_id=alice_id, user_role=SystemUserRole.USER, allow_project_session=True,
    )
    assert targets(await listed(session_id=second, project_id=project_id)) == ["https://portal.local"]


@pytest.mark.asyncio
async def test_deleting_a_session_deletes_its_vault(test_db: None) -> None:
    _, alice_id, _ = await _seed()
    chat = await agent_sessions.create_session(alice_id)
    await _save(alice_id, session_id=chat)

    assert await agent_sessions.delete_session(chat, user_id=alice_id, user_role=SystemUserRole.USER)

    assert await _stored() == []


@pytest.mark.asyncio
async def test_deleting_a_work_project_deletes_its_vault(test_db: None) -> None:
    _, alice_id, project_id = await _seed()
    await _save(alice_id, project_id=project_id)

    await delete_work_project(project_id)

    assert await _stored() == []


@pytest.mark.asyncio
async def test_deleting_a_user_keeps_project_credentials_and_drops_private_ones(test_db: None) -> None:
    _, alice_id, project_id = await _seed()
    chat = await agent_sessions.create_session(alice_id)
    await _save(alice_id, session_id=chat, target="private")
    await _save(alice_id, project_id=project_id, target="shared")

    await delete_system_user(alice_id)

    remaining = await _stored()
    assert [(row.target, row.project_id, row.owner_id) for row in remaining] == [("shared", project_id, None)]


@pytest.mark.asyncio
async def test_a_credential_is_deleted_only_through_its_own_vault(test_db: None) -> None:
    _, alice_id, project_id = await _seed()
    chat = await agent_sessions.create_session(alice_id)
    other_chat = await agent_sessions.create_session(alice_id)
    session_credential = await _save(alice_id, session_id=chat)
    project_credential = await _save(alice_id, project_id=project_id)

    with pytest.raises(NotFoundError):
        await credential_service.delete_credential(session_credential, session_id=other_chat)
    with pytest.raises(NotFoundError):
        await credential_service.delete_credential(session_credential, project_id=project_id)
    with pytest.raises(NotFoundError):
        await credential_service.delete_credential(project_credential, session_id=chat)
    assert len(await _stored()) == 2

    await credential_service.delete_credential(session_credential, session_id=chat)
    await credential_service.delete_credential(project_credential, project_id=project_id)
    assert await _stored() == []


@pytest.mark.asyncio
async def test_migration_moves_existing_credentials_into_their_vault(test_db: None) -> None:
    _, alice_id, project_id = await _seed()
    project_session = await agent_sessions.create_session(
        alice_id, session_type=SessionType.PROJECT, project_id=project_id,
    )
    chat = await agent_sessions.create_session(alice_id)

    async with get_engine().begin() as connection:
        await connection.run_sync(lambda sync: command.downgrade(alembic_config(sync), "0001"))
        # Rows as revision 0001 stored them: session_id is never null and nothing ties it to a session.
        legacy_rows = (
            ("entered on the project page", "", project_id),
            ("recorded by a project session", project_session, project_id),
            ("recorded by a project session without its project", project_session, None),
            ("recorded by a chat session", chat, None),
            ("recorded by a deleted session", "deleted-session", None),
            ("recorded without a session or project", "", None),
        )
        for target, session_id, row_project_id in legacy_rows:
            await connection.execute(
                text(
                    "INSERT INTO session_credentials "
                    "(owner_id, session_id, project_id, target, credential_type, username, secret, note, "
                    "created_at, updated_at) "
                    "VALUES (:owner, :session, :project, :target, 'password', '', 's3cret', '', now(), now())"
                ),
                {"owner": alice_id, "session": session_id, "project": row_project_id, "target": target},
            )
        await connection.run_sync(lambda sync: command.upgrade(alembic_config(sync), "head"))

    assert {(row.target, row.session_id, row.project_id) for row in await _stored()} == {
        ("entered on the project page", None, project_id),
        ("recorded by a project session", None, project_id),
        ("recorded by a project session without its project", None, project_id),
        ("recorded by a chat session", chat, None),
    }
