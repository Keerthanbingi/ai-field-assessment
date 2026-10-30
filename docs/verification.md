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
