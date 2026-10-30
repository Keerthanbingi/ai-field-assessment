#!/usr/bin/env python3
"""Reproducible live verification for Task 4."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from ai_field.task4_router.rate_limit import SQLiteTokenRateLimiter

ROOT = Path(__file__).parents[1]
API_KEY = "task4-verifier-development-key"
HASH_SECRET = "task4-verifier-hash-secret-at-least-32-characters"


def free_port() -> int:
    with socket.socket() as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


def start_server(app_path: str, port: int, environment: dict[str, str]) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            app_path,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{app_path} exited during startup")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.25).status_code == 200:
                return process
        except httpx.RequestError:
            pass
        time.sleep(0.05)
    raise TimeoutError(f"{app_path} did not become ready")


def stop_server(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def check(label: str, condition: bool) -> bool:
    print(f"{'PASS' if condition else 'FAIL'}: {label}")
    return condition


def configure(client: httpx.Client, provider_url: str, primary: str, secondary: str) -> None:
    response = client.post(
        f"{provider_url}/__test__/configure",
        json={"primary": primary, "secondary": secondary, "timeout_ms": 1000},
    )
    response.raise_for_status()


def clear_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM token_reservations")


@dataclass
class Clock:
    value: float = 1000.0

    def __call__(self) -> float:
        return self.value


async def concurrency_and_expiry(directory: Path) -> tuple[int, int, bool, bool]:
    concurrency_db = directory / "concurrency.sqlite3"
    first = SQLiteTokenRateLimiter(concurrency_db, limit_tokens=100)
    second = SQLiteTokenRateLimiter(concurrency_db, limit_tokens=100)
    decisions = await asyncio.gather(
        *(
            (first if index % 2 == 0 else second).reserve(
                "shared-tenant",
                10,
                f"concurrent-{index}",
            )
            for index in range(20)
        )
    )
    allowed = sum(decision.allowed for decision in decisions)
    usage = await first.usage("shared-tenant")

    clock = Clock()
    expiry = SQLiteTokenRateLimiter(
        directory / "expiry.sqlite3",
        limit_tokens=100,
        clock=clock,
    )
    before = (await expiry.reserve("tenant", 100, "before")).allowed
    clock.value += 60
    after = (await expiry.reserve("tenant", 100, "after")).allowed
    return allowed, usage, before, after


def main() -> int:
    results: list[bool] = []
    with tempfile.TemporaryDirectory(prefix="task4-verify-") as temporary:
        directory = Path(temporary)
        database_path = directory / "gateway.sqlite3"
        provider_port = free_port()
        gateway_port = free_port()
        base = os.environ.copy()
        provider = start_server("ai_field.mocks.task4_provider:app", provider_port, base)
        provider_url = f"http://127.0.0.1:{provider_port}"
        environment = {
            **base,
            "TASK4_RATE_LIMIT_DB_PATH": str(database_path),
            "TASK4_TENANT_KEYS_JSON": json.dumps({"verification-tenant": API_KEY}),
            "TASK4_TENANT_HASH_SECRET": HASH_SECRET,
            "TASK4_PRIMARY_URL": f"{provider_url}/v1/primary/completions",
            "TASK4_SECONDARY_URL": f"{provider_url}/v1/secondary/completions",
            "TASK4_PRIMARY_TIMEOUT_SECONDS": "0.15",
            "TASK4_PROVIDER_TIMEOUT_SECONDS": "1.5",
        }
        gateway = start_server("ai_field.task4_router.app:app", gateway_port, environment)
        gateway_url = f"http://127.0.0.1:{gateway_port}/v1/completions"
        headers = {"X-API-Key": API_KEY}
        payload = {"model": "mock-model", "prompt": "hello", "max_tokens": 20}
        try:
            with httpx.Client(timeout=5) as client:
                results.append(check("on-disk SQLite file created", database_path.is_file()))

                configure(client, provider_url, "success", "success")
                clear_database(database_path)
                primary = client.post(gateway_url, json=payload, headers=headers)
                primary_state = client.get(f"{provider_url}/__test__/state").json()
                print(f"PRIMARY_COUNTS={primary_state['primary_count']},0")
                results.append(
                    check(
                        "primary success avoids secondary",
                        primary.status_code == 200
                        and primary.headers.get("x-model-provider") == "primary"
                        and primary_state["secondary_count"] == 0,
                    )
                )

                configure(client, provider_url, "rate_limit", "success")
                clear_database(database_path)
                fallback = client.post(gateway_url, json=payload, headers=headers)
                fallback_state = client.get(f"{provider_url}/__test__/state").json()
                with sqlite3.connect(database_path) as connection:
                    reservation_rows = connection.execute(
                        "SELECT COUNT(*) FROM token_reservations"
                    ).fetchone()[0]
                print(
                    "FALLBACK_COUNTS="
                    f"{fallback_state['primary_count']},{fallback_state['secondary_count']}"
                )
                print(f"FALLBACK_RESERVATION_ROWS={reservation_rows}")
                results.append(
                    check(
                        "upstream 429 falls back once without double charge",
                        fallback.status_code == 200
                        and fallback.headers.get("x-model-provider") == "secondary"
                        and fallback_state["primary_count"] == 1
                        and fallback_state["secondary_count"] == 1
                        and reservation_rows == 1,
                    )
                )

                configure(client, provider_url, "timeout", "success")
                clear_database(database_path)
                timeout_response = client.post(gateway_url, json=payload, headers=headers)
                results.append(
                    check(
                        "primary timeout falls back",
                        timeout_response.status_code == 200
                        and timeout_response.headers.get("x-model-provider") == "secondary",
                    )
                )

                clear_database(database_path)
                local_limit = client.post(
                    gateway_url,
                    json={**payload, "max_tokens": 50_000},
                    headers=headers,
                )
                print("LOCAL_LIMIT_ERROR=" + json.dumps(local_limit.json(), separators=(",", ":")))
                results.append(
                    check(
                        "local token limit uses standardized 429",
                        local_limit.status_code == 429
                        and local_limit.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED",
                    )
                )
        finally:
            stop_server(gateway)
            stop_server(provider)

        allowed, usage, before, after = asyncio.run(concurrency_and_expiry(directory))
        print(f"CONCURRENT_ALLOWED={allowed}/20")
        print(f"CONCURRENT_USAGE={usage}/100")
        results.append(check("two limiter instances remain atomic", allowed == 10 and usage == 100))
        print(f"SLIDING_WINDOW_BEFORE={before} AFTER_60_SECONDS={after}")
        results.append(check("60-second expiry uses injected clock", before and after))

    print(f"Summary: {sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
