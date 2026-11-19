from __future__ import annotations

import asyncio
import json
from datetime import datetime
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, func, or_
from sqlmodel import col, select

from core.errors import NotFoundError
from database import get_async_session
from logger import get_logger
from model.sandbox.containers import SandboxContainer
from model.sandbox.traffic import SandboxTrafficEntry
from model.work_project.projects import WorkProject
from schema.sandbox.containers import SandboxContainerStatus
from schema.sandbox.traffic import (
    IngestTrafficRequest,
    TrafficEntryDetailSchema,
    TrafficEntrySummarySchema,
)
from schema.system_user.users import SystemUserRole
from service.common.access import (
    can_access_work_project,
    can_view_sandbox_container,
    can_view_traffic_entry,
    traffic_entry_scope,
)
from service.common.pagination import Page, paginate_statement
from service.sandbox.control_proxy import (
    _get_http_client,
    resolve_sandbox_control_proxy_target,
    sandbox_control_proxy_token_headers,
)


logger = get_logger(__name__)

_MAX_BODY_CHARS = 262_144
_PROXY_TRAFFIC_ATTEMPTS = 2
_PROXY_TRAFFIC_RETRY_SECONDS = 0.25
_TRAFFIC_SYNC_INTERVAL_SECONDS = 3.0
_TRAFFIC_SYNC_BATCH_SIZE = 16

_traffic_sync_task: asyncio.Task[None] | None = None
_container_sync_locks: dict[int, asyncio.Lock] = {}


def _sync_lock_for(container_id: int) -> asyncio.Lock:
    lock = _container_sync_locks.get(container_id)
    if lock is None:
        lock = asyncio.Lock()
        _container_sync_locks[container_id] = lock
    return lock


def _parse_url_parts(raw_url: str) -> tuple[str, str]:
    try:
        split = urlsplit(raw_url.strip())
        host = (split.netloc or "").strip()[:255]
        path = split.path or "/"
        if split.query:
            path = f"{path}?{split.query}"
        return host, path[:1024]
    except Exception:
        return "", "/"


def _storable_text(value: str) -> str:
    """PostgreSQL text holds neither NUL nor unpaired surrogates; captured payloads can carry both."""
    return value.replace("\x00", "").encode("utf-8", errors="replace").decode("utf-8")


def _dumps_headers(headers: object) -> str:
    return _storable_text(json.dumps(headers or {}, ensure_ascii=False))


def _loads_headers(raw: str) -> dict[str, str]:
    try:
        parsed = json.loads(raw or "{}")
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except Exception:
        pass
    return {}


def _parse_proxy_created_at(raw_value: object, fallback: datetime) -> datetime:
    if not isinstance(raw_value, str) or not raw_value.strip():
        return fallback
    text_value = raw_value.strip()
    if text_value.endswith("Z"):
        text_value = f"{text_value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text_value)
    except ValueError:
        return fallback
    if parsed.tzinfo is not None:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


def _to_summary(row: SandboxTrafficEntry) -> TrafficEntrySummarySchema:
    return TrafficEntrySummarySchema(
        id=row.id or 0,
        sandbox_container_id=row.sandbox_container_id,
        project_id=row.project_id,
        source=row.source,
        method=row.method,
        url=row.url,
        host=row.host,
        path=row.path,
        status_code=row.status_code,
        response_bytes=row.response_bytes,
        duration_ms=row.duration_ms,
        created_at=row.created_at,
    )


def _to_detail(row: SandboxTrafficEntry) -> TrafficEntryDetailSchema:
    return TrafficEntryDetailSchema(
        id=row.id or 0,
        sandbox_container_id=row.sandbox_container_id,
        project_id=row.project_id,
        source=row.source,
        method=row.method,
        url=row.url,
        host=row.host,
        path=row.path,
        status_code=row.status_code,
        response_bytes=row.response_bytes,
        duration_ms=row.duration_ms,
        created_at=row.created_at,
        request_headers=_loads_headers(row.request_headers_json),
        request_body=row.request_body,
        response_headers=_loads_headers(row.response_headers_json),
        response_body=row.response_body,
    )


async def _bound_container_id_for_project(project_id: int) -> int | None:
    async with get_async_session() as session:
        project = await session.get(WorkProject, project_id)
        return project.sandbox_container_id if project is not None else None


async def _ensure_scope_access(
    *,
    user_id: int,
    user_role: SystemUserRole,
    sandbox_container_id: int | None,
    project_id: int | None,
) -> None:
    if sandbox_container_id is not None:
        if not await can_view_sandbox_container(sandbox_container_id, user_id, user_role):
            raise NotFoundError("sandbox container not found")
    if project_id is not None:
        if not await can_access_work_project(project_id, user_id, user_role):
            raise NotFoundError("work project not found")


async def _resolve_container_owner_and_project(
    sandbox_container_id: int,
    *,
    owner_id: int | None,
    project_id: int | None,
) -> tuple[int, int | None]:
    if owner_id is not None and owner_id > 0 and project_id is not None:
        return owner_id, project_id
    async with get_async_session() as session:
        container = await session.get(SandboxContainer, sandbox_container_id)
        resolved_owner = (
            owner_id
            if owner_id is not None and owner_id > 0
            else (container.owner_id if container is not None else 0)
        )
        resolved_project = project_id
        if resolved_project is None:
            resolved_project = (
                await session.exec(
                    select(WorkProject.id)
                    .where(WorkProject.sandbox_container_id == sandbox_container_id)
                    .limit(1)
                )
            ).first()
    return resolved_owner, resolved_project


async def _request_proxy_traffic(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, int | str] | None = None,
) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(_PROXY_TRAFFIC_ATTEMPTS):
        try:
            response = await _get_http_client().request(
                method,
                url,
                params=params,
                headers=headers,
                timeout=3.0,
            )
            response.raise_for_status()
            return response
        except (httpx.HTTPError, OSError) as exc:
            last_error = exc
            if attempt < _PROXY_TRAFFIC_ATTEMPTS - 1:
                await asyncio.sleep(_PROXY_TRAFFIC_RETRY_SECONDS)
    assert last_error is not None
    raise last_error


async def sync_container_traffic(
    sandbox_container_id: int,
    *,
    owner_id: int | None = None,
    project_id: int | None = None,
) -> int:
    """Pull new HTTP/HTTPS traffic entries from the sandbox container's Go control proxy."""
    async with _sync_lock_for(sandbox_container_id):
        target = await resolve_sandbox_control_proxy_target(sandbox_container_id, require_running=True)
        if target is None:
            return 0

        resolved_owner, resolved_project = await _resolve_container_owner_and_project(
            sandbox_container_id,
            owner_id=owner_id,
            project_id=project_id,
        )

        # Proxy sequence numbers restart with the proxy process, so the cursor is the pair
        # (boot id, sequence) of the most recently stored exchange.
        async with get_async_session() as session:
            cursor_boot = (
                await session.exec(
                    select(SandboxTrafficEntry.proxy_boot)
                    .where(
                        SandboxTrafficEntry.sandbox_container_id == sandbox_container_id,
                        SandboxTrafficEntry.source == "proxy",
                    )
                    .order_by(col(SandboxTrafficEntry.id).desc())
                    .limit(1)
                )
            ).first() or ""
            cursor_seq = int(
                (
                    await session.exec(
                        select(func.coalesce(func.max(SandboxTrafficEntry.proxy_seq), 0)).where(
                            SandboxTrafficEntry.sandbox_container_id == sandbox_container_id,
                            SandboxTrafficEntry.source == "proxy",
                            SandboxTrafficEntry.proxy_boot == cursor_boot,
                        )
                    )
                ).one()
                or 0
            )

        response = await _request_proxy_traffic(
            "GET",
            f"{target.base_url}/traffic",
            headers=sandbox_control_proxy_token_headers(target),
            params={"after_seq": cursor_seq, "boot_id": cursor_boot},
        )
        payload = response.json()
        raw_items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list) or not raw_items:
            return 0
        proxy_boot = str(payload.get("boot_id") or "")[:64]
        last_seq = cursor_seq if proxy_boot == cursor_boot else 0

        now = datetime.now()
        seen_seqs: set[int] = set()
        rows: list[SandboxTrafficEntry] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            seq = int(item.get("seq") or 0)
            if seq <= last_seq or seq in seen_seqs:
                continue
            seen_seqs.add(seq)
            raw_url = _storable_text(str(item.get("url") or ""))[:2048]
            host, path = _parse_url_parts(raw_url)
            host = _storable_text(str(item.get("host") or host))[:255]
            path = _storable_text(str(item.get("path") or path))[:1024]
            req_body = _storable_text(str(item.get("request_body") or ""))[:_MAX_BODY_CHARS]
            resp_body = _storable_text(str(item.get("response_body") or ""))[:_MAX_BODY_CHARS]
            resp_bytes = int(item.get("response_bytes") or len(resp_body.encode("utf-8", errors="ignore")))
            rows.append(
                SandboxTrafficEntry(
                    owner_id=resolved_owner,
                    sandbox_container_id=sandbox_container_id,
                    project_id=resolved_project,
                    proxy_seq=seq,
                    proxy_boot=proxy_boot,
                    source="proxy",
                    method=_storable_text(str(item.get("method") or "GET")).upper()[:16],
                    url=raw_url,
                    host=host,
                    path=path,
                    status_code=int(item.get("status_code") or 0),
                    request_headers_json=_dumps_headers(item.get("request_headers")),
                    request_body=req_body,
                    response_headers_json=_dumps_headers(item.get("response_headers")),
                    response_body=resp_body,
                    response_bytes=max(resp_bytes, 0),
                    duration_ms=max(int(item.get("duration_ms") or 0), 0),
                    created_at=_parse_proxy_created_at(item.get("created_at"), now),
                )
            )
        if not rows:
            return 0
        async with get_async_session() as session:
            session.add_all(rows)
            await session.commit()
        return len(rows)


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


async def ingest_traffic(
    request: IngestTrafficRequest,
    *,
    owner_id: int,
    user_role: SystemUserRole = SystemUserRole.USER,
) -> int:
    await _ensure_scope_access(
        user_id=owner_id,
        user_role=user_role,
        sandbox_container_id=request.sandbox_container_id,
        project_id=request.project_id,
    )
    now = datetime.now()
    rows: list[SandboxTrafficEntry] = []
    for item in request.items:
        raw_url = _storable_text(item.url).strip()[:2048]
        host, path = _parse_url_parts(raw_url)
        req_body = _storable_text(item.request_body)[:_MAX_BODY_CHARS]
        resp_body = _storable_text(item.response_body)[:_MAX_BODY_CHARS]
        resp_bytes = item.response_bytes or len(resp_body.encode("utf-8", errors="ignore"))
        rows.append(
            SandboxTrafficEntry(
                owner_id=owner_id,
                sandbox_container_id=request.sandbox_container_id,
                project_id=request.project_id,
                proxy_seq=0,
                source="ingest",
                method=_storable_text(item.method).strip().upper()[:16] or "GET",
                url=raw_url,
                host=host,
                path=path,
                status_code=item.status_code,
                request_headers_json=_dumps_headers(item.request_headers),
                request_body=req_body,
                response_headers_json=_dumps_headers(item.response_headers),
                response_body=resp_body,
                response_bytes=max(resp_bytes, 0),
                duration_ms=max(item.duration_ms, 0),
                created_at=now,
            )
        )
    async with get_async_session() as session:
        session.add_all(rows)
        await session.commit()
    return len(rows)


def _apply_status_filter(stmt, status: str):
    cleaned = status.strip().lower()
    if not cleaned:
        return stmt
    if len(cleaned) == 3 and cleaned.endswith("xx") and cleaned[0].isdigit():
        base = int(cleaned[0]) * 100
        return stmt.where(
            col(SandboxTrafficEntry.status_code) >= base,
            col(SandboxTrafficEntry.status_code) < base + 100,
        )
    try:
        code = int(cleaned)
        return stmt.where(SandboxTrafficEntry.status_code == code)
    except ValueError:
        return stmt


async def query_traffic(
    *,
    user_id: int,
    user_role: SystemUserRole,
    sandbox_container_id: int | None = None,
    project_id: int | None = None,
    query: str = "",
    path: str = "",
    status: str = "",
    method: str = "",
    page: int = 1,
    size: int = 20,
) -> Page[TrafficEntrySummarySchema]:
    await _ensure_scope_access(
        user_id=user_id,
        user_role=user_role,
        sandbox_container_id=sandbox_container_id,
        project_id=project_id,
    )
    project_bound_container_id = (
        await _bound_container_id_for_project(project_id)
        if project_id is not None and sandbox_container_id is None
        else None
    )
    sync_container_id = sandbox_container_id if sandbox_container_id is not None else project_bound_container_id
    if sync_container_id is not None:
        await sync_container_traffic(sync_container_id, owner_id=user_id, project_id=project_id)

    stmt = (
        select(SandboxTrafficEntry)
        .where(traffic_entry_scope(user_id, user_role))
        .order_by(col(SandboxTrafficEntry.id).desc())
    )
    if sandbox_container_id is not None:
        stmt = stmt.where(SandboxTrafficEntry.sandbox_container_id == sandbox_container_id)
    if project_id is not None:
        if sandbox_container_id is None and project_bound_container_id is not None:
            stmt = stmt.where(
                or_(
                    SandboxTrafficEntry.project_id == project_id,
                    SandboxTrafficEntry.sandbox_container_id == project_bound_container_id,
                )
            )
        else:
            stmt = stmt.where(SandboxTrafficEntry.project_id == project_id)
    if method.strip():
        stmt = stmt.where(SandboxTrafficEntry.method == method.strip().upper())
    if path.strip():
        stmt = stmt.where(col(SandboxTrafficEntry.path).ilike(f"%{path.strip()}%"))
    stmt = _apply_status_filter(stmt, status)
    if query.strip():
        needle = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                col(SandboxTrafficEntry.url).ilike(needle),
                col(SandboxTrafficEntry.request_headers_json).ilike(needle),
                col(SandboxTrafficEntry.request_body).ilike(needle),
                col(SandboxTrafficEntry.response_headers_json).ilike(needle),
                col(SandboxTrafficEntry.response_body).ilike(needle),
            )
        )

    page_result = await paginate_statement(stmt, page=page, size=size)
    return Page(
        page=page_result.page,
        size=page_result.size,
        total=page_result.total,
        items=[_to_summary(row) for row in page_result.items],
    )


async def get_traffic_entry(
    traffic_id: int,
    *,
    user_id: int,
    user_role: SystemUserRole,
) -> TrafficEntryDetailSchema:
    async with get_async_session() as session:
        row = await session.get(SandboxTrafficEntry, traffic_id)
    if row is None or not await can_view_traffic_entry(row, user_id, user_role):
        raise NotFoundError(f"Traffic entry {traffic_id} not found")
    return _to_detail(row)


async def clear_traffic(
    *,
    user_id: int,
    user_role: SystemUserRole,
    sandbox_container_id: int | None = None,
    project_id: int | None = None,
) -> int:
    await _ensure_scope_access(
        user_id=user_id,
        user_role=user_role,
        sandbox_container_id=sandbox_container_id,
        project_id=project_id,
    )
    project_bound_container_id = (
        await _bound_container_id_for_project(project_id)
        if project_id is not None and sandbox_container_id is None
        else None
    )
    clear_container_id = sandbox_container_id if sandbox_container_id is not None else project_bound_container_id
    if clear_container_id is not None:
        async with _sync_lock_for(clear_container_id):
            target = await resolve_sandbox_control_proxy_target(clear_container_id, require_running=True)
            if target is not None:
                await _request_proxy_traffic(
                    "DELETE",
                    f"{target.base_url}/traffic",
                    headers=sandbox_control_proxy_token_headers(target),
                )

    stmt = delete(SandboxTrafficEntry).where(traffic_entry_scope(user_id, user_role))
    if sandbox_container_id is not None:
        stmt = stmt.where(SandboxTrafficEntry.sandbox_container_id == sandbox_container_id)
    if project_id is not None:
        if sandbox_container_id is None and project_bound_container_id is not None:
            stmt = stmt.where(
                or_(
                    SandboxTrafficEntry.project_id == project_id,
                    SandboxTrafficEntry.sandbox_container_id == project_bound_container_id,
                )
            )
        else:
            stmt = stmt.where(SandboxTrafficEntry.project_id == project_id)
    async with get_async_session() as session:
        result = await session.execute(stmt)
        await session.commit()
    return int(result.rowcount or 0)
