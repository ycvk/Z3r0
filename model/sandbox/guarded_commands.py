from datetime import datetime

from sqlmodel import Field, SQLModel


class GuardedCommand(SQLModel, table=True):
    """A destructive command a session tried to run: waiting for the operator, or approved to run once."""

    __tablename__: str = "sandbox_guarded_commands"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True, foreign_key="agent_sessions.session_id", ondelete="CASCADE")
    command: str = Field(default="")
    approved: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.now)
