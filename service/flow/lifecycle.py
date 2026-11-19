"""What happens to Flows when the user who started them or their work project is deleted.

A Flow is deleted by deleting its session; the Flow record goes with it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ColumnElement, update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_async_session
from logger import get_logger
from model.agent.sessions import AgentSessionMeta
from model.flow.flows import Flow
from schema.agent.sessions import SessionType
from schema.flow.flows import FLOW_TERMINAL_STATUSES, FlowStatus
from service.agent.sessions import delete_flow_session
from service.flow.runs import cancel_flow_run


logger = get_logger(__name__)


async def delete_private_flows_for_owner(owner_id: int) -> None:
    """Delete every Flow outside a work project that a removed user started."""
    await _delete_flows(
        col(AgentSessionMeta.owner_id) == owner_id,
        col(AgentSessionMeta.project_id).is_(None),
    )


async def delete_work_project_flows(project_id: int) -> None:
    await _delete_flows(col(AgentSessionMeta.project_id) == project_id)


async def cancel_project_flows_for_owner(owner_id: int, reason: str) -> None:
    """Cancel the unfinished Flows that a removed user started inside work projects."""
    unfinished = (
        col(Flow.owner_id) == owner_id,
        col(Flow.project_id).is_not(None),
        col(Flow.status).not_in(FLOW_TERMINAL_STATUSES),
    )
    async with get_async_session() as session:
        public_ids = list((await session.exec(select(Flow.public_id).where(*unfinished))).all())
    for public_id in public_ids:
        await cancel_flow_run(public_id)
    async with get_async_session() as session:
        flows = (await session.exec(select(Flow).where(*unfinished).with_for_update())).all()
        now = datetime.now()
        for flow in flows:
            flow.status = FlowStatus.CANCELLED
            flow.error = reason
            flow.ask_question = None
            flow.pending_input = None
            flow.finished_at = now
            flow.updated_at = now
            session.add(flow)
        await session.commit()


async def hand_over_project_flows_in_tx(session: AsyncSession, removed_owner_id: int) -> None:
    """Give the Flows a removed user started inside work projects to the owner of their session."""
    await session.execute(
        update(Flow)
        .where(
            col(Flow.owner_id) == removed_owner_id,
            col(Flow.session_id) == col(AgentSessionMeta.session_id),
        )
        .values(owner_id=AgentSessionMeta.owner_id)
    )


async def _delete_flows(*conditions: ColumnElement[bool]) -> None:
    async with get_async_session() as session:
        rows = (await session.exec(
            select(AgentSessionMeta.session_id, Flow.public_id)
            .join(Flow, col(Flow.session_id) == col(AgentSessionMeta.session_id), isouter=True)
            .where(col(AgentSessionMeta.session_type) == SessionType.FLOW, *conditions)
        )).all()
    for session_id, public_id in rows:
        if public_id is not None:
            await cancel_flow_run(public_id)
        await delete_flow_session(session_id)
        logger.info("flow deleted: session=%s public_id=%s", session_id, public_id)
