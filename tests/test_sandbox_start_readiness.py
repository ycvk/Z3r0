from __future__ import annotations

import asyncio
import socket

import pytest
from aiohttp import web

from core.errors import ValidationError
from database import get_async_session
from model.host.hosts import ManagedHost
from model.sandbox.containers import SandboxContainer
from model.sandbox.images import SandboxImage
from model.system_user.users import SystemUser
from schema.sandbox.containers import SandboxContainerStatus
from schema.system_user.users import SystemUserRole
from service.sandbox import control_proxy, lifecycle
from service.sandbox.docker_ops import DockerContainerState
from service.sandbox.status import ContainerStatusSnapshot, save_sandbox_container_status


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _seed_created_container(port: int) -> int:
    async with get_async_session() as session:
        user = SystemUser(role=SystemUserRole.USER, email="start@test.local", username="starter")
        host = ManagedHost(ip_address="127.0.0.1")
        image = SandboxImage(image_name="penetration-runtime:latest", control_proxy_port=8000)
        session.add_all([user, host, image])
        await session.commit()
        for obj in (user, host, image):
            await session.refresh(obj)
        container = SandboxContainer(
            host_id=host.id or 0,
            container_name="start-sbx",
            container_hash="hash-start",
            owner_id=user.id or 0,
            image_id=image.id or 0,
            control_proxy_host_port=port,
            control_proxy_token="start-token",
            status=SandboxContainerStatus.CREATED,
        )
        session.add(container)
        await session.commit()
        await session.refresh(container)
        return container.id or 0


def _fake_docker(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def mark_running(snapshot: ContainerStatusSnapshot) -> None:
        await save_sandbox_container_status(snapshot.id, SandboxContainerStatus.RUNNING)

    monkeypatch.setattr(lifecycle, "start_container_sync", lambda host, container_hash: calls.append("start"))
    monkeypatch.setattr(lifecycle, "stop_container_sync", lambda host, container_hash: calls.append("stop"))
    monkeypatch.setattr(
        lifecycle,
        "inspect_container_state_sync",
        lambda host, container_hash: DockerContainerState(exists=True, status="running"),
    )
    monkeypatch.setattr(lifecycle, "sync_container_status_unlocked", mark_running)
    return calls


@pytest.mark.asyncio
async def test_start_applies_egress_once_slow_control_proxy_is_ready(
    test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port = _free_port()
    container_id = await _seed_created_container(port)
    calls = _fake_docker(monkeypatch)
    egress_payloads: list[dict] = []
    statuses_at_egress: list[SandboxContainerStatus] = []

    async def healthz(request: web.Request) -> web.Response:
        assert request.headers.get("X-Sandbox-Token") == "start-token"
        return web.Response(text="ok")

    async def egress(request: web.Request) -> web.Response:
        egress_payloads.append(await request.json())
        async with get_async_session() as session:
            container = await session.get(SandboxContainer, container_id)
            assert container is not None
            statuses_at_egress.append(container.status)
        return web.json_response({"ok": True})

    proxy_app = web.Application()
    proxy_app.router.add_get("/healthz", healthz)
    proxy_app.router.add_post("/egress", egress)
    runner = web.AppRunner(proxy_app)
    await runner.setup()

    # The entrypoint installs the MITM CA before the control proxy listens.
    async def listen_late() -> None:
        await asyncio.sleep(3.0)
        await web.TCPSite(runner, "127.0.0.1", port).start()

    late = asyncio.create_task(listen_late())
    try:
        record = await lifecycle.start_sandbox_container(container_id)
    finally:
        await late
        await runner.cleanup()

    assert record.container.status == SandboxContainerStatus.RUNNING
    assert calls == ["start"]
    assert len(egress_payloads) == 1
    assert egress_payloads[0]["environment"]["HTTPS_PROXY"] == "http://127.0.0.1:8118"
    # The container is not reported as running while its control proxy is still coming up.
    assert statuses_at_egress == [SandboxContainerStatus.CREATED]


@pytest.mark.asyncio
async def test_start_fails_and_stops_container_when_control_proxy_never_listens(
    test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container_id = await _seed_created_container(_free_port())
    calls = _fake_docker(monkeypatch)
    monkeypatch.setattr(control_proxy, "_CONTROL_PROXY_READY_TIMEOUT_SECONDS", 1.0)

    with pytest.raises(ValidationError, match="control proxy"):
        await lifecycle.start_sandbox_container(container_id)

    assert calls == ["start", "stop"]
    async with get_async_session() as session:
        container = await session.get(SandboxContainer, container_id)
        assert container is not None and container.status == SandboxContainerStatus.STOPPED
