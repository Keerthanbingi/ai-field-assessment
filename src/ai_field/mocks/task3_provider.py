"""Configurable offline SSE provider used for Task 3 integration verification."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError


class ProviderRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    scenario: StrictStr = "normal"
    deltas: list[StrictStr] | None = None
    delay_ms: StrictInt = Field(default=0, ge=0, le=5000)
    wire_chunk_size: StrictInt | None = Field(default=None, ge=1, le=65536)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.started = 0
    app.state.completed = 0
    app.state.cancelled = 0
    app.state.active = 0
    app.state.lock = asyncio.Lock()
    yield


app = FastAPI(title="Task 3 Mock Streaming LLM", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/__test__/state")
async def state(request: Request) -> dict[str, int]:
    return {
        "started": request.app.state.started,
        "completed": request.app.state.completed,
        "cancelled": request.app.state.cancelled,
        "active": request.app.state.active,
    }


@app.post("/__test__/reset")
async def reset(request: Request) -> dict[str, bool]:
    async with request.app.state.lock:
        request.app.state.started = 0
        request.app.state.completed = 0
        request.app.state.cancelled = 0
        request.app.state.active = 0
    return {"reset": True}


def _scenario_deltas(settings: ProviderRequest) -> list[str]:
    scenarios = {
        "normal": ["Hello ", "from ", "the model."],
        "email": ["Contact alice@example.com for help."],
        "split_email": ["Contact john@", "gmail.", "com now."],
        "ssn": ["SSN 123-45-6789 received."],
        "split_ssn": ["SSN 123-", "45-", "6789 received."],
        "card": ["Card 4111-1111-1111-1111 accepted."],
        "split_card": ["Card 4111 ", "1111 ", "1111 ", "1111 accepted."],
        "multiple": ["Email a@example.com, SSN 123-45-6789, card 4111 1111 1111 1111 done."],
        "utf8": ["Café 🍵 says hello."],
        "delayed": ["Early safe text. ", "Late text."],
        "long": ["A" * 400 + "@example.com done."],
    }
    if settings.scenario == "custom" and settings.deltas is not None:
        return settings.deltas
    return scenarios.get(settings.scenario, ["Unknown scenario."])


def _event_bytes(content: str, index: int) -> bytes:
    payload: dict[str, Any] = {
        "id": "mock-stream",
        "model": "mock-llm",
        "sequence": index,
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {index}\nevent: delta\ndata: {body}\n\n".encode()


async def _chunks(data: bytes, size: int | None) -> AsyncIterator[bytes]:
    if size is None:
        yield data
        return
    for index in range(0, len(data), size):
        yield data[index : index + size]
        await asyncio.sleep(0)


@app.post("/v1/provider/stream")
async def provider_stream(request: Request) -> StreamingResponse:
    try:
        settings = ProviderRequest.model_validate(await request.json())
    except (ValidationError, json.JSONDecodeError):
        settings = ProviderRequest(scenario="malformed_request")

    async def generate() -> AsyncIterator[bytes]:
        async with request.app.state.lock:
            request.app.state.started += 1
            request.app.state.active += 1
        try:
            if settings.scenario == "malformed":
                unsafe = _event_bytes("secret@example.com", 0)
                yield unsafe[:-5]
                return
            if settings.scenario == "malformed_json":
                yield b'data: {"choices":[{"delta":{"content":"secret@example.com"}\n\n'
                return
            if settings.scenario == "invalid_utf8":
                yield b"data: \xff\n\n"
                return
            for index, content in enumerate(_scenario_deltas(settings)):
                if settings.delay_ms and index > 0:
                    await asyncio.sleep(settings.delay_ms / 1000)
                async for chunk in _chunks(_event_bytes(content, index), settings.wire_chunk_size):
                    yield chunk
            if settings.scenario == "metadata":
                metadata = {
                    "id": "mock-stream",
                    "model": "mock-llm",
                    "usage": {"output_tokens": 3},
                }
                yield f"event: usage\ndata: {json.dumps(metadata)}\n\n".encode()
            async for chunk in _chunks(b"data: [DONE]\n\n", settings.wire_chunk_size):
                yield chunk
            async with request.app.state.lock:
                request.app.state.completed += 1
        except asyncio.CancelledError:
            async with request.app.state.lock:
                request.app.state.cancelled += 1
            raise
        finally:
            async with request.app.state.lock:
                request.app.state.active -= 1

    return StreamingResponse(generate(), media_type="text/event-stream")
