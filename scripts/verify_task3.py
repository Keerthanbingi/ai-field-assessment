#!/usr/bin/env python3
"""Launch Task 3 services and verify streaming redaction over real HTTP."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

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


def check(label: str, condition: bool) -> bool:
    print(f"{'PASS' if condition else 'FAIL'}: {label}")
    return condition


def parse_frames(raw: bytes) -> list[tuple[str | None, str]]:
    text = raw.decode("utf-8", errors="strict")
    frames: list[tuple[str | None, str]] = []
    for frame in text.split("\n\n")[:-1]:
        event = None
        data: list[str] = []
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line.partition(":")[2].strip()
            elif line.startswith("data:"):
                data.append(line.partition(":")[2].lstrip())
        frames.append((event, "\n".join(data)))
    return frames


def output_text(raw: bytes) -> str:
    output: list[str] = []
    for event, data in parse_frames(raw):
        if data == "[DONE]" or event == "error":
            continue
        payload = json.loads(data)
        if payload.get("choices"):
            output.append(payload["choices"][0]["delta"].get("content") or "")
    return "".join(output)


def request_bytes(client: httpx.Client, url: str, payload: dict[str, Any]) -> bytes:
    with client.stream("POST", url, json=payload) as response:
        response.raise_for_status()
        return b"".join(response.iter_bytes())


def main() -> int:
    provider_port = free_port()
    gateway_port = free_port()
    environment = os.environ.copy()
    provider = start_server("ai_field.mocks.task3_provider:app", provider_port, environment)
    gateway_environment = {
        **environment,
        "TASK3_PROVIDER_URL": f"http://127.0.0.1:{provider_port}/v1/provider/stream",
        "TASK3_CONNECT_TIMEOUT_SECONDS": "1.0",
        "TASK3_READ_TIMEOUT_SECONDS": "5.0",
        "TASK3_MAX_CANDIDATE_CHARS": "320",
    }
    gateway = start_server("ai_field.task3_guardrail.app:app", gateway_port, gateway_environment)
    gateway_url = f"http://127.0.0.1:{gateway_port}/v1/stream"
    results: list[bool] = []

    try:
        with httpx.Client(timeout=10) as client:
            normal = request_bytes(client, gateway_url, {"scenario": "normal"})
            results.append(
                check("normal text preserved", output_text(normal) == "Hello from the model.")
            )

            split_email = request_bytes(client, gateway_url, {"scenario": "split_email"})
            split_email_text = output_text(split_email)
            print('BEFORE_DELTAS=["Contact john@","gmail.","com now."]')
            print(f"AFTER_TEXT={split_email_text}")
            results.append(
                check(
                    "split email redacted",
                    split_email_text == "Contact [REDACTED] now."
                    and b"john@gmail.com" not in split_email,
                )
            )

            split_ssn = request_bytes(client, gateway_url, {"scenario": "split_ssn"})
            results.append(
                check(
                    "split SSN redacted",
                    output_text(split_ssn) == "SSN [REDACTED] received.",
                )
            )

            split_card = request_bytes(client, gateway_url, {"scenario": "split_card"})
            results.append(
                check(
                    "split Luhn-valid card redacted",
                    output_text(split_card) == "Card [REDACTED] accepted.",
                )
            )

            byte_split = request_bytes(
                client,
                gateway_url,
                {"scenario": "utf8", "wire_chunk_size": 1},
            )
            results.append(
                check(
                    "one-byte UTF-8/SSE splits remain valid",
                    output_text(byte_split) == "Café 🍵 says hello.",
                )
            )

            started = time.monotonic()
            first_text_ms: float | None = None
            delayed_raw = bytearray()
            with client.stream(
                "POST",
                gateway_url,
                json={"scenario": "delayed", "delay_ms": 350},
            ) as response:
                for chunk in response.iter_bytes():
                    delayed_raw.extend(chunk)
                    if first_text_ms is None and b"Early safe text" in delayed_raw:
                        first_text_ms = (time.monotonic() - started) * 1000
            total_ms = (time.monotonic() - started) * 1000
            print(f"FIRST_SANITIZED_TEXT_MS={first_text_ms:.1f}")
            print(f"TOTAL_STREAM_MS={total_ms:.1f}")
            results.append(
                check(
                    "safe output arrives before provider completion",
                    first_text_ms is not None
                    and first_text_ms < 250
                    and total_ms >= 300
                    and output_text(bytes(delayed_raw)) == "Early safe text. Late text.",
                )
            )

            malformed = request_bytes(client, gateway_url, {"scenario": "malformed"})
            frames = parse_frames(malformed)
            results.append(
                check(
                    "malformed provider event fails closed",
                    b"secret@example.com" not in malformed and frames[-1][0] == "error",
                )
            )
    finally:
        stop_server(gateway)
        stop_server(provider)

    print(f"Summary: {sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
