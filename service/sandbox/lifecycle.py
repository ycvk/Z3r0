import asyncio
import re
import secrets
from datetime import datetime

import docker
from sqlalchemy import or_, update
from sqlmodel import select

from core.errors import DomainError, ForbiddenError, NotFoundError, ValidationError
from core.sandbox.command_jobs import cancel_sandbox_async_commands
from database import get_async_session
from logger import get_logger
from model.agent.sessions import AgentSessionMeta
from model.egress_proxy.proxies import EgressProxy
from model.host.hosts import ManagedHost
from model.sandbox.containers import SandboxContainer
from model.sandbox.images import SandboxImage
from model.system_user.users import SystemUser
from model.work_project.projects import WorkProject
from schema.sandbox.containers import (
    SandboxContainerEgressMode,
    SandboxContainerPortMapping,
    SandboxContainerStatus,
)
from schema.system_user.users import SystemUserRole
from service.common.access import can_assign_sandbox_container_owner, can_manage_sandbox_container_by_id
from service.host.docker import docker_client_for_host, inspect_image_on_host_sync
from service.sandbox.control_proxy import apply_container_egress
from service.sandbox.docker_ops import (
    create_container_sync,
    docker_status_to_sandbox_status,
    inspect_container_state_sync,
    pause_container_sync,
    remove_container_sync,
    resume_container_sync,
    start_container_sync,
    stop_container_sync,
)
from service.sandbox.egress import SandboxEgressSelection, sandbox_egress_container_environment
from service.sandbox.locking import serialized_sandbox_container_mutation
from service.sandbox.records import load_sandbox_container_record
from service.sandbox.status import (
    ContainerStatusSnapshot,
    invalidate_agent_tool_bindings,
    save_sandbox_container_status,
    sync_container_status_unlocked,
)
from service.sandbox.types import SandboxContainerRecord
from service.system_user.locking import system_user_lifecycle_lock


logger = get_logger(__name__)
_CONTROL_PROXY_HOST_PORT_MIN = 30000
_CONTROL_PROXY_HOST_PORT_MAX = 60999
_CONTROL_PROXY_HOST_PORT_RETRIES = 32


def _container_name_prefix(image_name: str) -> str:
    short_name = image_name.rsplit("/", 1)[-1].split("@", 1)[0].split(":", 1)[0]
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", short_name).strip("-.")
    return normalized or "sandbox"


def _serialize_port_mappings(port_mappings: list[SandboxContainerPortMapping]) -> list[dict]:
    return [mapping.model_dump() for mapping in port_mappings]


async def require_manageable_sandbox_container(
    container_id: int,
    user_id: int,
    user_role: SystemUserRole,
) -> None:
    if not await can_manage_sandbox_container_by_id(container_id, user_id, user_role):
        raise NotFoundError("Sandbox container not found")


async def create_sandbox_container(
    host_id: int,
    image_id: int,
    egress_mode: SandboxContainerEgressMode,
    egress_proxy_id: int | None,
    owner_id: int,
    port_mappings: list[SandboxContainerPortMapping],
    *,
    actor_user_id: int | None = None,
    actor_user_role: SystemUserRole | None = None,
) -> SandboxContainerRecord:
    if (
        actor_user_id is not None
        and actor_user_role is not None
        and not can_assign_sandbox_container_owner(owner_id, actor_user_id, actor_user_role)
    ):
        raise ForbiddenError("No permission to assign the sandbox container owner")
    async with system_user_lifecycle_lock(owner_id):
        return await _create_sandbox_container(
            host_id=host_id,
            image_id=image_id,
            egress_mode=egress_mode,
            egress_proxy_id=egress_proxy_id,
            owner_id=owner_id,
            port_mappings=port_mappings,
        )


async def _create_sandbox_container(
    host_id: int,
    image_id: int,
    egress_mode: SandboxContainerEgressMode,
    egress_proxy_id: int | None,
    owner_id: int,
    port_mappings: list[SandboxContainerPortMapping],
) -> SandboxContainerRecord:
    docker_host: ManagedHost | None = None
    container_hash = ""
    container_id: int | None = None
    try:
        async with get_async_session() as session, session.begin():
            host = (await session.exec(
                select(ManagedHost).where(ManagedHost.id == host_id).with_for_update()
            )).one_or_none()
            if host is None:
                raise NotFoundError("Managed host not found")
            docker_host = host.model_copy(deep=True)
            sandbox_image = (await session.exec(
                select(SandboxImage).where(SandboxImage.id == image_id).with_for_update()
            )).one_or_none()
            if sandbox_image is None:
                raise NotFoundError("Sandbox image not found")
            owner = await session.get(SystemUser, owner_id)
            if owner is None:
                raise NotFoundError("System user not found")
            egress_proxy = await _resolve_egress_selection(
                session,
                sandbox_image,
                egress_mode,
                egress_proxy_id,
                lock=True,
            )

            for mapping in port_mappings:
                if mapping.container_port == sandbox_image.control_proxy_port and mapping.protocol == "tcp":
                    raise ValidationError("Control proxy port is reserved for the sandbox control proxy")

            await asyncio.to_thread(_assert_host_image_exists, docker_host, sandbox_image.image_name)
            reserved_tcp_host_ports = {mapping.host_port for mapping in port_mappings if mapping.protocol == "tcp"}
            control_proxy_host_port = await asyncio.to_thread(
                _allocate_control_proxy_host_port,
                docker_host,
                reserved_tcp_host_ports,
            )
            control_proxy_token = secrets.token_urlsafe(32)
            user_port_mappings = _serialize_port_mappings(port_mappings)
            docker_port_mappings = [*user_port_mappings, {
                "container_port": sandbox_image.control_proxy_port,
                "host_port": control_proxy_host_port,
                "protocol": "tcp",
            }]
            docker_port_mappings = [
                SandboxContainerPortMapping.model_validate(mapping)
                for mapping in docker_port_mappings
            ]
            container_hash, container_name = await asyncio.to_thread(
                create_container_sync,
                docker_host,
                sandbox_image.image_name,
                _container_name_prefix(sandbox_image.image_name),
                docker_port_mappings,
                {
                    "SANDBOX_CONTROL_PROXY_TOKEN": control_proxy_token,
                    **sandbox_egress_container_environment(SandboxEgressSelection(egress_mode, egress_proxy)),
                },
            )

            now = datetime.now()
            sandbox_container = SandboxContainer(
                host_id=host_id,
                container_name=container_name,
                container_hash=container_hash,
                owner_id=owner_id,
                image_id=image_id,
                egress_mode=egress_mode,
                egress_proxy_id=egress_proxy.id if egress_proxy is not None else None,
                control_proxy_host_port=control_proxy_host_port,
                control_proxy_token=control_proxy_token,
                port_mappings=user_port_mappings,
                status=SandboxContainerStatus.CREATED,
                created_at=now,
                updated_at=now,
            )
            session.add(sandbox_container)
            await session.flush()
            container_id = sandbox_container.id
            if container_id is None:
                raise RuntimeError("Sandbox container ID was not generated")
    except (asyncio.CancelledError, DomainError):
        raise
    except docker.errors.ImageNotFound as exc:
        raise ValidationError("Image does not exist on the selected host") from exc
    except BaseException as exc:
        if docker_host is not None and container_hash:
            await _rollback_created_container(docker_host, container_hash, exc)
        logger.exception("Sandbox container create failed for host=%s image=%s", host_id, image_id)
        raise ValidationError("Failed to create sandbox container") from exc

    if container_id is None:
        raise RuntimeError("Sandbox container transaction completed without an ID")

    logger.info("Sandbox container created: %s", container_id)
    record = await load_sandbox_container_record(container_id)
    if record is None:
        raise NotFoundError("Sandbox container not found")
    return record


@serialized_sandbox_container_mutation
async def start_sandbox_container(id: int) -> SandboxContainerRecord:
    record = await load_sandbox_container_record(id)
    if record is None:
        raise NotFoundError("Sandbox container not found")
    if record.container.status not in {SandboxContainerStatus.CREATED, SandboxContainerStatus.STOPPED}:
        raise ValidationError("Only created or stopped sandbox containers can be started")

    try:
        host = await _load_container_host(record.container.host_id)
        if host is None:
            raise ValidationError("Managed host not found")
        await asyncio.to_thread(start_container_sync, host, record.container.container_hash)
        await asyncio.sleep(1)
        # The container is recorded as running only once its control proxy answers, so nothing
        # reaches it while the entrypoint is still installing the MITM CA.
        state = await asyncio.to_thread(inspect_container_state_sync, host, record.container.container_hash)
        if (
            state.exists
            and docker_status_to_sandbox_status(state.status) == SandboxContainerStatus.RUNNING
            and record.container.control_proxy_host_port > 0
        ):
            try:
                await apply_container_egress(id, starting=True)
            except Exception as exc:
                logger.exception("Sandbox container control proxy did not accept egress after start: %s", id)
                await asyncio.to_thread(stop_container_sync, host, record.container.container_hash)
                await save_sandbox_container_status(id, SandboxContainerStatus.STOPPED)
                raise ValidationError(
                    "Sandbox container was stopped because its control proxy did not become ready"
                ) from exc
        await sync_container_status_unlocked(ContainerStatusSnapshot(
            id=record.container.id or id,
            host_id=record.container.host_id,
            container_hash=record.container.container_hash,
            status=record.container.status,
        ))
    except (asyncio.CancelledError, DomainError):
        raise
    except docker.errors.NotFound as exc:
        logger.debug("Sandbox container instance not found while starting: %s", id)
        await save_sandbox_container_status(id, SandboxContainerStatus.ERROR)
        raise ValidationError("Sandbox container instance not found") from exc
    except Exception as exc:
        logger.exception("Sandbox container start failed: %s", id)
        await save_sandbox_container_status(id, SandboxContainerStatus.ERROR)
        raise ValidationError("Failed to start sandbox container") from exc

    next_record = await load_sandbox_container_record(id)
    if next_record is not None and next_record.container.status == SandboxContainerStatus.RUNNING:
        logger.info("Sandbox container started: %s", id)
        return next_record

    logger.info("Sandbox container exited after start: %s", id)
    raise ValidationError("Sandbox container is not running after start")


@serialized_sandbox_container_mutation
async def update_sandbox_container_egress(
    id: int,
    egress_mode: SandboxContainerEgressMode,
    egress_proxy_id: int | None,
) -> tuple[SandboxContainerRecord, bool]:
    record = await load_sandbox_container_record(id)
    if record is None:
        raise NotFoundError("Sandbox container not found")

    async with get_async_session() as session:
        container = (await session.exec(
            select(SandboxContainer).where(SandboxContainer.id == id).with_for_update()
        )).one_or_none()
        if container is None:
            raise NotFoundError("Sandbox container not found")
        sandbox_image = await session.get(SandboxImage, container.image_id)
        if sandbox_image is None:
            raise ValidationError("Sandbox image not found")
        egress_proxy = await _resolve_egress_selection(
            session,
            sandbox_image,
            egress_mode,
            egress_proxy_id,
            lock=True,
        )
        resolved_egress_proxy_id = egress_proxy.id if egress_proxy is not None else None
        if container.control_proxy_host_port <= 0:
            raise ValidationError("Sandbox container has no control proxy port for egress updates")
        if container.egress_mode == egress_mode and container.egress_proxy_id == resolved_egress_proxy_id:
            return record, False
        previous_egress_mode = container.egress_mode
        previous_egress_proxy_id = container.egress_proxy_id
        container.egress_mode = egress_mode
        container.egress_proxy_id = resolved_egress_proxy_id
        container.updated_at = datetime.now()
        session.add(container)
        await session.commit()

    if record.container.status == SandboxContainerStatus.RUNNING:
        try:
            await apply_container_egress(id)
        except Exception as exc:
            await _save_container_egress(id, previous_egress_mode, previous_egress_proxy_id)
            logger.exception("Sandbox container egress apply failed: %s", id)
            raise ValidationError("Failed to apply egress to the running sandbox container") from exc

    updated = await load_sandbox_container_record(id)
    if updated is None:
        raise NotFoundError("Sandbox container not found")
    return updated, True


@serialized_sandbox_container_mutation
async def stop_sandbox_container(id: int) -> SandboxContainerRecord:
    record = await load_sandbox_container_record(id)
    if record is None:
        raise NotFoundError("Sandbox container not found")
    if record.container.status != SandboxContainerStatus.RUNNING:
        raise ValidationError("Only running sandbox containers can be stopped")

    try:
        host = await _load_container_host(record.container.host_id)
        if host is None:
            raise ValidationError("Managed host not found")
        await asyncio.to_thread(stop_container_sync, host, record.container.container_hash)
    except (asyncio.CancelledError, DomainError):
        raise
    except docker.errors.NotFound as exc:
        logger.debug("Sandbox container instance not found while stopping: %s", id)
        await save_sandbox_container_status(id, SandboxContainerStatus.ERROR)
        raise ValidationError("Sandbox container instance not found") from exc
    except Exception as exc:
        logger.exception("Sandbox container stop failed: %s", id)
        await save_sandbox_container_status(id, SandboxContainerStatus.ERROR)
        raise ValidationError("Failed to stop sandbox container") from exc

    logger.info("Sandbox container stopped: %s", id)
    stopped = await save_sandbox_container_status(id, SandboxContainerStatus.STOPPED)
    if stopped is None:
        raise NotFoundError("Sandbox container not found")
    return stopped


@serialized_sandbox_container_mutation
async def pause_sandbox_container(id: int) -> SandboxContainerRecord:
    record = await load_sandbox_container_record(id)
    if record is None:
        raise NotFoundError("Sandbox container not found")
    if record.container.status != SandboxContainerStatus.RUNNING:
        raise ValidationError("Only running sandbox containers can be paused")

    try:
        host = await _load_container_host(record.container.host_id)
        if host is None:
            raise ValidationError("Managed host not found")
        await asyncio.to_thread(pause_container_sync, host, record.container.container_hash)
    except (asyncio.CancelledError, DomainError):
        raise
    except docker.errors.NotFound as exc:
        logger.debug("Sandbox container instance not found while pausing: %s", id)
        await save_sandbox_container_status(id, SandboxContainerStatus.ERROR)
        raise ValidationError("Sandbox container instance not found") from exc
    except Exception as exc:
        logger.exception("Sandbox container pause failed: %s", id)
        await save_sandbox_container_status(id, SandboxContainerStatus.ERROR)
        raise ValidationError("Failed to pause sandbox container") from exc

    logger.info("Sandbox container paused: %s", id)
    paused = await save_sandbox_container_status(id, SandboxContainerStatus.PAUSED)
    if paused is None:
        raise NotFoundError("Sandbox container not found")
    return paused


@serialized_sandbox_container_mutation
async def resume_sandbox_container(id: int) -> SandboxContainerRecord:
    record = await load_sandbox_container_record(id)
    if record is None:
        raise NotFoundError("Sandbox container not found")
    if record.container.status != SandboxContainerStatus.PAUSED:
        raise ValidationError("Only paused sandbox containers can be resumed")

    try:
        host = await _load_container_host(record.container.host_id)
        if host is None:
            raise ValidationError("Managed host not found")
        await asyncio.to_thread(resume_container_sync, host, record.container.container_hash)
        await sync_container_status_unlocked(ContainerStatusSnapshot(
            id=record.container.id or id,
            host_id=record.container.host_id,
            container_hash=record.container.container_hash,
            status=record.container.status,
        ))
        next_record = await load_sandbox_container_record(id)
        if (
            next_record is not None
            and next_record.container.status == SandboxContainerStatus.RUNNING
            and next_record.container.control_proxy_host_port > 0
        ):
            try:
                await apply_container_egress(id)
            except Exception:
                logger.warning("Sandbox container resumed, but egress refresh failed: %s", id, exc_info=True)
    except (asyncio.CancelledError, DomainError):
        raise
    except docker.errors.NotFound as exc:
        logger.debug("Sandbox container instance not found while resuming: %s", id)
        await save_sandbox_container_status(id, SandboxContainerStatus.ERROR)
        raise ValidationError("Sandbox container instance not found") from exc
    except Exception as exc:
        logger.exception("Sandbox container resume failed: %s", id)
        await save_sandbox_container_status(id, SandboxContainerStatus.ERROR)
        raise ValidationError("Failed to resume sandbox container") from exc

    next_record = await load_sandbox_container_record(id)
    if next_record is not None and next_record.container.status == SandboxContainerStatus.RUNNING:
        logger.info("Sandbox container resumed: %s", id)
        return next_record
    raise ValidationError("Sandbox container is not running after resume")


@serialized_sandbox_container_mutation
async def delete_sandbox_container(id: int) -> None:
    async with get_async_session() as session:
        sandbox_container = await session.get(SandboxContainer, id)
        if sandbox_container is None:
            raise NotFoundError("Sandbox container not found")
        host = await session.get(ManagedHost, sandbox_container.host_id)
        container_hash = sandbox_container.container_hash

    await cancel_sandbox_async_commands(id)
    await invalidate_agent_tool_bindings(id)
    if host is not None:
        await asyncio.to_thread(remove_container_sync, host, container_hash)

    async with get_async_session() as session:
        sandbox_container = await session.get(SandboxContainer, id)
        if sandbox_container is None:
            return
        await _clear_sandbox_container_references(session, id)
        await session.delete(sandbox_container)
        await session.commit()

    logger.info("Sandbox container deleted: %s", id)


async def _rollback_created_container(
    host: ManagedHost,
    container_hash: str,
    original_error: BaseException,
) -> None:
    try:
        await asyncio.shield(asyncio.to_thread(remove_container_sync, host, container_hash))
    except Exception as cleanup_error:
        logger.exception("Failed to remove Docker container after database create failure: %s", container_hash)
        original_error.add_note(f"Docker container cleanup also failed: {cleanup_error}")


async def _clear_sandbox_container_references(session, id: int) -> None:
    await session.execute(
        update(WorkProject)
        .where(WorkProject.sandbox_container_id == id)
        .values(sandbox_container_id=None)
    )

    await session.execute(
        update(AgentSessionMeta)
        .where(
            or_(
                AgentSessionMeta.selected_sandbox_container_id == id,
                AgentSessionMeta.runtime_sandbox_container_id == id,
            )
        )
        .values(
            selected_sandbox_container_id=None,
            selected_sandbox_container_generation=0,
            runtime_sandbox_container_id=None,
            runtime_sandbox_container_generation=0,
        )
    )


def _assert_host_image_exists(host: ManagedHost, image_name: str) -> None:
    inspect_image_on_host_sync(host, image_name)


def _published_host_ports(host: ManagedHost) -> set[int]:
    client = docker_client_for_host(host)
    try:
        used: set[int] = set()
        for container in client.containers.list(all=True):
            bindings = (container.attrs.get("HostConfig") or {}).get("PortBindings") or {}
            for mapped in bindings.values():
                if not mapped:
                    continue
                for binding in mapped:
                    host_port = (binding or {}).get("HostPort")
                    if host_port:
                        used.add(int(host_port))
        return used
    finally:
        client.close()


def _allocate_control_proxy_host_port(host: ManagedHost, reserved_tcp_ports: set[int]) -> int:
    used_ports = _published_host_ports(host) | reserved_tcp_ports
    for _ in range(_CONTROL_PROXY_HOST_PORT_RETRIES):
        port = secrets.randbelow(
            _CONTROL_PROXY_HOST_PORT_MAX - _CONTROL_PROXY_HOST_PORT_MIN + 1
        ) + _CONTROL_PROXY_HOST_PORT_MIN
        if port not in used_ports:
            return port
    raise RuntimeError("Failed to allocate the control proxy host port")


async def _load_container_host(host_id: int) -> ManagedHost | None:
    async with get_async_session() as session:
        return await session.get(ManagedHost, host_id)


async def _resolve_egress_selection(
    session,
    sandbox_image: SandboxImage,
    egress_mode: SandboxContainerEgressMode,
    egress_proxy_id: int | None,
    *,
    lock: bool = False,
) -> EgressProxy | None:
    if egress_mode == SandboxContainerEgressMode.DIRECT:
        if egress_proxy_id is not None:
            raise ValidationError("An egress proxy is only valid for proxy egress mode")
        return None
    if egress_mode == SandboxContainerEgressMode.TOR:
        if egress_proxy_id is not None:
            raise ValidationError("An egress proxy is only valid for proxy egress mode")
        if not sandbox_image.supports_tor:
            raise ValidationError("The sandbox image does not support Tor egress")
        return None
    if egress_proxy_id is None:
        raise ValidationError("An egress proxy is required for proxy egress mode")
    statement = select(EgressProxy).where(EgressProxy.id == egress_proxy_id)
    if lock:
        statement = statement.with_for_update()
    egress_proxy = (await session.exec(statement)).one_or_none()
    if egress_proxy is None:
        raise ValidationError("Egress proxy not found")
    return egress_proxy


async def _save_container_egress(
    id: int,
    egress_mode: SandboxContainerEgressMode,
    egress_proxy_id: int | None,
) -> None:
    async with get_async_session() as session:
        container = await session.get(SandboxContainer, id)
        if container is None:
            return
        container.egress_mode = egress_mode
        container.egress_proxy_id = egress_proxy_id
        container.updated_at = datetime.now()
        session.add(container)
        await session.commit()
