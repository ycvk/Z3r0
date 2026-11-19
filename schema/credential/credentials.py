from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SessionCredentialSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    session_id: str = ""
    project_id: int | None = None
    target: str
    credential_type: str = "password"
    username: str = ""
    secret: str
    note: str = ""
    created_at: datetime
    updated_at: datetime


class CreateSessionCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=255)
    credential_type: str = Field(default="password", min_length=1, max_length=64)
    username: str = Field(default="", max_length=255)
    secret: str = Field(min_length=1, max_length=4096)
    note: str = Field(default="", max_length=500)

    @field_validator("target", "credential_type", "username", "secret", "note", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value
