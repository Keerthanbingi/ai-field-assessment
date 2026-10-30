# Scripts

- `verify_task1.py` runs the Task 1 stdio protocol checks.
- `generate_task2_tokens.py` generates signed viewer/admin development JWTs.
- `verify_task2.py` launches both Task 2 HTTP services and verifies the main security paths.
- `verify_task3.py` launches the streaming gateway and mock LLM provider, then verifies
  cross-chunk redaction, byte-split SSE handling, fail-closed errors, and observed streaming
  latency.
- `verify_task4.py` launches the completion gateway and mock model providers and proves on-disk
  persistence, bounded fallback, single charging, atomic concurrency, and clock-driven expiry.
