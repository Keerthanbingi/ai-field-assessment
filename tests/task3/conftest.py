"""Real Uvicorn process fixtures for Task 3 streaming integration tests."""

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

PROJECT_ROOT = Path(__file__).parents[2]


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
class Task3Stack:
    gateway_url: str
    provider_url: str


@pytest.fixture(scope="module")
def task3_stack() -> Iterator[Task3Stack]:
    environment = os.environ.copy()
    provider_port = _free_port()
    gateway_port = _free_port()
    provider_process = _start(
        "ai_field.mocks.task3_provider:app",
        provider_port,
        environment,
    )
    gateway_environment = {
        **environment,
        "TASK3_PROVIDER_URL": f"http://127.0.0.1:{provider_port}/v1/provider/stream",
        "TASK3_CONNECT_TIMEOUT_SECONDS": "1.0",
        "TASK3_READ_TIMEOUT_SECONDS": "5.0",
        "TASK3_MAX_CANDIDATE_CHARS": "320",
    }
    gateway_process = _start(
        "ai_field.task3_guardrail.app:app",
        gateway_port,
        gateway_environment,
    )
    try:
        yield Task3Stack(
            gateway_url=f"http://127.0.0.1:{gateway_port}",
            provider_url=f"http://127.0.0.1:{provider_port}",
        )
    finally:
        _stop(gateway_process)
        _stop(provider_process)
