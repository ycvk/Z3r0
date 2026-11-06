# Z3r0 Initial Baseline

Date: `2026-08-25`
Status: `initial dual-baseline snapshot`

## 1. Purpose
Capture the product and architecture truth that the Flow runtime design must
respect. Later alignment checks should use this snapshot until a newer
baseline supersedes it.

## 2. Workspace Structure
- `main.py` / `app.py` — FastAPI process entry and lifespan
- `router/` `handler/` `service/` `model/` `schema/` — control-plane layers
- `core/` — agent runtime, conversation, delegation, sandbox command jobs, tools
- `web/` — React workbench (`Playground` is the default admin path)
- `sandbox/` — Docker image, control proxy, sandbox-local skills
- `.z3r0/agents/` — specialist instruction files (`cso` lead + five specialists)

## 3. Current Authority Surfaces
- `README.md` / `README_zh.md` — product positioning: red-team collaboration workbench
- `docs/zh/guide/overview.md` — evidence plane and specialist model
- `CHANGELOG.md` — released behavior through 0.2.2
- No ADR directory and no prior Flow contract exist

Authority gap: autonomous end-to-end execution is described in specialist
prompts, not in a runtime owner.

## 4. Product / Requirement Baseline
### 4.1 Current Truth
- Target users: authorized operators running scoped assessments
- Current default path: configure host/image/container, open Playground chat,
  CSO delegates specialists, operators drive every turn
- Durable records live in WorkProject (assets, evidence, findings, attack paths)
- Sandbox execution is mandatory for live testing
- Session timeline is replayable via REST + WebSocket

### 4.2 Non-negotiables
1. Authorized-scope operation only; no unsandboxed host execution
2. Playground chat remains a working assistant path
3. WorkProject remains the assessment ledger when a project is bound
4. Existing specialist codes (`cso`, `cae`, `cie`, `cpe`, `cre`, `cce`) stay

### 4.3 Product Non-goals (as of this snapshot)
- Competing with PentAGI by rewriting the stack in Go or Rust
- CALDERA-style campaign packs
- Forced WorkProject usage for every agent run

## 5. Architecture / Runtime Boundary Baseline
### 5.1 Current Truth
- Canonical chat owner: `core/runtime/session.py` (`AgentSession`)
- Tool owner: `core/agent/specs.py` + `core/tools/`
- Sandbox command owner: `core/sandbox/command_jobs.py` + `service/sandbox/`
- Persistence: PostgreSQL via SQLModel
- Agent SDK: `openai-agents` (`agents.Runner`)
- Frontend contract: OpenAPI → `web/src/shared/api/generated/schema.ts`

### 5.2 Architecture Non-negotiables
1. Routers declare contracts; services own domain behavior; models persist
2. Control plane does not execute payload commands on the API host
3. Chat sessions and WorkProject records are separate aggregates

### 5.3 Architecture Non-goals
- Splitting the process into microservices
- Replacing LightRAG, the sandbox proxy, or the React workbench in the Flow slice

## 6. Ownership / Contract Snapshot
- Chat turns → `router/agent/sessions.py`
- WorkProject → `router/work_project/`
- Sandbox containers → `router/sandbox/containers.py`
- Specialist tool mounts → `core/agent/specs.py`
- Missing seam: no Flow aggregate, no `done`/`ask` barrier tools, command
  results are metadata-only until a second read tool

## 7. Current State and Risks
- Version 0.2.2, Python/FastAPI/React
- Risk: investigation loop exists only in `.z3r0/agents/cso/AGENTS.md`
- Risk: time-to-first-test is dominated by infrastructure ceremony

## 8. Alignment Use
- Product questions: can an operator finish a scoped test without babysitting?
- Architecture questions: which aggregate owns plan progression?
- Report `scope: both` when a change alters Flow behavior and chat/session contracts

## 9. Compatibility Boundary
The Flow slice must not break Playground session list, turn submit, WebSocket
replay, sandbox command jobs, or WorkProject CRUD.
