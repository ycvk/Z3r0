"""Background synchronization of captured traffic from every running sandbox container."""

from __future__ import annotations

import asyncio

from sqlmodel import select

from database import get_async_session
from logger import get_logger
from model.sandbox.containers import SandboxContainer
from schema.sandbox.containers import SandboxContainerStatus
from service.traffic.entries import sync_container_traffic


logger = get_logger(__name__)

_TRAFFIC_SYNC_INTERVAL_SECONDS = 3.0
_TRAFFIC_SYNC_BATCH_SIZE = 16

_traffic_sync_task: asyncio.Task[None] | None = None


async def sync_running_containers_traffic() -> None:
    async with get_async_session() as session:
        container_ids = list(
            (
                await session.exec(
                    select(SandboxContainer.id).where(
                        SandboxContainer.status == SandboxContainerStatus.RUNNING,
                        SandboxContainer.control_proxy_host_port > 0,
                        SandboxContainer.control_proxy_token != "",
                    )
                )
            ).all()
        )
    valid_ids = [cid for cid in container_ids if cid is not None]
    for offset in range(0, len(valid_ids), _TRAFFIC_SYNC_BATCH_SIZE):
        batch = valid_ids[offset:offset + _TRAFFIC_SYNC_BATCH_SIZE]
        await asyncio.gather(*(_sync_container_traffic_safely(cid) for cid in batch))


async def _sync_container_traffic_safely(container_id: int) -> None:
    try:
        await sync_container_traffic(container_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("background sandbox traffic sync failed: container=%s", container_id, exc_info=True)


async def _traffic_sync_loop() -> None:
    while True:
        try:
            await sync_running_containers_traffic()
            await asyncio.sleep(_TRAFFIC_SYNC_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("sandbox traffic sync loop iteration failed")
            await asyncio.sleep(_TRAFFIC_SYNC_INTERVAL_SECONDS)


async def start_sandbox_traffic_sync_runtime() -> None:
    global _traffic_sync_task
    if _traffic_sync_task is not None and not _traffic_sync_task.done():
        return
    _traffic_sync_task = asyncio.create_task(
        _traffic_sync_loop(),
        name="sandbox-traffic-sync-runtime",
    )
    logger.info("sandbox traffic sync runtime started")


async def stop_sandbox_traffic_sync_runtime() -> None:
    global _traffic_sync_task
    task, _traffic_sync_task = _traffic_sync_task, None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("sandbox traffic sync runtime stopped")
