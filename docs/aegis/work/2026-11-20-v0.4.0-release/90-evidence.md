# Evidence (v0.4.0 Release)

Run on 2026-11-20 on the release commit unless a line names another commit. arm64 host, PostgreSQL in Docker.

- `uv run ruff check`: pass
- `uv run lint-imports`: 3 contracts kept
- `uv run basedpyright`: 0 new errors, 649 in the baseline
- `uv run pytest`: 46 passed
  - `test_access_matrix.py`: every list and record endpoint as an admin and two users
  - `test_domain_errors.py`: each `DomainError` subclass and its HTTP status through real routes
  - `test_credential_vault.py`: vault scope, deletion with the project, session, and recording user, and revision `0002` on rows written under `0001`
  - `test_flow_lifecycle.py`: deleting a user, a work project, and a sandbox container that Flows refer to; a waiting Flow answered while no controller runs
  - `test_command_guard.py`: stored pending command and approval, deleted with the session
  - `test_agent_turn.py`: a chat turn end to end against a stub model server
  - `test_migrations.py`: models match the revisions
- `cd sandbox/proxy && gofmt -l . && go vet ./... && go test -race ./...`: pass; on `e2870e1` the proxy tests also passed 200 consecutive runs under `-race`
- `sh sandbox/.agents/validate-skills.sh sandbox/.agents/skills`: pass
- `uv run python scripts/export_schema.py && npm --prefix web run generate:api`: the committed contract is in sync
- `npm --prefix web run build && npm --prefix web run build:landing`: pass
- Migrations, each checked on the commit that added the revision
  - Empty database: `0001` through `0004` applied, models in sync, a second start changes nothing
  - Copy of a database created before migrations, seeded with v0.3.1-shaped credential and Flow rows: adopted and upgraded through `0003`, rows moved into their vaults, models in sync
  - Revision `0004` ran on an empty database and on the test database
- Application image against an empty database, over its HTTP API, on `c9a8427`, `eab7a3e`, and `b384088`
  - Project credential saved, its recording user deleted, the credential still listed for another owner, the project deleted
  - A Flow session rejected by the session endpoints with 404; a container with a waiting Flow rejected with 400; a user who started Flows and a project with a Flow deleted
  - A waiting Flow answered, the container restarted, the controller took the answer and the intercepted command received one approval
  - The Flow rows in these runs were written to the database directly and no model was called
- Real-container acceptance with `penetration-runtime:latest` (image `a48401f831dd`): 48 of 48 checks passed
  - Same harness and checks as the v0.3.1 acceptance, driven through the v0.4.0 services
  - Start: the control proxy answered 18.8 s after the first start request and 12.4 s after a restart
  - Burst: 650 concurrent requests were all received by the target and all persisted by the periodic pull
- GitHub CI passed on `b384088`, the commit before the release commit
