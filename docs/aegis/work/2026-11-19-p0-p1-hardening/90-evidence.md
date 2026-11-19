# Evidence (v0.3.0 Runtime Guards & Traffic Capture)

- `uv run --active --no-project --with pytest==8.4.2 --with pytest-asyncio==1.2.0 pytest -v tests/`: 8 passed
  - `test_traffic_idor_blocked_for_other_user`: cross-tenant list, detail (`owner_id=0` rows), ingest, and clear return 404; owner and admin succeed
  - `test_proxy_traffic_sync_dedup_and_clear_error_surface`: concurrent `sync_container_traffic` calls deduplicate by `proxy_seq`; proxy `DELETE /traffic` failure surfaces
  - `test_agent_cannot_self_bypass_with_allow_guard_prefix`: command-text prefix cannot bypass Command Guard
  - `test_approval_is_exact_command_and_one_shot`: operator grant permits only the exact intercepted command once
  - `test_credentials_context_error_surfaces`: Credentials Vault read failure propagates out of turn context activation
  - `test_token_ledger_and_credentials_vault_end_to_end`: atomic token accumulation on `/api/flows` and `/api/agent-sessions`, REST + FunctionTool credential CRUD, and turn context injection
  - `test_command_guard_flow_and_playground_tools_acceptance`: Flow `terminal` `ask` barrier and Playground `execute_sync_command` one-shot operator approval
  - `test_live_go_proxy_http_https_mitm_and_high_volume_sync`: live `sandbox-proxy` binary with `--init-ca`, HTTP + self-signed HTTPS MITM decryption (including gzip response decoding), 622-request burst with periodic PostgreSQL sync and zero dropped entries, `search_traffic` / `get_traffic_detail` verification
- `cd sandbox/proxy && go vet ./... && go test -v -race ./...`: 3 passed (HTTPS MITM keep-alive + gzip, raw TCP CONNECT fallback, `/ca.crt` token auth)
- `bash sandbox/.agents/validate-skills.sh sandbox/.agents/skills`: pass
- `uv run --active --no-project python scripts/export_schema.py && npm --prefix web run build && npm --prefix web run build:landing`: pass
