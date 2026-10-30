"""Task 4 completion gateway application."""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_field.task4_router.auth import AuthenticationFailed, TenantAuthenticator
from ai_field.task4_router.models import CompletionRequest, GatewayError, GatewayErrorEnvelope
from ai_field.task4_router.rate_limit import Clock, SQLiteTokenRateLimiter
from ai_field.task4_router.router import ModelFallbackRouter, ProvidersUnavailable
from ai_field.task4_router.tokenizer import TokenCounter

logger = logging.getLogger(__name__)


class Task4Settings(BaseModel):
    model_config = ConfigDict(strict=True)

    database_path: str
    tenant_keys: dict[str, str]
    tenant_hash_secret: str = Field(min_length=32, repr=False)
    primary_url: str
    secondary_url: str
    token_limit: int = Field(default=50_000, gt=0)
    window_seconds: float = Field(default=60.0, gt=0)
    primary_timeout_seconds: float = Field(default=3.0, gt=0)
    provider_timeout_seconds: float = Field(default=10.0, gt=0)
    sqlite_busy_timeout_ms: int = Field(default=5_000, gt=0)

    @classmethod
    def from_environment(cls) -> Task4Settings:
        raw_keys = os.getenv("TASK4_TENANT_KEYS_JSON")
        if raw_keys is None:
            raise RuntimeError("TASK4_TENANT_KEYS_JSON is required")
        try:
            tenant_keys = json.loads(raw_keys)
        except json.JSONDecodeError as error:
            raise RuntimeError("TASK4_TENANT_KEYS_JSON must be valid JSON") from error
        if not isinstance(tenant_keys, dict) or not all(
            isinstance(key, str) and isinstance(value, str) and value
            for key, value in tenant_keys.items()
        ):
            raise RuntimeError("TASK4_TENANT_KEYS_JSON must map tenant names to API keys")
        hash_secret = os.getenv("TASK4_TENANT_HASH_SECRET")
        if hash_secret is None:
            raise RuntimeError("TASK4_TENANT_HASH_SECRET is required")
        return cls(
            database_path=os.getenv("TASK4_RATE_LIMIT_DB_PATH", "./var/rate_limits.sqlite3"),
            tenant_keys=tenant_keys,
            tenant_hash_secret=hash_secret,
            primary_url=os.getenv(
                "TASK4_PRIMARY_URL",
                "http://127.0.0.1:9021/v1/primary/completions",
            ),
            secondary_url=os.getenv(
                "TASK4_SECONDARY_URL",
                "http://127.0.0.1:9021/v1/secondary/completions",
            ),
            token_limit=int(os.getenv("TASK4_TOKEN_LIMIT", "50000")),
            window_seconds=float(os.getenv("TASK4_WINDOW_SECONDS", "60")),
            primary_timeout_seconds=float(os.getenv("TASK4_PRIMARY_TIMEOUT_SECONDS", "3.0")),
            provider_timeout_seconds=float(os.getenv("TASK4_PROVIDER_TIMEOUT_SECONDS", "10.0")),
            sqlite_busy_timeout_ms=int(os.getenv("TASK4_SQLITE_BUSY_TIMEOUT_MS", "5000")),
        )


def gateway_error(status_code: int, code: str, message: str, request_id: str) -> JSONResponse:
    envelope = GatewayErrorEnvelope(
        error=GatewayError(code=code, message=message, request_id=request_id)
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(),
        headers={"X-Request-ID": request_id},
    )


def create_app(
    settings: Task4Settings | None = None,
    *,
    clock: Clock | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved = settings or Task4Settings.from_environment()
        authenticator = TenantAuthenticator(
            resolved.tenant_keys,
            resolved.tenant_hash_secret,
        )
        limiter_options: dict[str, Any] = {}
        if clock is not None:
            limiter_options["clock"] = clock
        limiter = SQLiteTokenRateLimiter(
            resolved.database_path,
            limit_tokens=resolved.token_limit,
            window_seconds=resolved.window_seconds,
            busy_timeout_ms=resolved.sqlite_busy_timeout_ms,
            **limiter_options,
        )
        timeout = httpx.Timeout(resolved.provider_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            app.state.settings = resolved
            app.state.authenticator = authenticator
            app.state.limiter = limiter
            app.state.token_counter = TokenCounter()
            app.state.router = ModelFallbackRouter(
                client,
                primary_url=resolved.primary_url,
                secondary_url=resolved.secondary_url,
                primary_timeout_seconds=resolved.primary_timeout_seconds,
            )
            yield

    gateway = FastAPI(title="Task 4 Resilient Model Router", lifespan=lifespan)

    @gateway.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @gateway.post("/v1/completions")
    async def completions(
        request: Request,
        x_api_key: str | None = Header(default=None),
    ) -> JSONResponse:
        request_id = str(uuid.uuid4())
        try:
            tenant_hash = request.app.state.authenticator.authenticate(x_api_key)
        except AuthenticationFailed:
            return gateway_error(
                401,
                "AUTHENTICATION_FAILED",
                "Tenant authentication failed",
                request_id,
            )

        try:
            raw_payload = await request.json()
            completion_request = CompletionRequest.model_validate(raw_payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError):
            return gateway_error(400, "INVALID_REQUEST", "Invalid completion request", request_id)

        try:
            input_tokens = request.app.state.token_counter.count_input_tokens(completion_request)
            reservation_tokens = input_tokens + completion_request.max_tokens
            decision = await request.app.state.limiter.reserve(
                tenant_hash,
                reservation_tokens,
                request_id,
            )
        except Exception:
            logger.exception("Task 4 local reservation failure")
            return gateway_error(
                500, "GATEWAY_INTERNAL_ERROR", "Gateway internal error", request_id
            )

        if not decision.allowed:
            return gateway_error(
                429,
                "RATE_LIMIT_EXCEEDED",
                "Tenant token budget exceeded",
                request_id,
            )

        try:
            routed = await request.app.state.router.complete(completion_request.provider_payload())
        except ProvidersUnavailable:
            return gateway_error(
                502,
                "PROVIDERS_UNAVAILABLE",
                "Primary and backup providers unavailable",
                request_id,
            )
        except Exception:
            logger.exception("Task 4 unexpected routing failure")
            return gateway_error(
                500, "GATEWAY_INTERNAL_ERROR", "Gateway internal error", request_id
            )

        return JSONResponse(
            content=routed.payload,
            headers={"X-Request-ID": request_id, "X-Model-Provider": routed.provider},
        )

    return gateway


app = create_app()
