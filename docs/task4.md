# Task 4: Token-Aware Rate Limiting and Resilient Model Fallback

## Architecture

```text
authenticated client
        |
        v
POST /v1/completions
        |
        +-- strict Pydantic request validation
        +-- tiktoken cl100k_base input count
        +-- atomic SQLite rolling-window reservation
        |
        v
primary provider -- one bounded fallback --> secondary provider
```

The gateway, limiter, tokenizer, authentication, and routing code are isolated in
`src/ai_field/task4_router/`. `src/ai_field/mocks/task4_provider.py` implements local primary and
secondary providers with success, 429, timeout, and 500 behavior plus inspectable counters.

## Authentication and tenant identity

Clients provide `X-API-Key`. Development keys are configured through
`TASK4_TENANT_KEYS_JSON`, for example `{"tenant-a":"local-development-key"}`. Keys are compared
with `hmac.compare_digest`. After verification, the limiter receives an HMAC-SHA256 fingerprint of
the tenant ID using `TASK4_TENANT_HASH_SECRET`.

Raw API keys are never passed to SQLite. The database contains only the HMAC-derived tenant hash,
reservation UUID, token count, and timestamp. Keys and the HMAC secret remain environment/runtime
configuration and must be supplied by a real secret manager in production.

## Token accounting

Input text is encoded with the maintained `tiktoken` library using `cl100k_base`.

- Prompt requests count the encoded prompt.
- Chat requests conservatively reserve three request-envelope tokens plus four structural tokens
  per message, encoded role tokens, and encoded content tokens.
- Before provider execution, the gateway reserves `input_tokens + max_tokens`.
- Strict validation rejects missing, boolean, string, zero, negative, and excessive output budgets.

The chat overhead is a documented gateway policy, not a claim that every provider uses identical
token framing. A production integration should select accounting matched to its exact provider and
model revision.

## SQLite schema and location

The configured default location is `./var/rate_limits.sqlite3`, overridden with
`TASK4_RATE_LIMIT_DB_PATH`. In-memory SQLite is explicitly rejected.

```sql
CREATE TABLE token_reservations (
    reservation_id TEXT PRIMARY KEY,
    tenant_hash TEXT NOT NULL,
    reserved_tokens INTEGER NOT NULL CHECK (reserved_tokens > 0),
    reserved_at REAL NOT NULL
);

CREATE INDEX idx_token_reservations_tenant_time
ON token_reservations (tenant_hash, reserved_at);
```

SQLite uses WAL mode, a configurable busy timeout (default 5000 ms), and a separate connection per
operation. Reservations execute as follows:

1. `BEGIN IMMEDIATE` obtains the SQLite write reservation.
2. Rows with `reserved_at <= now - 60` are deleted globally.
3. The tenant's remaining rows are summed.
4. The request is allowed only when `used + requested <= limit`.
5. An allowed request inserts exactly one row and commits; a rejected request commits eviction but
   inserts nothing.

This transaction serializes competing writers across threads, processes, and limiter instances.
The default limit is exactly 50,000 tokens in a rolling 60-second window. A clock callable is
injectable for deterministic expiry tests.

Unused output reservations are intentionally not reconciled. The full worst-case reservation
remains for 60 seconds even if the provider emits fewer tokens or fails. This is conservative,
simple, and prevents fallback or error paths from accidentally restoring spendable capacity. A
production reconciliation design would require trusted provider usage and idempotent adjustments.

## Fallback policy

The production default primary timeout is exactly 3.0 seconds. It is configurable through
`TASK4_PRIMARY_TIMEOUT_SECONDS` for deterministic tests.

- Primary success returns immediately; secondary is not called.
- Primary 429, timeout, connection error, invalid JSON, or any non-success status makes exactly one
  secondary attempt.
- A primary timeout uses `asyncio.timeout`, cancelling the pending HTTPX request.
- Secondary success is returned.
- Secondary failure or connection error returns a sanitized `PROVIDERS_UNAVAILABLE` response.
- The secondary is never retried, and provider fallback never calls the limiter again.

Consequently, an upstream provider 429 triggers fallback and is distinct from the gateway's local
tenant-budget HTTP 429.

## Standard errors

All gateway errors use:

```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Tenant token budget exceeded",
    "request_id": "generated-uuid"
  }
}
```

Supported safe codes are `RATE_LIMIT_EXCEEDED`, `INVALID_REQUEST`, `AUTHENTICATION_FAILED`,
`PROVIDERS_UNAVAILABLE`, and `GATEWAY_INTERNAL_ERROR`. Responses never contain provider response
bodies, exception representations, stack traces, API keys, or internal URLs.

## Run locally

Terminal 1 — mock providers:

```bash
source .venv/bin/activate
python -m uvicorn ai_field.mocks.task4_provider:app \
  --host 127.0.0.1 --port 9021
```

Terminal 2 — gateway:

```bash
source .venv/bin/activate
export TASK4_RATE_LIMIT_DB_PATH=./var/rate_limits.sqlite3
export TASK4_TENANT_KEYS_JSON='{"tenant-a":"replace-with-local-development-key"}'
export TASK4_TENANT_HASH_SECRET='replace-with-random-value-at-least-32-characters'
export TASK4_PRIMARY_URL=http://127.0.0.1:9021/v1/primary/completions
export TASK4_SECONDARY_URL=http://127.0.0.1:9021/v1/secondary/completions
python -m uvicorn ai_field.task4_router.app:app \
  --host 127.0.0.1 --port 9020
```

Manual request:

```bash
curl -sS -X POST http://127.0.0.1:9020/v1/completions \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: replace-with-local-development-key' \
  -d '{"model":"mock-model","prompt":"hello","max_tokens":20}'
```

Tests and verifier:

```bash
.venv/bin/python -m pytest tests/task4 -v
.venv/bin/python scripts/verify_task4.py
```

## Limitations

- SQLite is appropriate for this single-host assessment. High-volume distributed deployments
  would need a datastore offering equivalent atomic rolling-window semantics.
- Wall-clock time is used in production. A strongly controlled deployment may prefer a trusted
  time source and explicit handling for large clock corrections.
- The gateway validates that successful provider responses are JSON objects but does not impose a
  provider-specific completion response schema.
- API-key rotation requires updating configuration and restarting/reloading the gateway.
- The secondary uses the general provider timeout and is attempted only once.
- Reservations are conservative and not reconciled, as described above.

