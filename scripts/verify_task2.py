#!/usr/bin/env python3
"""Launch both Task 2 servers and run reproducible HTTP security checks."""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from ai_field.task2_gateway.auth import create_development_token

ROOT = Path(__file__).parents[1]


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
    deadline = time.monotonic() + 10
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


def rpc(request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def check(label: str, condition: bool) -> bool:
    print(f"{'PASS' if condition else 'FAIL'}: {label}")
    return condition


def main() -> int:
    secret = secrets.token_urlsafe(48)
    base_environment = os.environ.copy()
    mock_port = free_port()
    gateway_port = free_port()
    mock_process = start_server(
        "ai_field.mocks.task2_downstream:app",
        mock_port,
        base_environment,
    )
    gateway_environment = {
        **base_environment,
        "TASK2_JWT_SECRET": secret,
        "TASK2_DOWNSTREAM_URL": f"http://127.0.0.1:{mock_port}/mcp",
        "TASK2_DOWNSTREAM_TIMEOUT_SECONDS": "1.0",
    }
    gateway_process = start_server(
        "ai_field.task2_gateway.app:app",
        gateway_port,
        gateway_environment,
    )
    gateway_url = f"http://127.0.0.1:{gateway_port}/mcp"
    mock_url = f"http://127.0.0.1:{mock_port}"
    viewer = create_development_token("viewer", secret=secret)
    admin = create_development_token("admin", secret=secret)
    results: list[bool] = []

    try:
        with httpx.Client(timeout=2) as client:
            viewer_headers = {"Authorization": f"Bearer {viewer}"}
            admin_headers = {"Authorization": f"Bearer {admin}"}

            listed = client.post(
                gateway_url,
                json=rpc(1, "tools/list", {}),
                headers=viewer_headers,
            )
            results.append(check("viewer tools/list forwarded", listed.status_code == 200))

            regular = client.post(
                gateway_url,
                json=rpc(
                    2,
                    "tools/call",
                    {"name": "get_customer_record", "arguments": {"customer_id": "CUST-1"}},
                ),
                headers=viewer_headers,
            )
            results.append(
                check(
                    "viewer regular tool allowed",
                    regular.json().get("result", {}).get("isError") is False,
                )
            )
            print("ALLOWED_RESPONSE=" + json.dumps(regular.json(), separators=(",", ":")))

            client.post(f"{mock_url}/__test__/reset")
            count_before_block = client.get(f"{mock_url}/__test__/requests").json()["count"]
            blocked = client.post(
                gateway_url,
                json=rpc(3, "tools/call", {"name": "admin_reset_key", "arguments": {}}),
                headers=viewer_headers,
            )
            count_after_block = client.get(f"{mock_url}/__test__/requests").json()["count"]
            results.append(
                check(
                    "viewer admin tool blocked locally",
                    blocked.json().get("error", {}).get("code") == -32001
                    and count_before_block == count_after_block,
                )
            )
            print("BLOCKED_RESPONSE=" + json.dumps(blocked.json(), separators=(",", ":")))
            print(f"DOWNSTREAM_COUNT_BEFORE_BLOCK={count_before_block}")
            print(f"DOWNSTREAM_COUNT_AFTER_BLOCK={count_after_block}")

            allowed = client.post(
                gateway_url,
                json=rpc(4, "tools/call", {"name": "admin_reset_key", "arguments": {}}),
                headers=admin_headers,
            )
            authorized_log = client.get(f"{mock_url}/__test__/requests").json()
            results.append(
                check(
                    "admin tool allowed for admin",
                    allowed.json().get("result", {}).get("isError") is False,
                )
            )
            results.append(
                check(
                    "authorized admin call reached downstream",
                    authorized_log["count"] == count_after_block + 1
                    and authorized_log["invocations"][-1]["tool"] == "admin_reset_key",
                )
            )

            missing_auth = client.post(gateway_url, json=rpc(5, "tools/list", {}))
            results.append(check("missing token rejected", missing_auth.status_code == 401))

            batch = client.post(
                gateway_url,
                json=[rpc(6, "tools/call", {"name": "admin_reset_key"})],
                headers=viewer_headers,
            )
            results.append(
                check(
                    "batch rejected",
                    batch.status_code == 400
                    and batch.json().get("error", {}).get("code") == -32600,
                )
            )
    finally:
        stop_server(gateway_process)
        stop_server(mock_process)

    print(f"Summary: {sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
