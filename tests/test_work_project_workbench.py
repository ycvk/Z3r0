from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app import create_app
from core.runtime.context import AgentRuntimeContext, AgentUserContext
from service.work_project.context import activate_work_project_context
from database import get_async_session
from model.host.hosts import ManagedHost
from model.sandbox.containers import SandboxContainer
from model.sandbox.images import SandboxImage
from model.sandbox.traffic import SandboxTrafficEntry
from model.system_user.users import SystemUser
from model.work_project.assets import WorkProjectAsset
from model.work_project.projects import WorkProject, WorkProjectOwner
from model.work_project.workflow import WorkProjectWorkItem
from schema.sandbox.containers import SandboxContainerStatus
from schema.system_user.users import SystemUserRole
from schema.work_project.assets import (
    WorkProjectAssetCriticality,
    WorkProjectAssetKind,
    WorkProjectAssetOrigin,
    WorkProjectAssetScope,
    WorkProjectAssetState,
)
from schema.work_project.workflow import (
    WorkProjectWorkItemPhase,
    WorkProjectWorkItemPriority,
    WorkProjectWorkItemStatus,
)
from service.agent import sessions as agent_sessions
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_work_project_credentials_traffic_scope_and_one_click_evidence(test_db: None) -> None:
    async with get_async_session() as session:
        alice = SystemUser(role=SystemUserRole.USER, email="alice@wp.local", username="alice")
        bob = SystemUser(role=SystemUserRole.USER, email="bob@wp.local", username="bob")
        host = ManagedHost(ip_address="127.0.0.1")
        image = SandboxImage(image_name="penetration-runtime:latest", control_proxy_port=8000)
        session.add_all([alice, bob, host, image])
        await session.commit()
        for obj in (alice, bob, host, image):
            await session.refresh(obj)

        alice_container = SandboxContainer(
            host_id=host.id or 0,
            container_name="alice-proj-sbx",
            container_hash="hash-alice-proj",
            owner_id=alice.id or 0,
            image_id=image.id or 0,
            status=SandboxContainerStatus.STOPPED,
        )
        bob_container = SandboxContainer(
            host_id=host.id or 0,
            container_name="bob-proj-sbx",
            container_hash="hash-bob-proj",
            owner_id=bob.id or 0,
            image_id=image.id or 0,
            status=SandboxContainerStatus.STOPPED,
        )
        session.add_all([alice_container, bob_container])
        await session.commit()
        await session.refresh(alice_container)
        await session.refresh(bob_container)

        project = WorkProject(name="Portal Red Team", sandbox_container_id=alice_container.id)
        session.add(project)
        await session.commit()
        for obj in (project, alice, bob, alice_container, bob_container):
            await session.refresh(obj)
        session.add(WorkProjectOwner(project_id=project.id or 0, user_id=alice.id or 0, position=0))

        asset = WorkProjectAsset(
            project_id=project.id or 0,
            kind=WorkProjectAssetKind.APPLICATION,
            name="Admin Portal",
            locator="https://portal.internal.local",
            origin=WorkProjectAssetOrigin.DECLARED,
            scope=WorkProjectAssetScope.IN_SCOPE,
            criticality=WorkProjectAssetCriticality.HIGH,
            state=WorkProjectAssetState.UNKNOWN,
        )
        work_item = WorkProjectWorkItem(
            project_id=project.id or 0,
            title="Validate portal auth bypass",
            objective="Check admin endpoints for missing auth",
            phase=WorkProjectWorkItemPhase.VALIDATION,
            priority=WorkProjectWorkItemPriority.HIGH,
            status=WorkProjectWorkItemStatus.ACTIVE,
            assignee_agent_code="cpe",
        )
        # Traffic synced directly on the bound container (project_id=None) must still show under the project
        bound_traffic = SandboxTrafficEntry(
            owner_id=alice.id or 0,
            sandbox_container_id=alice_container.id,
            project_id=None,
            source="proxy",
            proxy_seq=7,
            method="GET",
            url="https://portal.internal.local/api/admin/export",
            host="portal.internal.local",
            path="/api/admin/export",
            status_code=200,
            request_headers_json='{"Cookie":"role=admin"}',
            response_headers_json='{"Content-Type":"application/json"}',
            response_body='{"users":[{"username":"root","api_key":"ak_live_999"}]}',
            response_bytes=55,
            duration_ms=19,
        )
        unrelated_traffic = SandboxTrafficEntry(
            owner_id=bob.id or 0,
            sandbox_container_id=bob_container.id,
            project_id=None,
            source="proxy",
            proxy_seq=1,
            method="GET",
            url="https://bob.other.local/secret",
            host="bob.other.local",
            path="/secret",
            status_code=200,
            response_body="bob-only",
        )
        session.add_all([asset, work_item, bound_traffic, unrelated_traffic])
        await session.commit()
        for obj in (alice, bob, alice_container, bob_container, project, asset, work_item, bound_traffic, unrelated_traffic):
            await session.refresh(obj)

    proj_session_id = await agent_sessions.create_session(
        alice.id or 0,
        session_type=agent_sessions.SessionType.PROJECT,
        project_id=project.id,
        sandbox_container_id=alice_container.id,
    )

    app = create_app()
    alice_hdr = auth_headers(alice)
    bob_hdr = auth_headers(bob)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Project Credentials CRUD + tenant isolation
        r_bob_creds = await client.get(f"/api/work-projects/{project.id}/credentials", headers=bob_hdr)
        assert r_bob_creds.status_code == 404

        r_add = await client.post(
            f"/api/work-projects/{project.id}/credentials",
            json={
                "target": "https://portal.internal.local",
                "credential_type": "api_key",
                "username": "root",
                "secret": "ak_live_999",
                "note": "Exported from /api/admin/export",
            },
            headers=alice_hdr,
        )
        assert r_add.status_code == 200
        cred_id = r_add.json()["data"]["id"]

        r_list = await client.get(
            f"/api/work-projects/{project.id}/credentials",
            params={"keyword": "portal.internal"},
            headers=alice_hdr,
        )
        assert r_list.status_code == 200
        assert r_list.json()["data"]["total"] == 1

        # Project session automatically receives the saved project credential in turn context
        ctx = AgentRuntimeContext(
            session_id=proj_session_id,
            work_project_id=project.id,
            agent_code="cso",
            user=AgentUserContext(id=alice.id or 0, username=alice.username, email=alice.email, role=alice.role),
        )
        async with activate_work_project_context(ctx):
            assert "# Credentials Vault" in ctx.work_project_context
            assert "ak_live_999" in ctx.work_project_context

        # 2. Querying traffic by project_id includes entries captured on the project's bound sandbox container
        r_traffic = await client.get(
            "/api/traffic",
            params={"project_id": project.id, "query": "ak_live_999"},
            headers=alice_hdr,
        )
        assert r_traffic.status_code == 200
        assert r_traffic.json()["data"]["total"] == 1
        assert r_traffic.json()["data"]["items"][0]["id"] == bound_traffic.id

        # 3. Convert captured HTTP/HTTPS exchange into immutable WorkProject HTTP_EXCHANGE Evidence
        # Unrelated traffic entry from another container is rejected with 404
        r_cross = await client.post(
            f"/api/work-projects/{project.id}/traffic/{unrelated_traffic.id}/evidence",
            json={"work_item_id": work_item.id},
            headers=alice_hdr,
        )
        assert r_cross.status_code == 404

        r_ev = await client.post(
            f"/api/work-projects/{project.id}/traffic/{bound_traffic.id}/evidence",
            json={
                "work_item_id": work_item.id,
                "primary_asset_id": asset.id,
                "note": "Unauthenticated admin export returned root API key.",
            },
            headers=alice_hdr,
        )
        assert r_ev.status_code == 200, r_ev.text
        ev_record = r_ev.json()["data"]
        assert ev_record["evidence"]["kind"] == "http_exchange"
        assert ev_record["evidence"]["created_by_agent_code"] == "operator"
        assert len(ev_record["evidence"]["sha256"]) == 64
        assert "ak_live_999" in ev_record["evidence"]["summary"]
        assert ev_record["primary_asset"]["id"] == asset.id

        # Verify the new evidence appears in /evidence list
        r_ev_list = await client.get(
            f"/api/work-projects/{project.id}/evidence",
            params={"kind": "http_exchange"},
            headers=alice_hdr,
        )
        assert r_ev_list.status_code == 200
        assert r_ev_list.json()["data"]["total"] == 1

        # 4. Delete credential
        r_del = await client.delete(f"/api/work-projects/{project.id}/credentials/{cred_id}", headers=alice_hdr)
        assert r_del.status_code == 200
        r_after = await client.get(f"/api/work-projects/{project.id}/credentials", headers=alice_hdr)
        assert r_after.json()["data"]["total"] == 0
