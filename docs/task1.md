# Task 1: Strict MCP Server over stdio

## Architecture

`ai_field.task1_mcp.server` builds the official Python SDK low-level `Server`. It registers
`tools/list` with the SDK and registers a raw SDK `CallToolRequest` handler for `tools/call`.
Pydantic v2 models validate arguments in strict mode with undeclared fields forbidden. Customer
records and refund confirmations are local data only; `trigger_refund` never contacts a payment
service or performs a financial action.

The executable uses the official SDK `stdio_server()` transport. A small stream adapter sits
between that transport and `Server.run()`. The adapter converts parsing exceptions exposed by
the SDK transport into JSON-RPC errors and rejects unsupported request methods. It does not
implement a competing JSON-RPC server: framing, message models, serialization, initialization,
MCP capabilities, tool requests, and session lifecycle remain owned by the official SDK.

## Run

```bash
cd /Users/saikeerthanbingi/ai-field-assessment
source .venv/bin/activate
python -m ai_field.task1_mcp.server
```

The process expects one JSON-RPC message per stdin line. stdout contains JSON-RPC only. Python
and SDK logs use stderr.

## Error behavior

| Condition | Wire behavior |
|---|---|
| Invalid JSON text | JSON-RPC `-32700`, ID `null` |
| Invalid JSON-RPC envelope | JSON-RPC `-32600`, ID `null` |
| Unsupported request method | JSON-RPC `-32601`, original ID |
| Invalid tool name or arguments | JSON-RPC `-32602`, original ID |
| Unexpected implementation failure | Sanitized JSON-RPC `-32603`, original ID; details only in stderr |
| Valid customer ID absent from mock data | Successful `tools/call` response with `isError: true` |

The final row is a safe, recoverable domain failure rather than malformed protocol input.

### Why validation is a protocol error here

Normal MCP tool input validation is generally reported as a tool result with `isError: true`,
which lets a model inspect and repair its call. The assessment explicitly requires schema
violations to be top-level JSON-RPC `-32602` responses. In SDK 1.30, the convenience
`Server.call_tool()` decorator catches validation failures and creates `isError: true`. This
project therefore uses the supported low-level `Server` request-handler registry and raises the
SDK's `McpError(ErrorData(code=INVALID_PARAMS, ...))`. This preserves the official SDK's session
and response machinery while meeting the assessment-specific contract.

References:

- [MCP 2025-11-25 stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [MCP 2025-11-25 tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)
- [Official Python SDK low-level server guide](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/advanced/low-level-server.md)

## Automated and manual verification

```bash
python -m pytest tests/task1 -v
python -m ruff check .
python scripts/verify_task1.py
```

The pytest suite launches the server as a subprocess, performs initialization, sends genuine
newline-delimited MCP messages, checks tool schemas and calls, exercises error responses, and
parses every stdout line as JSON. It separately asserts that startup/request logs appear on
stderr and never on stdout. `test_official_client.py` independently launches the same server
through the official SDK `stdio_client` and uses `ClientSession` to initialize, list, and call
both tools; it also confirms that invalid arguments surface to an SDK client as `McpError`
`-32602` rather than a tool result.

## Limitations

- Data is reset whenever the process exits; refunds are confirmations only and are not persisted.
- Authentication and authorization are outside Task 1 and belong to Task 2.
- The test-only `AI_FIELD_TASK1_TEST_FORCE_INTERNAL_ERROR=1` environment variable injects an
  internal failure so sanitization can be verified. It is not needed during normal use.
- SDK 1.30's `JSONRPCError` model does not accept the JSON-RPC-required `id: null`. The transport
  adapter uses a narrowly scoped SDK message subclass to serialize null-ID parse/invalid-request
  errors. Ordinary MCP messages continue through the unmodified official serializer.
