"""Inspectable mock downstream MCP HTTP/JSON-RPC server for Task 2."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ai_field.task2_gateway.protocol import jsonrpc_error


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.request_count = 0
    app.state.invocations = []
    app.state.counter_lock = asyncio.Lock()
    yield


app = FastAPI(title="Task 2 Mock Downstream MCP", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/__test__/requests")
async def request_log(request: Request) -> dict[str, Any]:
    return {
        "count": request.app.state.request_count,
        "invocations": list(request.app.state.invocations),
    }


@app.post("/__test__/reset")
async def reset_request_log(request: Request) -> dict[str, bool]:
    async with request.app.state.counter_lock:
        request.app.state.request_count = 0
        request.app.state.invocations.clear()
    return {"reset": True}


@app.post("/mcp")
async def mcp(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse(content=jsonrpc_error(None, -32700, "Parse error"), status_code=400)

    request_id = payload.get("id") if isinstance(payload, dict) else None
    method = payload.get("method") if isinstance(payload, dict) else None
    params = payload.get("params") if isinstance(payload, dict) else None
    tool_name = params.get("name") if isinstance(params, dict) else None

    async with request.app.state.counter_lock:
        request.app.state.request_count += 1
        request.app.state.invocations.append(
            {
                "id": request_id,
                "method": method,
                "params": params,
                "tool": tool_name,
            }
        )

    if method == "tools/list":
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "tools": [
                        {
                            "name": "get_customer_record",
                            "description": "Return a mock customer record.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"customer_id": {"type": "string"}},
                                "required": ["customer_id"],
                                "additionalProperties": False,
                            },
                        },
                        {
                            "name": "admin_reset_key",
                            "description": "Simulate an administrative key reset.",
                            "inputSchema": {"type": "object", "additionalProperties": False},
                        },
                    ]
                },
            }
        )

    if method != "tools/call":
        return JSONResponse(content=jsonrpc_error(request_id, -32601, "Method not found"))

    if not isinstance(params, dict) or not isinstance(tool_name, str):
        return JSONResponse(content=jsonrpc_error(request_id, -32602, "Invalid params"))

    arguments = params.get("arguments") or {}
    if tool_name == "get_customer_record":
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Mock customer record returned.",
                        }
                    ],
                    "structuredContent": {
                        "customer_id": arguments.get("customer_id"),
                        "name": "Gateway Test Customer",
                        "status": "active",
                    },
                    "isError": False,
                },
            }
        )

    if tool_name == "admin_reset_key":
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": "Administrative key reset simulated."}],
                    "structuredContent": {
                        "tool": "admin_reset_key",
                        "status": "simulated",
                    },
                    "isError": False,
                },
            }
        )

    return JSONResponse(content=jsonrpc_error(request_id, -32602, "Unknown tool"))
