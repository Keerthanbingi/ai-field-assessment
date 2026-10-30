# Verification Log

This document records reproducible checks. Expected output describes success criteria; actual
output must be copied from a real run and must never be fabricated.

## Setup verification

Run from the repository root after activating `.venv`:

```bash
python --version
python -c "import fastapi, httpx, mcp, pydantic; print('dependency imports: ok')"
python -m pytest
python -m ruff check .
```

Expected:

- Python reports version 3.11 or newer.
- The import command prints `dependency imports: ok`.
- Pytest reports the setup smoke test passing.
- Ruff reports `All checks passed!`.

Actual output from 2026-10-30:

```text
Python 3.12.14
dependency imports: ok
fastapi=0.162.0 httpx=0.28.1 mcp=installed pydantic=2.14.2
collected 5 items
tests/test_setup.py .....                                                [100%]
============================== 5 passed in 0.22s ===============================
All checks passed!
No broken requirements found.
```

This is the final clean rerun after setting pytest-asyncio's fixture loop scope. Timing may
differ on another machine.

## Task 1 verification

Commands run on 2026-10-30:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pip check
.venv/bin/python scripts/verify_task1.py
```

Actual output:

```text
collected 44 items
tests/task1/test_official_client.py ..
tests/task1/test_stdio_integration.py .....................................
tests/test_setup.py .....                                                [100%]
============================== 44 passed in 7.32s ==============================
All checks passed!
25 files already formatted
No broken requirements found.
PASS: initialize
PASS: tools/list exposes exactly two tools
PASS: get_customer_record succeeds
PASS: invalid arguments map to -32602
PASS: malformed JSON maps to -32700
PASS: server logs are present on stderr
Summary: 6/6 checks passed
```

The Task 1 suite contains 39 protocol cases; the remaining five are repository setup smoke
tests. There were zero failed or skipped tests.

Captured successful tool response (refund IDs vary; this record response is deterministic):

```json
{"jsonrpc":"2.0","id":10,"result":{"content":[{"type":"text","text":"{\"customer_id\": \"CUST-12345\", \"email\": \"jordan.rivera@example.test\", \"name\": \"Jordan Rivera\", \"status\": \"active\"}"}],"structuredContent":{"customer_id":"CUST-12345","name":"Jordan Rivera","email":"jordan.rivera@example.test","status":"active"},"isError":false}}
```

Captured invalid-amount response:

```json
{"jsonrpc":"2.0","id":11,"error":{"code":-32602,"message":"Invalid tool arguments","data":{"issues":[{"field":"amount","message":"Input should be greater than 0","type":"greater_than"}]}}}
```

## Task 1 independent audit

Audit commands run on 2026-10-30 after adding official SDK client interoperability, exact
reason-boundary, and safe-validation-diagnostics coverage:

```bash
.venv/bin/python -m pytest tests/task1 -v
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
```

Actual final output:

```text
tests/task1: collected 39 items
============================== 39 passed in 7.26s ==============================
full repository: collected 44 items
============================== 44 passed in 7.32s ==============================
All checks passed!
26 files already formatted
```

An additional live subprocess probe sent initialization, `tools/list`, a successful tool call,
an invalid tool call, and malformed JSON. It captured five stdout lines and stderr separately:

```text
stdout_lines=5
all_stdout_json=True
stderr_has_startup_log=True
stdout_has_startup_log=False
```

The invalid call returned ID `audit-invalid`, code `-32602`, and safe issue data containing only
the field (`amount`), message, and validation type. The malformed JSON response returned ID
`null` and code `-32700`.

## Task 2 verification

Commands run on 2026-10-30:

```bash
.venv/bin/python -m pytest tests/task2 -v
.venv/bin/python scripts/verify_task2.py
.venv/bin/python -m pytest tests/task1 -q
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
```

Actual test output:

```text
Task 2: collected 18 items
============================== 18 passed in 1.30s ==============================
Task 1 regression: 39 passed in 7.25s
Full repository: collected 62 items
============================== 62 passed in 8.62s ==============================
All checks passed!
35 files already formatted
```

The Task 2 tests launch real Uvicorn gateway and downstream processes and send HTTPX requests over
TCP. A separate gateway instance points to an unused port to exercise sanitized connection
failure handling. The original standalone verifier reported 6/6 checks passing. Its raw response
examples and downstream blocked-call count are recorded in `docs/task2.md`.

## Task 2 independent audit

The implementation was re-read and tested as untrusted code on 2026-10-30. The audit expanded
the real HTTP suite to cover explicit downstream counters before and after every security
rejection, multiple lowercase `admin_` names, Bearer parsing, missing and forged roles, a viewer
payload changed to admin without re-signing, attacker-signed admin tokens, null/empty/missing tool
names, malformed JSON-RPC envelopes, batch requests, and notifications.

Commands run from the repository root:

```bash
.venv/bin/python -m pytest tests/task2 -v
.venv/bin/python scripts/verify_task2.py
.venv/bin/python -m pytest tests/task1 -q
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pip check
```

Actual final output:

```text
Task 2: collected 36 items
============================== 36 passed in 1.30s ==============================
PASS: viewer tools/list forwarded
PASS: viewer regular tool allowed
PASS: viewer admin tool blocked locally
DOWNSTREAM_COUNT_BEFORE_BLOCK=0
DOWNSTREAM_COUNT_AFTER_BLOCK=0
PASS: admin tool allowed for admin
PASS: authorized admin call reached downstream
PASS: missing token rejected
PASS: batch rejected
Summary: 7/7 checks passed
Task 1 regression: 39 passed in 7.23s
Full repository: collected 80 items
============================== 80 passed in 8.67s ==============================
All checks passed!
35 files already formatted
No broken requirements found.
```

The verifier's exact blocked response was:

```json
{"jsonrpc":"2.0","id":3,"error":{"code":-32001,"message":"Unauthorized Tool Call"}}
```

After the blocked request left the count unchanged at zero, the authorized admin call made the
count one; its invocation log named `admin_reset_key`. The test suite independently asserts the
same before/after property for `admin_`, `admin_reset_key`, `admin_rotate_secret`, and
`admin__nested`.

During test expansion, one proposed header value with trailing spaces could not be emitted by
HTTPX/h11 because it is illegal at the HTTP transport layer. It was replaced with the legal but
invalid value `Bearer abc def`; the gateway returned the sanitized invalid-token response and did
not forward it. This was a test-construction issue, not a gateway failure.

## Task 3 verification

Task 3 was implemented and verified on 2026-10-30 with separate Uvicorn gateway and provider
processes. All integration cases use HTTPX over real TCP; they do not substitute in-process helper
calls for the streaming protocol path.

Commands:

```bash
.venv/bin/python -m pytest tests/task3 -v
.venv/bin/python scripts/verify_task3.py
.venv/bin/python -m pytest tests/task1 -q
.venv/bin/python -m pytest tests/task2 -q
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pip check
```

Standalone verifier output:

```text
PASS: normal text preserved
BEFORE_DELTAS=["Contact john@","gmail.","com now."]
AFTER_TEXT=Contact [REDACTED] now.
PASS: split email redacted
PASS: split SSN redacted
PASS: split Luhn-valid card redacted
PASS: one-byte UTF-8/SSE splits remain valid
FIRST_SANITIZED_TEXT_MS=1.2
TOTAL_STREAM_MS=355.5
PASS: safe output arrives before provider completion
PASS: malformed provider event fails closed
Summary: 7/7 checks passed
```

The first expanded integration run reported 21 passed and two failures. One failure identified a
real metadata-ordering defect: terminal usage metadata could precede the final safety-buffered
word. The gateway was changed to defer bounded metadata until the sanitized tail is emitted. The
second was a test assumption that expected only one delta despite a valid metadata-preserving
empty delta. After the production fix and corrected assertion, the Task 3 suite passed 23/23.

Two additional defensive cases were then added: malformed JSON containing an email fails closed,
and a card-like number that fails Luhn is preserved. Final clean-run output:

```text
Task 3: collected 25 items
============================== 25 passed in 1.39s ==============================
Task 1 regression: 39 passed in 7.23s
Task 2 regression: 36 passed in 1.30s
Full repository: collected 105 items
============================= 105 passed in 9.99s ==============================
All checks passed!
43 files already formatted
No broken requirements found.
```

## Task 3 independent correctness, security, latency, and memory audit

Audit commands run on 2026-10-30:

```bash
.venv/bin/python -m pytest tests/task3 -v
.venv/bin/python scripts/verify_task3.py
.venv/bin/python -m pytest tests/task1 -q
.venv/bin/python -m pytest tests/task2 -q
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pip check
```

The live adversarial suite sent 406 boundary variants through separate Uvicorn gateway and mock
provider processes: 42 every-position single splits, 289 every-pair splits, and 75 fixed-seed
random multi-chunk partitions. All variants covered email, formatted SSN, and space-separated
Luhn-valid card values, and asserted both reconstructed output and raw client bytes.

Five defects were fixed during the audit: trailing-period email matching, a card/punctuation
overlap leak, forwarding unknown text-bearing JSON as metadata, late enforcement of the SSE limit
for an unterminated line, and fixed-size HTTPX coalescing that delayed output until completion.
The final latency test also queried provider state when the first text arrived and observed
`active=1` and `completed=0`.

Actual final output:

```text
Task 3: collected 38 items
============================== 38 passed in 7.19s ==============================
FIRST_SANITIZED_TEXT_MS=1.4
TOTAL_STREAM_MS=353.9
Standalone verifier: 7/7 checks passed
Task 1 regression: 39 passed in 7.22s
Task 2 regression: 36 passed in 1.29s
Full repository: 118 passed in 15.98s
All checks passed!
44 files already formatted
No broken requirements found.
```

The code-level redactor bound is the configured candidate limit plus one decision character
(321 characters for the default email-like candidate and 97 for numeric candidates). Parser
application state is limited to the configured 65,536-character event plus one 4,096-byte parser
slice. One naturally arriving network chunk is transiently owned by HTTPX and is never accumulated
with the response. A candidate that exceeds its bound emits `[REDACTED]` and remains suppressed to
its boundary; this can over-redact a long benign token but does not release it.

## Task 4 verification

Task 4 was implemented and verified on 2026-10-30 with real temporary on-disk SQLite files and
separate Uvicorn gateway/provider processes. The test suite does not use in-memory SQLite or paid
providers.

Commands:

```bash
.venv/bin/python -m pytest tests/task4 -v
.venv/bin/python scripts/verify_task4.py
.venv/bin/python -m pytest tests/task1 -q
.venv/bin/python -m pytest tests/task2 -q
.venv/bin/python -m pytest tests/task3 -q
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pip check
```

Standalone verifier output:

```text
PASS: on-disk SQLite file created
PRIMARY_COUNTS=1,0
PASS: primary success avoids secondary
FALLBACK_COUNTS=1,1
FALLBACK_RESERVATION_ROWS=1
PASS: upstream 429 falls back once without double charge
PASS: primary timeout falls back
LOCAL_LIMIT_ERROR={"error":{"code":"RATE_LIMIT_EXCEEDED","message":"Tenant token budget exceeded","request_id":"<generated-uuid>"}}
PASS: local token limit uses standardized 429
CONCURRENT_ALLOWED=10/20
CONCURRENT_USAGE=100/100
PASS: two limiter instances remain atomic
SLIDING_WINDOW_BEFORE=True AFTER_60_SECONDS=True
PASS: 60-second expiry uses injected clock
Summary: 7/7 checks passed
```

Actual final suite output:

```text
Task 4: collected 26 items
============================== 26 passed in 1.78s ==============================
Task 1 regression: 39 passed in 7.21s
Task 2 regression: 36 passed in 1.28s
Task 3 regression: 38 passed in 7.11s
Full repository: 144 passed in 17.75s
All checks passed!
56 files already formatted
No broken requirements found.
```

Concurrency tests ran 20 simultaneous 10-token reservations against a 100-token limit. Both one
limiter instance and two independent instances sharing one file allowed exactly 10 reservations,
stored 10 rows, and reported exactly 100 used tokens. The `BEGIN IMMEDIATE` transaction prevents
the read/check/insert race.

The injected-clock test reserved the full limit, rejected another token at 59.999 seconds, then
advanced to exactly 60 seconds. The old row was deleted and a new full-limit reservation succeeded;
the row count remained one. Reinitializing a limiter against the same file retained active usage.

`tiktoken` 0.14.0 with `cl100k_base` is installed and tested independently for prompt and chat
accounting. Fallback tests inspect the ledger after primary 429 and find exactly one reservation
row containing exactly `input_tokens + max_tokens`.

## Task 4 independent audit

The independent audit was run on 2026-10-30 without relying on the implementation summary. The
baseline suite was executed first and reported 26/26 passing. Additional adversarial tests then
verified the SQLite file signature, byte-scanned the database/WAL/shared-memory files for the raw
API key, distinguished a staggered rolling window from fixed minute buckets, asserted retry counts
when both providers fail, checked the exact production defaults, and scanned every client-facing
error class.

The new induced-failure test found one genuine defect: an unexpected exception during token
counting/reservation escaped the standardized JSON envelope and became FastAPI's plain-text 500.
`app.py:completions` now catches unexpected local reservation-stage exceptions, logs them only on
the server, and returns the sanitized `GATEWAY_INTERNAL_ERROR` envelope. A deliberately
secret-bearing `RuntimeError` now produces no raw exception, URL, traceback, tenant key, or hash
secret in the response.

Audit commands:

```bash
.venv/bin/python -m pytest tests/task4 -v
.venv/bin/python scripts/verify_task4.py
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pip check
.venv/bin/python -m pytest -q
```

Observed concurrency evidence used a temporary on-disk database, a 100-token limit, twenty
simultaneous 10-token reservations, and two separate limiter instances. Exactly 10 reservations
succeeded; database usage was exactly 100 tokens and the ledger contained exactly 10 rows.

Observed rolling-window evidence used reservations at timestamps 1000 and 1030. At 1060 only the
first reservation expired, leaving the second active; at 1090 the second expired independently.
The exact boundary rule is `reserved_at > now - 60`, and eviction deletes rows at or before the
cutoff.

Final actual output:

```text
Task 4: collected 28 items
============================== 28 passed in 1.63s ==============================
Standalone verifier: Summary: 7/7 checks passed
Ruff lint: All checks passed!
Ruff format: 56 files already formatted
Dependency check: No broken requirements found.
Full repository: 146 passed in 17.61s
```
