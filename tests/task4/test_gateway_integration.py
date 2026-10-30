"""Real HTTP integration tests for Task 4 routing and safe errors."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

from ai_field.task4_router.app import Task4Settings, create_app
from ai_field.task4_router.models import CompletionRequest
from ai_field.task4_router.tokenizer import TokenCounter

from .conftest import HASH_SECRET, TENANT_A_KEY, Task4Stack


def headers(api_key: str = TENANT_A_KEY) -> dict[str, str]:
    return {"X-API-Key": api_key}


def completion_payload(max_tokens: Any = 20) -> dict[str, Any]:
    return {"model": "mock-model", "prompt": "hello world", "max_tokens": max_tokens}


def assert_safe_gateway_error(
    response: httpx.Response,
    *,
    status_code: int,
    code: str,
) -> None:
    assert response.status_code == status_code
    payload = response.json()
    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "message", "request_id"}
    assert payload["error"]["code"] == code
    assert payload["error"]["request_id"]
    assert response.headers["x-request-id"] == payload["error"]["request_id"]
    for forbidden in (
        "Traceback",
        "RuntimeError",
        "http://",
        "https://",
        "127.0.0.1",
        ".invalid",
        "mock upstream",
        TENANT_A_KEY,
        HASH_SECRET,
    ):
        assert forbidden not in response.text


def assert_sqlite_files_exclude_key(database_path: Path, raw_key: str) -> None:
    assert database_path.read_bytes().startswith(b"SQLite format 3\x00")
    for suffix in ("", "-wal", "-shm"):
        sqlite_file = Path(f"{database_path}{suffix}")
        if sqlite_file.exists():
            assert raw_key.encode() not in sqlite_file.read_bytes()


async def reset_provider(
    client: httpx.AsyncClient,
    stack: Task4Stack,
    *,
    primary: str = "success",
    secondary: str = "success",
    timeout_ms: int = 1000,
) -> None:
    response = await client.post(
        f"{stack.provider_url}/__test__/configure",
        json={"primary": primary, "secondary": secondary, "timeout_ms": timeout_ms},
    )
    response.raise_for_status()
    with sqlite3.connect(stack.database_path) as connection:
        connection.execute("DELETE FROM token_reservations")


async def provider_state(client: httpx.AsyncClient, stack: Task4Stack) -> dict[str, Any]:
    response = await client.get(f"{stack.provider_url}/__test__/state")
    response.raise_for_status()
    return response.json()


@pytest.mark.asyncio
async def test_primary_success_avoids_secondary(task4_stack: Task4Stack) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        await reset_provider(client, task4_stack)
        response = await client.post(
            f"{task4_stack.gateway_url}/v1/completions",
            json=completion_payload(),
            headers=headers(),
        )
        state = await provider_state(client, task4_stack)
    assert response.status_code == 200
    assert response.headers["x-model-provider"] == "primary"
    assert response.json()["provider"] == "primary"
    assert (state["primary_count"], state["secondary_count"]) == (1, 0)


@pytest.mark.asyncio
async def test_primary_429_falls_back_once_and_is_not_local_rate_limit(
    task4_stack: Task4Stack,
) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        await reset_provider(client, task4_stack, primary="rate_limit")
        response = await client.post(
            f"{task4_stack.gateway_url}/v1/completions",
            json=completion_payload(),
            headers=headers(),
        )
        state = await provider_state(client, task4_stack)
    assert response.status_code == 200
    assert response.headers["x-model-provider"] == "secondary"
    assert response.json()["provider"] == "secondary"
    assert (state["primary_count"], state["secondary_count"]) == (1, 1)


@pytest.mark.asyncio
async def test_primary_timeout_falls_back_and_primary_is_cancelled(
    task4_stack: Task4Stack,
) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        await reset_provider(
            client,
            task4_stack,
            primary="timeout",
            timeout_ms=1000,
        )
        response = await client.post(
            f"{task4_stack.gateway_url}/v1/completions",
            json=completion_payload(),
            headers=headers(),
        )
        state: dict[str, Any] = {}
        for _ in range(100):
            state = await provider_state(client, task4_stack)
            if state["primary_active"] == 0:
                break
            await asyncio.sleep(0.01)
    assert response.status_code == 200
    assert response.headers["x-model-provider"] == "secondary"
    assert (state["primary_count"], state["secondary_count"]) == (1, 1)
    assert state["primary_active"] == 0
    assert state["primary_disconnected"] == 1


@pytest.mark.asyncio
async def test_primary_500_uses_documented_single_fallback(task4_stack: Task4Stack) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        await reset_provider(client, task4_stack, primary="server_error")
        response = await client.post(
            f"{task4_stack.gateway_url}/v1/completions",
            json=completion_payload(),
            headers=headers(),
        )
        state = await provider_state(client, task4_stack)
    assert response.status_code == 200
    assert response.json()["provider"] == "secondary"
    assert (state["primary_count"], state["secondary_count"]) == (1, 1)


@pytest.mark.asyncio
async def test_both_provider_failures_return_sanitized_error(task4_stack: Task4Stack) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        await reset_provider(
            client,
            task4_stack,
            primary="server_error",
            secondary="server_error",
        )
        response = await client.post(
            f"{task4_stack.gateway_url}/v1/completions",
            json=completion_payload(),
            headers=headers(),
        )
        state = await provider_state(client, task4_stack)
    assert (state["primary_count"], state["secondary_count"]) == (1, 1)
    assert_safe_gateway_error(
        response,
        status_code=502,
        code="PROVIDERS_UNAVAILABLE",
    )


@pytest.mark.asyncio
async def test_unreachable_providers_return_sanitized_error(unreachable_gateway: str) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.post(
            f"{unreachable_gateway}/v1/completions",
            json=completion_payload(),
            headers=headers(),
        )
    assert_safe_gateway_error(
        response,
        status_code=502,
        code="PROVIDERS_UNAVAILABLE",
    )


@pytest.mark.asyncio
async def test_fallback_creates_one_reservation_and_never_double_charges(
    task4_stack: Task4Stack,
) -> None:
    request_model = CompletionRequest.model_validate(completion_payload())
    expected_tokens = TokenCounter().count_input_tokens(request_model) + request_model.max_tokens
    async with httpx.AsyncClient(timeout=5) as client:
        await reset_provider(client, task4_stack, primary="rate_limit")
        response = await client.post(
            f"{task4_stack.gateway_url}/v1/completions",
            json=completion_payload(),
            headers=headers(),
        )
    assert response.status_code == 200
    with sqlite3.connect(task4_stack.database_path) as connection:
        row = connection.execute(
            "SELECT COUNT(*), SUM(reserved_tokens) FROM token_reservations"
        ).fetchone()
    assert row == (1, expected_tokens)


@pytest.mark.asyncio
async def test_authenticated_raw_api_key_is_never_stored_in_sqlite(
    task4_stack: Task4Stack,
) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        await reset_provider(client, task4_stack)
        response = await client.post(
            f"{task4_stack.gateway_url}/v1/completions",
            json=completion_payload(),
            headers=headers(),
        )
    assert response.status_code == 200
    with sqlite3.connect(task4_stack.database_path) as connection:
        stored = connection.execute(
            "SELECT reservation_id, tenant_hash, reserved_tokens, reserved_at "
            "FROM token_reservations"
        ).fetchall()
    assert len(stored) == 1
    assert TENANT_A_KEY not in repr(stored)
    assert len(stored[0][1]) == 64
    await asyncio.to_thread(
        assert_sqlite_files_exclude_key,
        task4_stack.database_path,
        TENANT_A_KEY,
    )


@pytest.mark.asyncio
async def test_chat_messages_are_accepted_and_malformed_json_is_rejected(
    task4_stack: Task4Stack,
) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        await reset_provider(client, task4_stack)
        chat = await client.post(
            f"{task4_stack.gateway_url}/v1/completions",
            json={
                "model": "mock-model",
                "messages": [
                    {"role": "system", "content": "Be concise."},
                    {"role": "user", "content": "Hello"},
                ],
                "max_tokens": 20,
            },
            headers=headers(),
        )
        malformed = await client.post(
            f"{task4_stack.gateway_url}/v1/completions",
            content=b"{not-json",
            headers={**headers(), "Content-Type": "application/json"},
        )
    assert chat.status_code == 200
    assert_safe_gateway_error(malformed, status_code=400, code="INVALID_REQUEST")


@pytest.mark.asyncio
async def test_authentication_invalid_requests_and_local_limit_use_distinct_safe_errors(
    task4_stack: Task4Stack,
) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        await reset_provider(client, task4_stack)
        missing_auth = await client.post(
            f"{task4_stack.gateway_url}/v1/completions",
            json=completion_payload(),
        )
        wrong_auth = await client.post(
            f"{task4_stack.gateway_url}/v1/completions",
            json=completion_payload(),
            headers=headers("wrong-key"),
        )
        invalid = await client.post(
            f"{task4_stack.gateway_url}/v1/completions",
            json=completion_payload(max_tokens=True),
            headers=headers(),
        )
        local_limit = await client.post(
            f"{task4_stack.gateway_url}/v1/completions",
            json=completion_payload(max_tokens=50_000),
            headers=headers(),
        )
        state = await provider_state(client, task4_stack)
    assert_safe_gateway_error(
        missing_auth,
        status_code=401,
        code="AUTHENTICATION_FAILED",
    )
    assert_safe_gateway_error(
        wrong_auth,
        status_code=401,
        code="AUTHENTICATION_FAILED",
    )
    assert_safe_gateway_error(invalid, status_code=400, code="INVALID_REQUEST")
    assert_safe_gateway_error(
        local_limit,
        status_code=429,
        code="RATE_LIMIT_EXCEEDED",
    )
    assert state["primary_count"] == state["secondary_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        completion_payload(max_tokens=0),
        completion_payload(max_tokens=-1),
        completion_payload(max_tokens="100"),
        {"model": "mock", "prompt": "x"},
        {"model": "mock", "prompt": "x", "messages": [], "max_tokens": 1},
        {"model": "mock", "messages": [], "max_tokens": 1},
        {"model": "mock", "prompt": "x", "max_tokens": 1, "extra": True},
    ],
)
async def test_invalid_token_budgets_and_malformed_payloads_rejected(
    task4_stack: Task4Stack,
    payload: dict[str, Any],
) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.post(
            f"{task4_stack.gateway_url}/v1/completions",
            json=payload,
            headers=headers(),
        )
    assert_safe_gateway_error(response, status_code=400, code="INVALID_REQUEST")


def test_default_primary_timeout_is_exactly_3000ms(tmp_path) -> None:
    settings = Task4Settings(
        database_path=str(tmp_path / "limits.sqlite3"),
        tenant_keys={"tenant": "development-key"},
        tenant_hash_secret=HASH_SECRET,
        primary_url="http://primary.invalid/completions",
        secondary_url="http://secondary.invalid/completions",
    )
    assert settings.primary_timeout_seconds == 3.0
    assert settings.token_limit == 50_000
    assert settings.window_seconds == 60.0


@pytest.mark.asyncio
async def test_unexpected_internal_failure_uses_standardized_sanitized_error(tmp_path) -> None:
    internal_secret = "super-secret-internal-exception"

    def broken_clock() -> float:
        raise RuntimeError(f"{internal_secret} at http://internal.service.local")

    settings = Task4Settings(
        database_path=str(tmp_path / "limits.sqlite3"),
        tenant_keys={"tenant": TENANT_A_KEY},
        tenant_hash_secret=HASH_SECRET,
        primary_url="http://primary.internal.invalid/completions",
        secondary_url="http://secondary.internal.invalid/completions",
    )
    application = create_app(settings, clock=broken_clock)
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway.test") as client:
            response = await client.post(
                "/v1/completions",
                json=completion_payload(),
                headers={**headers(), "X-Request-ID": internal_secret},
            )

    assert_safe_gateway_error(
        response,
        status_code=500,
        code="GATEWAY_INTERNAL_ERROR",
    )
    assert response.json()["error"]["message"] == "Gateway internal error"
    for forbidden in (
        internal_secret,
        "Traceback",
        "RuntimeError",
        "http://",
        "internal.service.local",
        TENANT_A_KEY,
        HASH_SECRET,
    ):
        assert forbidden not in response.text
