import json
import re
import secrets
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


ROOT_PATH = Path(__file__).resolve().parent
WORKSPACE = ROOT_PATH / ".z3r0"
CONFIG_FILE = WORKSPACE / "config.json"


# strict type config base model
class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# system config
class BootstrapAdminConfig(StrictConfigModel):
    enabled: bool = Field(default=False)
    username: str = Field(default="admin", min_length=1, max_length=64)
    email: str = Field(default="admin@z3r0.local", min_length=1, max_length=255)
    password: str = Field(default="", max_length=128)

    @model_validator(mode="after")
    def validate_password_when_enabled(self):
        if self.enabled and not self.password:
            raise ValueError("bootstrap admin password is required when bootstrap admin is enabled")
        return self


class SystemConfig(StrictConfigModel):
    listen_addr: str = Field(default="127.0.0.1", min_length=1)
    listen_port: int = Field(default=8000, ge=1, le=65535)
    encrypt_key: str = Field(default_factory=lambda: secrets.token_urlsafe(32), min_length=32)
    bootstrap_admin: BootstrapAdminConfig = Field(default_factory=BootstrapAdminConfig)


# database config
class DatabaseConfig(StrictConfigModel):
    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(default="z3r0", min_length=1)
    username: str = Field(default="root", min_length=1)
    password: str = Field(default="")
    pool_size: int = Field(default=32, ge=1)
    max_overflow: int = Field(default=32, ge=0)
    pool_timeout_seconds: int = Field(default=30, gt=0)
    pool_recycle_seconds: int = Field(default=1800, ge=0)
    pool_pre_ping: bool = Field(default=True)


# wire protocol a model endpoint speaks
class ModelApi(StrEnum):
    OPENAI_COMPLETIONS = "openai-completions"
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC_MESSAGES = "anthropic-messages"
    GOOGLE_GENERATIVE_AI = "google-generative-ai"


OPENAI_MODEL_APIS = frozenset({ModelApi.OPENAI_COMPLETIONS, ModelApi.OPENAI_RESPONSES})

ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
ProviderId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$")]

_REASONING_EFFORTS_BY_API: dict[ModelApi, frozenset[str]] = {
    ModelApi.OPENAI_COMPLETIONS: frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"}),
    ModelApi.OPENAI_RESPONSES: frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"}),
    ModelApi.ANTHROPIC_MESSAGES: frozenset({"none", "low", "medium", "high", "xhigh", "max"}),
    ModelApi.GOOGLE_GENERATIVE_AI: frozenset({"none", "minimal", "low", "medium", "high"}),
}
_ANTHROPIC_MIN_THINKING_BUDGET_TOKENS = 1024
_BASE_URL_PATTERN = re.compile(r"^https?://[^\s{}]+$")


# model provider: one credential and the base URL of each protocol it serves
class ProviderConfig(StrictConfigModel):
    name: str = Field(min_length=1, max_length=128)
    api_key: str = Field(default="")
    endpoints: dict[ModelApi, str] = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_endpoints(self) -> Self:
        for api, base_url in self.endpoints.items():
            if not _BASE_URL_PATTERN.fullmatch(base_url):
                raise ValueError(f"{api.value} endpoint must be an http(s) URL without placeholders: {base_url!r}")
        return self


# a model on one protocol endpoint of a configured provider
class ModelRef(StrictConfigModel):
    provider: ProviderId
    api: ModelApi
    id: str = Field(min_length=1)


# model selection plus the request parameters sent with every call
class ModelConfig(ModelRef):
    # tokens the runtime keeps a conversation within; 0 disables context compaction and budgeting
    context_window: int = Field(ge=0)
    # output token cap; None leaves it to the provider
    max_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    reasoning_effort: ReasoningEffort | None = Field(default=None)
    # token budget for models whose thinking is sized in tokens instead of effort levels
    thinking_budget_tokens: int | None = Field(default=None, ge=0)
    extra_headers: dict[str, str] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_protocol_parameters(self) -> Self:
        if self.reasoning_effort is not None and self.reasoning_effort not in _REASONING_EFFORTS_BY_API[self.api]:
            raise ValueError(f"{self.api.value} does not accept reasoning effort {self.reasoning_effort!r}")
        budget = self.thinking_budget_tokens
        if budget is not None and self.api in OPENAI_MODEL_APIS:
            raise ValueError(f"{self.api.value} does not accept a thinking token budget")
        if budget is not None and self.reasoning_effort is not None:
            raise ValueError("reasoning effort and thinking token budget are mutually exclusive")
        if self.api is ModelApi.ANTHROPIC_MESSAGES:
            if self.max_tokens is None:
                raise ValueError("anthropic-messages requires max_tokens")
            if budget is not None and not _ANTHROPIC_MIN_THINKING_BUDGET_TOKENS <= budget < self.max_tokens:
                raise ValueError(
                    "anthropic-messages thinking token budget must be at least "
                    f"{_ANTHROPIC_MIN_THINKING_BUDGET_TOKENS} and below max_tokens"
                )
        return self


# resolved connection details for one model call
@dataclass(frozen=True, slots=True)
class ModelEndpoint:
    api: ModelApi
    base_url: str
    api_key: str
    headers: dict[str, str]


# agent config
class AgentConfig(StrictConfigModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="")
    model: ModelConfig


# per-process agent runtime pool tuning
class AgentPoolConfig(StrictConfigModel):
    max_size: int = Field(default=256, ge=1)
    ttl_seconds: int = Field(default=30 * 60, ge=0)
    sweep_interval_seconds: int = Field(default=60, ge=1)


# per-process agent run tuning
class AgentRuntimeConfig(StrictConfigModel):
    main_max_turns: int = Field(default=1000, ge=1)
    subordinate_max_turns: int = Field(default=1000, ge=1)
    model_stream_idle_timeout_seconds: int = Field(default=300, ge=30)
    report_retention_seconds: int = Field(default=3 * 24 * 60 * 60, ge=0)
    # captured traffic older than this is deleted; 0 keeps it regardless of age
    traffic_retention_seconds: int = Field(default=7 * 24 * 60 * 60, ge=0)
    # the newest entries kept per sandbox container; 0 keeps every entry
    traffic_max_entries_per_container: int = Field(default=50_000, ge=0)
    context_compression_trigger_ratio: float = Field(default=0.90, gt=0, lt=1)
    context_compression_hard_stop_ratio: float = Field(default=0.98, gt=0, lt=1)
    context_compression_target_ratio: float = Field(default=0.20, gt=0, lt=1)
    context_budget_model_call_ratio: float = Field(default=0.80, gt=0, lt=1)
    context_compression_preserve_recent_ratio: float = Field(default=0.25, gt=0, lt=1)
    context_compression_preserve_recent_items: int = Field(default=20, ge=1)
    context_compression_min_items: int = Field(default=12, ge=1)
    context_compression_summary_max_tokens: int = Field(default=8000, ge=512)

    @model_validator(mode="after")
    def validate_context_thresholds(self) -> Self:
        if not (
            self.context_compression_target_ratio
            < self.context_compression_trigger_ratio
            < self.context_compression_hard_stop_ratio
        ):
            raise ValueError(
                "context compression ratios must satisfy target < trigger < hard stop"
            )
        return self


DEFAULT_PROVIDER_ID = "openai"


def _default_providers() -> dict[str, ProviderConfig]:
    return {
        DEFAULT_PROVIDER_ID: ProviderConfig(
            name="OpenAI",
            endpoints={ModelApi.OPENAI_RESPONSES: "https://api.openai.com/v1"},
        ),
    }


# LightRAG config
class LightRAGConfig(StrictConfigModel):
    embedding_api: str = Field(default="https://api.openai.com/v1", min_length=1)
    embedding_key: str = Field(default="")
    embedding_model: str = Field(default="text-embedding-3-small", min_length=1)
    embedding_dim: int = Field(default=1536, ge=1, le=16000)
    # extraction model; LightRAG talks Chat Completions, so it must sit on an OpenAI-protocol endpoint
    llm: ModelRef = Field(default_factory=lambda: ModelRef(
        provider=DEFAULT_PROVIDER_ID,
        api=ModelApi.OPENAI_RESPONSES,
        id="gpt-5",
    ))
    graph_matches: int = Field(default=5, ge=1, le=50)
    chunk_matches: int = Field(default=10, ge=1, le=50)


# FOFA asset search API config
class FofaConfig(StrictConfigModel):
    """Configuration for the optional FOFA asset-search integration."""

    enabled: bool = Field(default=False)
    base_url: str = Field(default="https://fofa.info", min_length=1)
    api_key: str = Field(default="", max_length=256)


# global config
class GlobalConfig(StrictConfigModel):
    system: SystemConfig = Field(default_factory=SystemConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    providers: dict[ProviderId, ProviderConfig] = Field(default_factory=_default_providers)
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    agent_pool: AgentPoolConfig = Field(default_factory=AgentPoolConfig)
    agent_runtime: AgentRuntimeConfig = Field(default_factory=AgentRuntimeConfig)
    lightrag: LightRAGConfig = Field(default_factory=LightRAGConfig)
    fofa: FofaConfig = Field(default_factory=FofaConfig)

    @model_validator(mode="after")
    def validate_agent_codes(self) -> Self:
        for code, agent in self.agents.items():
            if code != agent.code:
                raise ValueError(f"agent code mismatch: {code}")
        return self

    def model_endpoint(self, ref: ModelRef) -> ModelEndpoint:
        return resolve_model_endpoint(self.providers, ref)


def resolve_model_endpoint(providers: dict[str, ProviderConfig], ref: ModelRef) -> ModelEndpoint:
    """Resolve the provider endpoint a model reference points at."""
    provider = providers.get(ref.provider)
    if provider is None:
        raise ValueError(f"model {ref.id!r} references unknown provider {ref.provider!r}")
    base_url = provider.endpoints.get(ref.api)
    if base_url is None:
        raise ValueError(f"provider {ref.provider!r} has no {ref.api.value} endpoint for model {ref.id!r}")
    return ModelEndpoint(api=ref.api, base_url=base_url, api_key=provider.api_key, headers=dict(provider.headers))


def validate_model_refs(cfg: GlobalConfig) -> None:
    """Reject model references that no configured provider endpoint can serve.

    Providers, agents and LightRAG are assigned to the shared config object one field at a time, so
    this spans fields that are only consistent once a whole config has been assembled.
    """
    for agent in cfg.agents.values():
        cfg.model_endpoint(agent.model)
    cfg.model_endpoint(cfg.lightrag.llm)
    if cfg.lightrag.llm.api not in OPENAI_MODEL_APIS:
        raise ValueError("lightrag.llm must use an openai-completions or openai-responses endpoint")


###
# global config instance
###
_cfg: GlobalConfig = GlobalConfig()


def load_config() -> None:
    """Load validated configuration while preserving the shared object identity."""
    next_cfg = read_config_file()
    for field_name in type(_cfg).model_fields:
        setattr(_cfg, field_name, getattr(next_cfg, field_name))


def get_config() -> GlobalConfig:
    """Return the process-wide configuration object."""
    return _cfg


def read_config_file() -> GlobalConfig:
    """Read and validate config.json without mutating global state."""
    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    cfg = GlobalConfig.model_validate(data)
    validate_model_refs(cfg)
    return cfg


def write_config_file(cfg: GlobalConfig) -> None:
    """Atomically write a validated config.json."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = cfg.model_dump(mode="json")
    payload = json.dumps(data, ensure_ascii=False, indent=4)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=WORKSPACE,
            prefix=".config.",
            suffix=".json.tmp",
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            f.write(payload)
            f.write("\n")
        temp_path.replace(CONFIG_FILE)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
