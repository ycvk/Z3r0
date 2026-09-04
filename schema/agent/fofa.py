from pydantic import BaseModel, ConfigDict, Field


class FofaAssetSchema(BaseModel):
    """Canonical asset fields returned by the FOFA search tool."""

    model_config = ConfigDict(extra="forbid")

    ip: str = ""
    port: str = ""
    protocol: str = ""
    org: str = ""
    host: str = ""
    domain: str = ""
    title: str = ""


# The request projection follows the canonical asset schema declaration.
_FOFA_FIELDS: tuple[str, ...] = tuple(FofaAssetSchema.model_fields)
FOFA_FIELDS = ",".join(_FOFA_FIELDS)


class FofaSearchResultSchema(BaseModel):
    """Paginated FOFA search response exposed by the function tool."""

    model_config = ConfigDict(extra="forbid")

    query: str = ""
    total_size: int = Field(default=0, ge=0)
    current_page: int = Field(default=1, ge=1)
    results: list[FofaAssetSchema] = Field(default_factory=list)
