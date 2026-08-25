# Flow Runtime Design

Date: `2026-08-25`
Status: `approved`
Owner: `core/flow/` (runtime) · `service/flow/` (domain) · `web/src/features/flows/` (UI)

## 1. Why this exists

Z3r0 already has specialist agents, sandbox execution, replayable timelines, and
a WorkProject ledger. It does not have a runtime that owns “plan → execute →
replan → finish”. That loop currently lives in `cso` prompt text, so a real
assessment still needs an operator on every turn.

This slice adds a **Flow**: an autonomous assessment run. Success is product
behavior, not a language change. Python, FastAPI, `openai-agents`, and the
current sandbox stay.

## 2. Outcome

An authorized operator who already has a **running sandbox container** can:

1. Open `/flows`, enter a goal, select that container, optionally bind a WorkProject
2. Start the Flow and leave it running
3. Only interact when the runtime pauses on `ask`, or when the Flow completes
4. Receive a Markdown report that cites command output
5. If a WorkProject was bound, see new Evidence / Finding rows for material claims

Playground chat at `/playground` remains the assistant path and is unchanged.

## 3. Non-goals (this slice)

- Rewrite in Go, Eino, Rust, or any new agent SDK
- Auto-provision hosts, images, or containers
- First-class `browser` or `file` tools
- Search-provider fallback matrix (Tavily / Perplexity / Sploitus / etc.)
- Metasploit or new sandbox packages
- New specialist codes or rewriting `.z3r0/agents/*/AGENTS.md` as the loop owner
- Forcing WorkProject or WorkItem usage
- GraphQL, Langfuse, Grafana, OAuth, microservices
- Turning a Playground session into a Flow
- Changing CSO/specialist tool mounts for **chat** sessions

## 4. Primary scenario

**Given** the operator is logged in and sandbox container `42` is `running`  
**When** they create a Flow with goal
`Authorized test of http://TARGET.local:8080. Map the surface, validate one
exploitable finding if present, stop at proof. Scope is this host only.`
and no `project_id`  
**Then** the system:

- generates at most 15 subtasks
- executes them in order against container `42`
- uses `terminal` and `web_search` without asking the operator to chain
  `execute_*` then `read_sandbox_command_output`
- ends in `completed` or `waiting` (if `ask`) or `failed`
- produces a downloadable Markdown report

The operator does not submit a Playground turn to keep the loop alive.

## 5. Aggregates and owners

| Aggregate | Canonical owner | Source of truth |
|---|---|---|
| Flow + subtask plan + `ask`/`done` progression | **new** `core/flow/` + `service/flow/` | `flows` / `flow_subtasks` tables |
| Chat / project assistant session | existing `AgentSession` | `agent_sessions` + `agent_session_meta` |
| Command processes | existing sandbox async jobs | `sandbox_async_jobs` |
| Assessment ledger | existing WorkProject | `work_projects` and record tables |

A Flow **binds** one hidden `AgentSession` solely as the transcript and
WebSocket backend. That session is not listed in Playground. Flow status is
never inferred from `AgentSessionMeta.is_running`.

CSO and specialists are workers inside a subtask. They do not own whether the
Flow continues.

```text
Operator
  → POST /api/flows
  → FlowController (code)
      → Generator turn   (CSO + submit_subtask_list)
      → loop
          → Primary turn (CSO; may delegate specialists)
              tools: terminal, web_search, load_skill, delegation,
                     done, ask, WorkProject tools if bound
          → barrier: done | ask | failed
          → Refiner turn (CSO + patch_subtask_list)
      → Reporter turn    (CSO + export_report)
      → optional WorkProject writes (existing tools, best-effort)
```

### 5.1 Existence proof for the new Flow owner

Reuse candidate: `AgentSession` + CSO `AGENTS.md`.

Insufficient: session turns are operator-triggered; prompt text cannot enqueue
the next subtask, pause on `ask`, or refuse to skip the plan. Creating Flow as
a code state machine is the minimum new owner. Chat tools stay on the chat
owner.

## 6. Data model

### 6.1 `flows`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `public_id` | str unique | opaque URL id; same alphabet and length class as `session_id` |
| `owner_id` | int | `system_users.id` |
| `title` | str(80) | first line of goal, trimmed; no extra LLM call |
| `goal` | text | operator-authored, immutable after create |
| `status` | enum | see 6.3 |
| `sandbox_container_id` | int | required, must be `running` at start |
| `project_id` | int nullable | optional WorkProject |
| `session_id` | str | hidden transcript session |
| `ask_question` | text nullable | set only in `waiting` |
| `report_id` | str nullable | existing report export id |
| `error` | str | last fatal error |
| `created_at` / `updated_at` / `started_at` / `finished_at` | datetime | |

### 6.2 `flow_subtasks`

| Field | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `flow_id` | int FK | |
| `position` | int | 0-based, unique per flow |
| `title` | str(160) | |
| `description` | text | |
| `status` | enum | `planned` `running` `done` `skipped` `failed` |
| `summary` | text | `done` payload |
| `created_at` / `updated_at` | datetime | |

Constraints:

- at most 15 subtasks per flow
- exactly one `running` subtask at a time
- deleting a flow deletes subtasks; the hidden session is deleted through
  existing session delete

### 6.3 Flow status

`queued` → `planning` → `running` → (`waiting` ⇄ `running`) → `reporting` → `completed`

Failure/cancel: any non-terminal state → `failed` or `cancelled` (terminal).

Allowed transitions are enforced in `service/flow/`, not in the LLM.

## 7. Runtime contract

Module: `core/flow/controller.py`. This file owns progression.

Rules:

1. After create, the controller starts the Generator turn without a user
   Playground message.
2. Generator may only terminate the planning phase by `submit_subtask_list`.
   Unstructured “here is my plan” text does not start execution. After two
   failed planning turns, status=`failed`.
3. Primary executes the current `running` subtask. `done` completes it.
   `ask` sets status=`waiting` and stores the question. Any other stop
   (idle timeout, stream error) is `failed` for that subtask unless the
   controller classifies it as retryable (one retry).
4. After `done`, Refiner may `patch_subtask_list` against **planned**
   items only. It cannot edit `done` items or the just-finished summary.
5. The controller pops the next `planned` subtask. If none remain, it
   enters `reporting`.
6. Reporter must call existing `export_report`. If it does not, the
   controller writes a fallback Markdown report from subtask titles and
   the last terminal excerpts stored on the flow timeline.
7. `waiting` resumes only via `POST /api/flows/{id}/input`. The operator
   answer is injected as the next Primary turn, not as a new Flow.
8. `cancel` interrupts the bound session with the existing interrupt path,
   cancels sandbox async jobs for that session, then marks `cancelled`.

Turn execution **reuses** `AgentSession` / `run_until_idle` /
subagent delegation. Do not fork a second runner.

## 8. Tool contract (Flow-bound only)

Chat sessions keep current mounts from `core/agent/specs.py`. Flow turns
swap command tools as below.

### 8.1 `terminal`

Replaces `execute_sync_command` + `execute_async_command` for Flow-bound
agents (Primary and specialists).

| Arg | Rules |
|---|---|
| `command` | non-empty shell string |
| `timeout_seconds` | default 600, clamp 1–1800 |

Behavior:

- Dispatch through existing sandbox async jobs (same capture files)
- The turn is terminal on dispatch, same as today’s async command
- On job completion, the resume payload **includes an excerpt**: first 80
  lines and last 120 lines, hard cap 32 KiB of UTF-8 text, plus
  `output_file`, `exit_code`, `output_lines`, `truncated`
- `read_sandbox_command_output` remains for paging the full file
- Concurrency: keep the existing per-agent cap of 3

Do not add a parallel execution backend.

### 8.2 `web_search`

Single engine, no fallback matrix.

| Arg | Rules |
|---|---|
| `query` | 1–200 chars |
| `limit` | default 8, clamp 1–8 |

Returns JSON: `{results:[{title,url,snippet}]}`.

v1 engine: HTTP search against DuckDuckGo HTML (`html.duckduckgo.com/html/`),
timeout 15s, from the API process. If the engine fails, the tool returns
`status=failed` with the error; the Flow continues. The agent may fall back
to `terminal` + in-sandbox curl. Do not silently switch providers.

No API key in v1. If operators later want Tavily/Searxng, that is a later
config surface, not this spec.

### 8.3 `done`

```text
done(summary: str)
```

Valid only on a Flow Primary turn with a `running` subtask. Completes that
subtask. Specialist agents do not receive `done`.

### 8.4 `ask`

```text
ask(question: str)
```

Valid only on a Flow Primary turn. Pauses the Flow. Question length 1–2000
chars. Specialist agents do not receive `ask`.

### 8.5 `submit_subtask_list` / `patch_subtask_list`

Generator: `submit_subtask_list(items: [{title, description}, ...])`, 1–15 items.

Refiner: `patch_subtask_list(ops)` where each op is
`add` / `remove` / `replace` on **planned** positions. Resulting planned
count still ≤ 15.

These tools are rejected outside `planning` / post-`done` refine windows.

### 8.6 Unchanged tools

- `load_skill`
- specialist delegation
- `export_report`
- WorkProject tools, **only when** `project_id` is set; when unset they are
  not mounted

## 9. HTTP / UI contract

Prefix: `/api/flows`. Auth: existing JWT user middleware.

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/api/flows` | create + start |
| `GET` | `/api/flows` | paginated list for current user |
| `GET` | `/api/flows/{public_id}` | detail + subtasks + ask_question |
| `POST` | `/api/flows/{public_id}/input` | answer `ask` (`{text}`) |
| `POST` | `/api/flows/{public_id}/cancel` | cancel |
| `GET` | `/api/flows/{public_id}/report` | download Markdown via existing report files |

Live events: frontend subscribes to the bound `session_id` with the **existing**
agent-session WebSocket. No second event protocol.

Create body:

```json
{
  "goal": "string, 1-8000 chars",
  "sandbox_container_id": 1,
  "project_id": null
}
```

Create rejects: empty goal; container missing or not `running`; `project_id`
not owned / not found when provided.

Frontend:

- Nav item **Flows** (`/flows`), admin-visible like Work Projects
- List: title, status, sandbox, timestamps
- Detail `/flows/:id`: goal, subtask list with status, transcript (reuse
  Playground transcript components), `ask` composer when `waiting`, cancel,
  report download
- Create dialog: goal textarea, running-sandbox selector, optional project

Playground session list **excludes** Flow-bound sessions by adding
`SessionType.FLOW` and filtering that type out of Playground queries.

## 10. WorkProject binding

Optional. When `project_id` is null, no WorkProject tools are mounted and
the Flow is still allowed to complete.

When set:

- existing WorkProject tools are mounted on Primary and specialists
- writes are best-effort; a failed `save_finding` does not fail the Flow
- the report should mention bound project id
- this slice does **not** auto-create WorkItems or require lead review

## 11. Compatibility

Must not change:

- `POST /api/agent-sessions` turn behavior
- Playground list/create/stream for `chat` and `project` sessions
- sandbox async job schema for chat-originated commands
- WorkProject REST records
- specialist instruction files. Flow-bound extra rules are injected at
  runtime from `core/flow/prompts.py`; do not edit `SOUL.md` / `AGENTS.md`

Additive only: new tables, new router, new tools mounted only on Flow-bound
graphs, new frontend routes.

## 12. Acceptance

Observable, slice-complete when all of the following pass:

1. **Create-and-run:** with a running sandbox, `POST /api/flows` returns a
   Flow in `planning` or `running` without a Playground turn.
2. **No babysitting:** a fixture goal against a local sample service in the
   sandbox produces `completed` (or a single `waiting` that resumes after
   one `input`) without further operator turns. Wall clock may be long; the
   test is that the controller advances subtasks by itself.
3. **Terminal excerpt:** a `terminal` `echo hello` resume payload contains
   `hello` without a follow-up `read_sandbox_command_output`.
4. **Search:** `web_search` with query `example` returns 1–8 results or a
   structured tool error; it does not crash the Flow.
5. **Ask/done:** a Primary `ask` moves status to `waiting`; `input` resumes
   the same subtask; `done` advances to the next planned item or reporting.
6. **Isolation:** creating a Flow does not add a row to the Playground
   session list. Submitting a Playground chat turn does not create a Flow.
7. **Optional ledger:** create without `project_id` completes. Create with a
   valid `project_id` still completes if WorkProject tools error.
8. **Report:** completed Flow has a downloadable Markdown file with the
   original goal and at least the subtask titles.
9. **Cancel:** `cancel` during `running` yields `cancelled` and no new
   sandbox jobs for that session.
10. **Chat regression:** existing agent-session list/turn/websocket tests or
    a manual Playground send still work.

Falsifier for this whole product bet: if a 90-day implementation of this spec
cannot complete acceptance item 2 on an authorized local sample, consider a
new stack. That evaluation is out of scope until this spec is implemented.

## 13. Risks

- LLM ignores `done` and narrates instead of calling tools. Mitigation:
  planning already requires structured list tools; Primary turns that end
  without `done`/`ask`/in-flight terminal are retried once, then the
  subtask fails.
- DuckDuckGo blocks the API host. Mitigation: tool error is allowed; agent
  may use sandbox `terminal`.
- Long nmap still exceeds 1800s. Mitigation: documented limit; operator
  uses `ask` or splits subtasks. Do not raise to PentAGI’s 3h in v1.
- Hidden session leaks into Playground. Mitigation: list filter is an
  acceptance item.
- Operators expect one-click sandbox create. Explicitly deferred; do not
  expand this slice.

## 14. ADR signal (not an accepted ADR yet)

After implementation, record an ADR:

- Decision: Flow runtime is a separate aggregate from `AgentSession`; chat
  is not upgraded into a planner
- Alternatives rejected: WorkItem-as-plan; CSO-prompt-as-loop; Go/Eino or
  Rust rewrite; Playground dual-mode session
- Baseline sync: update the architecture baseline owner map to include
  `core/flow/`

Do not treat this spec as accepted architecture memory until the code exists.

## 15. Implementation notes for the later plan

Likely file seams (plan may refine):

- `model/flow/flows.py`, `schema/flow/flows.py`
- `service/flow/`, `core/flow/`
- `router/flow/flows.py`, `handler/flow/flows.py`
- `core/tools/flow.py` (`done`, `ask`, plan tools, `terminal`, `web_search`)
- `core/agent/specs.py` — Flow graph builder, do not overwrite chat mounts
- `web/src/features/flows/`, `web/src/app/routeManifest.ts`
- `scripts/export_schema.py` then `web` `generate:api`

Suggested delivery order:

1. tables + API skeleton + UI shell (create/list/detail, no autonomy)
2. controller + generator/refiner/done/ask
3. `terminal` excerpt on existing async jobs
4. `web_search`
5. report + optional WorkProject mount
6. acceptance run

---

## Appendix — design drafts

TaskIntentDraft:
- Outcome: operator starts one Flow and gets a finished, evidenced report
- Success evidence: acceptance items 1–10
- Stop: chat path unchanged; no new language; no forced WorkProject
- Non-goals: section 3
- Risk: loop still collapses into prompt if controller is not the owner

BaselineReadSetHint:
- `README.md`, `docs/zh/guide/overview.md`, `core/agent/specs.py`,
  `core/runtime/session.py`, `core/tools/sandbox.py`,
  `schema/agent/sessions.py`, `web/src/app/routeManifest.ts`

Requirement Ready Check: `ready` for this slice, pending user review of
this document.

Architecture Integrity Lens:
- Invariant: only the Flow controller advances the plan
- Canonical owner: `core/flow/` + `service/flow/`
- Overlap to avoid: CSO prompt re-stating the loop; Playground session
  `is_running` meaning Flow status
- Verdict: proceed after spec approval
