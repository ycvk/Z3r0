from datetime import datetime

from sqlalchemy import CheckConstraint
from sqlmodel import Field, SQLModel


class SessionCredential(SQLModel, table=True):
    """A credential in the vault of one work project, or of one agent session outside a project.

    The vault's project or session owns the record and takes it along when it is deleted.
    """

    __tablename__ = "session_credentials"
    __table_args__ = (
        CheckConstraint(
            "(project_id IS NULL) <> (session_id IS NULL)",
            name="ck_session_credentials_single_vault",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    # the user who recorded the credential
    owner_id: int | None = Field(default=None, index=True, foreign_key="system_users.id", ondelete="SET NULL")
    session_id: str | None = Field(
        default=None,
        index=True,
        foreign_key="agent_sessions.session_id",
        ondelete="CASCADE",
    )
    project_id: int | None = Field(default=None, index=True, foreign_key="work_projects.id", ondelete="CASCADE")
    target: str = Field(default="", max_length=255, index=True)
    credential_type: str = Field(default="password", max_length=64)
    username: str = Field(default="", max_length=255)
    secret: str = Field(default="")
    note: str = Field(default="", max_length=500)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
