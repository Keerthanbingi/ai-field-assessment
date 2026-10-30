"""On-disk SQLite rate-limiter correctness and concurrency tests."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass

import pytest
import tiktoken

from ai_field.task4_router.models import CompletionRequest
from ai_field.task4_router.rate_limit import SQLiteTokenRateLimiter
from ai_field.task4_router.tokenizer import TokenCounter


@dataclass
class MutableClock:
    value: float = 1_000.0

    def __call__(self) -> float:
        return self.value


@pytest.mark.asyncio
async def test_sqlite_file_created_with_wal_schema_and_no_raw_api_key(tmp_path) -> None:
    path = tmp_path / "limits.sqlite3"
    limiter = SQLiteTokenRateLimiter(path)
    assert path.is_file()
    assert path.read_bytes().startswith(b"SQLite format 3\x00")
    await limiter.reserve("tenant-hash", 10, "reservation-1")
    with sqlite3.connect(path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        columns = {row[1] for row in connection.execute("PRAGMA table_info(token_reservations)")}
        stored_tenants = connection.execute(
            "SELECT DISTINCT tenant_hash FROM token_reservations"
        ).fetchall()
    assert journal_mode.lower() == "wal"
    assert columns == {"reservation_id", "tenant_hash", "reserved_tokens", "reserved_at"}
    assert stored_tenants == [("tenant-hash",)]
    assert "api" not in " ".join(columns).lower()


@pytest.mark.asyncio
async def test_tenant_isolation_under_limit_over_limit_and_exact_boundary(tmp_path) -> None:
    limiter = SQLiteTokenRateLimiter(tmp_path / "limits.sqlite3", limit_tokens=50_000)
    exact = await limiter.reserve("tenant-a", 50_000, "a-exact")
    over = await limiter.reserve("tenant-a", 1, "a-over")
    independent = await limiter.reserve("tenant-b", 49_999, "b-under")
    assert exact.allowed is True
    assert over.allowed is False
    assert over.used_tokens == 50_000
    assert independent.allowed is True
    assert await limiter.usage("tenant-a") == 50_000
    assert await limiter.usage("tenant-b") == 49_999


@pytest.mark.asyncio
async def test_sliding_window_expiry_and_expired_row_eviction_without_waiting(tmp_path) -> None:
    clock = MutableClock()
    limiter = SQLiteTokenRateLimiter(
        tmp_path / "limits.sqlite3",
        limit_tokens=100,
        window_seconds=60,
        clock=clock,
    )
    assert (await limiter.reserve("tenant", 100, "old")).allowed
    clock.value += 59.999
    assert not (await limiter.reserve("tenant", 1, "too-soon")).allowed
    assert await limiter.row_count() == 1
    clock.value += 0.001
    decision = await limiter.reserve("tenant", 100, "new")
    assert decision.allowed
    assert decision.used_tokens == 0
    assert await limiter.row_count() == 1


@pytest.mark.asyncio
async def test_staggered_reservations_use_a_true_rolling_window_not_fixed_buckets(
    tmp_path,
) -> None:
    clock = MutableClock()
    limiter = SQLiteTokenRateLimiter(
        tmp_path / "limits.sqlite3",
        limit_tokens=100,
        window_seconds=60,
        clock=clock,
    )
    assert (await limiter.reserve("tenant", 60, "at-1000")).allowed
    clock.value = 1_030.0
    assert (await limiter.reserve("tenant", 40, "at-1030")).allowed

    # At the exact 60-second boundary only the first reservation expires. A
    # fixed-minute-bucket implementation would incorrectly discard both.
    clock.value = 1_060.0
    decision = await limiter.reserve("tenant", 60, "at-1060")
    assert decision.allowed
    assert decision.used_tokens == 40

    clock.value = 1_089.999
    assert not (await limiter.reserve("tenant", 1, "before-1090")).allowed
    clock.value = 1_090.0
    assert await limiter.usage("tenant") == 60
    assert (await limiter.reserve("tenant", 40, "at-1090")).allowed
    assert await limiter.usage("tenant") == 100
    assert await limiter.row_count() == 2


@pytest.mark.asyncio
async def test_usage_persists_across_rate_limiter_reinitialization(tmp_path) -> None:
    path = tmp_path / "limits.sqlite3"
    first = SQLiteTokenRateLimiter(path, limit_tokens=100)
    assert (await first.reserve("tenant", 60, "first")).allowed
    second = SQLiteTokenRateLimiter(path, limit_tokens=100)
    decision = await second.reserve("tenant", 41, "second")
    assert not decision.allowed
    assert decision.used_tokens == 60


@pytest.mark.asyncio
async def test_concurrent_requests_cannot_overspend_budget(tmp_path) -> None:
    limiter = SQLiteTokenRateLimiter(tmp_path / "limits.sqlite3", limit_tokens=100)
    decisions = await asyncio.gather(
        *(limiter.reserve("tenant", 10, f"request-{index}") for index in range(20))
    )
    assert sum(decision.allowed for decision in decisions) == 10
    assert await limiter.usage("tenant") == 100
    assert await limiter.row_count() == 10


@pytest.mark.asyncio
async def test_two_limiter_instances_cannot_race_past_shared_database_limit(tmp_path) -> None:
    path = tmp_path / "limits.sqlite3"
    first = SQLiteTokenRateLimiter(path, limit_tokens=100)
    second = SQLiteTokenRateLimiter(path, limit_tokens=100)
    decisions = await asyncio.gather(
        *(
            (first if index % 2 == 0 else second).reserve(
                "tenant",
                10,
                f"shared-{index}",
            )
            for index in range(20)
        )
    )
    assert sum(decision.allowed for decision in decisions) == 10
    assert await first.usage("tenant") == 100
    assert await first.row_count() == 10


def test_in_memory_database_is_explicitly_rejected() -> None:
    with pytest.raises(ValueError, match="on-disk"):
        SQLiteTokenRateLimiter(":memory:")


def test_token_counter_uses_documented_cl100k_encoding_and_chat_overhead() -> None:
    encoding = tiktoken.get_encoding("cl100k_base")
    counter = TokenCounter()
    prompt = CompletionRequest(model="mock", prompt="hello tokenizer", max_tokens=10)
    chat = CompletionRequest(
        model="mock",
        messages=[
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Hello"},
        ],
        max_tokens=10,
    )
    assert counter.count_input_tokens(prompt) == len(encoding.encode("hello tokenizer"))
    assert counter.count_input_tokens(prompt) != len("hello tokenizer")
    expected_chat = 3 + sum(
        4 + len(encoding.encode(message.role)) + len(encoding.encode(message.content))
        for message in chat.messages or []
    )
    assert counter.count_input_tokens(chat) == expected_chat
