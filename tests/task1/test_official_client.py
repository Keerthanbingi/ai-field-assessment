"""Interoperability tests using the official MCP Python client."""

from __future__ import annotations

import sys
import tempfile

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import McpError

from ai_field.task1_mcp.server import create_server

from .conftest import PROJECT_ROOT


def test_create_server_returns_official_sdk_server() -> None:
    assert isinstance(create_server(), Server)


@pytest.mark.asyncio
async def test_official_sdk_client_initializes_lists_and_calls_tools() -> None:
    """The actual SDK client can complete the full stdio lifecycle."""
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ai_field.task1_mcp.server"],
        cwd=PROJECT_ROOT,
    )

    with tempfile.TemporaryFile(mode="w+") as stderr_file:
        async with stdio_client(parameters, errlog=stderr_file) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as client:
                initialized = await client.initialize()
                assert initialized.protocolVersion == "2025-11-25"
                assert initialized.serverInfo.name == "ai-field-task1"

                listed = await client.list_tools()
                assert [tool.name for tool in listed.tools] == [
                    "get_customer_record",
                    "trigger_refund",
                ]

                customer = await client.call_tool(
                    "get_customer_record",
                    {"customer_id": "CUST-12345"},
                )
                assert customer.isError is False
                assert customer.structuredContent == {
                    "customer_id": "CUST-12345",
                    "name": "Jordan Rivera",
                    "email": "jordan.rivera@example.test",
                    "status": "active",
                }

                refund = await client.call_tool(
                    "trigger_refund",
                    {
                        "customer_id": "CUST-12345",
                        "amount": 12.5,
                        "reason": "Duplicate charge",
                    },
                )
                assert refund.isError is False
                assert refund.structuredContent is not None
                assert refund.structuredContent["status"] == "simulated"

                with pytest.raises(McpError) as invalid_call:
                    await client.call_tool(
                        "trigger_refund",
                        {
                            "customer_id": "CUST-12345",
                            "amount": "12.50",
                            "reason": "Duplicate charge",
                        },
                    )
                assert invalid_call.value.error.code == -32602
                assert invalid_call.value.error.message == "Invalid tool arguments"

        stderr_file.seek(0)
        stderr = stderr_file.read()

    assert "Task 1 MCP server starting on stdio" in stderr
    assert "Processing request of type ListToolsRequest" in stderr
