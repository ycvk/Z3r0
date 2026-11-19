from __future__ import annotations

import asyncio
from aiohttp import web
import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from app import create_app
from database import get_async_session
from model.host.hosts import ManagedHost
from model.sandbox.containers import SandboxContainer
from model.sandbox.images import SandboxImage
from model.traffic.entries import SandboxTrafficEntry
from model.system_user.users import SystemUser
from model.work_project.projects import WorkProject, WorkProjectOwner
from schema.sandbox.containers import SandboxContainerStatus
from schema.system_user.users import SystemUserRole
from service.traffic import entries as traffic_service
from tests.conftest import auth_headers


async def _seed_two_tenants() -> dict[str, object]:
    async with get_async_session() as session:
        alice = SystemUser(role=SystemUserRole.USER, email="alice@test.local", username="alice")
        bob = SystemUser(role=SystemUserRole.USER, email="bob@test.local", username="bob")
        admin = SystemUser(role=SystemUserRole.ADMIN, email="admin@test.local", username="admin")
        host = ManagedHost(ip_address="127.0.0.1")
        image = SandboxImage(image_name="penetration-runtime:latest", control_proxy_port=8000)
        session.add_all([alice, bob, admin, host, image])
        await session.commit()
        for obj in (alice, bob, admin, host, image):
            await session.refresh(obj)

        alice_container = SandboxContainer(
            host_id=host.id or 0,
            container_name="alice-sbx",
            container_hash="hash-alice",
            owner_id=alice.id or 0,
            image_id=image.id or 0,
            status=SandboxContainerStatus.STOPPED,
        )
        bob_container = SandboxContainer(
            host_id=host.id or 0,
            container_name="bob-sbx",
            container_hash="hash-bob",
            owner_id=bob.id or 0,
            image_id=image.id or 0,
            status=SandboxContainerStatus.STOPPED,
        )
        session.add_all([alice_container, bob_container])
        await session.commit()
        await session.refresh(alice_container)
        await session.refresh(bob_container)

        alice_project = WorkProject(name="Alice Assessment", sandbox_container_id=alice_container.id)
        session.add(alice_project)
        await session.commit()
        await session.refresh(alice_project)
        await session.refresh(alice)
        await session.refresh(bob)
        await session.refresh(admin)
        await session.refresh(alice_container)
        await session.refresh(bob_container)
        session.add(WorkProjectOwner(project_id=alice_project.id or 0, user_id=alice.id or 0, position=0))

        entry = SandboxTrafficEntry(
            owner_id=alice.id or 0,
            sandbox_container_id=alice_container.id,
            project_id=alice_project.id,
            source="proxy",
            proxy_seq=1,
            method="POST",
            url="http://10.0.0.8/api/secret",
            host="10.0.0.8",
            path="/api/secret",
            status_code=200,
            request_body="secret-token=123",
            response_body='{"ok":true}',
        )
        system_entry = SandboxTrafficEntry(
            owner_id=0,
            sandbox_container_id=alice_container.id,
            project_id=alice_project.id,
            source="proxy",
            proxy_seq=2,
            method="GET",
            url="http://10.0.0.8/admin",
            host="10.0.0.8",
            path="/admin",
            status_code=200,
        )
        session.add_all([entry, system_entry])
        await session.commit()
        for obj in (alice, bob, admin, alice_container, bob_container, alice_project, entry, system_entry):
            await session.refresh(obj)

    return {
        "alice": alice,
        "bob": bob,
        "admin": admin,
        "alice_container": alice_container,
        "bob_container": bob_container,
        "alice_project": alice_project,
        "entry": entry,
        "system_entry": system_entry,
    }


@pytest.mark.asyncio
async def test_traffic_idor_blocked_for_other_user(test_db: None) -> None:
    seeded = await _seed_two_tenants()
    alice: SystemUser = seeded["alice"]  # type: ignore[assignment]
    bob: SystemUser = seeded["bob"]  # type: ignore[assignment]
    admin: SystemUser = seeded["admin"]  # type: ignore[assignment]
    alice_container: SandboxContainer = seeded["alice_container"]  # type: ignore[assignment]
    alice_project: WorkProject = seeded["alice_project"]  # type: ignore[assignment]
    system_entry: SandboxTrafficEntry = seeded["system_entry"]  # type: ignore[assignment]

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Bob cannot list Alice's container or project traffic
        r1 = await client.get(
            "/api/traffic",
            params={"sandbox_container_id": alice_container.id},
            headers=auth_headers(bob),
        )
        assert r1.status_code == 404

        r2 = await client.get(
            "/api/traffic",
            params={"project_id": alice_project.id},
            headers=auth_headers(bob),
        )
        assert r2.status_code == 404

        # Bob cannot read owner_id=0 background-synced entry belonging to Alice's container
        r_detail = await client.get(
            f"/api/traffic/{system_entry.id}",
            headers=auth_headers(bob),
        )
        assert r_detail.status_code == 404

        # Bob cannot ingest spoofed traffic into Alice's container or project
        r_ingest = await client.post(
            "/api/traffic/ingest",
            json={
                "sandbox_container_id": alice_container.id,
                "items": [{"method": "GET", "url": "http://spoof.local/"}],
            },
            headers=auth_headers(bob),
        )
        assert r_ingest.status_code == 404

        # Bob cannot clear Alice's container or project traffic
        r_del1 = await client.delete(
            "/api/traffic",
            params={"sandbox_container_id": alice_container.id},
            headers=auth_headers(bob),
        )
        assert r_del1.status_code == 404

        r_del2 = await client.delete(
            "/api/traffic",
            params={"project_id": alice_project.id},
            headers=auth_headers(bob),
        )
        assert r_del2.status_code == 404

        # Alice and Admin still see both entries intact
        r_alice = await client.get(
            "/api/traffic",
            params={"sandbox_container_id": alice_container.id},
            headers=auth_headers(alice),
        )
        assert r_alice.status_code == 200
        assert r_alice.json()["data"]["total"] == 2

        r_alice_detail = await client.get(
            f"/api/traffic/{system_entry.id}",
            headers=auth_headers(alice),
        )
        assert r_alice_detail.status_code == 200

        r_admin = await client.get(
            "/api/traffic",
            params={"project_id": alice_project.id},
            headers=auth_headers(admin),
        )
        assert r_admin.status_code == 200
        assert r_admin.json()["data"]["total"] == 2


@pytest.mark.asyncio
async def test_proxy_traffic_sync_dedup_and_clear_error_surface(test_db: None) -> None:
    items = [
        {
            "seq": 1,
            "method": "GET",
            "url": "https://target.example/api/v1/me?full=1",
            "host": "target.example",
            "path": "/api/v1/me?full=1",
            "status_code": 200,
            "request_headers": {"Authorization": "Bearer abc"},
            "request_body": "",
            "response_headers": {"Content-Type": "application/json"},
            "response_body": '{"user":"alice"}',
            "response_bytes": 16,
            "duration_ms": 12,
            "created_at": "2026-11-19T03:00:00Z",
        },
        {
            "seq": 2,
            "method": "POST",
            "url": "https://target.example/api/v1/login",
            "host": "target.example",
            "path": "/api/v1/login",
            "status_code": 302,
            "request_headers": {},
            "request_body": "u=admin&p=123",
            "response_headers": {},
            "response_body": "",
            "response_bytes": 0,
            "duration_ms": 8,
            "created_at": "2026-11-19T03:00:01Z",
        },
    ]
    fail_delete = False

    async def handle_proxy_traffic(request: web.Request) -> web.Response:
        if request.headers.get("X-Sandbox-Token") != "secret-proxy-token":
            return web.Response(status=401, text="unauthorized")
        if request.method == "GET":
            after_seq = int(request.query.get("after_seq", "0"))
            filtered = [it for it in items if it["seq"] > after_seq]
            return web.json_response({"items": filtered, "last_seq": 2})
        if request.method == "DELETE":
            if fail_delete:
                return web.Response(status=500, text="proxy failure")
            items.clear()
            return web.json_response({"ok": True})
        return web.Response(status=405)

    proxy_app = web.Application()
    proxy_app.router.add_route("*", "/traffic", handle_proxy_traffic)
    runner = web.AppRunner(proxy_app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    try:
        async with get_async_session() as session:
            alice = SystemUser(role=SystemUserRole.USER, email="a2@test.local", username="alice2")
            host = ManagedHost(ip_address="127.0.0.1")
            image = SandboxImage(image_name="penetration-runtime:latest", control_proxy_port=8000)
            session.add_all([alice, host, image])
            await session.commit()
            for obj in (alice, host, image):
                await session.refresh(obj)
            container = SandboxContainer(
                host_id=host.id or 0,
                container_name="live-sbx",
                container_hash="hash-live",
                owner_id=alice.id or 0,
                image_id=image.id or 0,
                control_proxy_host_port=port,
                control_proxy_token="secret-proxy-token",
                status=SandboxContainerStatus.RUNNING,
            )
            session.add(container)
            await session.commit()
            await session.refresh(container)
            await session.refresh(alice)

        # Concurrent sync calls must not insert duplicate proxy_seq rows
        counts = await asyncio.gather(
            traffic_service.sync_container_traffic(container.id or 0),
            traffic_service.sync_container_traffic(container.id or 0),
            traffic_service.sync_container_traffic(container.id or 0),
        )
        assert sum(counts) == 2

        page = await traffic_service.query_traffic(
            user_id=alice.id or 0,
            user_role=SystemUserRole.USER,
            sandbox_container_id=container.id,
        )
        assert page.total == 2

        # Proxy failure during clear_traffic on a running container must surface, not be swallowed
        fail_delete = True
        with pytest.raises(httpx.HTTPStatusError):
            await traffic_service.clear_traffic(
                user_id=alice.id or 0,
                user_role=SystemUserRole.USER,
                sandbox_container_id=container.id,
            )

        fail_delete = False
        deleted = await traffic_service.clear_traffic(
            user_id=alice.id or 0,
            user_role=SystemUserRole.USER,
            sandbox_container_id=container.id,
        )
        assert deleted == 2
        assert items == []
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_proxy_sync_stores_binary_bodies_and_follows_proxy_restart(test_db: None) -> None:
    def entry(seq: int, path: str, **overrides: object) -> dict[str, object]:
        return {
            "seq": seq,
            "method": "GET",
            "url": f"https://target.example{path}",
            "host": "target.example",
            "path": path,
            "status_code": 200,
            "request_headers": {},
            "request_body": "",
            "response_headers": {"Content-Type": "application/json"},
            "response_body": f"body-{seq}",
            "response_bytes": 6,
            "duration_ms": 3,
            "created_at": "2026-11-19T03:00:00Z",
            **overrides,
        }

    proxy = {
        "boot_id": "boot-a",
        "items": [
            entry(1, "/first"),
            entry(2, "/binary", request_body="req\x00uest", response_body="\x00\x01proto\x00buf\ud800"),
            entry(3, "/after-binary"),
        ],
    }

    async def handle_proxy_traffic(request: web.Request) -> web.Response:
        after_seq = int(request.query.get("after_seq", "0"))
        if request.query.get("boot_id", "") != proxy["boot_id"]:
            after_seq = 0
        items = [it for it in proxy["items"] if it["seq"] > after_seq]
        last_seq = max((it["seq"] for it in proxy["items"]), default=0)
        return web.json_response({"boot_id": proxy["boot_id"], "items": items, "last_seq": last_seq})

    proxy_app = web.Application()
    proxy_app.router.add_get("/traffic", handle_proxy_traffic)
    runner = web.AppRunner(proxy_app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    try:
        async with get_async_session() as session:
            alice = SystemUser(role=SystemUserRole.USER, email="a3@test.local", username="alice3")
            host = ManagedHost(ip_address="127.0.0.1")
            image = SandboxImage(image_name="penetration-runtime:latest", control_proxy_port=8000)
            session.add_all([alice, host, image])
            await session.commit()
            for obj in (alice, host, image):
                await session.refresh(obj)
            container = SandboxContainer(
                host_id=host.id or 0,
                container_name="restart-sbx",
                container_hash="hash-restart",
                owner_id=alice.id or 0,
                image_id=image.id or 0,
                control_proxy_host_port=port,
                control_proxy_token="secret-proxy-token",
                status=SandboxContainerStatus.RUNNING,
            )
            session.add(container)
            await session.commit()
            await session.refresh(container)
            await session.refresh(alice)
        container_id = container.id or 0

        async def stored() -> list[SandboxTrafficEntry]:
            async with get_async_session() as session:
                return list((await session.exec(
                    select(SandboxTrafficEntry)
                    .where(SandboxTrafficEntry.sandbox_container_id == container_id)
                    .order_by(SandboxTrafficEntry.id)
                )).all())

        # A binary payload must neither fail the batch nor block the entries behind it.
        assert await traffic_service.sync_container_traffic(container_id) == 3
        rows = await stored()
        assert [row.path for row in rows] == ["/first", "/binary", "/after-binary"]
        assert rows[1].request_body == "request"
        assert "\x00" not in rows[1].response_body and "protobuf" in rows[1].response_body
        assert await traffic_service.sync_container_traffic(container_id) == 0

        # The proxy process restarts: its sequence starts over under a new boot id.
        proxy["boot_id"] = "boot-b"
        proxy["items"] = [entry(1, "/after-restart")]
        assert await traffic_service.sync_container_traffic(container_id) == 1
        assert await traffic_service.sync_container_traffic(container_id) == 0
        proxy["items"].append(entry(2, "/after-restart-2"))
        assert await traffic_service.sync_container_traffic(container_id) == 1
        rows = await stored()
        assert [row.path for row in rows] == [
            "/first", "/binary", "/after-binary", "/after-restart", "/after-restart-2",
        ]
        assert [(row.proxy_boot, row.proxy_seq) for row in rows[3:]] == [("boot-b", 1), ("boot-b", 2)]

        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r_ingest = await client.post(
                "/api/traffic/ingest",
                json={
                    "sandbox_container_id": container_id,
                    "items": [{
                        "method": "POST",
                        "url": "https://target.example/upload",
                        "request_body": "part\u0000one",
                        "response_body": "\u0000\u0000done",
                    }],
                },
                headers=auth_headers(alice),
            )
            assert r_ingest.status_code == 200
        ingested = (await stored())[-1]
        assert (ingested.request_body, ingested.response_body) == ("partone", "done")
    finally:
        await runner.cleanup()
