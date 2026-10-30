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
collected 40 items
tests/task1/test_stdio_integration.py ................................... [ 87%]
tests/test_setup.py .....                                                [100%]
============================== 40 passed in 6.78s ==============================
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

The Task 1 file contains 35 subprocess protocol cases; the remaining five are repository setup
smoke tests. There were zero failed or skipped tests.

Captured successful tool response (refund IDs vary; this record response is deterministic):

```json
{"jsonrpc":"2.0","id":10,"result":{"content":[{"type":"text","text":"{\"customer_id\": \"CUST-12345\", \"email\": \"jordan.rivera@example.test\", \"name\": \"Jordan Rivera\", \"status\": \"active\"}"}],"structuredContent":{"customer_id":"CUST-12345","name":"Jordan Rivera","email":"jordan.rivera@example.test","status":"active"},"isError":false}}
```

Captured invalid-amount response:

```json
{"jsonrpc":"2.0","id":11,"error":{"code":-32602,"message":"Invalid tool arguments","data":{"issues":[{"field":"amount","message":"Input should be greater than 0","type":"greater_than"}]}}}
```
