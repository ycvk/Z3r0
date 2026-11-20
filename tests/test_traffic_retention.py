from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from aiohttp import web
from sqlmodel import col, select

from config import get_config
from database import get_async_session
from model.host.hosts import ManagedHost
from model.sandbox.containers import SandboxContainer
from model.sandbox.images import SandboxImage
from model.system_user.users import SystemUser
from model.traffic.entries import SandboxTrafficEntry
from schema.sandbox.containers import SandboxContainerStatus
from schema.system_user.users import SystemUserRole
from service.traffic import entries as traffic_entries
from service.traffic import runtime as traffic_runtime
from service.traffic.retention import prune_traffic

NOW = datetime(2026, 11, 20, 12, 0, 0)
DAY = 24 * 60 * 60


async def _seed_containers(count: int) -> list[int]:
    async with get_async_session() as session:
        user = SystemUser(role=SystemUserRole.USER, email="keep@test.local", username="keeper")
        host = ManagedHost(ip_address="127.0.0.1")
        image = SandboxImage(image_name="penetration-runtime:latest", control_proxy_port=8000)
        session.add_all([user, host, image])
        await session.commit()
        for row in (user, host, image):
            await session.refresh(row)
        containers = [
            SandboxContainer(
                host_id=host.id or 0,
                container_name=f"sbx-{index}",
                container_hash=f"hash-{index}",
                owner_id=user.id or 0,
                image_id=image.id or 0,
                status=SandboxContainerStatus.STOPPED,
            )
            for index in range(count)
        ]
        session.add_all(containers)
        await session.commit()
        for row in containers:
            await session.refresh(row)
        return [row.id or 0 for row in containers]


async def _add(container_id: int | None, path: str, *, age_days: float = 0, source: str = "proxy", seq: int = 0) -> None:
    async with get_async_session() as session:
        session.add(SandboxTrafficEntry(
            sandbox_container_id=container_id,
            source=source,
            proxy_seq=seq,
            proxy_boot="boot-1" if source == "proxy" else "",
            path=path,
            created_at=NOW - timedelta(days=age_days),
        ))
        await session.commit()


async def _paths(container_id: int | None) -> list[str]:
    async with get_async_session() as session:
        scope = (
            col(SandboxTrafficEntry.sandbox_container_id).is_(None)
            if container_id is None
            else col(SandboxTrafficEntry.sandbox_container_id) == container_id
        )
        rows = (await session.exec(select(SandboxTrafficEntry).where(scope).order_by(col(SandboxTrafficEntry.id)))).all()
    return [row.path for row in rows]


@pytest.mark.asyncio
async def test_entries_past_their_retention_are_deleted(test_db: None) -> None:
    (container_id,) = await _seed_containers(1)
    await _add(container_id, "/old", age_days=8, seq=1)
    await _add(container_id, "/recent", age_days=6, seq=2)
    await _add(None, "/old-ingest", age_days=8, source="ingest")
    await _add(None, "/recent-ingest", age_days=1, source="ingest")

    deleted = await prune_traffic(retention_seconds=7 * DAY, max_entries_per_container=0, now=NOW)

    assert deleted == 2
    assert await _paths(container_id) == ["/recent"]
    assert await _paths(None) == ["/recent-ingest"]


@pytest.mark.asyncio
async def test_the_entry_synchronization_resumes_from_outlives_its_retention(test_db: None) -> None:
    (container_id,) = await _seed_containers(1)
    await _add(container_id, "/first", age_days=30, seq=41)
    await _add(container_id, "/last-pulled", age_days=29, seq=42)
    await _add(container_id, "/ingested-later", age_days=28, source="ingest")
    # A container that no longer exists has nothing to resume, so nothing of it is kept.
    await _add(999_999, "/of-deleted-container", age_days=30, seq=7)

    await prune_traffic(retention_seconds=7 * DAY, max_entries_per_container=0, now=NOW)

    assert await _paths(container_id) == ["/last-pulled"]
    assert await _paths(999_999) == []
    async with get_async_session() as session:
        kept = (await session.exec(select(SandboxTrafficEntry))).one()
    assert (kept.proxy_boot, kept.proxy_seq) == ("boot-1", 42)


@pytest.mark.asyncio
async def test_each_container_keeps_its_newest_entries_up_to_the_limit(test_db: None) -> None:
    crowded, quiet = await _seed_containers(2)
    for index in range(5):
        await _add(crowded, f"/crowded/{index}", seq=index + 1)
    for index in range(3):
        await _add(quiet, f"/quiet/{index}", seq=index + 1)
    for index in range(4):
        await _add(None, f"/ingest/{index}", source="ingest")

    deleted = await prune_traffic(retention_seconds=0, max_entries_per_container=3, now=NOW)

    assert deleted == 2
    assert await _paths(crowded) == ["/crowded/2", "/crowded/3", "/crowded/4"]
    assert await _paths(quiet) == ["/quiet/0", "/quiet/1", "/quiet/2"]
    assert len(await _paths(None)) == 4


@pytest.mark.asyncio
async def test_zero_keeps_everything(test_db: None) -> None:
    (container_id,) = await _seed_containers(1)
    for index in range(4):
        await _add(container_id, f"/kept/{index}", age_days=400, seq=index + 1)

    assert await prune_traffic(retention_seconds=0, max_entries_per_container=0, now=NOW) == 0
    assert len(await _paths(container_id)) == 4


@pytest.mark.asyncio
async def test_the_background_runtime_prunes_with_the_configured_limits(
    test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (container_id,) = await _seed_containers(1)
    for index in range(4):
        await _add(container_id, f"/entry/{index}", seq=index + 1)
    runtime_config = get_config().agent_runtime
    monkeypatch.setattr(runtime_config, "traffic_retention_seconds", 0)
    monkeypatch.setattr(runtime_config, "traffic_max_entries_per_container", 2)

    assert await traffic_runtime.prune_traffic_by_config() == 2
    assert await _paths(container_id) == ["/entry/2", "/entry/3"]


@pytest.mark.asyncio
async def test_pruning_does_not_make_the_next_pull_store_the_proxy_buffer_again(test_db: None) -> None:
    buffered = [
        {"seq": seq, "method": "GET", "url": f"https://target.example/{seq}", "status_code": 200,
         "created_at": "2026-11-01T00:00:00Z"}
        for seq in (1, 2, 3)
    ]

    async def handle_proxy_traffic(request: web.Request) -> web.Response:
        after_seq = int(request.query.get("after_seq", "0")) if request.query.get("boot_id") == "boot-a" else 0
        items = [item for item in buffered if item["seq"] > after_seq]
        return web.json_response({"boot_id": "boot-a", "items": items, "last_seq": 3})

    proxy_app = web.Application()
    proxy_app.router.add_get("/traffic", handle_proxy_traffic)
    runner = web.AppRunner(proxy_app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    try:
        (container_id,) = await _seed_containers(1)
        async with get_async_session() as session:
            container = await session.get(SandboxContainer, container_id)
            assert container is not None
            container.status = SandboxContainerStatus.RUNNING
            container.control_proxy_host_port = port
            container.control_proxy_token = "proxy-token"
            session.add(container)
            await session.commit()

        assert await traffic_entries.sync_container_traffic(container_id) == 3
        # Every stored entry is past its retention while the proxy still buffers all three.
        assert await prune_traffic(retention_seconds=DAY, max_entries_per_container=0, now=NOW) == 2

        assert await traffic_entries.sync_container_traffic(container_id) == 0
        assert await _paths(container_id) == ["/3"]
    finally:
        await runner.cleanup()
