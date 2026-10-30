"""Real gateway-to-provider HTTP streaming tests for Task 3."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from ai_field.task3_guardrail.redaction import REDACTION, IncrementalPIIRedactor

from .conftest import Task3Stack


@dataclass(frozen=True)
class ParsedEvent:
    data: str
    event: str | None
    event_id: str | None


def parse_sse(raw: bytes) -> list[ParsedEvent]:
    text = raw.decode("utf-8", errors="strict")
    assert text.endswith("\n\n")
    events: list[ParsedEvent] = []
    for frame in text.split("\n\n")[:-1]:
        data: list[str] = []
        event = event_id = None
        for line in frame.splitlines():
            field, separator, value = line.partition(":")
            if separator and value.startswith(" "):
                value = value[1:]
            if field == "data":
                data.append(value)
            elif field == "event":
                event = value
            elif field == "id":
                event_id = value
        assert data
        events.append(ParsedEvent("\n".join(data), event, event_id))
    return events


def streamed_text(events: list[ParsedEvent]) -> str:
    output: list[str] = []
    for event in events:
        if event.data == "[DONE]" or event.event == "error":
            continue
        payload = json.loads(event.data)
        choices = payload.get("choices")
        if choices:
            output.append(choices[0]["delta"].get("content") or "")
    return "".join(output)


async def request_stream(
    stack: Task3Stack, payload: dict[str, Any]
) -> tuple[bytes, list[ParsedEvent]]:
    async with httpx.AsyncClient(timeout=10) as client:
        async with client.stream(
            "POST",
            f"{stack.gateway_url}/v1/stream",
            json=payload,
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            raw = b"".join([chunk async for chunk in response.aiter_bytes()])
    return raw, parse_sse(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected"),
    [
        ("normal", "Hello from the model."),
        ("email", f"Contact {REDACTION} for help."),
        ("split_email", f"Contact {REDACTION} now."),
        ("ssn", f"SSN {REDACTION} received."),
        ("split_ssn", f"SSN {REDACTION} received."),
        ("card", f"Card {REDACTION} accepted."),
        ("split_card", f"Card {REDACTION} accepted."),
        ("multiple", f"Email {REDACTION}, SSN {REDACTION}, card {REDACTION} done."),
    ],
)
async def test_text_and_pii_scenarios(
    task3_stack: Task3Stack,
    scenario: str,
    expected: str,
) -> None:
    raw, events = await request_stream(task3_stack, {"scenario": scenario})
    assert streamed_text(events) == expected
    assert events[-1].data == "[DONE]"
    assert b"alice@example.com" not in raw
    assert b"john@gmail.com" not in raw
    assert b"123-45-6789" not in raw
    assert b"4111 1111 1111 1111" not in raw
    assert b"4111-1111-1111-1111" not in raw


@pytest.mark.asyncio
@pytest.mark.parametrize("split_at", [1, 2, 4, 7, 10, 13, 16])
async def test_email_redacted_at_arbitrary_delta_split_positions(
    task3_stack: Task3Stack,
    split_at: int,
) -> None:
    email = "split@example.com"
    _, events = await request_stream(
        task3_stack,
        {
            "scenario": "custom",
            "deltas": ["Address ", email[:split_at], email[split_at:], " end."],
            "wire_chunk_size": 3,
        },
    )
    assert streamed_text(events) == f"Address {REDACTION} end."


@pytest.mark.asyncio
async def test_utf8_byte_splits_and_sse_framing_are_valid(task3_stack: Task3Stack) -> None:
    raw, events = await request_stream(
        task3_stack,
        {"scenario": "utf8", "wire_chunk_size": 1},
    )
    assert raw.decode("utf-8", errors="strict")
    assert streamed_text(events) == "Café 🍵 says hello."
    assert all(event.event in {"delta", None} for event in events)
    assert {event.event_id for event in events[:-1]} == {"0"}
    assert events[-1].data == "[DONE]"


@pytest.mark.asyncio
async def test_non_text_metadata_is_preserved(task3_stack: Task3Stack) -> None:
    _, events = await request_stream(task3_stack, {"scenario": "metadata"})
    payloads = [json.loads(event.data) for event in events if event.data != "[DONE]"]
    assert payloads[0]["id"] == "mock-stream"
    assert payloads[0]["model"] == "mock-llm"
    assert payloads[0]["sequence"] == 0
    assert payloads[-1] == {
        "id": "mock-stream",
        "model": "mock-llm",
        "usage": {"output_tokens": 3},
    }
    assert events[-2].event == "usage"


@pytest.mark.asyncio
async def test_safe_text_arrives_before_delayed_provider_finishes(task3_stack: Task3Stack) -> None:
    started = time.monotonic()
    first_text_at: float | None = None
    raw = bytearray()
    async with httpx.AsyncClient(timeout=5) as client:
        async with client.stream(
            "POST",
            f"{task3_stack.gateway_url}/v1/stream",
            json={"scenario": "delayed", "delay_ms": 350},
        ) as response:
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                events = parse_sse(bytes(raw)) if raw.endswith(b"\n\n") else []
                if first_text_at is None and streamed_text(events):
                    first_text_at = time.monotonic() - started
    total = time.monotonic() - started
    assert first_text_at is not None
    assert first_text_at < 0.25
    assert total >= 0.30
    assert streamed_text(parse_sse(bytes(raw))) == "Early safe text. Late text."


@pytest.mark.asyncio
async def test_fail_closed_overflow_is_bounded_and_does_not_leak(task3_stack: Task3Stack) -> None:
    candidate = "A" * 5000 + "@example.com"
    raw, events = await request_stream(
        task3_stack,
        {"scenario": "custom", "deltas": [candidate, " done."], "wire_chunk_size": 11},
    )
    assert streamed_text(events) == f"{REDACTION} done."
    assert candidate.encode() not in raw

    redactor = IncrementalPIIRedactor(max_candidate_chars=64, max_numeric_candidate_chars=32)
    output = redactor.feed(candidate) + redactor.finish()
    assert output == REDACTION
    assert redactor.peak_buffered_chars <= 65


@pytest.mark.asyncio
async def test_luhn_invalid_card_like_number_is_preserved(task3_stack: Task3Stack) -> None:
    _, events = await request_stream(
        task3_stack,
        {
            "scenario": "custom",
            "deltas": ["Reference 4111 1111 ", "1111 1112 is not a valid card."],
        },
    )
    assert streamed_text(events) == "Reference 4111 1111 1111 1112 is not a valid card."


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["malformed", "malformed_json", "invalid_utf8"])
async def test_malformed_provider_stream_fails_closed_without_pii_leak(
    task3_stack: Task3Stack,
    scenario: str,
) -> None:
    raw, events = await request_stream(task3_stack, {"scenario": scenario})
    assert b"secret@example.com" not in raw
    assert events[-1].event == "error"
    assert json.loads(events[-1].data) == {
        "error": {
            "code": "provider_stream_error",
            "message": "Provider stream could not be processed safely",
        }
    }
    assert all(event.data != "[DONE]" for event in events)


@pytest.mark.asyncio
async def test_done_event_terminates_stream_exactly_once(task3_stack: Task3Stack) -> None:
    _, events = await request_stream(task3_stack, {"scenario": "normal", "wire_chunk_size": 2})
    assert sum(event.data == "[DONE]" for event in events) == 1
    assert events[-1].data == "[DONE]"
    assert all(event.event != "error" for event in events)


@pytest.mark.asyncio
async def test_client_disconnect_cancels_provider_stream(task3_stack: Task3Stack) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        await client.post(f"{task3_stack.provider_url}/__test__/reset")
        async with client.stream(
            "POST",
            f"{task3_stack.gateway_url}/v1/stream",
            json={
                "scenario": "custom",
                "deltas": ["safe chunk. "] * 50,
                "delay_ms": 100,
            },
        ) as response:
            async for chunk in response.aiter_bytes():
                if b"safe chunk" in chunk:
                    break

        state: dict[str, int] = {}
        for _ in range(50):
            state = (await client.get(f"{task3_stack.provider_url}/__test__/state")).json()
            if state["active"] == 0:
                break
            await asyncio.sleep(0.02)

    assert state["started"] == 1
    assert state["active"] == 0
    assert state["completed"] == 0
    assert state["cancelled"] == 1
