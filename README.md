# AI Field Assessment

A Python 3.11+ assessment project for four independent AI infrastructure exercises:

1. An MCP server with strict validation and stdio transport.
2. An authenticated MCP security gateway with tool-level authorization.
3. A streaming LLM gateway guardrail for PII redaction.
4. A token-aware, SQLite-backed model router with provider fallback.

## Current status

Tasks 1–4 are implemented and verified independently.

## Quick start

```bash
cd /Users/saikeerthanbingi/ai-field-assessment
source .venv/bin/activate
python --version
python -m pytest
python -m ruff check .
python scripts/verify_task1.py
python scripts/verify_task2.py
python scripts/verify_task3.py
python scripts/verify_task4.py
```

The project uses offline mocks for future integration tests; no paid API is required.
See `docs/architecture.md`, `docs/verification.md`, and `docs/acceptance_matrix.md`.

Task 1 details and protocol examples are in `docs/task1.md`.
Task 2 architecture, security policy, and run commands are in `docs/task2.md`.
Task 3 streaming architecture, safety bounds, latency tradeoffs, and run commands are in
`docs/task3.md`.
Task 4 SQLite rate limiting, token accounting, fallback policy, and run commands are in
`docs/task4.md`.
