# ADR-0002 Backend layer direction and guardrails

Status: `accepted`
Date: `2026-11-19`
Owners: `pyproject.toml` (contracts and tool configuration) · `.github/workflows/ci.yml` (enforcement) · `app.py` (runtime wiring)

## Context

The outer backend layers imported in one direction: `router` -> `handler` ->
`service`, with `model` and `schema` as leaves. `core` (agent runtime and
tools) and `service` (domain services and persistence) imported each other: 37
module pairs went from `core` to `service`, and 16 of their 20 sub-packages
formed one mutually dependent cluster. The cycles were held together by
import-time callbacks, function-level imports, and duplicated logic.

The repository had 12 backend tests and no lint, type check, import check,
migration tool, lock file, or CI, so structural changes could not be verified.

## Decision

Layers:

- Backend packages import downward only, in this order: `router`, `handler`
  and `tools`, `service`, `middleware`, `core`, `model`, `schema`, `utils`.
  `lint-imports` enforces the order and has no exceptions.
- `handler` is the entry for HTTP requests and `tools` is the entry for agent
  tool calls. They sit on the same layer and do not import each other.
- `core` is the agent runtime and the state that only the runtime writes: the
  notification queue, the timeline event log, subagent task records, and
  sandbox async job records.
- `service` holds the domain services and the orchestration built on the
  runtime, including the Flow controller and the work project turn context.

Wiring:

- `core/runtime/ports.py` declares what the runtime needs from the layers
  above it as `RuntimePorts`: session run state, turn context, sandbox command
  execution, and waking the main agent.
- `tools/catalog.py` declares the chat and Flow tool mounts of every agent.
  `ToolMount` fields tell the registry what it needs to know about a tool, so
  `core` never imports one.
- `app.py` installs the ports and the catalog in `install_agent_runtime()`.
  The runtime raises when it is used before that.

Checks:

- `basedpyright` runs in `basic` mode against `.basedpyright/baseline.json`;
  the baseline only shrinks.
- `ruff` runs the `E4`, `E7`, `E9`, `F`, `B`, and `ASYNC` rules on every
  Python file in the repository.
- Dependencies are declared in `pyproject.toml` and pinned by `uv.lock`, with
  a 7-day `exclude-newer` window for new resolutions.
- Schema changes are Alembic revisions under `migrations/versions`, applied at
  startup. `tests/test_migrations.py` fails when models and revisions differ.
- `.github/workflows/ci.yml` runs every check on pushes to `main` and on pull
  requests.

## Consequences

- An upward import fails `lint-imports`. A new need of the runtime becomes a
  field of `RuntimePorts`, and `basedpyright` checks the implementation that
  `app.py` passes for it.
- A new agent tool is a module under `tools/` and a `ToolMount` in
  `tools/catalog.py`.
- Code that runs outside the application lifespan, such as a test, calls
  `install_agent_runtime()` before it uses the runtime.
- A model change without a revision fails the test suite.
- The application image installs from the lock file with `uv sync --frozen`.
- The baseline revision is conditional throughout, so databases created
  before migrations are adopted at their first start.

## Evidence

2026-11-19, local:

- `uv run ruff check`, `uv run lint-imports` (1 contract kept, no ignored
  imports), `uv run basedpyright` (0 new errors, 661 in baseline), and
  `uv run pytest` (20 passed).
- `tests/test_agent_turn.py` runs a chat turn against a stub model server and
  covers the session run state and turn context ports;
  `tests/test_runtime_ports.py` covers the command execution and wake-up
  ports; `tests/test_tool_catalog.py` covers the tool mounts.
- Every agent binding (6 agents, chat and Flow, with and without a sandbox and
  a work project) kept the same tool list and instructions across the move of
  the tool catalog.
- `go vet` and `go test -race` in `sandbox/proxy`, the API contract export
  with no diff, and both frontend builds.
- The application image booted against an empty database at revision `0001`.
  Through its HTTP API, a chat turn reached a stub model server and settled
  with its usage recorded, an instance config update rebuilt the agent pool,
  and a further turn ran on the rebuilt pool.
- A copy of the development database was adopted with models in sync.
