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
| Task 3.1 | Real asynchronous HTTPX provider streaming | PASS | `app.py:_guarded_stream`; real Uvicorn integration suite |
| Task 3.2 | Parse UTF-8, SSE frames, and JSON deltas incrementally | PASS | one-byte live network split; strict parser; oversized unterminated event test |
| Task 3.3 | Email, formatted SSN, and Luhn-valid card redaction | PASS | normal cases, positions, punctuation, consecutive values, Luhn negative control |
| Task 3.4 | PII spanning arbitrary deltas is not leaked | PASS | 42 exhaustive single-split, 289 exhaustive pair-split, and 75 fixed-seed random live cases |
| Task 3.5 | Valid SSE and supported metadata preserved | PASS | UTF-8/framing and bounded metadata-order integration tests |
| Task 3.6 | Streaming begins before provider completion | PASS | first text 1.4 ms; provider observed `active=1`, `completed=0`; total 353.9 ms |
| Task 3.7 | Buffering bounded with fail-closed overflow | PASS | redactor ≤ candidate cap + 1; parser ≤ event cap + 4096; overlong live case |
| Task 3.8 | Malformed provider data fails closed | PASS | partial SSE, invalid JSON/UTF-8, unknown text shape, and oversized event; raw PII absent |
| Task 3.9 | Termination, disconnect, and cancellation safe | PASS | exact `[DONE]` and real client-disconnect provider-state tests |
| Task 3.10 | No candidate fragments emitted early | PASS | character-per-delta live cases assert every emitted content is empty or `[REDACTED]` |
| Task 3 audit | Independent correctness/security/latency/memory audit | PASS (38 tests; 406 boundary cases) | `docs/verification.md` Task 3 audit section |
| Task 4 rate 1 | Genuine SQLite database on disk | PASS | `rate_limit.py:_initialize`; SQLite file signature, WAL, schema, and `:memory:` rejection tests |
| Task 4 rate 2 | Raw tenant API keys are not persisted | PASS | `auth.py:TenantAuthenticator`; authenticated request followed by SQL-row and DB/WAL/SHM byte scans |
| Task 4 rate 3 | Tokenizer-based accounting, not string length | PASS | `tokenizer.py:TokenCounter`; independent `tiktoken` encoding equality and character-length inequality |
| Task 4 rate 4 | Default limit is 50,000 tokens per rolling 60 seconds | PASS | `Task4Settings` and limiter defaults; exact-boundary and default-setting tests |
| Task 4 rate 5 | Window is rolling, not fixed minute buckets | PASS | staggered reservations at 1000/1030/1060/1090 verify individual expiry |
| Task 4 rate 6 | Concurrent requests cannot bypass limit | PASS | 20 simultaneous 10-token requests at limit 100 allow exactly 10 |
| Task 4 rate 7 | Read/check/insert is one atomic SQLite transaction | PASS | `rate_limit.py:_reserve_sync` uses `BEGIN IMMEDIATE`; shared-file contention test stores exactly 100 tokens |
| Task 4 rate 8 | Tenant usage is isolated | PASS | tenant A at 50,000 does not block tenant B at 49,999 |
| Task 4 rate 9 | Records expire at exact 60-second boundary | PASS | 59.999 rejected; 60.000 accepted; expired row evicted |
| Task 4 rate 10 | Reopening the database preserves active usage | PASS | second limiter instance sees first instance's 60-token reservation |
| Task 4 concurrency | Two limiter instances share one on-disk database safely | PASS | alternating instances, 20 simultaneous reservations: 10 allowed, usage 100, 10 rows |
| Task 4 fallback 1 | Primary success sends zero backup calls | PASS | live provider counters `(1, 0)` |
| Task 4 fallback 2 | Primary HTTP 429 falls back successfully | PASS | live provider counters `(1, 1)` and secondary response |
| Task 4 fallback 3 | Primary timeout falls back successfully | PASS | real delayed primary and secondary response |
| Task 4 fallback 4 | Default primary timeout is 3000 ms | PASS | `Task4Settings.primary_timeout_seconds == 3.0` |
| Task 4 fallback 5 | Timed-out primary is cancelled/closed | PASS | provider reports `primary_active=0`, `primary_disconnected=1` |
| Task 4 fallback 6 | No infinite retries | PASS | primary 500 and both-fail cases each produce counters `(1, 1)` |
| Task 4 fallback 7 | Fallback is not double-billed | PASS | exactly one reservation row with `input_tokens + max_tokens` |
| Task 4 fallback 8 | Both providers can be forced to fail | PASS | live primary/secondary 500 modes return 502 after counters `(1, 1)` |
| Task 4 fallback 9 | Provider failure uses standardized gateway error | PASS | exact `PROVIDERS_UNAVAILABLE` envelope and request-ID assertions |
| Task 4 security | Every client error class is sanitized | PASS | auth, invalid request, local limit, unavailable providers, and induced internal failure scanned for traces, URLs, secrets, and raw exceptions |
| Task 4 defect fix | Unexpected local reservation failures use standardized errors | PASS | `app.py:completions` catches unexpected reservation failures; induced secret-bearing `RuntimeError` returns safe `GATEWAY_INTERNAL_ERROR` |
| Task 4 audit | Complete executable evidence | PASS (28 Task 4 tests) | `docs/verification.md` independent Task 4 audit section |
