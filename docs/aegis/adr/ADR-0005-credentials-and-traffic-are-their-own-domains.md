# ADR-0005 Credentials and captured traffic are their own domains

Status: `accepted`
Date: `2026-11-19`
Owners: `service/credential` (vault) · `service/traffic` (captured exchanges) · `pyproject.toml` (import contract)

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

## Consequences

- Vault behavior such as encryption at rest, and traffic behavior such as
  retention, each have one package to change.

## Evidence

2026-11-19, local: `uv run ruff check`, `uv run lint-imports` (3 contracts
kept), `uv run basedpyright` (0 new errors), and `uv run pytest` pass; the API
contract export has no diff.
