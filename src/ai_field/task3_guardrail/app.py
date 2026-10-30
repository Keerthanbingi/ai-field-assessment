"""Task 3 streaming LLM gateway with incremental PII redaction."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ai_field.task3_guardrail.redaction import IncrementalPIIRedactor
from ai_field.task3_guardrail.sse import IncrementalSSEParser, ProviderStreamError, SSEEvent

logger = logging.getLogger(__name__)
UPSTREAM_CHUNK_BYTES = 4096
SUPPORTED_EVENT_KEYS = {
    "id",
    "object",
    "created",
    "model",
    "system_fingerprint",
    "sequence",
    "choices",
    "usage",
}
SUPPORTED_CHOICE_KEYS = {"index", "delta", "finish_reason", "logprobs"}
SUPPORTED_DELTA_KEYS = {"content", "role"}


class GuardrailSettings(BaseModel):
    model_config = ConfigDict(strict=True)

    provider_url: str
    connect_timeout_seconds: float = Field(gt=0)
    read_timeout_seconds: float = Field(gt=0)
    max_candidate_chars: int = Field(ge=64, le=4096)
    max_sse_event_chars: int = Field(ge=1024, le=1_048_576)

    @classmethod
    def from_environment(cls) -> GuardrailSettings:
        return cls(
            provider_url=os.getenv(
                "TASK3_PROVIDER_URL",
                "http://127.0.0.1:9011/v1/provider/stream",
            ),
            connect_timeout_seconds=float(os.getenv("TASK3_CONNECT_TIMEOUT_SECONDS", "2.0")),
            read_timeout_seconds=float(os.getenv("TASK3_READ_TIMEOUT_SECONDS", "10.0")),
            max_candidate_chars=int(os.getenv("TASK3_MAX_CANDIDATE_CHARS", "320")),
            max_sse_event_chars=int(os.getenv("TASK3_MAX_SSE_EVENT_CHARS", "65536")),
        )


def _error_event() -> bytes:
    return SSEEvent(
        event="error",
        data=json.dumps(
            {
                "error": {
                    "code": "provider_stream_error",
                    "message": "Provider stream could not be processed safely",
                }
            },
            separators=(",", ":"),
        ),
    ).encode()


def _event_with_content(event: SSEEvent, payload: dict[str, Any], content: str) -> bytes:
    sanitized = copy.deepcopy(payload)
    sanitized["choices"][0]["delta"]["content"] = content
    return SSEEvent(
        data=json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")),
        event=event.event,
        event_id=event.event_id,
        retry=event.retry,
    ).encode()


def _parse_text_delta(event: SSEEvent) -> tuple[dict[str, Any], str | None]:
    try:
        payload = json.loads(event.data)
    except json.JSONDecodeError as error:
        raise ProviderStreamError("provider data was not valid JSON") from error
    if not isinstance(payload, dict):
        raise ProviderStreamError("provider data must be a JSON object")
    if set(payload) - SUPPORTED_EVENT_KEYS:
        raise ProviderStreamError("provider event contained unsupported fields")
    choices = payload.get("choices")
    if choices is None:
        return payload, None
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ProviderStreamError("provider choices shape is unsupported")
    if set(choices[0]) - SUPPORTED_CHOICE_KEYS:
        raise ProviderStreamError("provider choice contained unsupported fields")
    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        raise ProviderStreamError("provider delta shape is unsupported")
    if set(delta) - SUPPORTED_DELTA_KEYS:
        raise ProviderStreamError("provider delta contained unsupported fields")
    content = delta.get("content")
    if content is not None and not isinstance(content, str):
        raise ProviderStreamError("provider delta content must be text")
    return payload, content


async def _guarded_stream(
    request: Request,
    payload: dict[str, Any],
) -> AsyncIterator[bytes]:
    settings: GuardrailSettings = request.app.state.settings
    client: httpx.AsyncClient = request.app.state.provider_client
    parser = IncrementalSSEParser(max_event_chars=settings.max_sse_event_chars)
    redactor = IncrementalPIIRedactor(max_candidate_chars=settings.max_candidate_chars)
    last_text_event: tuple[SSEEvent, dict[str, Any]] | None = None
    deferred_metadata: list[bytes] = []
    saw_done = False

    try:
        async with client.stream("POST", settings.provider_url, json=payload) as response:
            response.raise_for_status()
            async for network_chunk in response.aiter_bytes():
                if await request.is_disconnected():
                    raise asyncio.CancelledError
                for offset in range(0, len(network_chunk), UPSTREAM_CHUNK_BYTES):
                    parser_chunk = network_chunk[offset : offset + UPSTREAM_CHUNK_BYTES]
                    for event in parser.feed(parser_chunk):
                        if event.data == "[DONE]":
                            final_text = redactor.finish()
                            if final_text:
                                if last_text_event is None:
                                    raise ProviderStreamError("text stream had no delta template")
                                yield _event_with_content(*last_text_event, final_text)
                            for deferred in deferred_metadata:
                                yield deferred
                            yield event.encode()
                            saw_done = True
                            return
                        parsed, content = _parse_text_delta(event)
                        if content is None:
                            if redactor.buffered_chars or redactor.suppressed_kind is not None:
                                if len(deferred_metadata) >= 32:
                                    raise ProviderStreamError("too many deferred metadata events")
                                deferred_metadata.append(event.encode())
                            else:
                                yield event.encode()
                            continue
                        last_text_event = (event, parsed)
                        sanitized = redactor.feed(content)
                        # Emit metadata even while content is retained for boundary safety.
                        yield _event_with_content(event, parsed, sanitized)
            parser.finish()
            if not saw_done:
                raise ProviderStreamError("provider stream ended without a done event")
    except asyncio.CancelledError:
        logger.info("Task 3 client disconnected; provider stream cancelled")
        raise
    except (httpx.HTTPError, ProviderStreamError, TypeError, ValueError):
        logger.warning("Task 3 provider stream failed safely")
        yield _error_event()


def create_app(settings: GuardrailSettings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved = settings or GuardrailSettings.from_environment()
        app.state.settings = resolved
        timeout = httpx.Timeout(
            connect=resolved.connect_timeout_seconds,
            read=resolved.read_timeout_seconds,
            write=resolved.connect_timeout_seconds,
            pool=resolved.connect_timeout_seconds,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            app.state.provider_client = client
            yield

    gateway = FastAPI(title="Task 3 Streaming PII Guardrail", lifespan=lifespan)

    @gateway.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @gateway.post("/v1/stream")
    async def stream(request: Request) -> StreamingResponse:
        try:
            payload = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {"scenario": "malformed_request"}
        if not isinstance(payload, dict):
            payload = {"scenario": "malformed_request"}
        return StreamingResponse(
            _guarded_stream(request, payload),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return gateway


app = create_app()
