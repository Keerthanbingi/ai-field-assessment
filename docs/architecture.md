# Architecture

The repository is a single installable Python package with four deliberately separate task
modules. Shared offline provider fixtures belong in `src/ai_field/mocks/`; task-specific logic
must remain in its task module.

Task 1 uses the official MCP SDK low-level server and stdio transport. A narrow stream adapter
maps transport parsing failures to the assessment's required JSON-RPC errors; Pydantic v2 models
provide strict tool validation. See `task1.md`. Tasks 2–4 remain unimplemented.
