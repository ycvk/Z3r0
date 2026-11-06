from datetime import datetime

from sqlmodel import Field, SQLModel

from schema.flow.flows import FlowStatus, FlowSubtaskStatus


class Flow(SQLModel, table=True):
    __tablename__ = "flows"

    id: int | None = Field(default=None, primary_key=True)
    public_id: str = Field(index=True, unique=True, max_length=64)
    owner_id: int = Field(index=True, foreign_key="system_users.id")
    title: str = Field(default="", max_length=80)
    goal: str = Field(default="")
    status: FlowStatus = Field(default=FlowStatus.QUEUED, index=True)
    sandbox_container_id: int = Field(foreign_key="sandbox_containers.id", index=True)
    project_id: int | None = Field(default=None, foreign_key="work_projects.id", index=True)
    session_id: str = Field(index=True, foreign_key="agent_sessions.session_id")
    ask_question: str | None = Field(default=None)
    report_id: str | None = Field(default=None)
    error: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    started_at: datetime | None = Field(default=None)
    finished_at: datetime | None = Field(default=None)


class FlowSubtask(SQLModel, table=True):
    __tablename__ = "flow_subtasks"

    id: int | None = Field(default=None, primary_key=True)
    flow_id: int = Field(foreign_key="flows.id", index=True, ondelete="CASCADE")
    position: int = Field(default=0)
    title: str = Field(default="", max_length=160)
    description: str = Field(default="")
    status: FlowSubtaskStatus = Field(default=FlowSubtaskStatus.PLANNED, index=True)
    summary: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
