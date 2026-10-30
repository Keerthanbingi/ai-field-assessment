# Architecture

The repository is a single installable Python package with four deliberately separate task
modules. Shared offline provider fixtures belong in `src/ai_field/mocks/`; task-specific logic
must remain in its task module.

Task 1 uses the official MCP SDK low-level server and stdio transport. A narrow stream adapter
maps transport parsing failures to the assessment's required JSON-RPC errors; Pydantic v2 models
provide strict tool validation. See `task1.md`.

Task 2 uses two FastAPI processes: an authenticated gateway and an inspectable offline mock MCP
HTTP server. The gateway verifies signed JWT claims, performs tool-level authorization before
forwarding, and uses a shared-lifespan HTTPX async client. See `task2.md`. Tasks 3–4 remain
unimplemented.
