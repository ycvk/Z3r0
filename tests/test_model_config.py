from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

import config
from app import create_app
from config import (
    WORKSPACE,
    GlobalConfig,
    ModelApi,
    ModelConfig,
    ModelRef,
    ProviderConfig,
    get_config,
    read_config_file,
    validate_model_refs,
    write_config_file,
)
from core.agent.constants import DEFAULT_AGENT_CODE
from core.runtime.session import get_agent_pool
from database import get_async_session
from model.system_user.users import SystemUser
from schema.system_user.users import SystemUserRole
from service.system_user.users import create_system_user
from tests.conftest import auth_headers


def _model(api: ModelApi, **fields: Any) -> ModelConfig:
    defaults: dict[str, Any] = {"provider": "acme", "api": api, "id": "m", "context_window": 200_000}
    if api is ModelApi.ANTHROPIC_MESSAGES:
        defaults["max_tokens"] = 8192
    return ModelConfig(**{**defaults, **fields})


@pytest.mark.parametrize(
    ("api", "fields"),
    [
        (ModelApi.OPENAI_RESPONSES, {"reasoning_effort": "xhigh", "max_tokens": 1000, "temperature": 0, "top_p": 1}),
        (ModelApi.OPENAI_COMPLETIONS, {"reasoning_effort": "none", "context_window": 0}),
        (ModelApi.ANTHROPIC_MESSAGES, {"reasoning_effort": "max"}),
        (ModelApi.ANTHROPIC_MESSAGES, {"thinking_budget_tokens": 1024}),
        (ModelApi.GOOGLE_GENERATIVE_AI, {"reasoning_effort": "minimal"}),
        (ModelApi.GOOGLE_GENERATIVE_AI, {"thinking_budget_tokens": 0}),
    ],
)
def test_model_config_accepts_what_the_protocol_takes(api: ModelApi, fields: dict[str, Any]) -> None:
    _model(api, **fields)


@pytest.mark.parametrize(
    ("api", "fields", "message"),
    [
        (ModelApi.ANTHROPIC_MESSAGES, {"max_tokens": None}, "anthropic-messages requires max_tokens"),
        (ModelApi.ANTHROPIC_MESSAGES, {"reasoning_effort": "minimal"}, "does not accept reasoning effort 'minimal'"),
        (ModelApi.ANTHROPIC_MESSAGES, {"thinking_budget_tokens": 512}, "at least 1024 and below max_tokens"),
        (ModelApi.ANTHROPIC_MESSAGES, {"thinking_budget_tokens": 8192}, "at least 1024 and below max_tokens"),
        (ModelApi.GOOGLE_GENERATIVE_AI, {"reasoning_effort": "xhigh"}, "does not accept reasoning effort 'xhigh'"),
        (
            ModelApi.GOOGLE_GENERATIVE_AI,
            {"reasoning_effort": "low", "thinking_budget_tokens": 1024},
            "mutually exclusive",
        ),
        (ModelApi.OPENAI_RESPONSES, {"thinking_budget_tokens": 1024}, "does not accept a thinking token budget"),
        (ModelApi.OPENAI_COMPLETIONS, {"max_tokens": 0}, "greater than or equal to 1"),
        (ModelApi.OPENAI_COMPLETIONS, {"temperature": 2.5}, "less than or equal to 2"),
        (ModelApi.OPENAI_COMPLETIONS, {"provider": "Not A Slug"}, "String should match pattern"),
        (ModelApi.OPENAI_COMPLETIONS, {"unknown": 1}, "Extra inputs are not permitted"),
    ],
)
def test_model_config_rejects_what_the_protocol_does_not_take(
    api: ModelApi,
    fields: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _model(api, **fields)


@pytest.mark.parametrize(
    "endpoints",
    [
        {},
        {"openai-completions": "api.acme.test/v1"},
        {"openai-completions": "https://gateway.test/v1/{ACCOUNT_ID}/compat"},
        {"openai-completions": ""},
        {"bedrock-converse-stream": "https://bedrock.test"},
    ],
)
def test_provider_needs_a_usable_url_for_each_supported_protocol(endpoints: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        ProviderConfig.model_validate({"name": "Acme", "endpoints": endpoints})


def _config(**fields: Any) -> GlobalConfig:
    data: dict[str, Any] = {
        "providers": {"acme": {"name": "Acme", "api_key": "k", "endpoints": {
            "openai-completions": "https://api.acme.test/v1",
            "anthropic-messages": "https://api.acme.test",
        }}},
        "agents": {"cso": {"code": "cso", "name": "Z3r0", "model": {
            "provider": "acme",
            "api": "anthropic-messages",
            "id": "claude",
            "context_window": 200_000,
            "max_tokens": 8192,
        }}},
        "lightrag": {"llm": {"provider": "acme", "api": "openai-completions", "id": "extractor"}},
    }
    return GlobalConfig.model_validate({**data, **fields})


def test_model_references_resolve_to_the_provider_endpoint_of_their_protocol() -> None:
    cfg = _config()
    validate_model_refs(cfg)

    endpoint = cfg.model_endpoint(cfg.agents["cso"].model)
    assert (endpoint.api, endpoint.base_url, endpoint.api_key) == (
        ModelApi.ANTHROPIC_MESSAGES,
        "https://api.acme.test",
        "k",
    )
    assert cfg.model_endpoint(cfg.lightrag.llm).base_url == "https://api.acme.test/v1"


def test_model_references_must_point_at_a_configured_endpoint() -> None:
    unknown_provider = _config(lightrag={"llm": {"provider": "nowhere", "api": "openai-completions", "id": "x"}})
    with pytest.raises(ValueError, match="references unknown provider 'nowhere'"):
        validate_model_refs(unknown_provider)

    cfg = _config()
    cfg.agents["cso"].model = _model(ModelApi.GOOGLE_GENERATIVE_AI, id="gemini")
    with pytest.raises(ValueError, match="provider 'acme' has no google-generative-ai endpoint for model 'gemini'"):
        validate_model_refs(cfg)

    cfg = _config()
    cfg.lightrag.llm = ModelRef(provider="acme", api=ModelApi.ANTHROPIC_MESSAGES, id="claude")
    with pytest.raises(ValueError, match="lightrag.llm must use an openai-completions or openai-responses endpoint"):
        validate_model_refs(cfg)


def test_example_config_is_a_valid_config() -> None:
    example = json.loads((WORKSPACE / "config.json.example").read_text(encoding="utf-8"))
    cfg = GlobalConfig.model_validate(example)
    validate_model_refs(cfg)
    assert cfg.agents


@pytest.fixture()
def config_file(test_db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give the instance config service its own config.json, seeded from the loaded config."""
    monkeypatch.setattr(config, "WORKSPACE", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    write_config_file(get_config())
    return tmp_path / "config.json"


async def _admin() -> SystemUser:
    admin = await create_system_user("admin", "secret-password", "admin@test.local", SystemUserRole.ADMIN)
    async with get_async_session() as session:
        row = await session.get(SystemUser, admin.id)
        assert row is not None
        return row


def _update_payload(instance: dict[str, Any]) -> dict[str, Any]:
    return {
        **instance,
        "agents": {
            code: {"name": agent["name"], "description": agent["description"], "model": agent["model"]}
            for code, agent in instance["agents"].items()
        },
    }


@pytest.mark.asyncio
async def test_instance_config_update_adds_a_provider_and_moves_an_agent_onto_it(config_file: Path) -> None:
    headers = auth_headers(await _admin())
    pool_before = get_agent_pool()
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        try:
            loaded = await client.get("/api/system-config/instance", headers=headers)
            assert loaded.status_code == 200
            instance = loaded.json()["data"]
            assert instance["providers"].keys() == get_config().providers.keys()

            payload = _update_payload(instance)
            payload["providers"]["anthropic"] = {
                "name": "Anthropic",
                "api_key": "sk-ant-test",
                "endpoints": {"anthropic-messages": "https://api.anthropic.com"},
                "headers": {"anthropic-beta": "context-1m"},
            }
            payload["agents"][DEFAULT_AGENT_CODE]["model"] = {
                "provider": "anthropic",
                "api": "anthropic-messages",
                "id": "claude-fable-5",
                "context_window": 1_000_000,
                "max_tokens": 128_000,
                "reasoning_effort": "xhigh",
                "extra_body": {"speed": "fast"},
            }
            updated = await client.patch("/api/system-config/instance", headers=headers, json=payload)
            assert updated.status_code == 200, updated.text
            assert updated.json()["data"]["restarted"] is True
        finally:
            await get_agent_pool().stop()

    assert get_agent_pool() is not pool_before
    for cfg in (get_config(), read_config_file()):
        model = cfg.agents[DEFAULT_AGENT_CODE].model
        assert (model.provider, model.api, model.id) == ("anthropic", ModelApi.ANTHROPIC_MESSAGES, "claude-fable-5")
        assert (model.context_window, model.max_tokens, model.reasoning_effort) == (1_000_000, 128_000, "xhigh")
        assert model.extra_body == {"speed": "fast"}
        endpoint = cfg.model_endpoint(model)
        assert (endpoint.base_url, endpoint.api_key) == ("https://api.anthropic.com", "sk-ant-test")
        assert endpoint.headers == {"anthropic-beta": "context-1m"}


@pytest.mark.asyncio
async def test_instance_config_update_rejects_unusable_model_settings(config_file: Path) -> None:
    headers = auth_headers(await _admin())
    saved = read_config_file()

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        instance = (await client.get("/api/system-config/instance", headers=headers)).json()["data"]
        provider_id = next(iter(instance["providers"]))

        def patched(mutate: Any) -> dict[str, Any]:
            payload = json.loads(json.dumps(_update_payload(instance)))
            mutate(payload)
            return payload

        def use_unknown_provider(payload: dict[str, Any]) -> None:
            payload["agents"][DEFAULT_AGENT_CODE]["model"]["provider"] = "nowhere"

        def use_missing_endpoint(payload: dict[str, Any]) -> None:
            payload["agents"][DEFAULT_AGENT_CODE]["model"] = {
                "provider": provider_id,
                "api": "google-generative-ai",
                "id": "gemini",
                "context_window": 1_000_000,
            }

        def remove_used_provider(payload: dict[str, Any]) -> None:
            payload["providers"] = {"other": {"name": "Other", "endpoints": {
                "openai-completions": "https://api.other.test/v1",
            }}}

        def extract_with_anthropic(payload: dict[str, Any]) -> None:
            payload["providers"][provider_id]["endpoints"]["anthropic-messages"] = "https://api.acme.test"
            payload["lightrag"]["llm"] = {"provider": provider_id, "api": "anthropic-messages", "id": "claude"}

        def anthropic_without_output_cap(payload: dict[str, Any]) -> None:
            payload["agents"][DEFAULT_AGENT_CODE]["model"] = {
                "provider": provider_id,
                "api": "anthropic-messages",
                "id": "claude",
                "context_window": 200_000,
            }

        def drop_an_agent(payload: dict[str, Any]) -> None:
            del payload["agents"][DEFAULT_AGENT_CODE]

        cases = (
            (use_unknown_provider, 400, "references unknown provider 'nowhere'"),
            (use_missing_endpoint, 400, "has no google-generative-ai endpoint"),
            (remove_used_provider, 400, "references unknown provider"),
            (extract_with_anthropic, 400, "lightrag.llm must use an openai-completions or openai-responses endpoint"),
            (anthropic_without_output_cap, 422, "anthropic-messages requires max_tokens"),
            (drop_an_agent, 400, "Agent set cannot be changed"),
        )
        for mutate, status, message in cases:
            response = await client.patch("/api/system-config/instance", headers=headers, json=patched(mutate))
            assert response.status_code == status, response.text
            assert message in response.text
            assert read_config_file() == saved
