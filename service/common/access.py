"""Who may use which work project, sandbox container, agent session, Flow, and traffic entry.

Every access decision reads ownership through this module. A `can_*` function answers for one
record. A `*_scope` function is the same rule as a SQL condition for a list query.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, and_, or_, true
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_async_session
from model.agent.sessions import AgentSessionMeta
from model.flow.flows import Flow
from model.sandbox.containers import SandboxContainer
from model.sandbox.traffic import SandboxTrafficEntry
from model.work_project.projects import WorkProject, WorkProjectOwner
from schema.agent.sessions import SessionType
from schema.system_user.users import SystemUserRole


def _is_admin(user_role: SystemUserRole) -> bool:
    return user_role == SystemUserRole.ADMIN


# Work projects: an admin may use every project, other users the projects they own.


async def can_access_work_project(project_id: int, user_id: int, user_role: SystemUserRole) -> bool:
    async with get_async_session() as session:
        return await can_access_work_project_in_tx(session, project_id, user_id, user_role)


async def can_access_work_project_in_tx(
    session: AsyncSession,
    project_id: int,
    user_id: int,
    user_role: SystemUserRole,
) -> bool:
    if await session.get(WorkProject, project_id) is None:
        return False
    if _is_admin(user_role):
        return True
    return await session.get(WorkProjectOwner, (project_id, user_id)) is not None


def work_project_scope(user_id: int, user_role: SystemUserRole) -> ColumnElement[bool]:
    if _is_admin(user_role):
        return true()
    return col(WorkProject.id).in_(_owned_work_project_ids(user_id))


def _owned_work_project_ids(user_id: int):
    return select(WorkProjectOwner.project_id).where(WorkProjectOwner.user_id == user_id)


# Sandbox containers: an admin may manage every container, other users the containers they own.
# Viewing covers what a container produced, such as captured traffic. Whoever may manage a
# container may view it, and so may every owner of the project it is bound to.


def can_manage_sandbox_container(container: SandboxContainer, user_id: int, user_role: SystemUserRole) -> bool:
    return _is_admin(user_role) or container.owner_id == user_id


async def sandbox_container_manageability(
    container_id: int,
    user_id: int,
    user_role: SystemUserRole,
) -> bool | None:
    """Whether the user may manage the container; None when it does not exist."""
    async with get_async_session() as session:
        container = await session.get(SandboxContainer, container_id)
    return None if container is None else can_manage_sandbox_container(container, user_id, user_role)


def can_assign_sandbox_container_owner(owner_id: int, user_id: int, user_role: SystemUserRole) -> bool:
    """An admin may create a container for any user, other users only for themselves."""
    return _is_admin(user_role) or owner_id == user_id


def sandbox_container_scope(user_id: int, user_role: SystemUserRole) -> ColumnElement[bool]:
    if _is_admin(user_role):
        return true()
    return col(SandboxContainer.owner_id) == user_id


async def can_view_sandbox_container(container_id: int, user_id: int, user_role: SystemUserRole) -> bool:
    async with get_async_session() as session:
        container = await session.get(SandboxContainer, container_id)
        if container is None:
            return False
        if can_manage_sandbox_container(container, user_id, user_role):
            return True
        bound_owned_project = (await session.exec(
            select(WorkProject.id)
            .where(
                WorkProject.sandbox_container_id == container_id,
                col(WorkProject.id).in_(_owned_work_project_ids(user_id)),
            )
            .limit(1)
        )).first()
        return bound_owned_project is not None


def _viewable_sandbox_container_ids(user_id: int):
    bound_to_owned_project = select(WorkProject.sandbox_container_id).where(
        col(WorkProject.id).in_(_owned_work_project_ids(user_id)),
        col(WorkProject.sandbox_container_id).is_not(None),
    )
    return select(SandboxContainer.id).where(
        or_(
            col(SandboxContainer.owner_id) == user_id,
            col(SandboxContainer.id).in_(bound_to_owned_project),
        )
    )


# Agent sessions: a session outside a project is private to its owner, except that an admin may
# use every Flow session. A session inside a project follows the project.


async def can_access_agent_session(session_id: str, user_id: int, user_role: SystemUserRole) -> bool:
    async with get_async_session() as session:
        meta = await session.get(AgentSessionMeta, session_id)
        return meta is not None and await can_access_agent_session_in_tx(session, meta, user_id, user_role)


async def can_access_agent_session_in_tx(
    session: AsyncSession,
    meta: AgentSessionMeta,
    user_id: int,
    user_role: SystemUserRole,
) -> bool:
    if meta.session_type == SessionType.FLOW and _is_admin(user_role):
        return True
    if meta.project_id is None:
        return meta.owner_id == user_id
    return await can_access_work_project_in_tx(session, meta.project_id, user_id, user_role)


def agent_session_scope(user_id: int, user_role: SystemUserRole) -> ColumnElement[bool]:
    private = and_(col(AgentSessionMeta.project_id).is_(None), col(AgentSessionMeta.owner_id) == user_id)
    if _is_admin(user_role):
        return or_(
            private,
            col(AgentSessionMeta.project_id).is_not(None),
            col(AgentSessionMeta.session_type) == SessionType.FLOW,
        )
    return or_(private, col(AgentSessionMeta.project_id).in_(_owned_work_project_ids(user_id)))


# Flows: an admin may use every Flow, other users the Flows they started.


def can_access_flow(flow: Flow, user_id: int, user_role: SystemUserRole) -> bool:
    return _is_admin(user_role) or flow.owner_id == user_id


def flow_scope(user_id: int, user_role: SystemUserRole) -> ColumnElement[bool]:
    if _is_admin(user_role):
        return true()
    return col(Flow.owner_id) == user_id


# Traffic entries: visible to whoever captured them, whoever may view their container, and
# whoever may use their project.


async def can_view_traffic_entry(entry: SandboxTrafficEntry, user_id: int, user_role: SystemUserRole) -> bool:
    if _is_admin(user_role) or entry.owner_id == user_id:
        return True
    if entry.sandbox_container_id is not None and await can_view_sandbox_container(
        entry.sandbox_container_id, user_id, user_role
    ):
        return True
    return entry.project_id is not None and await can_access_work_project(entry.project_id, user_id, user_role)


def traffic_entry_scope(user_id: int, user_role: SystemUserRole) -> ColumnElement[bool]:
    if _is_admin(user_role):
        return true()
    return or_(
        col(SandboxTrafficEntry.owner_id) == user_id,
        col(SandboxTrafficEntry.sandbox_container_id).in_(_viewable_sandbox_container_ids(user_id)),
        col(SandboxTrafficEntry.project_id).in_(_owned_work_project_ids(user_id)),
    )
