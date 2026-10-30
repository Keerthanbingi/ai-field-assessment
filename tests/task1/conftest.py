"""Subprocess helpers for real stdio MCP integration tests."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).parents[2]


class MCPProcess:
    """Small newline-delimited JSON-RPC client for the server subprocess."""

    def __init__(self, *, extra_env: dict[str, str] | None = None) -> None:
        environment = os.environ.copy()
        environment.update(extra_env or {})
        environment["PYTHONUNBUFFERED"] = "1"
        self.process = subprocess.Popen(
            [sys.executable, "-m", "ai_field.task1_mcp.server"],
            cwd=PROJECT_ROOT,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.stdout_lines: list[str] = []

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.send_raw(json.dumps(payload, allow_nan=True))

    def send_raw(self, line: str) -> dict[str, Any]:
        assert self.process.stdin is not None
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        return self.read_response()

    def notify(self, payload: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()

    def read_response(self, timeout: float = 5.0) -> dict[str, Any]:
        assert self.process.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(self.process.stdout, selectors.EVENT_READ)
        try:
            if not selector.select(timeout):
                raise AssertionError("Timed out waiting for MCP server stdout")
            line = self.process.stdout.readline()
        finally:
            selector.close()
        if not line:
            raise AssertionError(f"MCP server exited unexpectedly with {self.process.returncode}")
        self.stdout_lines.append(line)
        return json.loads(line)

    def initialize(self, request_id: int = 1) -> dict[str, Any]:
        response = self.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest-client", "version": "1.0"},
                },
            }
        )
        self.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return response

    def close(self) -> str:
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        self.process.wait(timeout=5)
        assert self.process.stderr is not None
        return self.process.stderr.read()


@pytest.fixture
def mcp_process() -> MCPProcess:
    client = MCPProcess()
    client.initialize()
    yield client
    if client.process.poll() is None:
        client.close()
