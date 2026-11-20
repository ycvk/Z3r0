from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import col, select

from app import create_app
from config import ModelApi
from core.errors import ValidationError
from core.runtime.session import get_agent_pool
from core.sandbox import command_guard
from database import get_async_session
from middleware.system_user import AuthUser
from model.agent.sessions import AgentSessionMeta
from model.flow.flows import Flow, FlowSubtask
from model.host.hosts import ManagedHost
from model.sandbox.containers import SandboxContainer
from model.sandbox.images import SandboxImage
from model.system_user.users import SystemUser
from model.work_project.projects import WorkProject, WorkProjectOwner
from schema.agent.sessions import SessionType
from schema.flow.flows import FlowStatus, FlowSubtaskStatus
from schema.sandbox.containers import SandboxContainerStatus
from schema.system_user.users import SystemUserRole
from service.agent import sessions as agent_sessions
from service.flow import controller as flow_controller
from service.flow import records as flow_records
from service.flow.runs import flow_run_is_active, forget_flow_run
from service.sandbox import lifecycle
from service.system_user.users import delete_system_user
from service.work_project.projects import delete_work_project
from tests.conftest import auth_headers
from tests.model_stubs import model_stub, openai_chat_tool_call_stream, text_stream, use_stub_for_default_agent


@dataclass
class World:
    admin: SystemUser
    alice: SystemUser
    bob: SystemUser
    container_id: int
    project_id: int


async def _seed() -> World:
    """An admin who owns one container, and a work project owned by alice and bob."""
    async with get_async_session() as session:
        admin = SystemUser(role=SystemUserRole.ADMIN, email="admin@flow.local", username="admin")
        alice = SystemUser(role=SystemUserRole.USER, email="alice@flow.local", username="alice")
        bob = SystemUser(role=SystemUserRole.USER, email="bob@flow.local", username="bob")
        host = ManagedHost(ip_address="127.0.0.1")
        image = SandboxImage(image_name="penetration-runtime:latest", control_proxy_port=8000)
        project = WorkProject(name="Flow project")
        session.add_all([admin, alice, bob, host, image, project])
        await session.commit()
        for row in (admin, alice, bob, host, image, project):
            await session.refresh(row)
        container = SandboxContainer(
            host_id=host.id or 0,
            container_name="flow-sbx",
            container_hash="hash-flow",
            owner_id=admin.id or 0,
            image_id=image.id or 0,
            status=SandboxContainerStatus.STOPPED,
        )
        session.add_all([
            container,
            WorkProjectOwner(project_id=project.id or 0, user_id=alice.id or 0, position=0),
            WorkProjectOwner(project_id=project.id or 0, user_id=bob.id or 0, position=1),
        ])
        await session.commit()
        for row in (admin, alice, bob, container, project):
            await session.refresh(row)
        session.expunge_all()
    return World(admin, alice, bob, container.id or 0, project.id or 0)


async def _start_flow(
    world: World,
    owner: SystemUser,
    public_id: str,
    *,
    in_project: bool = False,
    status: FlowStatus = FlowStatus.COMPLETED,
    planned: bool = True,
) -> str:
    project_id = world.project_id if in_project else None
    session_id = await agent_sessions.create_session(
        owner.id or 0, session_type=SessionType.FLOW, project_id=project_id,
    )
    async with get_async_session() as session:
        flow = Flow(
            public_id=public_id,
            owner_id=owner.id or 0,
            title=public_id,
            goal="goal",
            status=status,
            sandbox_container_id=world.container_id,
            project_id=project_id,
            session_id=session_id,
        )
        session.add(flow)
        await session.commit()
        await session.refresh(flow)
        if planned:
            session.add(FlowSubtask(flow_id=flow.id or 0, title="step", status=FlowSubtaskStatus.RUNNING))
            await session.commit()
    return session_id


async def _flows() -> dict[str, Flow]:
    async with get_async_session() as session:
        return {flow.public_id: flow for flow in (await session.exec(select(Flow))).all()}


async def _session_ids() -> set[str]:
    async with get_async_session() as session:
        return set((await session.exec(select(col(AgentSessionMeta.session_id)))).all())


@pytest.mark.asyncio
async def test_deleting_a_user_deletes_their_private_flows_and_hands_over_project_flows(test_db: None) -> None:
    world = await _seed()
    await _start_flow(world, world.alice, "alice-private")
    finished = await _start_flow(world, world.alice, "alice-finished", in_project=True)
    unfinished = await _start_flow(world, world.alice, "alice-unfinished", in_project=True, status=FlowStatus.RUNNING)
    bob_session = await _start_flow(world, world.bob, "bob-project", in_project=True)

    await delete_system_user(world.alice.id or 0)

    flows = await _flows()
    assert set(flows) == {"alice-finished", "alice-unfinished", "bob-project"}
    assert await _session_ids() == {finished, unfinished, bob_session}
    # The project keeps the Flows; they pass to the project owner who takes over their sessions.
    assert {flow.owner_id for flow in flows.values()} == {world.bob.id}
    assert flows["alice-finished"].status == FlowStatus.COMPLETED
    assert flows["alice-unfinished"].status == FlowStatus.CANCELLED
    assert flows["alice-unfinished"].error == "Flow operator was removed."


@pytest.mark.asyncio
async def test_deleting_a_work_project_deletes_its_flows(test_db: None) -> None:
    world = await _seed()
    private_session = await _start_flow(world, world.alice, "alice-private")
    await _start_flow(world, world.alice, "alice-project", in_project=True)

    await delete_work_project(world.project_id)

    assert set(await _flows()) == {"alice-private"}
    assert await _session_ids() == {private_session}


@pytest.mark.asyncio
async def test_a_finished_flow_outlives_its_sandbox_container(
    test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = await _seed()
    await _start_flow(world, world.alice, "finished")
    monkeypatch.setattr(lifecycle, "remove_container_sync", lambda host, container_hash: None)

    await lifecycle.delete_sandbox_container(world.container_id)

    assert (await _flows())["finished"].sandbox_container_id is None
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        detail = await client.get("/api/flows/finished", headers=auth_headers(world.alice))
        listed = await client.get("/api/flows", headers=auth_headers(world.alice))
    assert detail.status_code == 200 and detail.json()["data"]["sandbox_container_id"] is None
    assert [item["sandbox_container_id"] for item in listed.json()["data"]["items"]] == [None]


@pytest.mark.asyncio
async def test_a_sandbox_container_is_kept_while_an_active_flow_uses_it(
    test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = await _seed()
    await _start_flow(world, world.alice, "active", status=FlowStatus.WAITING)
    removed: list[str] = []
    monkeypatch.setattr(lifecycle, "remove_container_sync", lambda host, container_hash: removed.append(container_hash))

    with pytest.raises(ValidationError, match="active Flow"):
        await lifecycle.delete_sandbox_container(world.container_id)

    assert removed == []
    assert (await _flows())["active"].sandbox_container_id == world.container_id


@pytest.mark.asyncio
async def test_a_flow_session_is_not_deleted_through_the_session_endpoints(test_db: None) -> None:
    world = await _seed()
    private_session = await _start_flow(world, world.alice, "alice-private")
    project_session = await _start_flow(world, world.alice, "alice-project", in_project=True)

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        as_chat = await client.delete(f"/api/agent-sessions/{private_session}", headers=auth_headers(world.alice))
        as_project_session = await client.delete(
            f"/api/work-projects/{world.project_id}/sessions/{project_session}",
            headers=auth_headers(world.bob),
        )

    assert (as_chat.status_code, as_project_session.status_code) == (404, 404)
    assert set(await _flows()) == {"alice-private", "alice-project"}
    assert await _session_ids() == {private_session, project_session}


@pytest.mark.asyncio
async def test_deleting_a_flow_stops_its_controller(test_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    world = await _seed()
    await _start_flow(world, world.alice, "waiting", status=FlowStatus.WAITING)
    waiting = asyncio.Event()
    wait_for_flow_input_signal = flow_controller.wait_for_flow_input_signal

    async def signal_then_wait(public_id: str) -> None:
        waiting.set()
        await wait_for_flow_input_signal(public_id)

    monkeypatch.setattr(flow_controller, "wait_for_flow_input_signal", signal_then_wait)
    await flow_controller.start_flow_runtime()
    try:
        await asyncio.wait_for(waiting.wait(), timeout=5)
        assert flow_run_is_active("waiting")

        await delete_system_user(world.alice.id or 0)

        assert not flow_run_is_active("waiting")
        assert await _flows() == {}
    finally:
        await flow_controller.stop_flow_runtime()


@pytest.mark.asyncio
async def test_a_waiting_flow_resumes_after_a_restart_with_the_answer_and_the_approval(
    test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = await _seed()
    session_id = await _start_flow(world, world.alice, "waiting", status=FlowStatus.WAITING)
    dangerous = "curl -X DELETE https://api.target.local/users/42"
    assert await command_guard.check_command_guard(dangerous, session_id=session_id) is not None
    alice = AuthUser(
        id=world.alice.id or 0, role=world.alice.role, email=world.alice.email, username=world.alice.username,
    )

    # No controller is running, as after a restart: the answer and the intercepted command are stored.
    await flow_records.answer_flow_input("waiting", alice, "ALLOW")
    assert (await _flows())["waiting"].pending_input == "ALLOW"

    resumed = asyncio.Event()
    prompts: list[str] = []

    async def record_subtask(public_id: str, user: AuthUser, flow_session_id: str, prompt: str) -> bool:
        prompts.append(prompt)
        resumed.set()
        return False

    monkeypatch.setattr(flow_controller, "_run_subtask", record_subtask)
    await flow_controller.start_flow_runtime()
    try:
        await asyncio.wait_for(resumed.wait(), timeout=5)
    finally:
        await flow_controller.stop_flow_runtime()

    flow = (await _flows())["waiting"]
    assert prompts == ["Operator answer:\nALLOW"]
    assert (flow.status, flow.pending_input, flow.ask_question) == (FlowStatus.RUNNING, None, None)
    # The approval given before the restart lets the exact command run once.
    assert await command_guard.check_command_guard(dangerous, session_id=session_id) is None
    assert await command_guard.check_command_guard(dangerous, session_id=session_id) is not None


@pytest.mark.asyncio
async def test_each_flow_signal_ends_its_turn_so_the_controller_reads_the_one_it_asked_for(
    test_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = await _seed()
    await _start_flow(world, world.alice, "fresh", status=FlowStatus.QUEUED, planned=False)
    finished = asyncio.Event()

    def forget_and_signal(public_id: str) -> None:
        forget_flow_run(public_id)
        finished.set()

    monkeypatch.setattr(flow_controller, "forget_flow_run", forget_and_signal)

    async with model_stub(ModelApi.OPENAI_COMPLETIONS) as stub:
        # One response per turn. A model that kept going after a signal would take the next
        # response in the same turn and replace the signal the controller is waiting for.
        stub.script(
            openai_chat_tool_call_stream("submit_subtask_list", {"items": [
                {"title": "Find the flag", "description": "Search the fixture."},
            ]}),
            openai_chat_tool_call_stream("done", {"summary": "Found FLAG{stub} in token.txt."}),
            openai_chat_tool_call_stream("patch_subtask_list", {"ops": []}),
            openai_chat_tool_call_stream("export_report", {"content": "# Report\n\nFLAG{stub}"}),
            text_stream(stub.api, "Report exported.", input_tokens=10, output_tokens=5),
        )
        use_stub_for_default_agent(stub)
        await flow_controller.start_flow_runtime()
        try:
            await asyncio.wait_for(finished.wait(), timeout=60)
        finally:
            await flow_controller.stop_flow_runtime()
            await get_agent_pool().stop()

    flow = (await _flows())["fresh"]
    assert (flow.status, flow.error) == (FlowStatus.COMPLETED, "")
    assert flow.report_id
    schema = await flow_records.load_flow_schema("fresh")
    assert schema is not None
    assert [(item.title, item.status, item.summary) for item in schema.subtasks] == [
        ("Find the flag", FlowSubtaskStatus.DONE, "Found FLAG{stub} in token.txt."),
    ]
    assert len(stub.requests) == 5
