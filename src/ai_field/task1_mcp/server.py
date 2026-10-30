"""Runnable Task 1 MCP server using the official SDK and stdio transport."""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from pydantic import ValidationError

from ai_field.task1_mcp.models import (
    GetCustomerRecordInput,
    TriggerRefundInput,
    input_schema,
)
from ai_field.task1_mcp.service import create_refund, get_customer
from ai_field.task1_mcp.transport import run_transport_adapter

logger = logging.getLogger(__name__)

GET_CUSTOMER_RECORD = "get_customer_record"
TRIGGER_REFUND = "trigger_refund"


def _configure_logging() -> None:
    """Configure all application and SDK logs to use stderr only."""
    logging.basicConfig(
        level=os.getenv("AI_FIELD_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )


def _tool_definitions() -> list[types.Tool]:
    return [
        types.Tool(
            name=GET_CUSTOMER_RECORD,
            description="Return a customer record from the local mock database.",
            inputSchema=input_schema(GetCustomerRecordInput),
        ),
        types.Tool(
            name=TRIGGER_REFUND,
            description=(
                "Simulate a local refund. This performs no payment-provider or financial action."
            ),
            inputSchema=input_schema(TriggerRefundInput),
        ),
    ]


def _invalid_params(error: ValidationError) -> McpError:
    details = [
        {
            "field": ".".join(str(part) for part in issue["loc"]),
            "message": issue["msg"],
            "type": issue["type"],
        }
        for issue in error.errors(include_url=False, include_input=False)
    ]
    return McpError(
        types.ErrorData(
            code=types.INVALID_PARAMS,
            message="Invalid tool arguments",
            data={"issues": details},
        )
    )


def _protocol_error(code: int, message: str, data: Any = None) -> McpError:
    return McpError(types.ErrorData(code=code, message=message, data=data))


def _success(payload: dict[str, Any]) -> types.ServerResult:
    return types.ServerResult(
        types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload, sort_keys=True))],
            structuredContent=payload,
            isError=False,
        )
    )


def _customer_not_found(customer_id: str) -> types.ServerResult:
    message = f"No customer record exists for {customer_id}."
    return types.ServerResult(
        types.CallToolResult(
            content=[types.TextContent(type="text", text=message)],
            isError=True,
        )
    )


def create_server() -> Server:
    """Build the low-level official MCP server and register Task 1 handlers."""
    server = Server(
        name="ai-field-task1",
        version="0.1.0",
        instructions="Local assessment server; refunds are simulations only.",
    )

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return _tool_definitions()

    async def call_tool(request: types.CallToolRequest) -> types.ServerResult:
        try:
            arguments = request.params.arguments or {}

            if request.params.name == GET_CUSTOMER_RECORD:
                parsed = GetCustomerRecordInput.model_validate(arguments)
                if os.getenv("AI_FIELD_TASK1_TEST_FORCE_INTERNAL_ERROR") == "1":
                    raise RuntimeError("private test failure details")
                customer = get_customer(parsed.customer_id)
                if customer is None:
                    return _customer_not_found(parsed.customer_id)
                return _success(customer)

            if request.params.name == TRIGGER_REFUND:
                parsed_refund = TriggerRefundInput.model_validate(arguments)
                if get_customer(parsed_refund.customer_id) is None:
                    return _customer_not_found(parsed_refund.customer_id)
                return _success(
                    create_refund(
                        customer_id=parsed_refund.customer_id,
                        amount=parsed_refund.amount,
                        reason=parsed_refund.reason,
                    )
                )

            raise _protocol_error(
                types.INVALID_PARAMS,
                "Unknown tool",
                {"tool": request.params.name},
            )
        except ValidationError as error:
            raise _invalid_params(error) from None
        except McpError:
            raise
        except Exception:
            logger.exception("Unexpected failure while executing tool %s", request.params.name)
            raise _protocol_error(types.INTERNAL_ERROR, "Internal server error") from None

    # Register directly so McpError reaches the SDK protocol layer. The convenience
    # decorator in SDK 1.30 converts validation exceptions to isError=true results.
    server.request_handlers[types.CallToolRequest] = call_tool
    return server


async def run() -> None:
    """Run one official MCP stdio session until the client closes stdin."""
    _configure_logging()
    server = create_server()
    logger.info("Task 1 MCP server starting on stdio")

    async with stdio_server() as (sdk_read, sdk_write):

        async def run_sdk_server(read_stream: Any, write_stream: Any) -> None:
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

        await run_transport_adapter(sdk_read, sdk_write, run_sdk_server)


def main() -> None:
    """Synchronous console entry point."""
    anyio.run(run)


if __name__ == "__main__":
    main()
