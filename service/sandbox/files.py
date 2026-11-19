import json as json_module
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from typing import BinaryIO

import httpx

from core.errors import ConflictError, DomainError, NotFoundError, UpstreamError, ValidationError
from logger import get_logger
from schema.sandbox.containers import (
    ContainerFileInfo,
    ContainerFileType,
    ContainerFileUploadItem,
    SandboxContainerStatus,
)
from schema.system_user.users import SystemUserRole
from service.common.access import can_manage_sandbox_container_by_id
from service.sandbox.control_proxy import (
    SandboxControlProxyTarget,
    resolve_sandbox_control_proxy_target,
    sandbox_control_proxy_token_headers,
)


logger = get_logger(__name__)
_http_client: httpx.AsyncClient | None = None


@dataclass(frozen=True)
class ContainerUploadSource:
    filename: str
    stream: BinaryIO


@dataclass(frozen=True)
class ContainerDownloadStream:
    filename: str
    media_type: str
    chunks: AsyncIterator[bytes]


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=httpx.Timeout(30.0), trust_env=False)
    return _http_client


async def close_file_http_client() -> None:
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None


async def require_running_file_container(
    container_id: int,
    *,
    action: str,
    user_id: int,
    user_role: SystemUserRole,
) -> None:
    if not await can_manage_sandbox_container_by_id(container_id, user_id, user_role):
        raise NotFoundError("Sandbox container not found")
    target = await resolve_sandbox_control_proxy_target(container_id)
    if target is None:
        raise NotFoundError("Sandbox container not found")
    if target.status != SandboxContainerStatus.RUNNING:
        raise ValidationError(f"Only running sandbox containers can {action}")


async def list_container_files(container_id: int, path: str) -> list[ContainerFileInfo]:
    payload = await _request_json(
        container_id, "GET", "/files", operation="list container files", params={"path": path}
    )
    files = payload.get("files", [])
    return [ContainerFileInfo.model_validate(file) for file in files if isinstance(file, dict)]


async def get_container_file_info(container_id: int, path: str) -> ContainerFileInfo | None:
    payload = await _request_json(
        container_id, "GET", "/files/info", operation="get container file info", params={"path": path}
    )
    info = payload.get("file")
    return ContainerFileInfo.model_validate(info) if isinstance(info, dict) else None


async def read_container_file(
    container_id: int,
    path: str,
    max_bytes: int = 1_048_576,
    *,
    base64_mode: bool = False,
) -> tuple[str, int]:
    info = await get_container_file_info(container_id, path)
    if info is None:
        raise NotFoundError("File not found")
    if info.type == ContainerFileType.DIRECTORY:
        raise ValidationError("Cannot read a directory")
    payload = await _request_json(
        container_id,
        "GET",
        "/files/read",
        operation="read container file",
        params={"path": path, "max_bytes": str(max_bytes), "base64": str(base64_mode).lower()},
    )
    return str(payload.get("content", "")), info.size


async def upload_container_files(
    container_id: int,
    path: str,
    sources: list[ContainerUploadSource],
    overwrite: bool,
) -> list[ContainerFileUploadItem]:
    if not sources:
        raise ValidationError("No files uploaded")
    files = []
    try:
        for source in sources:
            files.append(("files", (source.filename, source.stream, "application/octet-stream")))
        payload = await _request_json(
            container_id,
            "POST",
            "/files/upload",
            operation="upload container files",
            data={"path": path, "overwrite": str(overwrite).lower()},
            files=files,
        )
    finally:
        for source in sources:
            source.stream.close()
    uploaded = payload.get("files", [])
    return [ContainerFileUploadItem.model_validate(item) for item in uploaded if isinstance(item, dict)]


async def download_container_paths(container_id: int, paths: list[str]) -> ContainerDownloadStream:
    if not paths:
        raise ValidationError("Download path is required")
    operation = "download container files"
    target = await _resolve_running_target(container_id)
    if target is None:
        raise NotFoundError("Sandbox container not found")
    params = tuple(("path", path) for path in paths)
    try:
        stream = _get_http_client().stream(
            "GET",
            f"{target.base_url}/files/download",
            params=params,
            headers=sandbox_control_proxy_token_headers(target),
        )
        response = await stream.__aenter__()
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("Sandbox control proxy operation failed: container=%s operation=%s", container_id, operation)
        raise UpstreamError(f"Failed to {operation}") from exc
    try:
        await _raise_for_control_proxy_stream_response(response, container_id, operation)
    except Exception:
        await stream.__aexit__(None, None, None)
        raise
    filename = _download_filename(response.headers.get("content-disposition", ""))
    return ContainerDownloadStream(
        filename=filename,
        media_type=response.headers.get("content-type", "application/octet-stream"),
        chunks=_stream_response_bytes(response, stream),
    )


async def write_container_file(container_id: int, path: str, content: str) -> None:
    await _request_json(
        container_id, "POST", "/files/write", operation="write container file", json={"path": path, "content": content}
    )


async def copy_container_files(container_id: int, sources: list[str], destination: str) -> None:
    await _request_json(
        container_id,
        "POST",
        "/files/copy",
        operation="copy container files",
        json={"sources": sources, "destination": destination},
    )


async def move_container_files(container_id: int, sources: list[str], destination: str) -> None:
    await _request_json(
        container_id,
        "POST",
        "/files/move",
        operation="move container files",
        json={"sources": sources, "destination": destination},
    )


async def delete_container_files(container_id: int, paths: list[str]) -> None:
    await _request_json(
        container_id, "POST", "/files/delete", operation="delete container files", json={"paths": paths}
    )


async def create_container_directory(container_id: int, path: str) -> None:
    await _request_json(
        container_id, "POST", "/files/mkdir", operation="create container directory", json={"path": path}
    )


async def _request_json(
    container_id: int,
    method: str,
    path: str,
    *,
    operation: str,
    params: dict[str, str] | None = None,
    json: dict | None = None,
    data: dict | None = None,
    files: list | None = None,
) -> dict:
    target = await _resolve_running_target(container_id)
    if target is None:
        raise NotFoundError("Sandbox container not found")
    try:
        response = await _get_http_client().request(
            method,
            f"{target.base_url}{path}",
            params=params,
            json=json,
            data=data,
            files=files,
            headers=sandbox_control_proxy_token_headers(target),
        )
        _raise_for_control_proxy_response(response, container_id, operation)
        payload = response.json()
    except DomainError:
        raise
    except (httpx.HTTPError, OSError, json_module.JSONDecodeError) as exc:
        logger.warning("Sandbox control proxy operation failed: container=%s operation=%s", container_id, operation)
        raise UpstreamError(f"Failed to {operation}") from exc
    return payload if isinstance(payload, dict) else {}


async def _resolve_running_target(container_id: int) -> SandboxControlProxyTarget | None:
    return await resolve_sandbox_control_proxy_target(container_id, require_running=True)


def _raise_for_control_proxy_response(response: httpx.Response, container_id: int, operation: str) -> None:
    if response.status_code < 400:
        return
    message = response.text.strip()
    if response.status_code == 404:
        raise NotFoundError(message or "Path not found")
    if response.status_code == 409:
        raise ConflictError(message or "Path already exists")
    if response.status_code == 400:
        raise ValidationError(message or f"Failed to {operation}")
    logger.warning("Sandbox control proxy operation failed: container=%s operation=%s", container_id, operation)
    raise UpstreamError(f"Failed to {operation}")


async def _raise_for_control_proxy_stream_response(
    response: httpx.Response,
    container_id: int,
    operation: str,
) -> None:
    if response.status_code < 400:
        return
    content = await response.aread()
    message = content.decode(errors="replace").strip() if content else ""
    if response.status_code == 404:
        raise NotFoundError(message or "Path not found")
    if response.status_code == 409:
        raise ConflictError(message or "Path already exists")
    if response.status_code == 400:
        raise ValidationError(message or f"Failed to {operation}")
    logger.warning("Sandbox control proxy operation failed: container=%s operation=%s", container_id, operation)
    raise UpstreamError(f"Failed to {operation}")


async def _stream_response_bytes(response: httpx.Response, stream) -> AsyncIterator[bytes]:
    async with aclosing(response.aiter_bytes()) as chunks:
        try:
            async for chunk in chunks:
                yield chunk
        finally:
            await stream.__aexit__(None, None, None)


def _download_filename(content_disposition: str) -> str:
    marker = "filename="
    if marker not in content_disposition:
        return "download"
    raw = content_disposition.split(marker, 1)[1].split(";", 1)[0].strip().strip('"')
    return raw or "download"
