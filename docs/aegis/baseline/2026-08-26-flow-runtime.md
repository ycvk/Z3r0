# Z3r0 Baseline after Flow Runtime

Date: `2026-08-26`
Status: `current dual-baseline snapshot`
Supersedes: `docs/aegis/baseline/2026-08-25-initial-baseline.md` for Flow ownership
ADR: `docs/aegis/adr/ADR-0001-flow-is-a-separate-aggregate.md`

## 1. Purpose

Record product and architecture truth after the Flow runtime was implemented
and live-accepted. Use this snapshot for later alignment checks until a newer
baseline supersedes it.

## 2. Product / Requirement Baseline

### 2.1 Current Truth

- Authorized operators can start a Flow from `/flows` against a running sandbox.
- The controller advances plan → subtask → `ask`/`done` → report without a
  Playground turn per step.
- Playground chat remains the assistant path and does not list FLOW sessions.
- WorkProject remains optional. Flows without `project_id` complete.

### 2.2 Non-negotiables

1. Authorized-scope operation only; no unsandboxed host execution
2. Playground chat remains a working assistant path
3. WorkProject remains the assessment ledger when a project is bound
4. Existing specialist codes stay
5. Flow progression is owned by code in `core/flow/controller.py`, not CSO prompt

### 2.3 Product Non-goals (unchanged)

- Stack rewrite in Go/Eino/Rust
- Auto-provision sandbox
- Forced WorkProject for every run
- First-class browser/file tools or search-provider matrix in this slice

## 3. Architecture / Runtime Boundary Baseline

### 3.1 Current Truth

- Chat owner: `core/runtime/session.py` (`AgentSession`)
- Flow owner: `core/flow/controller.py` + `service/flow/`
- Flow tools/graph: `core/tools/flow.py`, `core/tools/search.py`, `core/flow/graph.py`
- Sandbox command owner: `core/sandbox/command_jobs.py` + `service/sandbox/`
- Persistence: PostgreSQL via SQLModel
- Agent SDK: `openai-agents`
- Frontend: `/flows` plus existing Playground

### 3.2 Architecture Non-negotiables

1. Routers declare contracts; services own domain behavior; models persist
2. Control plane does not execute payload commands on the API host
3. Chat sessions, Flow records, and WorkProject records are separate aggregates
4. `SessionType.FLOW` transcripts are hidden from Playground lists

## 4. Ownership / Contract Snapshot

- Chat turns → `router/agent/sessions.py`
- Flows → `router/flow/flows.py`
- WorkProject → `router/work_project/`
- Sandbox containers → `router/sandbox/containers.py`
- Chat specialist mounts → `core/agent/specs.py`
- Flow mounts → `core/flow/graph.py` (parallel to chat mounts)

## 5. Compatibility Boundary

The Flow slice must not break Playground session list (except excluding FLOW),
turn submit, WebSocket replay, sandbox command jobs, or WorkProject CRUD.
