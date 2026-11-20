"""Deletes captured traffic that is past its retention or beyond the per-container limit."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, Delete, delete, func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from database import get_async_session
from logger import get_logger
from model.sandbox.containers import SandboxContainer
from model.traffic.entries import SandboxTrafficEntry


logger = get_logger(__name__)


def _sync_cursor_entry_ids():
    """The newest proxy entry of every existing container.

    Synchronization resumes from that entry's boot id and sequence number. Without it the next
    pull would store the whole proxy buffer again.
    """
    return (
        select(func.max(SandboxTrafficEntry.id))
        .where(
            col(SandboxTrafficEntry.source) == "proxy",
            col(SandboxTrafficEntry.sandbox_container_id).in_(select(SandboxContainer.id)),
        )
        .group_by(col(SandboxTrafficEntry.sandbox_container_id))
    )


async def _delete(session: AsyncSession, statement: Delete) -> int:
    result = cast("CursorResult[Any]", await session.execute(statement))
    return int(result.rowcount or 0)


async def prune_traffic(
    *,
    retention_seconds: int,
    max_entries_per_container: int,
    now: datetime | None = None,
) -> int:
    """Delete expired entries, then the oldest entries of each container above the limit.

    A limit of 0 is not applied. Returns the number of deleted entries.
    """
    deleted = 0
    async with get_async_session() as session:
        if retention_seconds > 0:
            cutoff = (now or datetime.now()) - timedelta(seconds=retention_seconds)
            deleted += await _delete(session, delete(SandboxTrafficEntry).where(
                col(SandboxTrafficEntry.created_at) < cutoff,
                col(SandboxTrafficEntry.id).not_in(_sync_cursor_entry_ids()),
            ))

        if max_entries_per_container > 0:
            crowded = (await session.exec(
                select(SandboxTrafficEntry.sandbox_container_id)
                .where(col(SandboxTrafficEntry.sandbox_container_id).is_not(None))
                .group_by(col(SandboxTrafficEntry.sandbox_container_id))
                .having(func.count() > max_entries_per_container)
            )).all()
            for container_id in crowded:
                oldest_kept_id = (await session.exec(
                    select(SandboxTrafficEntry.id)
                    .where(col(SandboxTrafficEntry.sandbox_container_id) == container_id)
                    .order_by(col(SandboxTrafficEntry.id).desc())
                    .offset(max_entries_per_container - 1)
                    .limit(1)
                )).first()
                if oldest_kept_id is None:
                    # the container's traffic was cleared since it was counted
                    continue
                deleted += await _delete(session, delete(SandboxTrafficEntry).where(
                    col(SandboxTrafficEntry.sandbox_container_id) == container_id,
                    col(SandboxTrafficEntry.id) < oldest_kept_id,
                    col(SandboxTrafficEntry.id).not_in(_sync_cursor_entry_ids()),
                ))
        await session.commit()
    if deleted:
        logger.info("captured traffic pruned: %s entries", deleted)
    return deleted
