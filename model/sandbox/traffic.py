from datetime import datetime

from sqlmodel import Field, SQLModel


class SandboxTrafficEntry(SQLModel, table=True):
    """Captured HTTP request/response exchange from sandbox egress proxy or browser/API ingest."""

    __tablename__ = "sandbox_traffic_entries"

    id: int | None = Field(default=None, primary_key=True)
    owner_id: int = Field(default=0, index=True)
    sandbox_container_id: int | None = Field(default=None, index=True)
    project_id: int | None = Field(default=None, index=True)
    proxy_seq: int = Field(default=0, index=True)
    source: str = Field(default="proxy", max_length=32)
    method: str = Field(default="GET", max_length=16, index=True)
    url: str = Field(default="", max_length=2048)
    host: str = Field(default="", max_length=255, index=True)
    path: str = Field(default="/", max_length=1024)
    status_code: int = Field(default=0, index=True)
    request_headers_json: str = Field(default="{}")
    request_body: str = Field(default="")
    response_headers_json: str = Field(default="{}")
    response_body: str = Field(default="")
    response_bytes: int = Field(default=0)
    duration_ms: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.now, index=True)
