from __future__ import annotations

import asyncio
import gzip
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
from types import SimpleNamespace
from aiohttp import web
import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from agents import FunctionTool
from agents.tool_context import ToolContext

from app import create_app
from config import ROOT_PATH
from core.flow.barriers import pop_barrier
from core.runtime.context import AgentRuntimeContext, AgentUserContext
from core.sandbox import command_guard
from tools.credentials import list_credentials, save_credential
from tools.flow import terminal
from tools.sandbox import execute_sync_command
from tools.traffic import get_traffic_detail, search_traffic
from service.work_project.context import activate_work_project_context
from database import get_async_session
from model.flow.flows import Flow
from model.host.hosts import ManagedHost
from model.sandbox.containers import SandboxContainer
from model.sandbox.images import SandboxImage
from model.system_user.users import SystemUser
from schema.flow.flows import FlowStatus
from schema.sandbox.containers import SandboxContainerStatus
from schema.system_user.users import SystemUserRole
from service.agent import sessions as agent_sessions
from service.agent.session_state import record_session_usage
from service.sandbox import traffic as traffic_service
from tests.conftest import auth_headers


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _invoke_tool(tool: FunctionTool, ctx: AgentRuntimeContext, **kwargs: object) -> dict[str, object]:
    raw_args = json.dumps(kwargs)
    tool_ctx = ToolContext(
        context=ctx,
        tool_name=tool.name,
        tool_call_id="call-test",
        tool_arguments=raw_args,
    )
    raw = await tool.on_invoke_tool(tool_ctx, raw_args)
    return json.loads(raw)


@pytest.mark.asyncio
async def test_token_ledger_and_credentials_vault_end_to_end(test_db: None) -> None:
    async with get_async_session() as session:
        user = SystemUser(role=SystemUserRole.USER, email="op@test.local", username="operator")
        host = ManagedHost(ip_address="127.0.0.1")
        image = SandboxImage(image_name="penetration-runtime:latest", control_proxy_port=8000)
        session.add_all([user, host, image])
        await session.commit()
        for obj in (user, host, image):
            await session.refresh(obj)
        container = SandboxContainer(
            host_id=host.id or 0,
            container_name="op-sbx",
            container_hash="hash-op",
            owner_id=user.id or 0,
            image_id=image.id or 0,
            status=SandboxContainerStatus.RUNNING,
        )
        session.add(container)
        await session.commit()
        await session.refresh(container)
        await session.refresh(user)

    chat_sid = await agent_sessions.create_session(user.id or 0)
    flow_sid = await agent_sessions.create_session(
        user.id or 0,
        session_type=agent_sessions.SessionType.FLOW,
        title="Audit target",
        sandbox_container_id=container.id,
    )
    async with get_async_session() as session:
        flow_row = Flow(
            public_id="flow-accept-1",
            owner_id=user.id or 0,
            title="Audit target",
            goal="Verify credentials and ledger",
            status=FlowStatus.WAITING,
            sandbox_container_id=container.id or 0,
            session_id=flow_sid,
            ask_question="Need creds?",
        )
        session.add(flow_row)
        await session.commit()

    # Concurrent Token Ledger accumulation on both chat and flow sessions
    usage_a = SimpleNamespace(
        requests=2,
        input_tokens=1200,
        output_tokens=300,
        total_tokens=1500,
        input_tokens_details=SimpleNamespace(cached_tokens=400),
    )
    usage_b = SimpleNamespace(
        requests=1,
        input_tokens=800,
        output_tokens=200,
        total_tokens=1000,
        input_tokens_details=SimpleNamespace(cached_tokens=100),
    )
    await asyncio.gather(
        record_session_usage(flow_sid, usage_a),
        record_session_usage(flow_sid, usage_b),
        record_session_usage(chat_sid, usage_b),
    )

    app = create_app()
    headers = auth_headers(user)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Verify Token Ledger on /api/flows and /api/flows/{id}
        r_flows = await client.get("/api/flows", headers=headers)
        assert r_flows.status_code == 200
        summary = r_flows.json()["data"]["items"][0]
        assert summary["llm_requests"] == 3
        assert summary["input_tokens"] == 2000
        assert summary["output_tokens"] == 500
        assert summary["cached_tokens"] == 500
        assert summary["total_tokens"] == 2500

        # Verify Playground session list excludes FLOW and carries Token Ledger for chat
        r_sessions = await client.get("/api/agent-sessions", headers=headers)
        assert r_sessions.status_code == 200
        sess_items = r_sessions.json()["data"]["items"]
        assert [s["session_id"] for s in sess_items] == [chat_sid]
        assert sess_items[0]["total_tokens"] == 1000
        assert sess_items[0]["llm_requests"] == 1

        # Operator adds credential via /api/flows/{id}/credentials
        r_add = await client.post(
            "/api/flows/flow-accept-1/credentials",
            json={
                "target": "https://portal.target.local/login",
                "credential_type": "password",
                "username": "svc_audit",
                "secret": "S3cr3t!Pass",
                "note": "Operator seed",
            },
            headers=headers,
        )
        assert r_add.status_code == 200
        creds = r_add.json()["data"]["credentials"]
        assert len(creds) == 1
        cred_id = creds[0]["id"]

        # Context injection into subsequent Agent turn (even without WorkProject)
        ctx = AgentRuntimeContext(
            session_id=flow_sid,
            user=AgentUserContext(id=user.id or 0, username=user.username, email=user.email, role=user.role),
            sandbox_container_id=container.id,
        )
        async with activate_work_project_context(ctx):
            assert "# Credentials Vault" in ctx.work_project_context
            assert "S3cr3t!Pass" in ctx.work_project_context

        # Agent saves discovered token via save_credential and queries via list_credentials
        save_res = await _invoke_tool(
            save_credential,
            ctx,
            target="https://api.target.local",
            secret="eyJ.discovered.jwt",
            username="admin",
            credential_type="token",
            note="Extracted from login response",
        )
        assert save_res["status"] == "success"

        list_res = await _invoke_tool(list_credentials, ctx, target="api.target.local")
        assert list_res["status"] == "success"
        listed = json.loads(str(list_res["output"]))
        assert len(listed) == 1
        assert listed[0]["secret"] == "eyJ.discovered.jwt"

        # Flow detail now lists both operator and agent credentials
        r_detail = await client.get("/api/flows/flow-accept-1", headers=headers)
        assert r_detail.status_code == 200
        assert len(r_detail.json()["data"]["credentials"]) == 2

        # Delete operator credential via REST
        r_del = await client.delete(f"/api/flows/flow-accept-1/credentials/{cred_id}", headers=headers)
        assert r_del.status_code == 200
        assert len(r_del.json()["data"]["credentials"]) == 1


@pytest.mark.asyncio
async def test_command_guard_flow_and_playground_tools_acceptance(
    test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_guard.reset_guard_state()
    executed: list[str] = []

    async def fake_start_async_cmd(**kwargs: object) -> None:
        executed.append(str(kwargs["command"]))

    async def fake_count_running(**_: object) -> int:
        return 0

    async def fake_exec_sync(id: int, command: str, timeout_seconds: float) -> SimpleNamespace:
        executed.append(command)
        return SimpleNamespace(output="0 0\n", exit_code=0)

    monkeypatch.setattr("tools.flow.start_async_sandbox_command", fake_start_async_cmd)
    monkeypatch.setattr("tools.flow.sandbox_async_jobs.count_running_async_jobs_for_agent", fake_count_running)
    monkeypatch.setattr("tools.sandbox.execute_sandbox_container_command", fake_exec_sync)

    ctx = AgentRuntimeContext(
        session_id="sess-guard-e2e",
        agent_code="cpe",
        agent_instance_id="inst-1",
        sandbox_container_id=1,
        user=AgentUserContext(id=1, username="op", email="op@test.local", role=SystemUserRole.USER),
    )

    # 1. Flow terminal intercepts destructive command and sets ask barrier
    dangerous = "curl -X DELETE https://api.target.local/users/42"
    res_block = await _invoke_tool(terminal, ctx, command=dangerous)
    assert res_block["intercepted"] is True
    barrier = pop_barrier(ctx.session_id)
    assert barrier is not None and barrier.kind == "ask"
    assert executed == []

    # 2. Agent trying to prepend ALLOW_GUARD=1 on its own is still intercepted
    res_bypass = await _invoke_tool(terminal, ctx, command=f"ALLOW_GUARD=1 {dangerous}")
    assert res_bypass["intercepted"] is True
    pop_barrier(ctx.session_id)
    assert executed == []

    # 3. Intercept dangerous again, operator replies "允许", exact command runs once
    await _invoke_tool(terminal, ctx, command=dangerous)
    pop_barrier(ctx.session_id)
    assert command_guard.is_guard_approval_reply("允许")
    assert command_guard.approve_pending_command(ctx.session_id) is True

    # Unapproved destructive command is still blocked without invalidating the grant for `dangerous`
    other_res = await _invoke_tool(execute_sync_command, ctx, command="psql -c 'DROP TABLE audit_logs;'")
    assert other_res["status"] == "failed"
    assert "ALLOW_GUARD" not in str(other_res.get("error", ""))

    # Approved `dangerous` command executes once
    res_ok = await _invoke_tool(terminal, ctx, command=dangerous)
    assert "run_id" in res_ok
    assert executed == [dangerous]

    # Second execution of the same command is blocked again (one-shot)
    res_again = await _invoke_tool(terminal, ctx, command=dangerous)
    assert res_again["intercepted"] is True
    pop_barrier(ctx.session_id)


@pytest.mark.asyncio
async def test_live_go_proxy_http_https_mitm_and_high_volume_sync(
    test_db: None,
    tmp_path: Path,
) -> None:
    # Build real Go sandbox-proxy binary (with configurable listen addrs via short wrapper test or subproc)
    proxy_src = ROOT_PATH / "sandbox" / "proxy"
    bin_path = tmp_path / "sandbox-proxy-bin"
    await asyncio.to_thread(
        subprocess.run,
        ["go", "build", "-o", str(bin_path), "."],
        cwd=str(proxy_src),
        check=True,
    )

    control_port = _free_port()
    egress_port = _free_port()
    ca_cert = tmp_path / "ca.crt"
    ca_key = tmp_path / "ca.key"

    # Initialize CA first
    env = {
        **os.environ,
        "SANDBOX_MITM_CA_CERT": str(ca_cert),
        "SANDBOX_MITM_CA_KEY": str(ca_key),
        "SANDBOX_CONTROL_PROXY_TOKEN": "live-test-token",
        "SANDBOX_CONTROL_PROXY_ADDR": f"127.0.0.1:{control_port}",
        "SANDBOX_EGRESS_PROXY_ADDR": f"127.0.0.1:{egress_port}",
    }
    await asyncio.to_thread(subprocess.run, [str(bin_path), "--init-ca"], env=env, check=True)
    assert ca_cert.is_file() and ca_key.is_file()

    proc = await asyncio.create_subprocess_exec(
        str(bin_path),
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    # Spin up local HTTP + self-signed HTTPS upstream targets
    tls_cert = tmp_path / "upstream.crt"
    tls_key = tmp_path / "upstream.key"
    await asyncio.to_thread(
        subprocess.run,
        [
            "openssl", "req", "-x509", "-newkey", "ec",
            "-pkeyopt", "ec_paramgen_curve:prime256v1",
            "-nodes", "-days", "1",
            "-subj", "/CN=127.0.0.1",
            "-addext", "subjectAltName=IP:127.0.0.1",
            "-keyout", str(tls_key),
            "-out", str(tls_cert),
        ],
        check=True,
        capture_output=True,
    )

    async def upstream_handler(request: web.Request) -> web.Response:
        body = await request.text()
        if request.path == "/https-secret":
            payload = json.dumps({"flag": "FLAG{e2e_https_captured}", "req": body}).encode()
            return web.Response(
                status=200,
                body=gzip.compress(payload),
                headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
            )
        if request.path.startswith("/scan/"):
            idx = int(request.path.rsplit("/", 1)[-1])
            return web.Response(status=200 if idx == 42 else 404, text=f"scan-{idx}")
        return web.json_response({"plain": "http_ok", "echo": body})

    up_app = web.Application()
    up_app.router.add_route("*", "/{tail:.*}", upstream_handler)
    up_runner = web.AppRunner(up_app)
    await up_runner.setup()

    http_site = web.TCPSite(up_runner, "127.0.0.1", 0)
    await http_site.start()
    http_port = http_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_ctx.load_cert_chain(str(tls_cert), str(tls_key))
    https_site = web.TCPSite(up_runner, "127.0.0.1", 0, ssl_context=ssl_ctx)
    await https_site.start()
    https_port = https_site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    try:
        # Wait for Go control proxy readiness
        async with httpx.AsyncClient(trust_env=False) as probe:
            for _ in range(40):
                try:
                    r = await probe.get(
                        f"http://127.0.0.1:{control_port}/healthz",
                        headers={"X-Sandbox-Token": "live-test-token"},
                    )
                    if r.status_code == 200:
                        break
                except httpx.HTTPError:
                    await asyncio.sleep(0.05)
            else:
                raise RuntimeError("sandbox-proxy did not become ready")

        async with get_async_session() as session:
            user = SystemUser(role=SystemUserRole.USER, email="t@test.local", username="tester")
            host = ManagedHost(ip_address="127.0.0.1")
            image = SandboxImage(image_name="penetration-runtime:latest", control_proxy_port=control_port)
            session.add_all([user, host, image])
            await session.commit()
            for obj in (user, host, image):
                await session.refresh(obj)
            container = SandboxContainer(
                host_id=host.id or 0,
                container_name="mitm-sbx",
                container_hash="hash-mitm",
                owner_id=user.id or 0,
                image_id=image.id or 0,
                control_proxy_host_port=control_port,
                control_proxy_token="live-test-token",
                status=SandboxContainerStatus.RUNNING,
            )
            session.add(container)
            await session.commit()
            await session.refresh(container)
            await session.refresh(user)

        # Client trusting ONLY the sandbox MITM CA sends HTTP + HTTPS + 620 scan requests through egress proxy
        client_ssl = ssl.create_default_context(cafile=str(ca_cert))
        async with httpx.AsyncClient(
            proxy=f"http://127.0.0.1:{egress_port}",
            verify=client_ssl,
            trust_env=False,
            timeout=10.0,
        ) as proxied:
            r_http = await proxied.post(f"http://127.0.0.1:{http_port}/login", content="user=guest")
            assert r_http.status_code == 200

            r_https = await proxied.post(
                f"https://127.0.0.1:{https_port}/https-secret?mode=deep",
                content='{"probe":"tls"}',
                headers={"Accept-Encoding": "gzip"},
            )
            assert r_https.status_code == 200
            assert "FLAG{e2e_https_captured}" in r_https.text

            # Burst > 600 requests while periodic background sync drains into PostgreSQL
            for batch_start in range(0, 620, 200):
                await asyncio.gather(*(
                    proxied.get(f"http://127.0.0.1:{http_port}/scan/{i}")
                    for i in range(batch_start, min(batch_start + 200, 620))
                ))
                await traffic_service.sync_running_containers_traffic()

        # Verify Agent tools search_traffic + get_traffic_detail and REST API
        ctx = AgentRuntimeContext(
            session_id="sess-traffic-e2e",
            sandbox_container_id=container.id,
            user=AgentUserContext(id=user.id or 0, username=user.username, email=user.email, role=user.role),
        )
        hit_res = await _invoke_tool(search_traffic, ctx, query="FLAG{e2e_https_captured}")
        assert hit_res["status"] == "success"
        hit_data = json.loads(str(hit_res["output"]))
        assert hit_data["total"] == 1
        https_id = hit_data["items"][0]["id"]
        assert hit_data["items"][0]["url"].startswith("https://")

        detail_res = await _invoke_tool(get_traffic_detail, ctx, traffic_id=https_id)
        assert detail_res["status"] == "success"
        detail_obj = json.loads(str(detail_res["output"]))
        assert '{"probe":"tls"}' in detail_obj["request_body"]
        assert "FLAG{e2e_https_captured}" in detail_obj["response_body"]

        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r_all = await client.get(
                "/api/traffic",
                params={"sandbox_container_id": container.id, "size": 1},
                headers=auth_headers(user),
            )
            assert r_all.status_code == 200
            # 1 HTTP + 1 HTTPS + 620 scan requests = 622 total, zero dropped!
            assert r_all.json()["data"]["total"] == 622

            r_2xx = await client.get(
                "/api/traffic",
                params={"sandbox_container_id": container.id, "path": "/scan/", "status": "2xx"},
                headers=auth_headers(user),
            )
            assert r_2xx.status_code == 200
            assert r_2xx.json()["data"]["total"] == 1
            assert r_2xx.json()["data"]["items"][0]["path"] == "/scan/42"
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            proc.kill()
            await proc.wait()
        await up_runner.cleanup()
