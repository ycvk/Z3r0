from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from schema.common.responses import PaginatedResponse


class TrafficEntrySummarySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    sandbox_container_id: int | None = None
    project_id: int | None = None
    source: str = "proxy"
    method: str
    url: str
    host: str
    path: str
    status_code: int
    response_bytes: int = 0
    duration_ms: int = 0
    created_at: datetime


class TrafficEntryDetailSchema(TrafficEntrySummarySchema):
    request_headers: dict[str, str] = Field(default_factory=dict)
    request_body: str = ""
    response_headers: dict[str, str] = Field(default_factory=dict)
    response_body: str = ""


class IngestTrafficItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str = Field(default="GET", min_length=1, max_length=16)
    url: str = Field(min_length=1, max_length=2048)
    status_code: int = Field(default=200, ge=0, le=999)
    request_headers: dict[str, str] = Field(default_factory=dict)
    request_body: str = Field(default="", max_length=262144)
    response_headers: dict[str, str] = Field(default_factory=dict)
    response_body: str = Field(default="", max_length=262144)
    response_bytes: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)


class IngestTrafficRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sandbox_container_id: int | None = Field(default=None, gt=0)
    project_id: int | None = Field(default=None, gt=0)
    items: list[IngestTrafficItemRequest] = Field(min_length=1, max_length=50)


class IngestTrafficResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ingested: int


class ClearTrafficResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted: int


class ListTrafficResponse(PaginatedResponse[TrafficEntrySummarySchema]):
    pass
