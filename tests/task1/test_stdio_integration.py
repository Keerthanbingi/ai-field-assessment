"""Real subprocess integration tests for the Task 1 stdio MCP server."""

from __future__ import annotations

import math
import re
from typing import Any

import pytest

from .conftest import MCPProcess


def tool_call(request_id: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def assert_error(response: dict[str, Any], request_id: int | None, code: int) -> None:
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == request_id
    assert response["error"]["code"] == code


def test_successful_initialization() -> None:
    client = MCPProcess()
    try:
        response = client.initialize(request_id=101)
        assert response["id"] == 101
        assert response["result"]["protocolVersion"] == "2025-11-25"
        assert "tools" in response["result"]["capabilities"]
        assert response["result"]["serverInfo"]["name"] == "ai-field-task1"
    finally:
        client.close()


def test_tools_list_returns_exact_tools_and_strict_schemas(mcp_process: MCPProcess) -> None:
    response = mcp_process.send({"jsonrpc": "2.0", "id": 102, "method": "tools/list", "params": {}})
    tools = response["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["get_customer_record", "trigger_refund"]
    for tool in tools:
        assert tool["inputSchema"]["additionalProperties"] is False
        assert set(tool["inputSchema"]["required"]) == set(tool["inputSchema"]["properties"])


def test_get_customer_record_success(mcp_process: MCPProcess) -> None:
    response = mcp_process.send(
        tool_call(103, "get_customer_record", {"customer_id": "CUST-12345"})
    )
    result = response["result"]
    assert result["isError"] is False
    assert result["structuredContent"] == {
        "customer_id": "CUST-12345",
        "name": "Jordan Rivera",
        "email": "jordan.rivera@example.test",
        "status": "active",
    }


def test_valid_but_unknown_customer_is_safe_tool_error(mcp_process: MCPProcess) -> None:
    response = mcp_process.send(
        tool_call(104, "get_customer_record", {"customer_id": "CUST-99999"})
    )
    assert response["result"]["isError"] is True
    assert response["result"]["content"][0]["text"] == ("No customer record exists for CUST-99999.")
    assert "Traceback" not in response["result"]["content"][0]["text"]


def test_trigger_refund_success_is_simulated(mcp_process: MCPProcess) -> None:
    response = mcp_process.send(
        tool_call(
            105,
            "trigger_refund",
            {"customer_id": "CUST-00001", "amount": 25.5, "reason": "Duplicate purchase"},
        )
    )
    refund = response["result"]["structuredContent"]
    assert response["result"]["isError"] is False
    assert re.fullmatch(r"RFND-[0-9A-F]{12}", refund["refund_id"])
    assert refund["customer_id"] == "CUST-00001"
    assert refund["amount"] == 25.5
    assert refund["status"] == "simulated"


@pytest.mark.parametrize(
    "customer_id",
    [
        "CUST-1234",
        "CUST-ABCDE",
        "cust-12345",
        "CUST-123456",
        "CUST-12345\n",
        "CUST-\uff11\uff12\uff13\uff14\uff15",
    ],
)
def test_invalid_customer_id_formats(mcp_process: MCPProcess, customer_id: str) -> None:
    response = mcp_process.send(tool_call(200, "get_customer_record", {"customer_id": customer_id}))
    assert_error(response, 200, -32602)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_customer_record", {}),
        ("get_customer_record", {"customer_id": "CUST-12345", "extra": True}),
        ("trigger_refund", {"customer_id": "CUST-12345", "amount": 5.0}),
        (
            "trigger_refund",
            {
                "customer_id": "CUST-12345",
                "amount": 5.0,
                "reason": "Duplicate charge",
                "extra": "forbidden",
            },
        ),
    ],
)
def test_missing_and_extra_fields(
    mcp_process: MCPProcess, name: str, arguments: dict[str, Any]
) -> None:
    response = mcp_process.send(tool_call(201, name, arguments))
    assert_error(response, 201, -32602)


@pytest.mark.parametrize(
    "amount",
    [0, 0.0, -1, -0.01, "12.50", True, False, math.nan, math.inf, -math.inf],
)
def test_invalid_refund_amounts(mcp_process: MCPProcess, amount: Any) -> None:
    response = mcp_process.send(
        tool_call(
            202,
            "trigger_refund",
            {"customer_id": "CUST-12345", "amount": amount, "reason": "Duplicate charge"},
        )
    )
    assert_error(response, 202, -32602)


@pytest.mark.parametrize("reason", ["too short", "          ", "a b c d e f g h i"])
def test_invalid_refund_reasons(mcp_process: MCPProcess, reason: str) -> None:
    response = mcp_process.send(
        tool_call(
            203,
            "trigger_refund",
            {"customer_id": "CUST-12345", "amount": 10.0, "reason": reason},
        )
    )
    assert_error(response, 203, -32602)


def test_unknown_tool_name(mcp_process: MCPProcess) -> None:
    response = mcp_process.send(tool_call(204, "delete_everything", {}))
    assert_error(response, 204, -32602)
    assert response["error"]["message"] == "Unknown tool"


def test_malformed_json_returns_parse_error(mcp_process: MCPProcess) -> None:
    response = mcp_process.send_raw('{"jsonrpc":"2.0","id":205,not-json')
    assert_error(response, None, -32700)


@pytest.mark.parametrize("raw", ["{}", '{"jsonrpc":"2.0","id":206}'])
def test_malformed_jsonrpc_returns_invalid_request(mcp_process: MCPProcess, raw: str) -> None:
    response = mcp_process.send_raw(raw)
    assert_error(response, None, -32600)


def test_unsupported_method_preserves_request_id(mcp_process: MCPProcess) -> None:
    response = mcp_process.send(
        {"jsonrpc": "2.0", "id": 207, "method": "widgets/list", "params": {}}
    )
    assert_error(response, 207, -32601)


def test_unexpected_exception_is_sanitized_and_preserves_id() -> None:
    client = MCPProcess(extra_env={"AI_FIELD_TASK1_TEST_FORCE_INTERNAL_ERROR": "1"})
    try:
        client.initialize()
        response = client.send(tool_call(208, "get_customer_record", {"customer_id": "CUST-12345"}))
        assert_error(response, 208, -32603)
        assert response["error"]["message"] == "Internal server error"
        assert "private test failure details" not in str(response)
    finally:
        stderr = client.close()
    assert "private test failure details" in stderr


def test_stdout_contains_only_jsonrpc_and_logs_use_stderr() -> None:
    client = MCPProcess()
    try:
        client.initialize()
        client.send({"jsonrpc": "2.0", "id": 209, "method": "tools/list", "params": {}})
    finally:
        stderr = client.close()

    assert client.stdout_lines
    assert all(line.lstrip().startswith("{") for line in client.stdout_lines)
    assert all("Task 1 MCP server starting" not in line for line in client.stdout_lines)
    assert "Task 1 MCP server starting on stdio" in stderr
    assert "Processing request" in stderr
