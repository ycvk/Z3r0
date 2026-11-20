# ADR-0005 Credentials and captured traffic are their own domains

Status: `accepted`
Date: `2026-11-19`
Owners: `service/credential` (vault) · `service/traffic` (captured exchanges) · `pyproject.toml` (import contract) · `tests/test_credential_vault.py` (vault lifecycle)

## Decision

Chat sessions, Flows, and work projects all use the credentials vault and the
captured traffic. Each of the two is a domain with its own packages:

| Domain | Packages | Entry points |
| --- | --- | --- |
| Credentials | `model/credential`, `schema/credential`, `service/credential` | `tools/credentials.py`; the Flow and work project endpoints, which decide access before they call the service |
| Traffic | `model/traffic`, `schema/traffic`, `service/traffic`, `handler/traffic`, `router/traffic` | `/api/traffic`; `tools/traffic.py` |

Boundaries:

- `service.credential` and `service.traffic` do not import `service.agent`,
  `service.flow`, or `service.work_project`; a `lint-imports` contract
  enforces it. Those three import the two domains.
- `service/credential` is the only code that queries `session_credentials`.
  `service/credential/context.py` renders the vault section of an agent turn.
- `service/traffic` is the only code that queries `sandbox_traffic_entries`,
  apart from the ownership columns that `service/common/access.py` reads.
  `service/traffic/entries.py` stores, searches, and clears exchanges and pulls
  them from a container's control proxy; `service/traffic/runtime.py` runs the
  background synchronization. A work project turns an exchange into evidence
  through `get_project_traffic_entry`.

Credential ownership:

- A credential belongs to exactly one vault. Everything recorded in a work
  project, by an operator, a project session, or a Flow of the project, goes to
  the project's vault. A session outside a project has its own vault. A check
  constraint keeps one of `project_id` and `session_id` set.
- Deleting a work project or a session deletes its vault through
  `ON DELETE CASCADE`.
- `owner_id` names the user who recorded the credential and becomes null when
  that user is deleted.

Traffic entries reference their container, project, and capturing user by
plain id and stay after any of them is deleted. `service/traffic/retention.py`
deletes entries past `agent_runtime.traffic_retention_seconds` and, per
container, those beyond `agent_runtime.traffic_max_entries_per_container`. It
keeps the newest proxy entry of every existing container, which is where
synchronization resumes.

## Consequences

- Vault behavior such as encryption at rest, and traffic behavior such as
  retention, each have one package to change.
- A project credential reports `session_id` as null in the API.
- Revision `0002` moves existing credentials into their vault and removes
  those whose session no longer exists.

## Evidence

2026-11-19, local: `tests/test_credential_vault.py` covers the shared project
vault, the vault of a session, deletion of a session, a work project, and a
recording user, deletion through the wrong vault, and revision `0002` on rows
written under revision `0001`. Revision `0002` also ran on a copy of a
database that predates migrations, seeded with the same rows.
`uv run ruff check`, `uv run lint-imports` (3 contracts kept),
`uv run basedpyright` (0 new errors, 649 in baseline), and `uv run pytest`
(35 passed) pass; the API contract export differs only in the nullable
`session_id` of a credential, and both frontend builds pass.
