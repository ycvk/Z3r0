# Evidence

- `python -c` import of Flow controller/tools/app: pass
- OpenAPI paths: `/api/flows`, `/{public_id}`, `/input`, `/cancel`, `/report`
- `npm --prefix web run typecheck`: pass
- Live spec §12 (2026-08-26, local stack):
  1. create-and-run without Playground turn: pass
  2. unattended `completed` for `echo FLOW_OK`: pass (`38fff7fe…`, `8e31b89c…`)
  3. terminal excerpt contains `FLOW_OK` without `read_sandbox_command_output`: pass
  4. `web_search query=example` returns 8 results: pass (`fc3371ee…`)
  5. `ask` → `waiting` → `input` resumes: pass (`b53a9ad5…`)
  6. Flow session absent from Playground list; chat turn does not create Flow: pass
  7. create without `project_id` completes: pass
  8. completed Flow report downloadable Markdown: pass
  9. cancel → `cancelled`: pass
  10. Playground chat turn still works (`PONG`): pass
