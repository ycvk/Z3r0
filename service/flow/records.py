from datetime import datetime
from uuid import uuid4

from sqlmodel import col, select

from core.runtime.coordination import cancel_session_subagents
from core.sandbox.command_jobs import cancel_session_async_sandbox_commands
from database import get_async_session
from logger import get_logger
from middleware.system_user import AuthUser
from model.agent.sessions import AgentSessionMeta
from model.flow.flows import Flow, FlowSubtask
from schema.agent.credentials import CreateSessionCredentialRequest, SessionCredentialSchema
from schema.flow.flows import (
    FLOW_RECOVERABLE_STATUSES,
    FLOW_TERMINAL_STATUSES,
    MAX_FLOW_SUBTASKS,
    CreateFlowRequest,
    FlowSchema,
    FlowStatus,
    FlowSubtaskSchema,
    FlowSubtaskStatus,
    FlowSummarySchema,
)
from schema.sandbox.containers import SandboxContainerStatus
from schema.system_user.users import SystemUserRole
from schema.agent.sessions import SessionType
from service.agent import runtime as agent_runtime
from service.agent import credentials as agent_credentials
from service.agent import sessions as agent_sessions
from service.common.pagination import Page, paginate_statement
from service.flow.locking import lock_flow_by_public_id
from service.sandbox.records import load_sandbox_container_record, sandbox_container_can_manage
from service.work_project.projects import get_work_project_for_user


logger = get_logger(__name__)

_TITLE_MAX_LEN = 80


class FlowValidationError(ValueError):
    pass


def flow_title(goal: str) -> str:
    first_line = goal.strip().splitlines()[0].strip() if goal.strip() else ""
    return first_line[:_TITLE_MAX_LEN]


def _subtask_schema(row: FlowSubtask) -> FlowSubtaskSchema:
    return FlowSubtaskSchema(
        id=row.id or 0,
        position=row.position,
        title=row.title,
        description=row.description,
        status=row.status,
        summary=row.summary,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def to_schema(
    flow: Flow,
    subtasks: list[FlowSubtask] | tuple[FlowSubtask, ...] = (),
    *,
    meta: AgentSessionMeta | None = None,
    credentials: list[SessionCredentialSchema] | None = None,
) -> FlowSchema:
    return FlowSchema(
        id=flow.id or 0,
        public_id=flow.public_id,
        owner_id=flow.owner_id,
        title=flow.title,
        goal=flow.goal,
        status=flow.status,
        sandbox_container_id=flow.sandbox_container_id,
        project_id=flow.project_id,
        session_id=flow.session_id,
        ask_question=flow.ask_question,
        report_id=flow.report_id,
        error=flow.error,
        subtasks=[
            _subtask_schema(item)
            for item in sorted(subtasks, key=lambda row: (row.position, row.id or 0))
        ],
        credentials=credentials or [],
        llm_requests=meta.llm_requests if meta else 0,
        input_tokens=meta.input_tokens if meta else 0,
        output_tokens=meta.output_tokens if meta else 0,
        cached_tokens=meta.cached_tokens if meta else 0,
        total_tokens=meta.total_tokens if meta else 0,
        created_at=flow.created_at,
        updated_at=flow.updated_at,
        started_at=flow.started_at,
        finished_at=flow.finished_at,
    )


def to_summary(flow: Flow, *, meta: AgentSessionMeta | None = None) -> FlowSummarySchema:
    return FlowSummarySchema(
        id=flow.id or 0,
        public_id=flow.public_id,
        title=flow.title,
        status=flow.status,
        sandbox_container_id=flow.sandbox_container_id,
        project_id=flow.project_id,
        llm_requests=meta.llm_requests if meta else 0,
        input_tokens=meta.input_tokens if meta else 0,
        output_tokens=meta.output_tokens if meta else 0,
        cached_tokens=meta.cached_tokens if meta else 0,
        total_tokens=meta.total_tokens if meta else 0,
        created_at=flow.created_at,
        updated_at=flow.updated_at,
        started_at=flow.started_at,
        finished_at=flow.finished_at,
    )


def _can_access_flow(flow: Flow, user: AuthUser) -> bool:
    return user.role == SystemUserRole.ADMIN or flow.owner_id == user.id


async def _load_subtasks(flow_id: int) -> list[FlowSubtask]:
    async with get_async_session() as session:
        result = await session.exec(
            select(FlowSubtask).where(FlowSubtask.flow_id == flow_id).order_by(col(FlowSubtask.position))
        )
        return list(result.all())


async def _schema_for(flow: Flow) -> FlowSchema:
    subtasks = await _load_subtasks(flow.id or 0) if flow.id else []
    meta = await agent_sessions.get_session_meta(flow.session_id)
    creds = await agent_credentials.list_credentials_for_context(
        session_id=flow.session_id,
        project_id=flow.project_id,
    )
    return to_schema(flow, subtasks, meta=meta, credentials=creds)

async def create_flow(request: CreateFlowRequest, user: AuthUser) -> FlowSchema:
    record = await load_sandbox_container_record(request.sandbox_container_id)
    if record is None or not sandbox_container_can_manage(record.container, user.id, user.role):
        raise FlowValidationError("sandbox container not found")
    if record.container.status != SandboxContainerStatus.RUNNING:
        raise FlowValidationError("sandbox container is not running")
    if request.project_id is not None:
        project = await get_work_project_for_user(
            request.project_id,
            user_id=user.id,
            user_role=user.role,
        )
        if project is None:
            raise FlowValidationError("work project not found")

    title = flow_title(request.goal)
    public_id = str(uuid4())
    session_id = await agent_sessions.create_session(
        user.id,
        session_type=SessionType.FLOW,
        title=title,
        project_id=request.project_id,
        sandbox_container_id=request.sandbox_container_id,
        sandbox_container_generation=0,
    )
    now = datetime.now()
    flow = Flow(
        public_id=public_id,
        owner_id=user.id,
        title=title,
        goal=request.goal,
        status=FlowStatus.QUEUED,
        sandbox_container_id=request.sandbox_container_id,
        project_id=request.project_id,
        session_id=session_id,
        created_at=now,
        updated_at=now,
    )
    try:
        async with get_async_session() as session:
            session.add(flow)
            await session.commit()
            await session.refresh(flow)
    except Exception:
        logger.exception("failed to persist flow after session create: %s", session_id)
        await agent_sessions.delete_session(
            session_id,
            user_id=user.id,
            user_role=user.role,
            allow_project_session=request.project_id is not None,
        )
        raise
    logger.info("flow created: public_id=%s session=%s", public_id, session_id)
    from core.flow.controller import schedule_flow
    schedule_flow(public_id)
    return await _schema_for(flow)


async def list_flows(user: AuthUser, page: int, size: int) -> Page[FlowSummarySchema]:
    stmt = select(Flow).order_by(col(Flow.updated_at).desc(), col(Flow.id).desc())
    if user.role != SystemUserRole.ADMIN:
        stmt = stmt.where(Flow.owner_id == user.id)
    page_result = await paginate_statement(stmt, page=page, size=size)
    metas: dict[str, AgentSessionMeta] = {}
    if page_result.items:
        session_ids = [item.session_id for item in page_result.items]
        async with get_async_session() as session:
            rows = (await session.exec(
                select(AgentSessionMeta).where(col(AgentSessionMeta.session_id).in_(session_ids))
            )).all()
            metas = {row.session_id: row for row in rows}
    return Page(
        page=page_result.page,
        size=page_result.size,
        total=page_result.total,
        items=[to_summary(item, meta=metas.get(item.session_id)) for item in page_result.items],
    )


async def get_flow(public_id: str, user: AuthUser) -> FlowSchema | None:
    async with get_async_session() as session:
        flow = (await session.exec(select(Flow).where(Flow.public_id == public_id))).one_or_none()
    if flow is None or not _can_access_flow(flow, user):
        return None
    return await _schema_for(flow)


async def cancel_flow(public_id: str, user: AuthUser) -> FlowSchema | None:
    async with get_async_session() as session:
        flow = await lock_flow_by_public_id(session, public_id)
        if flow is None or not _can_access_flow(flow, user):
            return None
        if flow.status not in FLOW_TERMINAL_STATUSES:
            flow.status = FlowStatus.CANCELLED
            flow.ask_question = None
            flow.finished_at = datetime.now()
            flow.updated_at = flow.finished_at
            session.add(flow)
            await session.commit()
            await session.refresh(flow)
        else:
            await session.refresh(flow)
        session_id = flow.session_id
    from core.flow.controller import cancel_scheduled_flow
    await cancel_scheduled_flow(public_id)
    await cancel_session_subagents(session_id)
    await cancel_session_async_sandbox_commands(session_id)
    try:
        await agent_runtime.interrupt_turn(session_id=session_id, user=user)
    except Exception:
        logger.debug("flow interrupt skipped: %s", public_id, exc_info=True)
    return await _schema_for(flow)


async def answer_flow_input(public_id: str, user: AuthUser, text: str) -> FlowSchema | None:
    flow = await get_flow(public_id, user)
    if flow is None:
        return None
    if flow.status != FlowStatus.WAITING:
        raise FlowValidationError("flow is not waiting")
    from core.flow.controller import submit_flow_input
    await submit_flow_input(public_id, text)
    return await get_flow(public_id, user)


async def add_flow_credential(
    public_id: str,
    request: CreateSessionCredentialRequest,
    user: AuthUser,
) -> FlowSchema | None:
    flow = await load_flow_row(public_id)
    if flow is None or not _can_access_flow(flow, user):
        return None
    await agent_credentials.upsert_credential(
        owner_id=user.id,
        session_id=flow.session_id,
        project_id=flow.project_id,
        target=request.target,
        credential_type=request.credential_type,
        username=request.username,
        secret=request.secret,
        note=request.note,
    )
    return await _schema_for(flow)


async def remove_flow_credential(
    public_id: str,
    credential_id: int,
    user: AuthUser,
) -> FlowSchema | None:
    flow = await load_flow_row(public_id)
    if flow is None or not _can_access_flow(flow, user):
        return None
    deleted = await agent_credentials.delete_credential(
        credential_id,
        session_id=flow.session_id,
        project_id=flow.project_id,
    )
    if not deleted:
        raise FlowValidationError("credential not found")
    return await _schema_for(flow)


async def load_flow_row(public_id: str) -> Flow | None:
    async with get_async_session() as session:
        return (await session.exec(select(Flow).where(Flow.public_id == public_id))).one_or_none()


async def load_flow_row_by_session(session_id: str) -> Flow | None:
    async with get_async_session() as session:
        return (await session.exec(select(Flow).where(Flow.session_id == session_id))).one_or_none()


async def list_recoverable_flow_ids() -> list[str]:
    async with get_async_session() as session:
        rows = (await session.exec(
            select(Flow.public_id).where(col(Flow.status).in_(FLOW_RECOVERABLE_STATUSES))
        )).all()
    return [str(item) for item in rows]


async def update_flow_status(
    public_id: str,
    status: FlowStatus,
    *,
    error: str | None = None,
    ask_question: str | None = None,
    report_id: str | None = None,
    terminal: bool = False,
) -> Flow | None:
    async with get_async_session() as session:
        flow = await lock_flow_by_public_id(session, public_id)
        if flow is None:
            return None
        flow.status = status
        flow.updated_at = datetime.now()
        if error is not None:
            flow.error = error
        if ask_question is not None or status != FlowStatus.WAITING:
            flow.ask_question = ask_question
        if report_id is not None:
            flow.report_id = report_id
        if status == FlowStatus.PLANNING and flow.started_at is None:
            flow.started_at = flow.updated_at
        if terminal:
            flow.finished_at = flow.updated_at
            flow.ask_question = None
        session.add(flow)
        await session.commit()
        await session.refresh(flow)
        return flow


async def replace_plan(public_id: str, items: list[dict[str, str]]) -> Flow | None:
    async with get_async_session() as session:
        flow = await lock_flow_by_public_id(session, public_id)
        if flow is None or flow.id is None:
            return None
        existing = (await session.exec(select(FlowSubtask).where(FlowSubtask.flow_id == flow.id))).all()
        for row in existing:
            await session.delete(row)
        now = datetime.now()
        for position, item in enumerate(items[:MAX_FLOW_SUBTASKS]):
            session.add(FlowSubtask(
                flow_id=flow.id,
                position=position,
                title=item["title"],
                description=item.get("description") or "",
                status=FlowSubtaskStatus.PLANNED,
                created_at=now,
                updated_at=now,
            ))
        flow.status = FlowStatus.RUNNING
        flow.updated_at = now
        session.add(flow)
        await session.commit()
        await session.refresh(flow)
        return flow


async def activate_next_subtask(public_id: str) -> FlowSubtask | None:
    async with get_async_session() as session:
        flow = await lock_flow_by_public_id(session, public_id)
        if flow is None or flow.id is None:
            return None
        current = (await session.exec(
            select(FlowSubtask)
            .where(FlowSubtask.flow_id == flow.id, FlowSubtask.status == FlowSubtaskStatus.RUNNING)
        )).first()
        if current is not None:
            return current
        nxt = (await session.exec(
            select(FlowSubtask)
            .where(FlowSubtask.flow_id == flow.id, FlowSubtask.status == FlowSubtaskStatus.PLANNED)
            .order_by(col(FlowSubtask.position))
        )).first()
        if nxt is None:
            return None
        now = datetime.now()
        nxt.status = FlowSubtaskStatus.RUNNING
        nxt.updated_at = now
        flow.status = FlowStatus.RUNNING
        flow.updated_at = now
        session.add(nxt)
        session.add(flow)
        await session.commit()
        await session.refresh(nxt)
        return nxt


async def complete_running_subtask(public_id: str, summary: str) -> FlowSubtask | None:
    async with get_async_session() as session:
        flow = await lock_flow_by_public_id(session, public_id)
        if flow is None or flow.id is None:
            return None
        current = (await session.exec(
            select(FlowSubtask)
            .where(FlowSubtask.flow_id == flow.id, FlowSubtask.status == FlowSubtaskStatus.RUNNING)
        )).first()
        if current is None:
            return None
        now = datetime.now()
        current.status = FlowSubtaskStatus.DONE
        current.summary = summary
        current.updated_at = now
        flow.updated_at = now
        session.add(current)
        session.add(flow)
        await session.commit()
        await session.refresh(current)
        return current


async def fail_flow_run(public_id: str, error: str) -> None:
    async with get_async_session() as session:
        flow = await lock_flow_by_public_id(session, public_id)
        if flow is None or flow.status in FLOW_TERMINAL_STATUSES:
            return
        now = datetime.now()
        if flow.id is not None:
            current = (await session.exec(
                select(FlowSubtask)
                .where(FlowSubtask.flow_id == flow.id, FlowSubtask.status == FlowSubtaskStatus.RUNNING)
            )).first()
            if current is not None:
                current.status = FlowSubtaskStatus.FAILED
                current.updated_at = now
                session.add(current)
        flow.status = FlowStatus.FAILED
        flow.error = error
        flow.finished_at = now
        flow.updated_at = now
        flow.ask_question = None
        session.add(flow)
        await session.commit()


def reindex_flow_subtasks(
    frozen: list[FlowSubtask],
    planned: list[FlowSubtask],
    now: datetime,
) -> None:
    """Assign unique global positions: finished rows first, then remaining planned rows."""
    for index, row in enumerate((*frozen, *planned)):
        if row.position != index:
            row.position = index
            row.updated_at = now


async def apply_plan_patch(public_id: str, ops: list[dict[str, object]]) -> None:
    async with get_async_session() as session:
        flow = await lock_flow_by_public_id(session, public_id)
        if flow is None or flow.id is None or not ops:
            return
        rows = list((await session.exec(
            select(FlowSubtask)
            .where(FlowSubtask.flow_id == flow.id)
            .order_by(col(FlowSubtask.position), col(FlowSubtask.id))
        )).all())
        frozen = [row for row in rows if row.status != FlowSubtaskStatus.PLANNED]
        planned = [row for row in rows if row.status == FlowSubtaskStatus.PLANNED]
        now = datetime.now()
        for raw in ops:
            op = str(raw.get("op") or "")
            try:
                position = int(raw.get("position"))
            except (TypeError, ValueError):
                continue
            if op == "remove":
                match = next((row for row in planned if row.position == position), None)
                if match is not None:
                    planned.remove(match)
                    await session.delete(match)
            elif op == "replace":
                match = next((row for row in planned if row.position == position), None)
                if match is not None:
                    title = str(raw.get("title") or match.title).strip()
                    if title:
                        match.title = title[:160]
                    if "description" in raw:
                        match.description = str(raw.get("description") or "")
                    match.updated_at = now
                    session.add(match)
            elif op == "add" and len(planned) < MAX_FLOW_SUBTASKS:
                title = str(raw.get("title") or "").strip()
                if not title:
                    continue
                insert_at = sum(1 for row in planned if row.position < position)
                insert_at = min(max(insert_at, 0), len(planned))
                row = FlowSubtask(
                    flow_id=flow.id,
                    position=position,
                    title=title[:160],
                    description=str(raw.get("description") or ""),
                    status=FlowSubtaskStatus.PLANNED,
                    created_at=now,
                    updated_at=now,
                )
                planned.insert(insert_at, row)
                session.add(row)
        reindex_flow_subtasks(frozen, planned, now)
        for row in (*frozen, *planned):
            session.add(row)
        flow.updated_at = now
        session.add(flow)
        await session.commit()
