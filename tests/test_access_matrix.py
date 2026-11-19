from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app import create_app
from database import get_async_session
from model.flow.flows import Flow
from model.host.hosts import ManagedHost
from model.sandbox.containers import SandboxContainer
from model.sandbox.images import SandboxImage
from model.sandbox.traffic import SandboxTrafficEntry
from model.system_user.users import SystemUser
from model.work_project.projects import WorkProject, WorkProjectOwner
from schema.agent.sessions import SessionType
from schema.flow.flows import FlowStatus
from schema.sandbox.containers import SandboxContainerStatus
from schema.system_user.users import SystemUserRole
from service.agent import sessions as agent_sessions
from tests.conftest import auth_headers


MISSING_ID = 999_999
ALLOWED_BUT_STOPPED = 400


@dataclass(frozen=True)
class World:
    """alice and bob are plain users. alice owns project `alice`, bob owns project `bob`, and both own
    project `shared`, which is bound to container `shared`. alice owns containers `alice` and `shared`."""

    users: dict[str, SystemUser]
    containers: dict[str, int]
    projects: dict[str, int]
    sessions: dict[str, str]
    flow_public_id: str
    traffic: dict[str, int]


async def _seed() -> World:
    async with get_async_session() as session:
        users = {
            "admin": SystemUser(role=SystemUserRole.ADMIN, email="admin@test.local", username="admin"),
            "alice": SystemUser(role=SystemUserRole.USER, email="alice@test.local", username="alice"),
            "bob": SystemUser(role=SystemUserRole.USER, email="bob@test.local", username="bob"),
        }
        host = ManagedHost(ip_address="127.0.0.1")
        image = SandboxImage(image_name="penetration-runtime:latest", control_proxy_port=8000)
        session.add_all([*users.values(), host, image])
        await session.commit()
        for row in (*users.values(), host, image):
            await session.refresh(row)
        user_ids = {name: user.id or 0 for name, user in users.items()}

        container_rows = {
            name: SandboxContainer(
                host_id=host.id or 0,
                container_name=f"{name}-sbx",
                container_hash=f"hash-{name}",
                owner_id=user_ids[owner],
                image_id=image.id or 0,
                status=SandboxContainerStatus.STOPPED,
                control_proxy_host_port=18000,
                control_proxy_token="token",
            )
            for name, owner in (("alice", "alice"), ("bob", "bob"), ("shared", "alice"))
        }
        session.add_all(container_rows.values())
        await session.commit()
        for row in container_rows.values():
            await session.refresh(row)
        containers = {name: row.id or 0 for name, row in container_rows.items()}

        project_rows = {
            "alice": WorkProject(name="Alice project"),
            "bob": WorkProject(name="Bob project"),
            "shared": WorkProject(name="Shared project", sandbox_container_id=containers["shared"]),
        }
        session.add_all(project_rows.values())
        await session.commit()
        for row in project_rows.values():
            await session.refresh(row)
        projects = {name: row.id or 0 for name, row in project_rows.items()}
        session.add_all(
            WorkProjectOwner(project_id=projects[project], user_id=user_ids[owner], position=position)
            for project, owner, position in (("alice", "alice", 0), ("bob", "bob", 0), ("shared", "alice", 0), ("shared", "bob", 1))
        )

        traffic_rows = {
            name: SandboxTrafficEntry(
                owner_id=owner_id,
                sandbox_container_id=containers[container],
                project_id=project_id,
                source="proxy",
                proxy_seq=1,
                method="GET",
                url=f"http://10.0.0.8/{name}",
                host="10.0.0.8",
                path=f"/{name}",
                status_code=200,
            )
            for name, owner_id, container, project_id in (
                ("alice", user_ids["alice"], "alice", None),
                ("bob", user_ids["bob"], "bob", None),
                ("shared", 0, "shared", projects["shared"]),
            )
        }
        session.add_all(traffic_rows.values())
        await session.commit()
        for row in (*users.values(), *traffic_rows.values()):
            await session.refresh(row)
        traffic = {name: row.id or 0 for name, row in traffic_rows.items()}

    sessions = {
        "chat_alice": await agent_sessions.create_session(user_ids["alice"]),
        "chat_bob": await agent_sessions.create_session(user_ids["bob"]),
        "project_alice": await agent_sessions.create_session(
            user_ids["alice"], session_type=SessionType.PROJECT, project_id=projects["alice"]
        ),
        "project_shared": await agent_sessions.create_session(
            user_ids["alice"], session_type=SessionType.PROJECT, project_id=projects["shared"]
        ),
        "flow_alice": await agent_sessions.create_session(
            user_ids["alice"], session_type=SessionType.FLOW, sandbox_container_id=containers["alice"]
        ),
    }
    async with get_async_session() as session:
        session.add(Flow(
            public_id="flow-alice",
            owner_id=user_ids["alice"],
            title="Alice flow",
            goal="Assess the target",
            status=FlowStatus.COMPLETED,
            sandbox_container_id=containers["alice"],
            session_id=sessions["flow_alice"],
        ))
        await session.commit()

    return World(users, containers, projects, sessions, "flow-alice", traffic)


class _Api:
    def __init__(self, client: AsyncClient, world: World) -> None:
        self._client = client
        self._world = world

    async def status(self, user: str, method: str, url: str, **params: Any) -> int:
        response = await self._client.request(
            method, url, headers=auth_headers(self._world.users[user]), params=params or None
        )
        return response.status_code

    async def listed(self, user: str, url: str, key: str = "id", **params: Any) -> set[Any]:
        response = await self._client.get(url, headers=auth_headers(self._world.users[user]), params=params or None)
        assert response.status_code == 200, response.text
        return {item[key] for item in response.json()["data"]["items"]}

    async def statuses(self, method: str, url: str, **params: Any) -> dict[str, int]:
        return {user: await self.status(user, method, url, **params) for user in ("admin", "alice", "bob")}


@pytest_asyncio.fixture()
async def api(test_db: None) -> AsyncIterator[tuple[_Api, World]]:
    world = await _seed()
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        yield _Api(client, world), world


@pytest.mark.asyncio
async def test_work_projects_are_visible_to_their_owners_and_admins(api: tuple[_Api, World]) -> None:
    http, world = api
    projects = world.projects

    assert await http.listed("admin", "/api/work-projects") == set(projects.values())
    assert await http.listed("alice", "/api/work-projects") == {projects["alice"], projects["shared"]}
    assert await http.listed("bob", "/api/work-projects") == {projects["bob"], projects["shared"]}

    assert await http.statuses("GET", f"/api/work-projects/{projects['alice']}") == {"admin": 200, "alice": 200, "bob": 404}
    assert await http.statuses("GET", f"/api/work-projects/{projects['shared']}") == {"admin": 200, "alice": 200, "bob": 200}
    assert await http.statuses("GET", f"/api/work-projects/{MISSING_ID}") == {"admin": 404, "alice": 404, "bob": 404}

    sessions = world.sessions
    assert await http.statuses("GET", f"/api/work-projects/{projects['alice']}/sessions") == {
        "admin": 200, "alice": 200, "bob": 404,
    }
    for user in ("admin", "alice", "bob"):
        listed = await http.listed(user, f"/api/work-projects/{projects['shared']}/sessions", key="session_id")
        assert listed == {sessions["project_shared"]}


@pytest.mark.asyncio
async def test_sandbox_containers_are_managed_by_their_owner_and_admins(api: tuple[_Api, World]) -> None:
    http, world = api
    containers, projects = world.containers, world.projects

    assert await http.listed("admin", "/api/sandbox-containers") == set(containers.values())
    assert await http.listed("alice", "/api/sandbox-containers") == {containers["alice"], containers["shared"]}
    assert await http.listed("bob", "/api/sandbox-containers") == {containers["bob"]}

    # Owning a project that is bound to a container does not make its other owners managers of the container.
    files = "/api/sandbox-containers/{}/files"
    assert await http.statuses("GET", files.format(containers["alice"]), path="/") == {
        "admin": ALLOWED_BUT_STOPPED, "alice": ALLOWED_BUT_STOPPED, "bob": 403,
    }
    assert await http.statuses("GET", files.format(containers["shared"]), path="/") == {
        "admin": ALLOWED_BUT_STOPPED, "alice": ALLOWED_BUT_STOPPED, "bob": 403,
    }
    assert await http.statuses("GET", files.format(containers["bob"]), path="/") == {
        "admin": ALLOWED_BUT_STOPPED, "alice": 403, "bob": ALLOWED_BUT_STOPPED,
    }
    assert await http.statuses("GET", files.format(MISSING_ID), path="/") == {"admin": 404, "alice": 403, "bob": 403}

    assert await http.status("bob", "POST", f"/api/sandbox-containers/{containers['alice']}/stop") == 403
    assert await http.status("bob", "POST", f"/api/sandbox-containers/{containers['shared']}/stop") == 403
    assert await http.status("bob", "POST", f"/api/sandbox-containers/{MISSING_ID}/stop") == 404
    assert await http.status("admin", "POST", f"/api/sandbox-containers/{MISSING_ID}/stop") == 404

    available = "/api/sandbox-containers/available"
    assert await http.listed("admin", available, include_non_running=True) == {containers["alice"], containers["bob"]}
    assert await http.listed("alice", available, include_non_running=True) == {containers["alice"]}
    assert await http.listed("bob", available, include_non_running=True) == {containers["bob"]}

    # Picking a container for a project also offers the one that project is already bound to.
    for_shared = {"work_project_id": projects["shared"], "include_non_running": True}
    assert await http.listed("admin", available, **for_shared) == set(containers.values())
    assert await http.listed("alice", available, **for_shared) == {containers["alice"], containers["shared"]}
    assert await http.listed("bob", available, **for_shared) == {containers["bob"], containers["shared"]}
    for_alice_project = {"work_project_id": projects["alice"], "include_non_running": True}
    assert await http.listed("bob", available, **for_alice_project) == {containers["bob"]}


@pytest.mark.asyncio
async def test_agent_sessions_follow_their_owner_or_their_project(api: tuple[_Api, World]) -> None:
    http, world = api
    sessions = world.sessions

    assert await http.listed("admin", "/api/agent-sessions", key="session_id") == set()
    assert await http.listed("alice", "/api/agent-sessions", key="session_id") == {sessions["chat_alice"]}
    assert await http.listed("bob", "/api/agent-sessions", key="session_id") == {sessions["chat_bob"]}

    events = "/api/agent-sessions/{}/events"
    # A chat session is private to its owner, admins included.
    assert await http.statuses("GET", events.format(sessions["chat_alice"])) == {"admin": 404, "alice": 200, "bob": 404}
    assert await http.statuses("GET", events.format(sessions["project_alice"])) == {"admin": 200, "alice": 200, "bob": 404}
    assert await http.statuses("GET", events.format(sessions["project_shared"])) == {"admin": 200, "alice": 200, "bob": 200}
    assert await http.statuses("GET", events.format(sessions["flow_alice"])) == {"admin": 200, "alice": 200, "bob": 404}
    assert await http.statuses("GET", events.format("missing-session")) == {"admin": 404, "alice": 404, "bob": 404}


@pytest.mark.asyncio
async def test_flows_are_visible_to_their_owner_and_admins(api: tuple[_Api, World]) -> None:
    http, world = api

    assert await http.listed("admin", "/api/flows", key="public_id") == {world.flow_public_id}
    assert await http.listed("alice", "/api/flows", key="public_id") == {world.flow_public_id}
    assert await http.listed("bob", "/api/flows", key="public_id") == set()

    assert await http.statuses("GET", f"/api/flows/{world.flow_public_id}") == {"admin": 200, "alice": 200, "bob": 404}
    assert await http.statuses("GET", "/api/flows/missing-flow") == {"admin": 404, "alice": 404, "bob": 404}


@pytest.mark.asyncio
async def test_traffic_is_visible_through_the_container_or_its_project(api: tuple[_Api, World]) -> None:
    http, world = api
    containers, projects, traffic = world.containers, world.projects, world.traffic

    assert await http.listed("admin", "/api/traffic") == set(traffic.values())
    assert await http.listed("alice", "/api/traffic") == {traffic["alice"], traffic["shared"]}
    assert await http.listed("bob", "/api/traffic") == {traffic["bob"], traffic["shared"]}

    # bob owns the project bound to alice's `shared` container, which lets him read its traffic.
    assert await http.statuses("GET", "/api/traffic", sandbox_container_id=containers["shared"]) == {
        "admin": 200, "alice": 200, "bob": 200,
    }
    assert await http.statuses("GET", "/api/traffic", sandbox_container_id=containers["alice"]) == {
        "admin": 200, "alice": 200, "bob": 404,
    }
    assert await http.statuses("GET", "/api/traffic", project_id=projects["alice"]) == {
        "admin": 200, "alice": 200, "bob": 404,
    }
    assert await http.statuses("GET", f"/api/traffic/{traffic['shared']}") == {"admin": 200, "alice": 200, "bob": 200}
    assert await http.statuses("GET", f"/api/traffic/{traffic['alice']}") == {"admin": 200, "alice": 200, "bob": 404}
