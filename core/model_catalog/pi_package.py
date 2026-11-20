"""Build the model catalog from the published pi-ai package.

pi-ai ships one JSON file of model records per provider, grouped by wire protocol. The catalog keeps
the chat models whose protocol this application can speak and reduces each record to the fields
needed to prefill a model configuration.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from config import ModelApi
from schema.model_catalog.catalog import CatalogModel, CatalogProvider, CatalogSource, ModelCatalog


PI_PACKAGE = "@earendil-works/pi-ai"

_PACKAGE_JSON = "package/package.json"
_PROVIDERS_DIR = "package/dist/providers/"
_DATA_DIR = f"{_PROVIDERS_DIR}data/"
_MANIFEST = f"{_DATA_DIR}.manifest.json"
_MAX_MEMBER_BYTES = 32 * 1024 * 1024

# These providers exchange a login token for short-lived credentials instead of taking an API key.
_TOKEN_EXCHANGE_PROVIDERS = frozenset({"github-copilot"})

_PROVIDER_NAME_PATTERN = re.compile(r'createProvider\(\{\s*id:\s*"([^"]+)",\s*name:\s*"([^"]+)"')
_EFFORT_LEVELS = ("minimal", "low", "medium", "high", "xhigh", "max")
# Levels a reasoning model accepts unless its record maps them to null.
_DEFAULT_EFFORT_LEVELS = frozenset({"minimal", "low", "medium", "high"})
_STANDARD_EFFORTS = frozenset(_EFFORT_LEVELS)
_MISSING = object()


def read_package_files(tarball: bytes) -> dict[str, bytes]:
    """Extract the catalog source files from a pi-ai npm tarball."""
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile() or not _is_catalog_source(member.name):
                continue
            if member.size > _MAX_MEMBER_BYTES:
                raise ValueError(f"{member.name} is larger than {_MAX_MEMBER_BYTES} bytes")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError(f"{member.name} could not be read")
            files[member.name] = extracted.read()
    return files


def build_model_catalog(files: Mapping[str, bytes]) -> ModelCatalog:
    """Build the catalog from the files returned by read_package_files."""
    package = _load_json(files, _PACKAGE_JSON)
    if package.get("name") != PI_PACKAGE:
        raise ValueError(f"expected package {PI_PACKAGE}, got {package.get('name')!r}")
    manifest = _load_json(files, _MANIFEST)
    provider_names = _provider_names(files)

    providers: list[CatalogProvider] = []
    for file_name, digest in sorted(manifest["files"].items()):
        path = f"{_DATA_DIR}{file_name}"
        if path not in files:
            raise ValueError(f"{path} is listed in the manifest but missing from the package")
        if hashlib.sha256(files[path]).hexdigest() != digest:
            raise ValueError(f"{path} does not match its manifest digest")
        provider_id = file_name.removesuffix(".json")
        if provider_id in _TOKEN_EXCHANGE_PROVIDERS:
            continue
        models = _provider_models(provider_id, json.loads(files[path]))
        if not models:
            continue
        if provider_id not in provider_names:
            raise ValueError(f"provider {provider_id!r} has no display name in the package")
        providers.append(CatalogProvider(id=provider_id, name=provider_names[provider_id], models=models))

    return ModelCatalog(
        source=CatalogSource(
            package=PI_PACKAGE,
            version=package["version"],
            generated_at=datetime.fromisoformat(manifest["generatedAt"]),
        ),
        providers=providers,
    )


def _is_catalog_source(name: str) -> bool:
    if name == _PACKAGE_JSON:
        return True
    if name.startswith(_DATA_DIR):
        return name.endswith(".json")
    relative = name.removeprefix(_PROVIDERS_DIR)
    return (
        name.startswith(_PROVIDERS_DIR)
        and "/" not in relative
        and relative.endswith(".js")
        and not relative.endswith(".models.js")
    )


def _load_json(files: Mapping[str, bytes], path: str) -> dict[str, Any]:
    if path not in files:
        raise ValueError(f"{path} is missing from the package")
    data = json.loads(files[path])
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _provider_names(files: Mapping[str, bytes]) -> dict[str, str]:
    names: dict[str, str] = {}
    for path, content in files.items():
        if not path.endswith(".js"):
            continue
        for provider_id, name in _PROVIDER_NAME_PATTERN.findall(content.decode("utf-8")):
            names[provider_id] = name
    return names


def _provider_models(provider_id: str, data: dict[str, Any]) -> list[CatalogModel]:
    models: list[CatalogModel] = []
    for api_name, group in data.items():
        if api_name not in ModelApi:
            continue
        api = ModelApi(api_name)
        for record in group.values():
            # A record without a type is a chat model.
            if record.get("type", "chat") != "chat":
                continue
            try:
                models.append(_catalog_model(api, record))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid model record {provider_id}/{record.get('id')!r}: {exc}") from exc
    return sorted(models, key=lambda model: model.id)


def _catalog_model(api: ModelApi, record: dict[str, Any]) -> CatalogModel:
    reasoning = bool(record["reasoning"])
    thinking_budget = reasoning and _sized_by_token_budget(api, record)
    cost = record["cost"]
    return CatalogModel.model_validate({
        "id": record["id"],
        "name": record["name"],
        "api": api,
        "base_url": record["baseUrl"].rstrip("/"),
        "context_window": record["contextWindow"],
        "max_tokens": record["maxTokens"],
        "reasoning": reasoning,
        "reasoning_efforts": _reasoning_efforts(api, record, thinking_budget) if reasoning else [],
        "thinking_budget": thinking_budget,
        "image_input": "image" in record["input"],
        "cost": {
            "input": cost["input"],
            "output": cost["output"],
            "cache_read": cost["cacheRead"],
            "cache_write": cost["cacheWrite"],
        },
        "headers": record.get("headers") or {},
    })


def _sized_by_token_budget(api: ModelApi, record: dict[str, Any]) -> bool:
    if api is ModelApi.ANTHROPIC_MESSAGES:
        return (record.get("compat") or {}).get("forceAdaptiveThinking") is not True
    if api is ModelApi.GOOGLE_GENERATIVE_AI:
        return not record.get("thinkingLevelMap")
    return False


def _reasoning_efforts(api: ModelApi, record: dict[str, Any], thinking_budget: bool) -> list[str]:
    """Translate the record's thinking levels into the effort values sent on the wire.

    A level mapped to null is unsupported, a level mapped to a string is sent as that string, and an
    unmapped level is supported with its own name except for xhigh and max, which need a mapping.
    A mapping to a provider-specific word such as "default" is left out: only the standard effort
    values can be selected, and anything else goes through the model's extra request body.
    """
    level_map: dict[str, Any] = record.get("thinkingLevelMap") or {}
    efforts: list[str] = []
    if _can_disable_thinking(api, level_map.get("off", _MISSING)):
        efforts.append("none")
    if thinking_budget:
        return efforts
    for level in _EFFORT_LEVELS:
        mapped = level_map.get(level, _MISSING)
        if mapped is None or (mapped is _MISSING and level not in _DEFAULT_EFFORT_LEVELS):
            continue
        effort = mapped.lower() if isinstance(mapped, str) else level
        # Anthropic has no minimal effort; its lowest level is low.
        if api is ModelApi.ANTHROPIC_MESSAGES and effort == "minimal":
            effort = "low"
        if effort in _STANDARD_EFFORTS and effort not in efforts:
            efforts.append(effort)
    return efforts


def _can_disable_thinking(api: ModelApi, off: Any) -> bool:
    if off is None:
        return False
    # Chat Completions and Gemini endpoints stop thinking only when their record maps off to "none".
    if api in (ModelApi.OPENAI_COMPLETIONS, ModelApi.GOOGLE_GENERATIVE_AI):
        return off == "none"
    return True
