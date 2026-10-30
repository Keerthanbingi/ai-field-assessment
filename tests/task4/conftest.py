"""Real-process Task 4 gateway and provider fixtures."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).parents[2]
TENANT_A_KEY = "task4-tenant-a-development-key"
TENANT_B_KEY = "task4-tenant-b-development-key"
HASH_SECRET = "task4-test-hash-secret-that-is-at-least-32-characters"


def _free_port() -> int:
    with socket.socket() as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


def _wait_until_ready(url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            assert process.stderr is not None
            raise RuntimeError(f"Server exited during startup: {process.stderr.read()}")
        try:
            if httpx.get(url, timeout=0.25).status_code == 200:
                return
        except httpx.RequestError:
            pass
        time.sleep(0.05)
    raise TimeoutError(f"Server did not become ready: {url}")


def _start(app_path: str, port: int, environment: dict[str, str]) -> subprocess.Popen[str]:
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
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    _wait_until_ready(f"http://127.0.0.1:{port}/health", process)
    return process


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@dataclass(frozen=True)
class Task4Stack:
    gateway_url: str
    provider_url: str
    database_path: Path


def _gateway_environment(
    base: dict[str, str],
    *,
    database_path: Path,
    primary_url: str,
    secondary_url: str,
) -> dict[str, str]:
    return {
        **base,
        "TASK4_RATE_LIMIT_DB_PATH": str(database_path),
        "TASK4_TENANT_KEYS_JSON": json.dumps({"tenant-a": TENANT_A_KEY, "tenant-b": TENANT_B_KEY}),
        "TASK4_TENANT_HASH_SECRET": HASH_SECRET,
        "TASK4_PRIMARY_URL": primary_url,
        "TASK4_SECONDARY_URL": secondary_url,
        "TASK4_PRIMARY_TIMEOUT_SECONDS": "0.15",
        "TASK4_PROVIDER_TIMEOUT_SECONDS": "1.5",
        "TASK4_TOKEN_LIMIT": "50000",
        "TASK4_WINDOW_SECONDS": "60",
        "TASK4_SQLITE_BUSY_TIMEOUT_MS": "5000",
    }


@pytest.fixture(scope="module")
def task4_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Task4Stack]:
    base = os.environ.copy()
    directory = tmp_path_factory.mktemp("task4-live")
    database_path = directory / "rate-limits.sqlite3"
    provider_port = _free_port()
    gateway_port = _free_port()
    provider_process = _start("ai_field.mocks.task4_provider:app", provider_port, base)
    provider_url = f"http://127.0.0.1:{provider_port}"
    environment = _gateway_environment(
        base,
        database_path=database_path,
        primary_url=f"{provider_url}/v1/primary/completions",
        secondary_url=f"{provider_url}/v1/secondary/completions",
    )
    gateway_process = _start("ai_field.task4_router.app:app", gateway_port, environment)
    try:
        yield Task4Stack(
            gateway_url=f"http://127.0.0.1:{gateway_port}",
            provider_url=provider_url,
            database_path=database_path,
        )
    finally:
        _stop(gateway_process)
        _stop(provider_process)


@pytest.fixture(scope="module")
def unreachable_gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    base = os.environ.copy()
    directory = tmp_path_factory.mktemp("task4-unreachable")
    gateway_port = _free_port()
    primary_port = _free_port()
    secondary_port = _free_port()
    environment = _gateway_environment(
        base,
        database_path=directory / "rate-limits.sqlite3",
        primary_url=f"http://127.0.0.1:{primary_port}/completions",
        secondary_url=f"http://127.0.0.1:{secondary_port}/completions",
    )
    gateway_process = _start("ai_field.task4_router.app:app", gateway_port, environment)
    try:
        yield f"http://127.0.0.1:{gateway_port}"
    finally:
        _stop(gateway_process)
