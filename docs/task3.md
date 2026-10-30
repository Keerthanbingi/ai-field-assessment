# Task 3: Streaming LLM Gateway PII Guardrail

## Architecture

The implementation runs as two independent FastAPI services:

```text
client -> POST /v1/stream -> Task 3 gateway -> POST /v1/provider/stream -> mock LLM
                              |                                      |
                              +-- strict SSE/UTF-8 parser <-----------+
                              +-- bounded PII redactor
                              +-- sanitized SSE -> client
```

- `src/ai_field/task3_guardrail/app.py` owns the gateway, shared-lifespan HTTPX client, and
  streaming response lifecycle.
- `sse.py` incrementally decodes UTF-8 and parses complete SSE frames. Raw provider bytes are
  never regex-scanned or copied to the client.
- `redaction.py` inspects parsed `choices[0].delta.content` text and incrementally releases only
  prefixes that cannot still become supported PII.
- `src/ai_field/mocks/task3_provider.py` provides deterministic normal, sensitive, delayed,
  byte-split, malformed, and cancellation scenarios without an external API.

The supported provider format is a single-choice, OpenAI-style JSON delta carried in SSE. A strict
allowlist covers documented event, choice, and delta fields; unknown text-bearing shapes fail
closed instead of being treated as metadata. JSON fields such as ID, model, sequence, choice
index, and finish reason are preserved. SSE `event`, `id`, and numeric `retry` fields and allowed
JSON metadata events without `choices` are also preserved. A terminal `data: [DONE]` event is
required.

## Incremental safety algorithm

The gateway maintains only the unresolved suffix that could still become sensitive:

- An email-like token is held until a character outside the supported email token alphabet is
  observed or the stream terminates.
- A numeric suffix containing digits, spaces, or hyphens is held for SSN/card detection.
- Finalized text is scanned for ASCII email addresses, formatted US SSNs, and 13–19 digit card
  candidates. Cards must pass Luhn validation; repeated single-digit values are rejected as
  implausible.
- UTF-8 is decoded with a strict incremental decoder, so a code point split across HTTP chunks is
  reconstructed before SSE or text inspection.
- Incomplete or invalid UTF-8, malformed JSON, invalid delta shapes, oversized events, premature
  EOF, and missing `[DONE]` all produce a sanitized `provider_stream_error`. Buffered text is
  discarded rather than leaked.

The default maximum unresolved email-like candidate is 320 characters. Numeric candidate state
is capped at 96 characters. Input deltas are consumed character by character, so a large provider
delta does not enlarge this state; the measured peak is at most the applicable limit plus the
single character that triggers the decision. If a candidate exceeds the cap, the gateway emits
one `[REDACTED]` marker and suppresses that token until a safe boundary. This is deliberately
fail-closed and can over-redact an unusually long non-sensitive token.

Email local parts are theoretically unbounded. The gateway does not attempt unbounded buffering:
the 320-character cap and suppression behavior above define its handling. SSE events are capped at
65,536 decoded characters. Naturally arriving HTTPX chunks are sliced into 4,096-byte pieces before
parsing, giving parser application state a bound of the event cap plus one parser slice. HTTPX owns
one transient network chunk, which is not accumulated by the gateway. Up to 32 non-text metadata
events may be deferred while a sensitive candidate is unresolved, ensuring terminal usage
metadata follows the sanitized text it describes.

## Run the services

From the repository root, use two terminals.

Terminal 1 — mock provider:

```bash
source .venv/bin/activate
python -m uvicorn ai_field.mocks.task3_provider:app \
  --host 127.0.0.1 --port 9011
```

Terminal 2 — gateway:

```bash
source .venv/bin/activate
export TASK3_PROVIDER_URL=http://127.0.0.1:9011/v1/provider/stream
python -m uvicorn ai_field.task3_guardrail.app:app \
  --host 127.0.0.1 --port 9010
```

No credential or paid API is needed.

## Manual verification

Observe the unsafe provider stream directly:

```bash
curl -N -H 'Content-Type: application/json' \
  -d '{"scenario":"split_email"}' \
  http://127.0.0.1:9011/v1/provider/stream
```

The provider sends the text deltas `Contact john@`, `gmail.`, and `com now.`.

Observe the sanitized gateway stream:

```bash
curl -N -H 'Content-Type: application/json' \
  -d '{"scenario":"split_email"}' \
  http://127.0.0.1:9010/v1/stream
```

Concatenating the returned `delta.content` values produces:

```text
Contact [REDACTED] now.
```

Run the reproducible verifier and tests:

```bash
.venv/bin/python scripts/verify_task3.py
.venv/bin/python -m pytest tests/task3 -v
```

## Latency behavior

The gateway does not wait for provider completion. Safe prefixes ending at an unambiguous boundary
are emitted immediately. In the recorded delayed-provider check, the first sanitized text arrived
in 1.4 ms while total completion took 353.9 ms because the provider intentionally waited 350 ms
before its second delta.

Potentially sensitive suffixes necessarily add boundary-dependent latency. For example, the
gateway retains `john@gmail.com` until a following boundary or clean stream termination proves the
address is complete. Correctness takes priority over releasing those characters early.

## Limitations

- Email matching is ASCII-oriented and does not attempt internationalized mailbox normalization.
- SSNs are detected only in the required `XXX-XX-XXXX` format.
- Cards must contain 13–19 digits separated by optional single spaces or hyphens and pass Luhn.
- Only the documented single-choice JSON delta schema is accepted; unsupported/malformed provider
  shapes fail closed.
- SSE comments and unknown SSE fields are ignored. Supported SSE and JSON metadata are preserved.
- A response that has already begun cannot change its HTTP status after a provider stream failure;
  the gateway therefore returns a sanitized SSE `event: error` and terminates.
- This assessment guardrail uses pattern detection, not semantic entity recognition. Production
  systems would commonly combine it with provider-specific schemas and a broader DLP policy.

## Independent audit findings

The adversarial audit found and fixed five genuine issues:

1. A space-separated card followed immediately by sentence punctuation could be split between the
   numeric and email suffix states, leaking the card. Numeric candidates are now finalized before
   a non-numeric boundary can start an overlapping suffix.
2. A normal trailing sentence period prevented email matching. The domain-continuation check now
   distinguishes a trailing period from a period followed by another domain label.
3. Unknown JSON objects without `choices` were forwarded as metadata, allowing an unexpected
   `text` field to bypass inspection. Provider event, choice, and delta fields are now allowlisted.
4. An unterminated SSE line was not checked against the event limit until a newline arrived. The
   parser now enforces the cap while the partial line is still arriving.
5. Asking HTTPX for fixed 4,096-byte chunks introduced response coalescing and defeated early
   streaming. The gateway now consumes naturally arriving chunks immediately and slices each one
   only when feeding the bounded parser.

The final live audit covered every single and pair of split positions for representative email,
SSN, and card values, plus fixed-seed random partitions, positional contexts, punctuation,
consecutive values, byte-divided SSE, UTF-8 splits, overflow, and malformed events.
