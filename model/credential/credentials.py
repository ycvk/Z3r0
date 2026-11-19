from datetime import datetime

from sqlmodel import Field, SQLModel


class SessionCredential(SQLModel, table=True):
    """Per-session or per-project credential vault record."""

    __tablename__ = "session_credentials"

    id: int | None = Field(default=None, primary_key=True)
    owner_id: int = Field(index=True, foreign_key="system_users.id")
    session_id: str = Field(default="", index=True)
    project_id: int | None = Field(default=None, foreign_key="work_projects.id", index=True)
    target: str = Field(default="", max_length=255, index=True)
    credential_type: str = Field(default="password", max_length=64)
    username: str = Field(default="", max_length=255)
    secret: str = Field(default="")
    note: str = Field(default="", max_length=500)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
