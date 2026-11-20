"""Read the model catalog and refresh it from the published pi-ai package."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Awaitable, Callable
from urllib.parse import quote

import httpx

from core.errors import UpstreamError
from core.model_catalog.pi_package import PI_PACKAGE, build_model_catalog, read_package_files
from core.model_catalog.store import load_model_catalog, save_refreshed_model_catalog
from logger import get_logger
from schema.model_catalog.catalog import ModelCatalog


logger = get_logger(__name__)

_NPM_REGISTRY = "https://registry.npmjs.org"
_DOWNLOAD_TIMEOUT_SECONDS = 60.0
_MAX_PACKAGE_BYTES = 64 * 1024 * 1024

PackageDownload = Callable[[str], Awaitable[bytes]]


def get_model_catalog() -> ModelCatalog:
    return load_model_catalog()


async def download_pi_package(version: str) -> bytes:
    """Download a pi-ai release tarball and check it against the digest the registry publishes."""
    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        response = await client.get(f"{_NPM_REGISTRY}/{quote(PI_PACKAGE, safe='@')}/{quote(version, safe='')}")
        response.raise_for_status()
        dist = response.json()["dist"]
        algorithm, _, expected = dist["integrity"].partition("-")
        if algorithm != "sha512":
            raise ValueError(f"unsupported package integrity algorithm {algorithm!r}")

        chunks: list[bytes] = []
        size = 0
        async with client.stream("GET", dist["tarball"]) as download:
            download.raise_for_status()
            async for chunk in download.aiter_bytes():
                size += len(chunk)
                if size > _MAX_PACKAGE_BYTES:
                    raise ValueError(f"package is larger than {_MAX_PACKAGE_BYTES} bytes")
                chunks.append(chunk)
    tarball = b"".join(chunks)
    if base64.b64encode(hashlib.sha512(tarball).digest()).decode("ascii") != expected:
        raise ValueError("package does not match the registry digest")
    return tarball


async def refresh_model_catalog(download: PackageDownload = download_pi_package) -> ModelCatalog:
    """Replace the refreshed catalog copy with one built from the latest pi-ai release."""
    try:
        catalog = build_model_catalog(read_package_files(await download("latest")))
    except Exception as exc:
        logger.exception("Model catalog refresh failed")
        raise UpstreamError(f"Model catalog refresh failed: {exc}") from exc
    save_refreshed_model_catalog(catalog)
    logger.info(
        "Model catalog refreshed from %s@%s: %d providers",
        catalog.source.package,
        catalog.source.version,
        len(catalog.providers),
    )
    return load_model_catalog()
