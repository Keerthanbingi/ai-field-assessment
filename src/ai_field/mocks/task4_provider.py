"""Configurable local primary and secondary completion providers for Task 4."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

Behavior = Literal["success", "rate_limit", "timeout", "server_error"]


class ProviderConfiguration(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    primary: Behavior = "success"
    secondary: Behavior = "success"
    timeout_ms: int = Field(default=1000, ge=1, le=10_000)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.configuration = ProviderConfiguration()
    app.state.primary_count = 0
    app.state.secondary_count = 0
    app.state.primary_active = 0
    app.state.primary_disconnected = 0
    app.state.lock = asyncio.Lock()
    yield


app = FastAPI(title="Task 4 Mock Model Providers", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/__test__/configure")
async def configure(request: Request, configuration: ProviderConfiguration) -> dict[str, bool]:
    async with request.app.state.lock:
        request.app.state.configuration = configuration
        request.app.state.primary_count = 0
        request.app.state.secondary_count = 0
        request.app.state.primary_active = 0
        request.app.state.primary_disconnected = 0
    return {"configured": True}


@app.get("/__test__/state")
async def state(request: Request) -> dict[str, int | str]:
    configuration: ProviderConfiguration = request.app.state.configuration
    return {
        "primary": configuration.primary,
        "secondary": configuration.secondary,
        "primary_count": request.app.state.primary_count,
        "secondary_count": request.app.state.secondary_count,
        "primary_active": request.app.state.primary_active,
        "primary_disconnected": request.app.state.primary_disconnected,
    }


async def _timeout_until_disconnected(request: Request, timeout_ms: int) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    while asyncio.get_running_loop().time() < deadline:
        if await request.is_disconnected():
            async with request.app.state.lock:
                request.app.state.primary_disconnected += 1
            return
        await asyncio.sleep(0.01)


async def _respond(request: Request, provider: Literal["primary", "secondary"]) -> JSONResponse:
    configuration: ProviderConfiguration = request.app.state.configuration
    behavior: Behavior = getattr(configuration, provider)
    async with request.app.state.lock:
        counter_name = f"{provider}_count"
        setattr(request.app.state, counter_name, getattr(request.app.state, counter_name) + 1)
        if provider == "primary":
            request.app.state.primary_active += 1
    try:
        payload = await request.json()
        if behavior == "timeout":
            await _timeout_until_disconnected(request, configuration.timeout_ms)
        if behavior == "rate_limit":
            return JSONResponse(status_code=429, content={"error": "mock upstream limit"})
        if behavior == "server_error":
            return JSONResponse(status_code=500, content={"error": "mock upstream failure"})
        return JSONResponse(
            content={
                "id": f"mock-{provider}-completion",
                "model": payload.get("model"),
                "provider": provider,
                "choices": [{"text": f"response from {provider}"}],
                "usage": {"output_tokens": 4},
            }
        )
    finally:
        if provider == "primary":
            async with request.app.state.lock:
                request.app.state.primary_active -= 1


@app.post("/v1/primary/completions")
async def primary(request: Request) -> JSONResponse:
    return await _respond(request, "primary")


@app.post("/v1/secondary/completions")
async def secondary(request: Request) -> JSONResponse:
    return await _respond(request, "secondary")
