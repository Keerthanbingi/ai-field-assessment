"""Strict JSON-RPC request and error helpers for the Task 2 gateway."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

RequestId = StrictStr | StrictInt


class JSONRPCRequest(BaseModel):
    """Supported single-request JSON-RPC envelope."""

    model_config = ConfigDict(strict=True, extra="forbid")

    jsonrpc: Literal["2.0"]
    id: RequestId
    method: StrictStr
    params: dict[str, Any] | None = None


class ToolCallParams(BaseModel):
    """Security-relevant part of MCP tools/call parameters."""

    model_config = ConfigDict(strict=True, extra="allow")

    name: StrictStr = Field(min_length=1)
    arguments: dict[str, Any] | None = None


def jsonrpc_error(
    request_id: str | int | None,
    code: int,
    message: str,
    *,
    data: Any = None,
) -> dict[str, Any]:
    """Build a sanitized JSON-RPC 2.0 error payload."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}
