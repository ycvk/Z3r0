from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from schema.common.responses import PaginatedResponse


class FlowStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING = "waiting"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FlowSubtaskStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


FLOW_TERMINAL_STATUSES = (FlowStatus.COMPLETED, FlowStatus.FAILED, FlowStatus.CANCELLED)
FLOW_RECOVERABLE_STATUSES = (
    FlowStatus.QUEUED,
    FlowStatus.PLANNING,
    FlowStatus.RUNNING,
    FlowStatus.WAITING,
    FlowStatus.REPORTING,
)
MAX_FLOW_SUBTASKS = 15
MAX_FLOW_GOAL_CHARS = 8000


class FlowSubtaskSchema(BaseModel):
    id: int
    position: int
    title: str
    description: str = ""
    status: FlowSubtaskStatus
    summary: str = ""
    created_at: datetime
    updated_at: datetime


class FlowSchema(BaseModel):
    id: int
    public_id: str
    owner_id: int
    title: str
    goal: str
    status: FlowStatus
    sandbox_container_id: int
    project_id: int | None = None
    session_id: str
    ask_question: str | None = None
    report_id: str | None = None
    error: str = ""
    subtasks: list[FlowSubtaskSchema] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class FlowSummarySchema(BaseModel):
    id: int
    public_id: str
    title: str
    status: FlowStatus
    sandbox_container_id: int
    project_id: int | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class CreateFlowRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=MAX_FLOW_GOAL_CHARS)
    sandbox_container_id: int = Field(gt=0)
    project_id: int | None = Field(default=None, gt=0)

    @field_validator("goal", mode="before")
    @classmethod
    def strip_goal(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class FlowInputRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ListFlowsResponse(PaginatedResponse[FlowSummarySchema]):
    pass
