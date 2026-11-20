from __future__ import annotations

import hashlib
import io
import json
import tarfile
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import TypeAdapter

import core.model_catalog.store as catalog_store
import handler.model_catalog.catalog as catalog_handler
from app import create_app
from config import ModelApi, ProviderId
from core.errors import UpstreamError
from core.model_catalog.pi_package import PI_PACKAGE, build_model_catalog, read_package_files
from core.model_catalog.store import BUNDLED_CATALOG_FILE, load_model_catalog, read_model_catalog, write_model_catalog
from database import get_async_session
from model.system_user.users import SystemUser
from schema.model_catalog.catalog import CatalogModel, ModelCatalog
from schema.system_user.users import SystemUserRole
from service.model_catalog.refresh import refresh_model_catalog
from service.system_user.users import create_system_user
from tests.conftest import auth_headers


def _record(model_id: str, api: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": model_id,
        "name": model_id.title(),
        "api": api,
        "provider": "acme",
        "baseUrl": "https://api.acme.test/v1/",
        "reasoning": False,
        "input": ["text"],
        "cost": {"input": 1, "output": 2, "cacheRead": 0.1, "cacheWrite": 0},
        "contextWindow": 200_000,
        "maxTokens": 64_000,
        "type": "chat",
        **fields,
    }


def _provider_module(provider_id: str, name: str) -> bytes:
    return (
        'import { createProvider } from "../models.js";\n'
        f"export function provider() {{\n    return createProvider({{\n        id: \"{provider_id}\",\n"
        f'        name: "{name}",\n        baseUrl: "https://api.acme.test",\n    }});\n}}\n'
    ).encode()


def _package(
    data: dict[str, dict[str, Any]],
    *,
    names: dict[str, str] | None = None,
    generated_at: str = "2026-11-01T00:00:00.000Z",
    version: str = "9.9.9",
) -> bytes:
    """A pi-ai tarball with one data file per provider and a manifest of their digests."""
    data_files = {f"{provider_id}.json": json.dumps(records).encode() for provider_id, records in data.items()}
    members = {
        "package/package.json": json.dumps({"name": PI_PACKAGE, "version": version}).encode(),
        "package/dist/providers/data/.manifest.json": json.dumps({
            "generatedAt": generated_at,
            "files": {name: hashlib.sha256(content).hexdigest() for name, content in data_files.items()},
        }).encode(),
        **{f"package/dist/providers/data/{name}": content for name, content in data_files.items()},
        **{
            f"package/dist/providers/{provider_id}.js": _provider_module(provider_id, name)
            for provider_id, name in (names if names is not None else {key: key.title() for key in data}).items()
        },
        "package/dist/providers/acme.models.js": b"export const ACME_MODELS = {};\n",
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _models(catalog: ModelCatalog, provider_id: str) -> dict[str, CatalogModel]:
    provider = next(provider for provider in catalog.providers if provider.id == provider_id)
    return {model.id: model for model in provider.models}


def test_catalog_keeps_chat_models_on_supported_protocols() -> None:
    catalog = build_model_catalog(read_package_files(_package({
        "acme": {
            "openai-completions": {
                "chat:zeta": _record("zeta", "openai-completions"),
                "chat:alpha": _record("alpha", "openai-completions", headers={"X-Poll": "1"}, input=["text", "image"]),
                "image:paint": _record("paint", "openai-completions", type="image"),
                # A record without a type is a chat model.
                "chat:untyped": {k: v for k, v in _record("untyped", "openai-completions").items() if k != "type"},
            },
            "bedrock-converse-stream": {"chat:titan": _record("titan", "bedrock-converse-stream")},
        },
        "github-copilot": {"openai-completions": {"chat:gpt": _record("gpt", "openai-completions")}},
        "unsupported": {"mistral-conversations": {"chat:m": _record("m", "mistral-conversations")}},
    }, names={"acme": "Acme AI", "github-copilot": "GitHub Copilot"})))

    assert (catalog.source.package, catalog.source.version) == (PI_PACKAGE, "9.9.9")
    assert catalog.source.generated_at.isoformat() == "2026-11-01T00:00:00+00:00"
    assert [(provider.id, provider.name) for provider in catalog.providers] == [("acme", "Acme AI")]

    models = _models(catalog, "acme")
    assert list(models) == ["alpha", "untyped", "zeta"]
    alpha = models["alpha"]
    assert alpha.api is ModelApi.OPENAI_COMPLETIONS
    assert alpha.base_url == "https://api.acme.test/v1"
    assert (alpha.context_window, alpha.max_tokens) == (200_000, 64_000)
    assert alpha.headers == {"X-Poll": "1"}
    assert alpha.image_input and not models["zeta"].image_input
    assert alpha.cost.model_dump() == {"input": 1, "output": 2, "cache_read": 0.1, "cache_write": 0}
    assert (alpha.reasoning, alpha.reasoning_efforts, alpha.thinking_budget) == (False, [], False)


@pytest.mark.parametrize(
    ("api", "fields", "efforts", "thinking_budget"),
    [
        # Unmapped levels keep their names; xhigh and max need a mapping; off disables unless mapped to null.
        ("openai-responses", {}, ["none", "minimal", "low", "medium", "high"], False),
        (
            "openai-responses",
            {"thinkingLevelMap": {"off": None, "minimal": None, "xhigh": "xhigh", "max": None}},
            ["low", "medium", "high", "xhigh"],
            False,
        ),
        # Chat Completions endpoints disable thinking only where the record says how.
        ("openai-completions", {}, ["minimal", "low", "medium", "high"], False),
        ("openai-completions", {"thinkingLevelMap": {"off": "none", "low": None}}, ["none", "minimal", "medium", "high"], False),
        # A provider-specific word is not a selectable effort.
        ("openai-completions", {"thinkingLevelMap": {"off": "off", "high": "default", "max": "max"}}, ["minimal", "low", "medium", "max"], False),
        # Anthropic has no minimal effort, and older Claude models take a token budget.
        ("anthropic-messages", {"compat": {"forceAdaptiveThinking": True}}, ["none", "low", "medium", "high"], False),
        (
            "anthropic-messages",
            {"compat": {"forceAdaptiveThinking": True}, "thinkingLevelMap": {"off": None, "xhigh": "xhigh", "max": "max"}},
            ["low", "medium", "high", "xhigh", "max"],
            False,
        ),
        ("anthropic-messages", {}, ["none"], True),
        # Gemini models without a level map take a token budget.
        ("google-generative-ai", {}, [], True),
        ("google-generative-ai", {"thinkingLevelMap": {"off": None, "minimal": "MINIMAL", "low": None, "medium": None, "high": "HIGH"}}, ["minimal", "high"], False),
    ],
)
def test_catalog_derives_thinking_controls(
    api: str,
    fields: dict[str, Any],
    efforts: list[str],
    thinking_budget: bool,
) -> None:
    catalog = build_model_catalog(read_package_files(_package({
        "acme": {api: {"chat:thinker": _record("thinker", api, reasoning=True, **fields)}},
    })))

    model = _models(catalog, "acme")["thinker"]
    assert model.reasoning
    assert model.reasoning_efforts == efforts
    assert model.thinking_budget is thinking_budget


def test_catalog_rejects_a_package_that_does_not_match_its_manifest() -> None:
    files = read_package_files(_package({
        "acme": {"openai-completions": {"chat:alpha": _record("alpha", "openai-completions")}},
    }))
    files["package/dist/providers/data/acme.json"] += b" "
    with pytest.raises(ValueError, match="does not match its manifest digest"):
        build_model_catalog(files)


def test_catalog_rejects_incomplete_records_and_unnamed_providers() -> None:
    broken = {k: v for k, v in _record("alpha", "openai-completions").items() if k != "contextWindow"}
    with pytest.raises(ValueError, match="invalid model record acme/'alpha'"):
        build_model_catalog(read_package_files(_package({"acme": {"openai-completions": {"chat:alpha": broken}}})))

    unnamed = _package({"acme": {"openai-completions": {"chat:alpha": _record("alpha", "openai-completions")}}}, names={})
    with pytest.raises(ValueError, match="provider 'acme' has no display name"):
        build_model_catalog(read_package_files(unnamed))


def test_bundled_snapshot_is_a_valid_catalog_in_the_written_format(tmp_path: Path) -> None:
    catalog = read_model_catalog(BUNDLED_CATALOG_FILE)

    assert catalog.source.package == PI_PACKAGE
    assert len(catalog.providers) >= 30
    assert sum(len(provider.models) for provider in catalog.providers) >= 1000
    assert {model.api for provider in catalog.providers for model in provider.models} == set(ModelApi)
    provider_id = TypeAdapter(ProviderId)
    for provider in catalog.providers:
        provider_id.validate_python(provider.id)

    rewritten = tmp_path / "snapshot.json"
    write_model_catalog(catalog, rewritten)
    assert rewritten.read_bytes() == BUNDLED_CATALOG_FILE.read_bytes()


def _catalog(generated_at: str, version: str) -> ModelCatalog:
    return build_model_catalog(read_package_files(_package(
        {"acme": {"openai-completions": {"chat:alpha": _record("alpha", "openai-completions")}}},
        generated_at=generated_at,
        version=version,
    )))


def test_loaded_catalog_is_the_newer_of_the_snapshot_and_the_refreshed_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshed = tmp_path / "model_catalog.json"
    monkeypatch.setattr(catalog_store, "REFRESHED_CATALOG_FILE", refreshed)
    bundled = read_model_catalog(BUNDLED_CATALOG_FILE)
    assert load_model_catalog() == bundled

    write_model_catalog(_catalog("2000-01-01T00:00:00.000Z", "0.0.1"), refreshed)
    assert load_model_catalog() == bundled

    write_model_catalog(_catalog("2999-01-01T00:00:00.000Z", "99.0.0"), refreshed)
    assert load_model_catalog().source.version == "99.0.0"


@pytest.mark.asyncio
async def test_refresh_stores_the_latest_release_and_leaves_the_catalog_alone_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshed = tmp_path / "model_catalog.json"
    monkeypatch.setattr(catalog_store, "REFRESHED_CATALOG_FILE", refreshed)
    requested: list[str] = []

    async def download(version: str) -> bytes:
        requested.append(version)
        return _package(
            {"acme": {"openai-completions": {"chat:alpha": _record("alpha", "openai-completions")}}},
            generated_at="2999-01-01T00:00:00.000Z",
            version="99.0.0",
        )

    async def unreachable(_: str) -> bytes:
        raise OSError("registry unreachable")

    async def corrupt(_: str) -> bytes:
        return b"not a tarball"

    for failing in (unreachable, corrupt):
        with pytest.raises(UpstreamError, match="Model catalog refresh failed"):
            await refresh_model_catalog(failing)
        assert not refreshed.exists()

    catalog = await refresh_model_catalog(download)
    assert requested == ["latest"]
    assert catalog.source.version == "99.0.0"
    assert read_model_catalog(refreshed) == catalog


@pytest.mark.asyncio
async def test_model_catalog_routes_are_for_administrators(
    test_db: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshed = tmp_path / "model_catalog.json"
    monkeypatch.setattr(catalog_store, "REFRESHED_CATALOG_FILE", refreshed)

    async def download(_: str) -> bytes:
        return _package(
            {"acme": {"openai-completions": {"chat:alpha": _record("alpha", "openai-completions")}}},
            generated_at="2999-01-01T00:00:00.000Z",
            version="99.0.0",
        )

    monkeypatch.setattr(catalog_handler, "refresh_model_catalog", partial(refresh_model_catalog, download))

    admin = await create_system_user("admin", "secret-password", "admin@test.local", SystemUserRole.ADMIN)
    alice = await create_system_user("alice", "secret-password", "alice@test.local", SystemUserRole.USER)
    async with get_async_session() as session:
        admin_row = await session.get(SystemUser, admin.id)
        alice_row = await session.get(SystemUser, alice.id)
        assert admin_row is not None and alice_row is not None

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        for method, path in (("GET", "/api/model-catalog"), ("POST", "/api/model-catalog/refresh")):
            assert (await client.request(method, path)).status_code == 401
            assert (await client.request(method, path, headers=auth_headers(alice_row))).status_code == 403

        bundled = await client.get("/api/model-catalog", headers=auth_headers(admin_row))
        assert bundled.status_code == 200
        assert bundled.json()["data"]["source"]["package"] == PI_PACKAGE
        assert len(bundled.json()["data"]["providers"]) >= 30

        refreshed_response = await client.post("/api/model-catalog/refresh", headers=auth_headers(admin_row))
        assert refreshed_response.status_code == 200
        assert refreshed_response.json()["data"]["source"]["version"] == "99.0.0"

        current = await client.get("/api/model-catalog", headers=auth_headers(admin_row))
        assert [provider["id"] for provider in current.json()["data"]["providers"]] == ["acme"]
