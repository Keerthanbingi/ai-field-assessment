# Architecture

The repository is a single installable Python package with four deliberately separate task
modules. Shared offline provider fixtures belong in `src/ai_field/mocks/`; task-specific logic
must remain in its task module.

Task 1 uses the official MCP SDK low-level server and stdio transport. A narrow stream adapter
maps transport parsing failures to the assessment's required JSON-RPC errors; Pydantic v2 models
provide strict tool validation. See `task1.md`.

Task 2 uses two FastAPI processes: an authenticated gateway and an inspectable offline mock MCP
HTTP server. The gateway verifies signed JWT claims, performs tool-level authorization before
forwarding, and uses a shared-lifespan HTTPX async client. See `task2.md`.

Task 3 uses a FastAPI streaming gateway and a separate mock LLM provider. A strict incremental
UTF-8/SSE parser extracts JSON text deltas before a bounded, fail-closed PII redactor processes
them. The gateway preserves SSE framing and supported metadata and cancels the upstream HTTPX
stream when the client disconnects. See `task3.md`. Task 4 remains unimplemented.
