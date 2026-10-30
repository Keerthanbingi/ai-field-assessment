# Development Rules

These instructions apply to the entire repository.

## Scope and structure

- Keep Tasks 1–4 isolated under their matching `src/ai_field/task*_*/` and `tests/task*/`
  directories.
- Build incrementally. Do not implement a later task unless the user requests it.
- Prefer straightforward, typed Python that can be explained in an interview.
- Do not add Docker, Kubernetes, Redis, paid services, or unrelated infrastructure.

## Implementation requirements

- Target Python 3.11 or newer and use Pydantic v2.
- Use the official Python MCP SDK for MCP protocol integration.
- Keep Task 1 stdout exclusively for MCP JSON-RPC. Send diagnostics only to stderr.
- Use strict input validation and sanitized errors; never expose secrets or raw upstream traces.
- Use offline mock MCP and LLM providers for automated verification.
- Use an on-disk SQLite database for Task 4.
- Never commit real credentials. Document configuration in `.env.example`.

## Verification requirements

- Each task needs runnable code, automated tests, and a manual verification procedure.
- Protocol requirements need genuine integration coverage; helper-only tests are insufficient.
- Run Ruff and the relevant pytest suite after changes.
- Never skip, weaken, delete, or conceal a failing test to obtain a passing result.
- Never invent test results or claim an unverified requirement is complete.
- Record exact commands plus expected and actual outputs in `docs/verification.md`.
- Keep `docs/acceptance_matrix.md` synchronized with implemented and verified requirements.

