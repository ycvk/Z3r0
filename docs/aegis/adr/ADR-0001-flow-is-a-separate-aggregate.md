# ADR-0001 Flow is a separate aggregate from AgentSession

Status: `accepted`
Date: `2026-08-26`
Owners: `core/flow/` (progression) · `service/flow/` (persistence)

## Context

Z3r0 already had specialist agents, sandbox jobs, replayable session timelines,
and an optional WorkProject ledger. Plan progression still lived in `cso`
prompt text, so an authorized assessment needed an operator turn to keep going.

The executed product requirement is: start a goal against a running sandbox,
leave the loop running, and get a Markdown report. Chat at `/playground` stays
the assistant path.

## Decision

Flow is a separate aggregate from `AgentSession`.

- Source of truth for plan, subtask status, `ask`/`done`, and Flow status is
  `flows` / `flow_subtasks`, owned by `service/flow/` and advanced only by
  `core/flow/controller.py`.
- Each Flow binds one hidden `AgentSession` (`SessionType.FLOW`) for transcript
  and WebSocket. That session is excluded from Playground lists.
- Flow status is never inferred from `AgentSessionMeta.is_running`.
- CSO and specialists remain workers inside a subtask. They do not own whether
  the Flow continues.
- Chat tool mounts in `AGENT_SPECS` stay unchanged. Flow mounts a parallel
  graph from `core/flow/graph.py`.

## Alternatives rejected

- WorkItem-as-plan: WorkProject is an optional ledger, not the runtime loop.
- CSO-prompt-as-loop: prompts cannot enqueue subtasks or own `ask`/`done`.
- Go/Eino or Rust rewrite: rejected for this product slice.
- Playground dual-mode session: chat sessions must not become Flows.

## Consequences

- Additive `/api/flows` and `/flows` UI. Playground turn/list/stream contracts
  stay except the FLOW list filter.
- Primary turns that end without `done`/`ask`/in-flight terminal are retried
  once, then the subtask fails.
- `export_report` on a Flow-bound session is valid only in `reporting`.
- WorkProject writes remain optional and best-effort.

## Evidence

Live local acceptance on `feat/flow-runtime` (2026-08-26): create-and-run,
unattended `completed` with `echo FLOW_OK`, `web_search` returning 8 results,
`ask`/`input`, cancel, Playground isolation, report download.
