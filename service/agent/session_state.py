from datetime import datetime
from typing import Any

from sqlalchemy import update
from sqlmodel import select

from database import get_async_session
from model.agent.sessions import AgentSessionMeta
from model.system_user.users import SystemUser
from service.agent import notifications as agent_notifications
from service.system_user.locking import lock_system_user_lifecycle


async def list_running_sessions() -> list[AgentSessionMeta]:
    async with get_async_session() as session:
        return list((await session.exec(
            select(AgentSessionMeta).where(AgentSessionMeta.is_running.is_(True))
        )).all())


async def mark_session_running(
    session_id: str,
    *,
    agent_code: str,
    user_id: int,
    sandbox_container_id: int | None,
    sandbox_container_generation: int,
) -> None:
    async with get_async_session() as session:
        await lock_system_user_lifecycle(session, user_id)
        if await session.get(SystemUser, user_id) is None:
            raise PermissionError("system user no longer exists")
        meta = await session.get(AgentSessionMeta, session_id)
        if meta is None:
            return
        if meta.project_id is not None:
            meta.owner_id = user_id
        meta.is_running = True
        meta.runtime_agent_code = agent_code
        meta.runtime_sandbox_container_id = sandbox_container_id
        meta.runtime_sandbox_container_generation = sandbox_container_generation
        meta.run_started_at = datetime.now()
        meta.run_finished_at = None
        meta.run_error = ""
        session.add(meta)
        await session.commit()


async def mark_session_stopped(session_id: str, *, error: str = "") -> None:
    if await has_outstanding_session_work(session_id):
        return
    await finish_session_run(session_id, error=error)


async def finish_session_run(session_id: str, *, error: str = "") -> None:
    async with get_async_session() as session:
        meta = await session.get(AgentSessionMeta, session_id)
        if meta is None:
            return
        meta.is_running = False
        meta.run_finished_at = datetime.now()
        meta.run_error = _truncate_error(error)
        session.add(meta)
        await session.commit()


async def mark_sessions_stopped(session_ids: list[str], *, error: str = "") -> None:
    if not session_ids:
        return
    active_session_ids = {
        session_id for session_id in session_ids
        if await has_outstanding_session_work(session_id)
    }
    async with get_async_session() as session:
        metas = (await session.exec(
            select(AgentSessionMeta).where(AgentSessionMeta.session_id.in_(session_ids))
        )).all()
        for meta in metas:
            if meta.session_id in active_session_ids:
                continue
            meta.is_running = False
            meta.run_finished_at = datetime.now()
            meta.run_error = _truncate_error(error)
            session.add(meta)
        await session.commit()


async def force_mark_session_stopped(session_id: str, *, error: str = "") -> None:
    async with get_async_session() as session:
        meta = await session.get(AgentSessionMeta, session_id)
        if meta is None:
            return
        meta.is_running = False
        meta.run_finished_at = datetime.now()
        meta.run_error = _truncate_error(error)
        session.add(meta)
        await session.commit()


async def has_outstanding_session_work(session_id: str) -> bool:
    return await agent_notifications.has_active_session_notifications(session_id=session_id)


async def get_session_meta(session_id: str) -> AgentSessionMeta | None:
    async with get_async_session() as session:
        return await session.get(AgentSessionMeta, session_id)


async def record_session_usage(session_id: str, usage: Any) -> None:
    """Atomically accumulate LLM request and token counters onto AgentSessionMeta."""
    if not session_id or usage is None:
        return
    requests = max(int(getattr(usage, "requests", 0) or 0), 0)
    input_tokens = max(int(getattr(usage, "input_tokens", 0) or 0), 0)
    output_tokens = max(int(getattr(usage, "output_tokens", 0) or 0), 0)
    details = getattr(usage, "input_tokens_details", None)
    cached_tokens = max(int(getattr(details, "cached_tokens", 0) or 0), 0)
    total_tokens = max(int(getattr(usage, "total_tokens", 0) or 0), input_tokens + output_tokens)
    if not (requests or input_tokens or output_tokens or total_tokens):
        return
    async with get_async_session() as session:
        await session.execute(
            update(AgentSessionMeta)
            .where(AgentSessionMeta.session_id == session_id)
            .values(
                llm_requests=AgentSessionMeta.llm_requests + requests,
                input_tokens=AgentSessionMeta.input_tokens + input_tokens,
                output_tokens=AgentSessionMeta.output_tokens + output_tokens,
                cached_tokens=AgentSessionMeta.cached_tokens + cached_tokens,
                total_tokens=AgentSessionMeta.total_tokens + total_tokens,
            )
        )
        await session.commit()


def _truncate_error(value: str) -> str:
    value = value.strip().replace("\n", " ")
    return value if len(value) <= 500 else value[:499] + "..."
