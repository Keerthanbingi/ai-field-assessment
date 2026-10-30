# Acceptance Matrix

| Area | Requirement | Status | Evidence |
|---|---|---|---|
| Setup | Python 3.11+ virtual environment | Verified (3.12.14) | `docs/verification.md` |
| Setup | Required runtime and development dependencies import | Verified | `docs/verification.md` |
| Setup | Pytest and Ruff configured | Verified | `pyproject.toml`, `docs/verification.md` |
| Task 1.1 | Official MCP Python SDK used | PASS | `server.py:create_server`, `test_create_server_returns_official_sdk_server` |
| Task 1.2 | Both required tools registered | PASS | `server.py:_tool_definitions`, `test_tools_list_returns_exact_tools_and_strict_schemas` |
| Task 1.3 | Exact ASCII `CUST-XXXXX` validation | PASS | `models.py:GetCustomerRecordInput`, `test_invalid_customer_id_formats` |
| Task 1.4 | Refund amount positive, finite, and strict | PASS | `models.py:PositiveFiniteAmount`, `test_invalid_refund_amounts` |
| Task 1.5 | Reason trimmed with at least 10 non-whitespace characters | PASS | `models.py:TriggerRefundInput.enforce_semantics`, reason boundary/invalid tests |
| Task 1.6 | Missing and unexpected fields rejected | PASS | `models.py:StrictToolInput`, `test_missing_and_extra_fields` |
| Task 1.7 | Real stdio transport | PASS | `server.py:run`, official-client and raw subprocess tests |
| Task 1.8 | stdout contains only MCP JSON-RPC | PASS | `test_stdout_contains_only_jsonrpc_and_logs_use_stderr`, independent raw capture |
| Task 1.9 | Logs use stderr only | PASS | `server.py:_configure_logging`, stdout/stderr isolation test |
| Task 1.10 | Malformed requests return standard errors | PASS | `transport.py:run_transport_adapter`, parse/invalid/method tests |
| Task 1.11 | Invalid tool arguments return wire `-32602` | PASS | `server.py:_invalid_params`, raw and official-client tests |
| Task 1.12 | Errors sanitized with safe validation detail | PASS | `server.py:call_tool`, sanitization/diagnostics tests |
| Task 1.13 | Official MCP client initialize/list/call succeeds | PASS | `test_official_sdk_client_initializes_lists_and_calls_tools` |
| Task 1 audit | Complete executable evidence | PASS (39 Task 1 tests) | `docs/verification.md` audit section |
| Task 2.1 | Bearer tokens parsed correctly | PASS | `_authenticate`; valid/lowercase/missing/malformed-header HTTP tests |
| Task 2.2 | Roles cannot be forged or modified | PASS | `verify_token`; missing-role, attacker-signed, payload-modified, tampered, unsigned, expired, and unsupported-role tests |
| Task 2.3 | Authenticated `tools/list` forwarded transparently | PASS | `test_authenticated_roles_can_list_tools`; direct/proxy equality, counter `0 -> 1`, invocation log |
| Task 2.4 | `tools/call` validates and inspects `params.name` | PASS | `ToolCallParams`; malformed-params and allowed/blocked call tests |
| Task 2.5 | Every lowercase `admin_` prefix blocked for viewers | PASS | four-case `test_every_viewer_admin_prefix_call_is_blocked_locally_and_preserves_id` |
| Task 2.6 | Block response is exact `-32001` / `Unauthorized Tool Call` | PASS | exact whole-response assertion in viewer prefix test |
| Task 2.7 | Block response preserves request ID | PASS | parameter-derived ID asserted in exact response |
| Task 2.8 | Blocked calls never reach downstream | PASS | explicit counter before/after equality for every blocked prefix |
| Task 2.9 | Admin can call `admin_` tools | PASS | admin call counter `0 -> 1` and invocation-log tool assertion |
| Task 2.10 | Non-`admin_` tools forwarded | PASS | viewer regular call counter `0 -> 1`; exact downstream invocation asserted |
| Task 2.11 | Missing/malformed params, envelopes, batch, notification cannot bypass | PASS | five param cases, four envelope cases, malformed JSON, batch and notification tests; counters unchanged |
| Task 2.12 | Errors do not expose secrets or internal details | PASS | invalid-token, invalid-param, and unreachable-downstream sanitization assertions |
| Task 2 audit | Complete executable evidence | PASS (36 Task 2 tests) | `docs/verification.md` independent audit section |
| Task 3 | Streaming PII guardrail | Not started | — |
| Task 4 | SQLite rate limiter and fallback router | Not started | — |
