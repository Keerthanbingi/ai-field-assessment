"""Adversarial live-stream audit matrix for Task 3."""

from __future__ import annotations

import itertools
import json
import random

import httpx
import pytest

from ai_field.task3_guardrail.redaction import REDACTION, IncrementalPIIRedactor
from ai_field.task3_guardrail.sse import IncrementalSSEParser, ProviderStreamError

from .conftest import Task3Stack
from .test_streaming_integration import ParsedEvent, parse_sse, streamed_text

PATTERNS = {
    "email": "a.b@example.com",
    "ssn": "123-45-6789",
    "card": "4111 1111 1111 1111",
}


async def live_stream(
    client: httpx.AsyncClient,
    stack: Task3Stack,
    deltas: list[str],
    *,
    wire_chunk_size: int = 3,
) -> tuple[bytes, list[ParsedEvent]]:
    async with client.stream(
        "POST",
        f"{stack.gateway_url}/v1/stream",
        json={
            "scenario": "custom",
            "deltas": deltas,
            "wire_chunk_size": wire_chunk_size,
        },
    ) as response:
        assert response.status_code == 200
        raw = b"".join([chunk async for chunk in response.aiter_bytes()])
    return raw, parse_sse(raw)


def assert_only_redacted(
    raw: bytes,
    events: list[ParsedEvent],
    sensitive: str,
    expected: str,
    label: str,
) -> None:
    assert sensitive.encode() not in raw, label
    assert sensitive not in raw.decode("utf-8"), label
    assert streamed_text(events) == expected, label
    assert events[-1].data == "[DONE]", label


@pytest.mark.asyncio
async def test_every_single_split_position_for_all_pii(task3_stack: Task3Stack) -> None:
    cases = 0
    async with httpx.AsyncClient(timeout=10) as client:
        for name, sensitive in PATTERNS.items():
            for split in range(1, len(sensitive)):
                deltas = ["before ", sensitive[:split], sensitive[split:], " after"]
                raw, events = await live_stream(
                    client,
                    task3_stack,
                    deltas,
                    wire_chunk_size=(split % 7) + 1,
                )
                assert_only_redacted(
                    raw,
                    events,
                    sensitive,
                    f"before {REDACTION} after",
                    f"{name} single split {split}",
                )
                cases += 1
    assert cases == 42


@pytest.mark.asyncio
async def test_every_pair_of_split_positions_for_all_pii(task3_stack: Task3Stack) -> None:
    cases = 0
    async with httpx.AsyncClient(timeout=10) as client:
        for name, sensitive in PATTERNS.items():
            for first, second in itertools.combinations(range(1, len(sensitive)), 2):
                deltas = [
                    "before ",
                    sensitive[:first],
                    sensitive[first:second],
                    sensitive[second:],
                    " after",
                ]
                raw, events = await live_stream(
                    client,
                    task3_stack,
                    deltas,
                    wire_chunk_size=((first + second) % 11) + 1,
                )
                assert_only_redacted(
                    raw,
                    events,
                    sensitive,
                    f"before {REDACTION} after",
                    f"{name} pair {first},{second}",
                )
                cases += 1
    assert cases == 289


@pytest.mark.asyncio
async def test_seeded_random_multi_chunk_boundaries_for_all_pii(
    task3_stack: Task3Stack,
) -> None:
    seeds = {"email": 104729, "ssn": 130363, "card": 155921}
    cases = 0
    async with httpx.AsyncClient(timeout=10) as client:
        for name, sensitive in PATTERNS.items():
            generator = random.Random(seeds[name])
            for _ in range(25):
                count = generator.randint(2, min(8, len(sensitive) - 1))
                boundaries = sorted(generator.sample(range(1, len(sensitive)), count))
                chunks = [
                    sensitive[start:end]
                    for start, end in itertools.pairwise([0, *boundaries, len(sensitive)])
                ]
                raw, events = await live_stream(
                    client,
                    task3_stack,
                    ["before ", *chunks, " after"],
                    wire_chunk_size=generator.randint(1, 13),
                )
                assert_only_redacted(
                    raw,
                    events,
                    sensitive,
                    f"before {REDACTION} after",
                    f"{name} random boundaries {boundaries}",
                )
                cases += 1
    assert cases == 75


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(PATTERNS))
async def test_pii_positions_consecutive_values_and_punctuation(
    task3_stack: Task3Stack,
    name: str,
) -> None:
    sensitive = PATTERNS[name]
    contexts: list[tuple[list[str], str]] = [
        ([sensitive, " follows"], f"{REDACTION} follows"),
        (["ends with ", sensitive], f"ends with {REDACTION}"),
        (["before ", sensitive, " after"], f"before {REDACTION} after"),
        ([sensitive, ",", sensitive], f"{REDACTION},{REDACTION}"),
        (["(", sensitive, ")."], f"({REDACTION})."),
        ([sensitive, "."], f"{REDACTION}."),
    ]
    async with httpx.AsyncClient(timeout=10) as client:
        for index, (deltas, expected) in enumerate(contexts):
            raw, events = await live_stream(
                client,
                task3_stack,
                deltas,
                wire_chunk_size=(index % 3) + 1,
            )
            assert_only_redacted(raw, events, sensitive, expected, f"{name} context {index}")


@pytest.mark.asyncio
@pytest.mark.parametrize("sensitive", list(PATTERNS.values()))
async def test_no_sensitive_fragment_is_emitted_before_candidate_completion(
    task3_stack: Task3Stack,
    sensitive: str,
) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        raw, events = await live_stream(
            client,
            task3_stack,
            list(sensitive),
            wire_chunk_size=1,
        )
    contents: list[str] = []
    for event in events:
        if event.data == "[DONE]":
            continue
        payload = json.loads(event.data)
        contents.append(payload["choices"][0]["delta"].get("content") or "")
    assert set(contents) <= {"", REDACTION}
    assert streamed_text(events) == REDACTION
    assert sensitive.encode() not in raw


@pytest.mark.asyncio
async def test_partial_utf8_and_one_byte_sse_frames_around_pii(task3_stack: Task3Stack) -> None:
    sensitive = PATTERNS["email"]
    async with httpx.AsyncClient(timeout=10) as client:
        raw, events = await live_stream(
            client,
            task3_stack,
            ["🍵 before ", sensitive, " après café"],
            wire_chunk_size=1,
        )
    assert raw.decode("utf-8", errors="strict")
    assert_only_redacted(
        raw,
        events,
        sensitive,
        f"🍵 before {REDACTION} après café",
        "partial UTF-8",
    )


@pytest.mark.asyncio
async def test_oversized_sse_event_fails_closed_without_raw_text(task3_stack: Task3Stack) -> None:
    sensitive = "secret@example.com"
    huge = sensitive + "A" * 70_000
    async with httpx.AsyncClient(timeout=10) as client:
        raw, events = await live_stream(
            client,
            task3_stack,
            [huge],
            wire_chunk_size=8192,
        )
    assert sensitive.encode() not in raw
    assert events[-1].event == "error"
    assert "provider_stream_error" in events[-1].data


def test_code_level_candidate_and_sse_buffer_bounds() -> None:
    redactor = IncrementalPIIRedactor(max_candidate_chars=64, max_numeric_candidate_chars=32)
    output = redactor.feed("A" * 10_000 + "@example.com") + redactor.finish()
    assert output == REDACTION
    assert redactor.peak_buffered_chars <= 65

    parser = IncrementalSSEParser(max_event_chars=1024)
    with pytest.raises(ProviderStreamError):
        for _ in range(130):
            parser.feed(b"12345678")
    # The gateway requests 4096-byte upstream chunks; the parser rejects an
    # unterminated event no later than the configured event limit plus one chunk.
    assert parser.peak_buffered_chars <= 1032
