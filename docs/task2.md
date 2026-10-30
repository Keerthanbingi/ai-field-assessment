# Task 2: MCP Security Gateway Proxy

## Architecture

The Task 2 system consists of two independently runnable FastAPI applications:

```text
AI agent client
    |  Authorization: Bearer <signed JWT>
    |  single JSON-RPC 2.0 request
    v
Task 2 security gateway (/mcp)
    |  forwards only authenticated and authorized requests
    v
Mock downstream MCP HTTP server (/mcp)
```

`ai_field.task2_gateway.app` validates authentication, the JSON-RPC envelope, and the
security-relevant `tools/call` parameters before using an `httpx.AsyncClient` to forward the
original request body. `ai_field.mocks.task2_downstream` exposes the two assessment tools and a
local-only request log used to prove blocked requests never arrived.

## Authentication

Tokens are signed with HS256 using `TASK2_JWT_SECRET`. The gateway pins the accepted algorithm
and verifies the signature, expiry, issuer, audience, subject, issued-at time, and role before it
trusts `role`. Only `viewer` and `admin` roles are accepted. There is no default signing key, and
the configured development key must contain at least 32 characters.

Generate a random local key and copy the printed value into your shell:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
export TASK2_JWT_SECRET='paste-the-generated-value-here'
```

Generate one-hour sample tokens:

```bash
python scripts/generate_task2_tokens.py
```

The helper prints `VIEWER_TOKEN=...` and `ADMIN_TOKEN=...`. These are development credentials;
do not commit them.

## Run both services

Terminal 1 — mock downstream:

```bash
cd /Users/saikeerthanbingi/ai-field-assessment
source .venv/bin/activate
python -m uvicorn ai_field.mocks.task2_downstream:app --host 127.0.0.1 --port 9001
```

Terminal 2 — gateway:

```bash
cd /Users/saikeerthanbingi/ai-field-assessment
source .venv/bin/activate
export TASK2_JWT_SECRET='the-same-development-key'
export TASK2_DOWNSTREAM_URL='http://127.0.0.1:9001/mcp'
export TASK2_DOWNSTREAM_TIMEOUT_SECONDS='2.0'
python -m uvicorn ai_field.task2_gateway.app:app --host 127.0.0.1 --port 8000
```

## Authorization and errors

- Authenticated viewers and admins may use `tools/list` and ordinary tools.
- A tool name beginning with the exact case-sensitive prefix `admin_` requires `admin`.
- A blocked call returns `-32001 Unauthorized Tool Call` with the original request ID and is
  never forwarded.
- Missing or invalid credentials return HTTP 401 with sanitized JSON-RPC error `-32000`.
- Malformed JSON returns `-32700`; invalid envelopes return `-32600`; invalid `tools/call`
  parameters return `-32602`.
- Downstream connection failures/timeouts return sanitized `-32002` without URLs or traces.
- Forwarded downstream JSON-RPC bodies and HTTP statuses are preserved.

## Batch and notification limitation

This assessment implementation supports single JSON-RPC requests with IDs. JSON-RPC batches and
notifications are explicitly rejected as `-32600 Invalid Request` before any forwarding. That
policy prevents protected calls from being hidden inside a batch or a notification. Supporting
full batch/notification semantics would require authorizing every element and suppressing
notification responses, which is intentionally outside this task.

## Mock request inspection

The local mock exposes:

- `GET /__test__/requests` — returns `count` and the invocation log.
- `POST /__test__/reset` — clears both.

These endpoints belong only to the offline mock and must not be copied into a production MCP
server.

## Verification

```bash
python -m pytest tests/task2 -v
python scripts/verify_task2.py
python -m pytest
python -m ruff check .
```

The pytest suite launches the gateway, downstream mock, and a second gateway pointed at an
unreachable downstream as real Uvicorn processes on ephemeral localhost ports. Requests use
HTTPX over TCP; no ASGI in-process transport substitutes for the network path.

### Verified response examples

Allowed viewer call:

```json
{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"Mock customer record returned."}],"structuredContent":{"customer_id":"CUST-1","name":"Gateway Test Customer","status":"active"},"isError":false}}
```

Blocked viewer call, preserving ID `3`:

```json
{"jsonrpc":"2.0","id":3,"error":{"code":-32001,"message":"Unauthorized Tool Call"}}
```

The verifier resets the mock log immediately before the blocked call and then reads it:

```text
DOWNSTREAM_COUNT_AFTER_BLOCK=0
```
