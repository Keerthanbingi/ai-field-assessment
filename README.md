# AI Field Assessment

A Python 3.11+ assessment project for four independent AI infrastructure exercises:

1. An MCP server with strict validation and stdio transport.
2. An authenticated MCP security gateway with tool-level authorization.
3. A streaming LLM gateway guardrail for PII redaction.
4. A token-aware, SQLite-backed model router with provider fallback.

## Current status

Task 1 is implemented and verified. Tasks 2–4 are intentionally not implemented yet.

## Quick start

```bash
cd /Users/saikeerthanbingi/ai-field-assessment
source .venv/bin/activate
python --version
python -m pytest
python -m ruff check .
python scripts/verify_task1.py
```

The project uses offline mocks for future integration tests; no paid API is required.
See `docs/architecture.md`, `docs/verification.md`, and `docs/acceptance_matrix.md`.

Task 1 details and protocol examples are in `docs/task1.md`.
