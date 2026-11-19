# ADR-0002 Backend layer direction and guardrails

Status: `accepted`
Date: `2026-11-19`
Owners: `pyproject.toml` (contracts and tool configuration) · `.github/workflows/ci.yml` (enforcement)

## Context

The outer backend layers import in one direction: `router` -> `handler` ->
`service`, with `model` and `schema` as leaves. `core` (agent runtime) and
`service` (domain services and persistence) import each other: 23 module pairs
go from `core` to `service`. The cycles are held together by startup
callbacks, function-level imports, and duplicated logic.

The repository had 12 backend tests and no lint, type check, import check,
migration tool, lock file, or CI, so structural changes could not be verified.

## Decision

- Backend packages import downward only, in this order: `router`, `handler`
  and `tools`, `service`, `core`, `middleware`, `model`, `schema`, `utils`.
- `handler` is the entry for HTTP requests and `tools` is the entry for agent
  tool calls. They sit on the same layer and do not import each other.
- `tools/catalog.py` declares which tools each agent mounts. `core` receives
  that catalog from `app.py` and reads tool behaviour from `ToolMount` fields,
  so it never imports a tool.
- `lint-imports` enforces the order. The 18 existing `core` -> `service`
  imports are listed under `ignore_imports`; the list only shrinks, and a
  stale entry fails the check.
- `basedpyright` runs in `basic` mode against `.basedpyright/baseline.json`;
  the baseline only shrinks.
- `ruff` runs the `E4`, `E7`, `E9`, `F`, `B`, and `ASYNC` rules.
- Dependencies are declared in `pyproject.toml` and pinned by `uv.lock`, with
  a 7-day `exclude-newer` window for new resolutions.
- Schema changes are Alembic revisions under `migrations/versions`, applied at
  startup. `tests/test_migrations.py` fails when models and revisions differ.
- `.github/workflows/ci.yml` runs every check on pushes to `main` and on pull
  requests.

## Consequences

- Inverting the `core` -> `service` imports is tracked by the `ignore_imports`
  list in `pyproject.toml`.
- A model change without a revision fails the test suite.
- The application image installs from the lock file with `uv sync --frozen`.
- The baseline revision is conditional throughout, so databases created
  before migrations are adopted at their first start.

## Evidence

2026-11-19, local: `uv run ruff check`, `uv run lint-imports` (1 contract kept,
18 ignored imports), `uv run basedpyright` (0 new errors, 661 in baseline),
`uv run pytest` (17 passed), `go vet` and `go test -race` in `sandbox/proxy`,
API contract export with no diff, both frontend builds, an application image
build that booted against an empty database at revision `0001`, and adoption
of a copy of the development database with models in sync.
