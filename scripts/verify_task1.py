#!/usr/bin/env python3
"""End-to-end Task 1 protocol verification using a real server subprocess."""

from __future__ import annotations

import json
import selectors
import subprocess
import sys
from pathlib import Path
from typing import Any, TextIO

ROOT = Path(__file__).parents[1]


def read_json(stream: TextIO, timeout: float = 5.0) -> dict[str, Any]:
    selector = selectors.DefaultSelector()
    selector.register(stream, selectors.EVENT_READ)
    try:
        if not selector.select(timeout):
            raise TimeoutError("server response timed out")
        line = stream.readline()
    finally:
        selector.close()
    if not line:
        raise RuntimeError("server closed stdout")
    return json.loads(line)


def send(process: subprocess.Popen[str], payload: dict[str, Any] | str) -> dict[str, Any]:
    assert process.stdin is not None
    assert process.stdout is not None
    line = payload if isinstance(payload, str) else json.dumps(payload)
    process.stdin.write(line + "\n")
    process.stdin.flush()
    return read_json(process.stdout)


def check(label: str, condition: bool) -> bool:
    print(f"{'PASS' if condition else 'FAIL'}: {label}")
    return condition


def main() -> int:
    process = subprocess.Popen(
        [sys.executable, "-m", "ai_field.task1_mcp.server"],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    passed: list[bool] = []
    try:
        initialized = send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "verify-task1", "version": "1.0"},
                },
            },
        )
        passed.append(check("initialize", initialized.get("id") == 1 and "result" in initialized))

        assert process.stdin is not None
        process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        process.stdin.flush()

        listed = send(
            process,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        names = [tool["name"] for tool in listed.get("result", {}).get("tools", [])]
        passed.append(
            check(
                "tools/list exposes exactly two tools",
                names == ["get_customer_record", "trigger_refund"],
            )
        )

        customer = send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "get_customer_record",
                    "arguments": {"customer_id": "CUST-12345"},
                },
            },
        )
        passed.append(
            check(
                "get_customer_record succeeds",
                customer.get("result", {}).get("isError") is False,
            )
        )

        invalid = send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "trigger_refund",
                    "arguments": {
                        "customer_id": "CUST-12345",
                        "amount": 0,
                        "reason": "Duplicate charge",
                    },
                },
            },
        )
        passed.append(
            check(
                "invalid arguments map to -32602",
                invalid.get("error", {}).get("code") == -32602 and invalid.get("id") == 4,
            )
        )

        malformed = send(process, "{not-json")
        passed.append(
            check(
                "malformed JSON maps to -32700",
                malformed.get("error", {}).get("code") == -32700 and malformed.get("id") is None,
            )
        )
    except Exception as error:
        print(f"FAIL: verification aborted: {error}")
        passed.append(False)
    finally:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        process.wait(timeout=5)
        assert process.stderr is not None
        stderr = process.stderr.read()
        passed.append(
            check(
                "server logs are present on stderr",
                "Task 1 MCP server starting" in stderr,
            )
        )

    print(f"Summary: {sum(passed)}/{len(passed)} checks passed")
    return 0 if all(passed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
