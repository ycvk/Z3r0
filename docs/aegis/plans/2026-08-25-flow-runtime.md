# Flow Runtime Implementation Plan

Goal: Add an autonomous Flow aggregate so an operator with a running sandbox can start a goal, leave the loop running, and get a Markdown report without Playground babysitting.

Architecture: New `flows` / `flow_subtasks` tables owned by `service/flow/` + `core/flow/controller.py`. Each Flow binds one hidden `AgentSession` (`SessionType.FLOW`) for transcript and the existing WebSocket. CSO/specialists remain workers. Chat sessions keep current tool mounts.

Tech Stack: FastAPI, SQLModel, openai-agents, existing sandbox async jobs, React 19 + Semi UI, OpenAPI → `web/src/shared/api/generated/schema.ts`.

Baseline/Authority Refs:
- `docs/aegis/specs/2026-08-25-flow-runtime-design.md` (approved)
- `docs/aegis/baseline/2026-08-25-initial-baseline.md`
- `schema/agent/sessions.py` (`SessionType`)
- `service/agent/sessions.py` (list filter)
- `core/tools/sandbox.py` / `core/sandbox/command_jobs.py`
- `core/agent/specs.py` / `core/agent/registry.py`
- `core/delegation/notifications.py`
- `app.py` router mount
- `web/src/app/routeManifest.ts`

Compatibility Boundary:
- Do not change Playground turn/list/stream for `chat` and `project` sessions except exclude `SessionType.FLOW` from those lists
- Do not change chat tool mounts in `AGENT_SPECS`
- Do not edit `.z3r0/agents/*/AGENTS.md` or `SOUL.md`
- Do not auto-provision sandboxes
- Additive API `/api/flows` only
- WorkProject writes remain best-effort and optional

TDD Route:
- Mode: off
- Decision: skipped
- Strict authority: not applicable
- Test posture: post-change regression
- Reason: no project TDD request; repo has no pytest suite
- Verification: compile/import checks, OpenAPI export, `web` typecheck, focused Python snippets, manual Flow/Playground paths listed per task

Verification (plan-level):
1. `python -c` imports of new modules
2. `python scripts/export_schema.py` then `npm --prefix web run generate:api && npm --prefix web run typecheck`
3. Playground session list still returns only non-FLOW sessions
4. Spec acceptance items 1–10 in Task 7

Change Necessity:
- User-visible need: start a Flow and finish without per-turn chat
- No-change / non-code option: longer CSO prompts — rejected by spec
- Why code change is necessary: plan progression has no runtime owner
- Minimum change boundary: new Flow owner + Flow-only tools + `/flows` UI
- Decision: code-change

Existence Check:
- Proposed new surface: Flow aggregate, Flow tools, `/flows` UI
- Existing owner / reuse candidate: `AgentSession` + CSO prompt
- Why insufficient: turns are operator-triggered; prompt cannot enqueue subtasks or own `ask`/`done`
- Creation proof: spec §5.1
- Entropy / retirement impact: chat path stays; no deletion
- Decision: add-with-proof

Architecture Integrity Lens:
- Invariant: only `core/flow/controller.py` advances the plan
- Canonical owner: `service/flow/` persistence, `core/flow/` progression
- Overlap to avoid: CSO prompt as loop; `AgentSessionMeta.is_running` as Flow status
- Higher-level simplification: none — chat cannot own this
- Retirement / falsifier: spec §12 item 2 after 90 days
- Verdict: proceed

Plan Pressure Test:
- Owner / contract / retirement: new owner isolated; chat tools untouched
- Architecture integrity: controller is code
- Verification scope: API + list isolation + tool excerpt + controller transitions
- Task executability: sequential, each commit-sized
- Pressure result: proceed

Complexity Budget:
- Artifact class: new domain package + router/handler/service/model/schema + feature UI
- Target files / artifacts: ~25 new, ~12 modified
- Current pressure: `core/runtime/session.py` and `core/agent/registry.py` are large; do not grow them with Flow logic
- Projected post-change pressure: within-budget if Flow stays in `core/flow/` and `service/flow/`
- Budget result: at-risk only if controller is inlined into `session.py`
- Planned governance: new owner files; `session.py` only gains a create-session type argument and list filter

Plan-Time Complexity Check:
- Better file boundary: add `core/flow/`, `service/flow/`, `web/src/features/flows/`
- Recommendation: add owner file

ADR signal to preserve: Flow is a separate aggregate from `AgentSession`; record ADR after implementation, not before.

---

## File map

Create:
- `schema/flow/__init__.py`
- `schema/flow/flows.py`
- `model/flow/__init__.py`
- `model/flow/flows.py`
- `service/flow/__init__.py`
- `service/flow/records.py`
- `service/flow/locking.py`
- `core/flow/__init__.py`
- `core/flow/prompts.py`
- `core/flow/controller.py`
- `core/flow/graph.py`
- `core/tools/flow.py`
- `core/tools/search.py`
- `router/flow/__init__.py`
- `router/flow/flows.py`
- `handler/flow/__init__.py`
- `handler/flow/flows.py`
- `web/src/shared/api/flows.ts`
- `web/src/features/flows/FlowsPage.tsx`
- `web/src/features/flows/FlowCreateModal.tsx`
- `web/src/features/flows/FlowWorkspacePage.tsx`
- `web/src/features/flows/flowView.ts`

Modify:
- `schema/agent/sessions.py` — `SessionType.FLOW`
- `service/agent/sessions.py` — exclude FLOW from chat/project lists; typed session create
- `database.py` — register Flow models
- `app.py` — mount flow router; start/stop flow controller
- `core/sandbox/command_jobs.py` / `core/sandbox/command_output.py` / `core/delegation/notifications.py` — optional excerpt on async completion
- `core/agent/registry.py` — Flow graph builder stays in `core/flow/graph.py` (call site only if needed)
- `service/agent/recovery.py` — resume queued/running/planning flows
- `web/src/app/routeManifest.ts`
- `web/src/shared/api/types.ts` — after generate:api, add Flow aliases if needed
- `docs/aegis/INDEX.md`

Do not modify:
- `.z3r0/agents/**`
- `core/agent/specs.py` chat `AGENT_SPECS` mounts (Flow graph is a parallel builder)

---

## Task 1 — Persistence and session isolation

Files:
- create `schema/flow/flows.py`, `model/flow/flows.py`
- modify `schema/agent/sessions.py`, `service/agent/sessions.py`, `database.py`

Why: Flow needs its own tables; hidden sessions must not appear in Playground.

Change Necessity: tables and list filter cannot be docs-only.

Impact/Compatibility: additive enum value; list WHERE clause excludes FLOW.

Verification:
```bash
python -c "from schema.agent.sessions import SessionType; from model.flow.flows import Flow, FlowSubtask; assert SessionType.FLOW == 'flow'"
```

### Step 1 — Add `SessionType.FLOW`

In `schema/agent/sessions.py` change:

```python
class SessionType(StrEnum):
    CHAT = "chat"
    PROJECT = "project"
    FLOW = "flow"
```

### Step 2 — Exclude FLOW from operator session lists

In `service/agent/sessions.py` `_list_sessions`, after building `stmt`, add:

```python
stmt = stmt.where(meta_table.c.session_type != SessionType.FLOW)
```

Apply to both `project_id is None` (Playground) and project session lists so Flow-bound sessions never show as chat.

Keep `session_summary` by id working so the Flow detail page can reuse the bound session WebSocket.

### Step 3 — Extend session creation

Replace `create_session` signature in `service/agent/sessions.py`:

```python
async def create_session(
    user_id: int,
    *,
    session_type: SessionType = SessionType.CHAT,
    title: str = "",
    project_id: int | None = None,
    sandbox_container_id: int | None = None,
    sandbox_container_generation: int = 0,
) -> str:
    session_id = str(uuid4())
    async with get_async_session() as session:
        await lock_system_user_lifecycle(session, user_id)
        if await session.get(SystemUser, user_id) is None:
            raise PermissionError("system user no longer exists")
        await ensure_sdk_session_row(session, session_id)
        session.add(AgentSessionMeta(
            session_id=session_id,
            session_type=session_type,
            title=title,
            agent_code=DEFAULT_AGENT_CODE,
            owner_id=user_id,
            project_id=project_id,
            selected_sandbox_container_id=sandbox_container_id,
            selected_sandbox_container_generation=sandbox_container_generation,
        ))
        await session.commit()
    return session_id
```

Existing `create_session(user_id)` callers stay valid.

### Step 4 — Flow schemas

Create `schema/flow/__init__.py` (empty) and `schema/flow/flows.py`:

```python
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
```

### Step 5 — Flow models

Create `model/flow/__init__.py` (empty) and `model/flow/flows.py`:

```python
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
```

### Step 6 — Register models

In `database.py` import `Flow, FlowSubtask` from `model.flow.flows` and append them to `_registered_models`.

Commit after this task once the import command passes.

---

## Task 2 — Flow records service and HTTP API (no autonomy yet)

Files:
- create `service/flow/records.py`, `service/flow/locking.py`
- create `handler/flow/flows.py`, `router/flow/flows.py`
- modify `app.py`

Why: operator can create/list/get/cancel a Flow row and hidden session before the controller exists.

Change Necessity: REST contract is the `/flows` entry.

Impact/Compatibility: new router mounted before `api_not_found_router`.

Verification:
```bash
python -c "from app import create_app; app = create_app(); assert any(getattr(r,'path','').startswith('/api/flows') for r in app.routes)"
```

### Step 1 — Locking helper

`service/flow/locking.py`:

```python
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from model.flow.flows import Flow


async def lock_flow_by_public_id(session: AsyncSession, public_id: str) -> Flow | None:
    result = await session.exec(
        select(Flow).where(Flow.public_id == public_id).with_for_update()
    )
    return result.first()
```

SQLModel `session.exec` + `with_for_update` matches `service/work_project/projects.py`. If `exec` does not accept the ORM select, use the same `session.exec(select(...))` pattern already in that file.

### Step 2 — records service

`service/flow/records.py` must implement:

- `flow_title(goal: str) -> str` — first line, strip, max 80 chars
- `to_schema(flow, subtasks=()) -> FlowSchema`
- `to_summary(flow) -> FlowSummarySchema`
- `create_flow(request, user) -> FlowSchema`
  - validate sandbox container exists and status is `running` via `service.sandbox.records` / container get
  - if `project_id` set, `get_work_project_for_user` must succeed
  - `public_id = str(uuid4())`
  - `session_id = await create_session(..., session_type=SessionType.FLOW, title=title, project_id=..., sandbox_container_id=..., sandbox_container_generation=container.generation or 0)`
  - insert `Flow` status=`queued`
  - return schema
- `list_flows(user, page, size)`
- `get_flow(public_id, user) -> FlowSchema | None` (owner or admin)
- `cancel_flow(public_id, user) -> FlowSchema | None`
  - if already terminal, return as-is
  - set `cancelled`, `finished_at`, clear `ask_question`
  - call `cancel_session_subagents(session_id)` and `cancel_session_async_sandbox_commands(session_id)`
  - interrupt session via existing `interrupt` service if present
- `answer_flow_input` in Task 5 (stub as `waiting`-only 400 until controller)

Reuse sandbox lookup from `service/sandbox/records.py` (read that file at implementation time; use the existing get-by-id function, do not duplicate Docker calls).

### Step 3 — handler + router

Mirror `router/work_project/projects.py`:

- `APIRouter(prefix="/flows", tags=["flows"], dependencies=[Depends(require_user)])`
- `POST ""` create (`require_user`)
- `GET ""` list
- `GET "/{public_id}"` detail
- `POST "/{public_id}/input"` (implemented in Task 5; for now return 400 `"flow is not waiting"`)
- `POST "/{public_id}/cancel"`
- `GET "/{public_id}/report"` (Task 6; for now 404 `"report not found"`)

Handlers use `raise_api_error` like work projects.

### Step 4 — Mount in `app.py`

```python
from router.flow.flows import router as flow_router
...
app.include_router(flow_router, prefix=API_PREFIX)
```

Place **before** `api_not_found_router`.

---

## Task 3 — OpenAPI client and Flows UI shell

Files:
- `web/src/shared/api/flows.ts`
- `web/src/features/flows/FlowsPage.tsx`
- `web/src/features/flows/FlowCreateModal.tsx`
- `web/src/features/flows/FlowWorkspacePage.tsx`
- `web/src/features/flows/flowView.ts`
- `web/src/app/routeManifest.ts`
- `web/src/shared/api/types.ts` (after generate)

Why: `/flows` is the independent entry from Playground.

Change Necessity: UI cannot be docs-only.

Impact/Compatibility: new nav item; Playground default path unchanged.

Verification:
```bash
cd web && python ../scripts/export_schema.py && npm run generate:api && npx tsc --noEmit
```

### Step 1 — Export schema

From repo root:

```bash
python scripts/export_schema.py
npm --prefix web run generate:api
```

Add `web/src/shared/api/flows.ts` following `workProjects.ts` + `agentSessions.ts`:
- `queryFlows`
- `createFlow`
- `getFlow`
- `submitFlowInput`
- `cancelFlow`
- `downloadFlowReport` via `apiBlob`

Add type aliases in `web/src/shared/api/types.ts` for Flow paths after generate.

### Step 2 — Nav routes

In `routeManifest.ts` import `Waypoints` from lucide-react. Insert after Playground:

```ts
  defineRoute(
    { path: "/flows", label: "Flows", eyebrow: "Autonomous Runs", icon: Waypoints, adminOnly: false, navigation: true },
    () => import("../features/flows/FlowsPage"),
    "FlowsPage",
  ),
  defineRoute(
    { path: "/flows/:flowId", label: "Flows", eyebrow: "Autonomous Runs", icon: Waypoints, adminOnly: false, navigation: false },
    () => import("../features/flows/FlowWorkspacePage"),
    "FlowWorkspacePage",
  ),
```

`adminOnly: false` so non-admin operators can run Flows (same as Playground). Create still requires a running sandbox they can see.

### Step 3 — List + create modal

`FlowsPage.tsx` copies `WorkProjectsPage` shell:
- `usePagedResourceList({ query: queryFlows })`
- `useAdminResourceHeader({ createLabel: "Create Flow", onCreate, onRefresh })`
- columns: title, status tag, sandbox id, project, updated_at
- row click → `/flows/${public_id}`

`FlowCreateModal.tsx`:
- textarea `goal`
- Select of running sandbox containers from existing `querySandboxContainers` (filter status `running`)
- optional WorkProject select (can be empty)
- submit `createFlow` then `navigate(/flows/${public_id})`

`flowView.ts`: status tag colors for queued/planning/running/waiting/reporting/completed/failed/cancelled.

### Step 4 — Workspace shell

`FlowWorkspacePage.tsx` v1:
- load `getFlow(flowId)`
- show goal, status, subtask list (empty until Task 5)
- Cancel button calling `cancelFlow`
- placeholder transcript: "Waiting for controller" until Task 6

Do not hook WebSocket yet.

---

## Task 4 — Flow tools and terminal excerpt

Files:
- create `core/tools/flow.py`, `core/tools/search.py`
- modify `core/sandbox/command_output.py`, `core/sandbox/command_jobs.py`, `core/delegation/notifications.py`
- create `core/flow/graph.py`

Why: agents need `terminal`/`web_search`/`done`/`ask`/plan tools; excerpt removes the execute-then-read tax.

Change Necessity: tools are the agent contract.

Impact/Compatibility: excerpt is additive on notification payload; chat next-step text stays valid. Chat mounts unchanged.

Verification:
```bash
python -c "from core.tools.flow import terminal, done, ask, submit_subtask_list, patch_subtask_list; from core.tools.search import web_search; from core.sandbox.command_output import excerpt_output"
python -c "from core.flow.graph import flow_tool_mounts"
```

### Step 1 — Output excerpt helper

In `core/sandbox/command_output.py` add:

```python
EXCERPT_HEAD_LINES = 80
EXCERPT_TAIL_LINES = 120
EXCERPT_MAX_BYTES = 32 * 1024


def excerpt_text(content: str) -> tuple[str, bool]:
    raw = content or ""
    truncated = False
    encoded = raw.encode("utf-8")
    if len(encoded) > EXCERPT_MAX_BYTES:
        raw = encoded[:EXCERPT_MAX_BYTES].decode("utf-8", errors="ignore")
        truncated = True
    lines = raw.splitlines()
    if len(lines) <= EXCERPT_HEAD_LINES + EXCERPT_TAIL_LINES:
        return "\n".join(lines), truncated
    head = lines[:EXCERPT_HEAD_LINES]
    tail = lines[-EXCERPT_TAIL_LINES:]
    omitted = len(lines) - EXCERPT_HEAD_LINES - EXCERPT_TAIL_LINES
    return "\n".join([*head, f"... {omitted} lines omitted ...", *tail]), True
```

Add a reader that loads the captured file from the sandbox using existing `read_sandbox_command_output` internals, or read via `service.sandbox.files` / command_output path. Implement `async def excerpt_output(container_id, output_file) -> tuple[str, bool]` by calling the same line reader already used by `read_sandbox_command_output` for lines 1..200 plus last 120. Keep it inside `command_output.py` / `core/tools/sandbox.py` without a second file protocol.

### Step 2 — Attach excerpt on async completion

In `_queue_completion_notification` payload (find the function in `core/sandbox/command_jobs.py`), after status fields, if `output_file` and container id exist, compute excerpt and set:

```python
payload["output_excerpt"] = excerpt
payload["output_truncated"] = truncated
```

If excerpt fails, omit keys; do not fail the job.

### Step 3 — Notification prompt

In `_sandbox_async_job_prompt`, if `payload.get("output_excerpt")`:

```python
sections.append("## Output Excerpt\n\n" + str(payload["output_excerpt"]))
```

Change Next Step to:

```
The command is terminal. Use the excerpt if it is sufficient. Read captured output only when you need lines outside the excerpt, then continue.
```

Chat specialists still have `read_sandbox_command_output`. Additive text is allowed.

### Step 4 — `core/tools/search.py`

```python
from agents import function_tool
import httpx

@function_tool
async def web_search(query: str, limit: int = 8) -> str:
    """Search the public web. Returns JSON {results:[{title,url,snippet}]} or {status,error}."""
```

Implementation:
- clamp query 1–200 chars, limit 1–8
- POST/GET `https://html.duckduckgo.com/html/` with `q=query`, timeout 15s, browser-like User-Agent
- parse first `limit` result links (`a.result__a` / `a.result__url` — use regex on HTML if no parser dependency; stdlib `html.parser` is enough)
- on HTTP/parse failure return `{"status":"failed","error": "..."}` JSON
- never raise into the agent runtime

### Step 5 — `core/tools/flow.py`

Tools (all `function_tool`):

- `terminal(ctx, command, timeout_seconds=600)` — Flow-bound wrapper:
  - same dispatch as `execute_async_command`
  - timeout clamp 1–1800, default 600
  - turn-terminal on RUNNING
  - extend `_end_turn_after_async_dispatch` in registry/graph to treat `terminal` like `execute_async_command`
- `done(ctx, summary)` — set a context flag `flow_barrier` on a dedicated module-level store keyed by `session_id` that the controller reads after the turn (see Task 5). Tool result: `{"status":"ok","barrier":"done"}`. Reject if no running subtask (controller injects context).
- `ask(ctx, question)` — same store, barrier `ask`
- `submit_subtask_list(ctx, items: list[dict])` — 1–15 `{title, description}`
- `patch_subtask_list(ctx, ops: list[dict])` — ops `{"op":"add|remove|replace","position":int,"title":str,"description":str}`

Barrier store: `core/flow/barriers.py`

```python
from dataclasses import dataclass

@dataclass
class FlowBarrier:
    kind: str  # done | ask | submit_plan | patch_plan
    payload: dict

_barriers: dict[str, FlowBarrier] = {}

def set_barrier(session_id: str, barrier: FlowBarrier) -> None:
    _barriers[session_id] = barrier

def pop_barrier(session_id: str) -> FlowBarrier | None:
    return _barriers.pop(session_id, None)
```

Tools call `set_barrier`. Controller calls `pop_barrier` after each turn.

### Step 6 — Flow graph builder

`core/flow/graph.py` builds a **copy** of agent specs for Flow-bound sessions:

- CSO tools: WorkProject tools only if `work_project_id`; `export_report`; `terminal` is NOT on CSO if we keep delegation — spec says Primary gets terminal AND delegation. Mount on CSO: `terminal`, `web_search`, `load_skill` (optional — CSO may not need load_skill; spec says Primary gets terminal and web_search). Also `done`, `ask`, `submit_subtask_list`, `patch_subtask_list`, `export_report`, WorkProject tools if bound, plus subagents.
- Specialists: replace `execute_sync_command`/`execute_async_command` with `terminal`; keep `read_sandbox_command_output`, `load_skill`, WorkProject tools if bound; no `done`/`ask`.

Do not mutate `AGENT_SPECS`. `AgentRegistry.bind` currently uses `AGENT_SPECS`. Implementation: add optional `SessionAgentGraph` factory `build_flow_registry()` that constructs `AgentRegistry(specs=FLOW_AGENT_SPECS)` **or** pass a tool snapshot flag.

Cleanest: `AgentToolSnapshot` gains `flow_bound: bool = False`. `SessionAgentGraph` tool resolution: if `flow_bound`, swap sandbox command tools and add barrier tools for CSO.

Implement the swap in `core/flow/graph.py` as `flow_specs(work_project: bool) -> tuple[AgentSpec, ...]` and have the Flow controller create `AgentRegistry(flow_specs(...))` for that session pool bind.

`get_agent_pool()` is global and uses the default registry. Do **not** replace the global registry.

How Flow turns run: controller calls `get_agent_pool().get(session_id)` which uses the default chat registry. **This is the coupling risk.**

Required implementation choice (pinned): extend `AgentToolSnapshot` with `flow_bound: bool = False`. In `SessionAgentGraph._tools_for` (read `registry.py` fully at implementation), when snapshot.flow_bound, use Flow mounts from `core/flow/graph.py`. Chat snapshots keep `flow_bound=False`.

Set `flow_bound=True` when constructing runtime context for the hidden Flow session (controller + session start).

Also extend `_end_turn_after_async_dispatch` to treat tool name `terminal` as terminal dispatch.

---

## Task 5 — Flow controller

Files:
- `core/flow/prompts.py`
- `core/flow/controller.py`
- `core/runtime/context.py` (add `flow_bound: bool = False` on `AgentRuntimeContext`)
- `core/agent/tool_snapshot.py` (add `flow_bound`)
- `app.py` lifespan start/stop
- `service/agent/recovery.py` or `service/flow/recovery.py`
- wire `create_flow` to `controller.start`
- implement `answer_flow_input`

Why: this is the product loop.

Change Necessity: without the controller, API is just a form.

Impact/Compatibility: chat `AgentRuntimeContext` default `flow_bound=False`.

Verification: unit-level status transitions with a fake runner are optional; minimum is:

```bash
python -c "from core.flow.controller import start_flow_runtime, stop_flow_runtime, start_flow"
```

Plus a dry create that reaches `planning` when the API is up (manual). Automated: controller functions map statuses as specified.

### Step 1 — Prompts

`core/flow/prompts.py` extra system text injected only for Flow-bound instruction builder.

Add a hook in `core/agent/instructions.py` `build_instructions`: if `tool_snapshot.flow_bound`, append `FLOW_PRIMARY_ADDENDUM` for CSO and a shorter specialist addendum. Do not edit workspace markdown files.

CSO addendum must state:
- You are inside a Flow. Call `submit_subtask_list` in planning. Call `done` or `ask` to end a subtask. Do not narrate a plan without the tool. Use `terminal` and `web_search`. Do not wait for the operator unless `ask`.

### Step 2 — Controller

`core/flow/controller.py` owns an asyncio task per in-flight Flow.

API:
- `start_flow_runtime()` / `stop_flow_runtime()`
- `await start_flow(public_id: str)` — spawn task
- `await submit_input(public_id, text)`
- `await cancel_flow_runtime(public_id)`

Loop:

```
load flow
set planning, started_at
run CSO turn with planning prompt (goal + sandbox reminder)
pop_barrier:
  submit_plan → insert FlowSubtask rows (planned), else retry once, else failed
while planned exists and status not terminal:
  mark next planned as running, flow running
  run CSO turn with current subtask description + remaining list
  if in-flight terminal jobs, wait for session idle (existing run_until_idle / notifications)
  pop_barrier:
    done → summary on subtask, status done, refine turn
    ask → flow waiting, store question, wait for submit_input future
    missing → retry one Primary turn; still missing → subtask failed, flow failed
  refine: CSO turn with patch_subtask_list allowed; ignore missing patch
reporting:
  CSO turn "call export_report with the final markdown"
  if no report_id, write fallback markdown via service.agent.reports.export_session_report
completed
```

How to run a turn without HTTP: use the same path as `submit_agent_session_turn` internally. Read `handler/agent/sessions.py` / `service/agent/runtime.py` and extract a function `run_session_turn(session_id, content, user)` if one exists; if the handler is the only entry, add `service/agent/runtime.py` helper **used by both** the HTTP handler and the controller. Do not duplicate Runner logic.

Pinned: add `service/flow/turns.py` that builds `AgentRuntimeContext` with `flow_bound=True`, sandbox from the Flow row, project from Flow.project_id, then calls the existing pool `submit`/`drive` method. Read `core/runtime/session.py` for the public method the HTTP handler uses (`AgentSession.submit` or similar) and call that.

Planning/refine/report prompts are `TurnTrigger` text, not user Playground messages, but they still persist on the hidden session timeline so the UI can replay them.

Max two planning failures. One Primary retry per subtask. Generator cannot start execution without `submit_subtask_list`.

`ask` creates `asyncio.Event` per flow id in the controller dict; `submit_input` sets the event and injects the operator text as the next Primary turn.

### Step 3 — Create starts the loop

At end of `create_flow`, `asyncio.create_task(start_flow(public_id))` through the runtime module so tests can stub it.

`app.py` lifespan: `await start_flow_runtime()` after agent pool start; shutdown `stop_flow_runtime`.

### Step 4 — Recovery

`service/flow/recovery.py`: on boot, flows in `queued|planning|running|reporting` are restarted; `waiting` stays waiting (ask_question preserved). Call from `app.py` next to `recover_pending_sessions`.

---

## Task 6 — Flow workspace live UI and report download

Files:
- `web/src/features/flows/FlowWorkspacePage.tsx`
- `handler/flow/flows.py` report route
- reuse Playground transcript components

Why: operator watches subtasks, answers `ask`, downloads report.

Verification: `npx tsc --noEmit` in `web`. Manual: waiting state shows composer; completed shows download.

### Step 1 — Report route

If `flow.report_id`, stream via existing `resolve_report_download_path` used by agent session report download. 404 otherwise.

### Step 2 — Workspace

Reuse `buildAgentStreamUrl(flow.session_id)` and Playground `Transcript` / `timelineStore` **without** Composer except when `status === waiting`.

Layout:
- left/main: transcript (existing components)
- right or top: subtask list with status
- banner when waiting: question + textarea + submit `submitFlowInput`
- header: Cancel if not terminal; Download report if `report_id`

Poll `getFlow` every 2s while not terminal **or** rely on websocket run-state plus a light poll for subtask/status fields (Flow status is not a session event). Poll is required for Flow status/subtasks.

Do not list the hidden session in `AgentSessionProvider`. Playground provider stays as-is.

---

## Task 7 — Acceptance and chat regression

Files: none required unless fixes.

Why: spec §12.

Verification commands (dev stack must be running for 2–10; 1 can be offline):

1. Create-and-run:
```bash
# after login cookie/token
curl -sS -H "X-Access-Token: TOKEN" -H "Content-Type: application/json" \
  -d '{"goal":"Echo hello in the sandbox and stop.","sandbox_container_id":ID}' \
  http://127.0.0.1:8000/api/flows
# expect public_id, status queued or planning or running, session_id set
```

2. No babysitting: Flow with goal `Run echo FLOW_OK in the sandbox, then finish.` reaches `completed` without further POSTs (besides create). Subtask list non-empty. This requires a live LLM and running sandbox.

3. Terminal excerpt: a `terminal` `echo hello` resume on the bound session timeline / notification contains `hello`.

4. Search: in a Flow, `web_search` query `example` returns results or structured error; Flow does not crash.

5. Ask/done: inspect DB/API — `done` advances subtask; force `ask` by goal `Ask the operator one yes/no question before any command.` → status `waiting` → `POST /input` → running.

6. Isolation:
```bash
# list agent-sessions must not include the Flow session_id
```

7. Optional ledger: create with `project_id` null completes; with invalid project_id → 404; with valid project_id does not 500 if tools fail.

8. Report: `GET /api/flows/{id}/report` returns markdown containing the original goal.

9. Cancel during running → `cancelled`, no new async jobs.

10. Chat regression: Playground new turn still streams; session list still works.

Stop if item 6 or 10 fails — those are compatibility blockers.

---

## Risks

- Global `AgentRegistry` used for Flow sessions: mitigated by `flow_bound` on snapshot, not a second pool
- DuckDuckGo block: tool error allowed
- LLM never calls `done`: one retry then fail the subtask
- `session.py` growth: controller must not land there
- Hidden session leak: Task 1 list filter is a hard gate

## Retirement

None. Chat tools and Playground remain canonical for assistant mode.

## Execution Readiness View

- Intent Lock: autonomous Flow on current Python stack
- Scope Fence: spec non-goals; no rewrite; no forced WorkProject; no chat tool remount
- Baseline Lock: approved spec + initial baseline
- Approved Behavior: spec §2, §4, §7, §8, §12
- Owner / Contract Constraints: `core/flow/` + `service/flow/` + `/api/flows`
- Compatibility Boundary: chat/project session lists and turns
- Retirement Boundary: none this slice
- Task Batches: 1 persistence → 2 API → 3 UI shell → 4 tools → 5 controller → 6 live UI → 7 acceptance
- Test Obligations: imports, openapi/typecheck, curl isolation, live happy path
- Review Gates: after Task 1 (list filter), Task 5 (controller owner), Task 7 (acceptance)
- Drift / Rewind Rules: if implementation puts loop in CSO prompt or `session.py`, rewind to Task 5
- Evidence Required Before Completion: spec items 1–10
- Advisory Boundary: method-pack execution guidance only
