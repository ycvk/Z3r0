from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import delete, func, or_
from sqlmodel import col, select

from database import get_async_session
from logger import get_logger
from model.sandbox.traffic import SandboxTrafficEntry
from schema.sandbox.traffic import (
    IngestTrafficRequest,
    TrafficEntryDetailSchema,
    TrafficEntrySummarySchema,
)
from schema.system_user.users import SystemUserRole
from service.common.pagination import Page, paginate_statement
from service.sandbox.control_proxy import (
    _get_http_client,
    _url_host,
    resolve_sandbox_control_proxy_target,
    sandbox_control_proxy_token_headers,
)


logger = get_logger(__name__)

_MAX_BODY_CHARS = 262_144


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


def _loads_headers(raw: str) -> dict[str, str]:
    try:
        parsed = json.loads(raw or "{}")
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except Exception:
        pass
    return {}


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


async def sync_container_traffic(
    sandbox_container_id: int,
    *,
    owner_id: int = 0,
    project_id: int | None = None,
) -> int:
    """Pull new HTTP traffic entries from the sandbox container's Go control proxy."""
    try:
        target = await resolve_sandbox_control_proxy_target(sandbox_container_id, require_running=True)
    except Exception:
        return 0
    if target is None:
        return 0

    async with get_async_session() as session:
        last_seq = int(
            (
                await session.exec(
                    select(func.coalesce(func.max(SandboxTrafficEntry.proxy_seq), 0)).where(
                        SandboxTrafficEntry.sandbox_container_id == sandbox_container_id,
                        SandboxTrafficEntry.source == "proxy",
                    )
                )
            ).one()
            or 0
        )

    url = f"http://{_url_host(target.host_address)}:{target.control_port}/traffic"
    try:
        response = await _get_http_client().get(
            url,
            params={"after_seq": last_seq},
            headers=sandbox_control_proxy_token_headers(target),
            timeout=3.0,
        )
        if response.status_code != 200:
            return 0
        payload = response.json()
    except Exception:
        return 0

    raw_items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list) or not raw_items:
        return 0

    now = datetime.now()
    rows: list[SandboxTrafficEntry] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        seq = int(item.get("seq") or 0)
        if seq <= last_seq:
            continue
        raw_url = str(item.get("url") or "")[:2048]
        host, path = _parse_url_parts(raw_url)
        host = str(item.get("host") or host)[:255]
        path = str(item.get("path") or path)[:1024]
        req_body = str(item.get("request_body") or "")[:_MAX_BODY_CHARS]
        resp_body = str(item.get("response_body") or "")[:_MAX_BODY_CHARS]
        resp_bytes = int(item.get("response_bytes") or len(resp_body.encode("utf-8", errors="ignore")))
        rows.append(
            SandboxTrafficEntry(
                owner_id=owner_id,
                sandbox_container_id=sandbox_container_id,
                project_id=project_id,
                proxy_seq=seq,
                source="proxy",
                method=str(item.get("method") or "GET").upper()[:16],
                url=raw_url,
                host=host,
                path=path,
                status_code=int(item.get("status_code") or 0),
                request_headers_json=json.dumps(item.get("request_headers") or {}, ensure_ascii=False),
                request_body=req_body,
                response_headers_json=json.dumps(item.get("response_headers") or {}, ensure_ascii=False),
                response_body=resp_body,
                response_bytes=max(resp_bytes, 0),
                duration_ms=max(int(item.get("duration_ms") or 0), 0),
                created_at=now,
            )
        )
    if not rows:
        return 0
    async with get_async_session() as session:
        session.add_all(rows)
        await session.commit()
    return len(rows)


async def ingest_traffic(request: IngestTrafficRequest, *, owner_id: int) -> int:
    now = datetime.now()
    rows: list[SandboxTrafficEntry] = []
    for item in request.items:
        raw_url = item.url.strip()[:2048]
        host, path = _parse_url_parts(raw_url)
        req_body = item.request_body[:_MAX_BODY_CHARS]
        resp_body = item.response_body[:_MAX_BODY_CHARS]
        resp_bytes = item.response_bytes or len(resp_body.encode("utf-8", errors="ignore"))
        rows.append(
            SandboxTrafficEntry(
                owner_id=owner_id,
                sandbox_container_id=request.sandbox_container_id,
                project_id=request.project_id,
                proxy_seq=0,
                source="ingest",
                method=item.method.strip().upper()[:16] or "GET",
                url=raw_url,
                host=host,
                path=path,
                status_code=item.status_code,
                request_headers_json=json.dumps(item.request_headers or {}, ensure_ascii=False),
                request_body=req_body,
                response_headers_json=json.dumps(item.response_headers or {}, ensure_ascii=False),
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
    if sandbox_container_id is not None:
        await sync_container_traffic(sandbox_container_id, owner_id=user_id, project_id=project_id)

    stmt = select(SandboxTrafficEntry).order_by(col(SandboxTrafficEntry.id).desc())
    if user_role != SystemUserRole.ADMIN and sandbox_container_id is None and project_id is None:
        stmt = stmt.where(SandboxTrafficEntry.owner_id == user_id)
    if sandbox_container_id is not None:
        stmt = stmt.where(SandboxTrafficEntry.sandbox_container_id == sandbox_container_id)
    if project_id is not None:
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
) -> TrafficEntryDetailSchema | None:
    async with get_async_session() as session:
        row = await session.get(SandboxTrafficEntry, traffic_id)
    if row is None:
        return None
    if user_role != SystemUserRole.ADMIN and row.owner_id not in {0, user_id}:
        return None
    return _to_detail(row)


async def clear_traffic(
    *,
    user_id: int,
    user_role: SystemUserRole,
    sandbox_container_id: int | None = None,
    project_id: int | None = None,
) -> int:
    if sandbox_container_id is not None:
        try:
            target = await resolve_sandbox_control_proxy_target(sandbox_container_id, require_running=True)
            if target is not None:
                url = f"http://{_url_host(target.host_address)}:{target.control_port}/traffic"
                await _get_http_client().delete(url, headers=sandbox_control_proxy_token_headers(target), timeout=3.0)
        except Exception:
            pass

    stmt = delete(SandboxTrafficEntry)
    if user_role != SystemUserRole.ADMIN and sandbox_container_id is None and project_id is None:
        stmt = stmt.where(SandboxTrafficEntry.owner_id == user_id)
    if sandbox_container_id is not None:
        stmt = stmt.where(SandboxTrafficEntry.sandbox_container_id == sandbox_container_id)
    if project_id is not None:
        stmt = stmt.where(SandboxTrafficEntry.project_id == project_id)
    async with get_async_session() as session:
        result = await session.execute(stmt)
        await session.commit()
    return int(result.rowcount or 0)
