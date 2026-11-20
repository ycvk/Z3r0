from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from config import ModelApi, ProviderId, ReasoningEffort


class _CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CatalogModelCost(_CatalogModel):
    """USD per million tokens."""

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_read: float = Field(ge=0)
    cache_write: float = Field(ge=0)


class CatalogModel(_CatalogModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    api: ModelApi
    # empty when the provider has no fixed endpoint, such as a per-resource Azure deployment
    base_url: str
    context_window: int = Field(ge=1)
    max_tokens: int = Field(ge=1)
    reasoning: bool
    # values the model accepts as reasoning effort; empty when it has no effort control
    reasoning_efforts: list[ReasoningEffort]
    # whether thinking is sized by a token budget instead of an effort level
    thinking_budget: bool
    image_input: bool
    cost: CatalogModelCost
    # headers the provider requires for this model
    headers: dict[str, str] = Field(default_factory=dict)


class CatalogProvider(_CatalogModel):
    id: ProviderId
    name: str = Field(min_length=1)
    models: list[CatalogModel] = Field(min_length=1)


class CatalogSource(_CatalogModel):
    package: str = Field(min_length=1)
    version: str = Field(min_length=1)
    generated_at: datetime


class ModelCatalog(_CatalogModel):
    source: CatalogSource
    providers: list[CatalogProvider]
