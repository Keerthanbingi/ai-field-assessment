"""Official MCP stdio transport adapter for precise JSON-RPC error mapping."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import types
from mcp.shared.message import SessionMessage
from pydantic import ValidationError

logger = logging.getLogger(__name__)

_SUPPORTED_REQUEST_METHODS = frozenset({"initialize", "ping", "tools/list", "tools/call"})


class _NullIdJSONRPCMessage(types.JSONRPCMessage):
    """Work around SDK 1.30 rejecting the JSON-RPC-required null error ID."""

    def model_dump_json(self, *args: object, **kwargs: object) -> str:
        error = self.root.error
        return json.dumps(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": error.model_dump(by_alias=True, exclude_none=True),
            },
            separators=(",", ":"),
        )


def _error_message(request_id: str | int | None, code: int, message: str) -> SessionMessage:
    error_data = types.ErrorData(code=code, message=message)
    if request_id is None:
        # JSON-RPC requires null when an invalid request has no recoverable ID.
        # SDK 1.30's JSONRPCError annotation omits None, so construct the exact
        # SDK model without validation and override serialization for this case.
        error = types.JSONRPCError.model_construct(
            jsonrpc="2.0",
            id=None,
            error=error_data,
        )
        message_model = _NullIdJSONRPCMessage.model_construct(root=error)
        return SessionMessage(message_model)

    error = types.JSONRPCError(
        jsonrpc="2.0",
        id=request_id,
        error=error_data,
    )
    return SessionMessage(types.JSONRPCMessage(error))


def _transport_validation_error(exc: Exception) -> tuple[int, str]:
    """Distinguish invalid JSON text from an invalid JSON-RPC envelope."""
    if isinstance(exc, ValidationError) and any(
        error["type"] == "json_invalid" for error in exc.errors()
    ):
        return types.PARSE_ERROR, "Parse error"
    return types.INVALID_REQUEST, "Invalid Request"


async def run_transport_adapter(
    sdk_read: MemoryObjectReceiveStream[SessionMessage | Exception],
    sdk_write: MemoryObjectSendStream[SessionMessage],
    run_server: Callable[
        [
            MemoryObjectReceiveStream[SessionMessage | Exception],
            MemoryObjectSendStream[SessionMessage],
        ],
        Awaitable[None],
    ],
) -> None:
    """Bridge official stdio streams to the SDK server and map transport errors.

    The official ``stdio_server`` remains responsible for newline-delimited UTF-8
    input/output and JSON-RPC serialization. This adapter handles the exceptions
    that transport exposes for invalid input before forwarding valid MCP messages.
    """
    inbound_send, inbound_receive = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    outbound_send, outbound_receive = anyio.create_memory_object_stream[SessionMessage](0)

    async def pump_inbound() -> None:
        async with inbound_send:
            async for item in sdk_read:
                if isinstance(item, Exception):
                    code, message = _transport_validation_error(item)
                    logger.warning("Rejected invalid stdio input: %s", message)
                    await sdk_write.send(_error_message(None, code, message))
                    continue

                root = item.message.root
                if (
                    isinstance(root, types.JSONRPCRequest)
                    and root.method not in _SUPPORTED_REQUEST_METHODS
                ):
                    await sdk_write.send(
                        _error_message(root.id, types.METHOD_NOT_FOUND, "Method not found")
                    )
                    continue

                await inbound_send.send(item)

    async def pump_outbound() -> None:
        async with outbound_receive:
            async for item in outbound_receive:
                await sdk_write.send(item)

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(pump_inbound)
        task_group.start_soon(pump_outbound)
        await run_server(inbound_receive, outbound_send)
        task_group.cancel_scope.cancel()
    await sdk_write.aclose()
