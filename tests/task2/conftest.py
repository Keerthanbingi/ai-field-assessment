"""Real Uvicorn process fixtures for Task 2 HTTP integration tests."""

from __future__ import annotations

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

from ai_field.task2_gateway.auth import create_development_token

PROJECT_ROOT = Path(__file__).parents[2]
TEST_SECRET = "task2-integration-secret-that-is-longer-than-32-characters"


def _free_port() -> int:
    with socket.socket() as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


def _wait_until_ready(url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            assert process.stderr is not None
            raise RuntimeError(f"Server exited during startup: {process.stderr.read()}")
        try:
            response = httpx.get(url, timeout=0.25)
            if response.status_code == 200:
                return
        except httpx.RequestError:
            pass
        time.sleep(0.05)
    raise TimeoutError(f"Server did not become ready: {url}")


def _start_uvicorn(app_path: str, port: int, env: dict[str, str]) -> subprocess.Popen[str]:
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
        env=env,
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
class GatewayStack:
    gateway_url: str
    downstream_url: str


@pytest.fixture(scope="module")
def gateway_stack() -> Iterator[GatewayStack]:
    base_env = os.environ.copy()
    mock_port = _free_port()
    gateway_port = _free_port()
    mock_process = _start_uvicorn(
        "ai_field.mocks.task2_downstream:app",
        mock_port,
        base_env,
    )

    gateway_env = {
        **base_env,
        "TASK2_JWT_SECRET": TEST_SECRET,
        "TASK2_DOWNSTREAM_URL": f"http://127.0.0.1:{mock_port}/mcp",
        "TASK2_DOWNSTREAM_TIMEOUT_SECONDS": "0.5",
    }
    gateway_process = _start_uvicorn(
        "ai_field.task2_gateway.app:app",
        gateway_port,
        gateway_env,
    )

    try:
        yield GatewayStack(
            gateway_url=f"http://127.0.0.1:{gateway_port}",
            downstream_url=f"http://127.0.0.1:{mock_port}",
        )
    finally:
        _stop(gateway_process)
        _stop(mock_process)


@pytest.fixture(scope="module")
def broken_gateway_url() -> Iterator[str]:
    base_env = os.environ.copy()
    gateway_port = _free_port()
    unused_downstream_port = _free_port()
    gateway_env = {
        **base_env,
        "TASK2_JWT_SECRET": TEST_SECRET,
        "TASK2_DOWNSTREAM_URL": f"http://127.0.0.1:{unused_downstream_port}/mcp",
        "TASK2_DOWNSTREAM_TIMEOUT_SECONDS": "0.2",
    }
    process = _start_uvicorn(
        "ai_field.task2_gateway.app:app",
        gateway_port,
        gateway_env,
    )
    try:
        yield f"http://127.0.0.1:{gateway_port}"
    finally:
        _stop(process)


@pytest.fixture(scope="module")
def viewer_token() -> str:
    return create_development_token("viewer", subject="integration-viewer", secret=TEST_SECRET)


@pytest.fixture(scope="module")
def admin_token() -> str:
    return create_development_token("admin", subject="integration-admin", secret=TEST_SECRET)
