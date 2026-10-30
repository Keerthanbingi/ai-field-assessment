"""FastAPI MCP security gateway for Task 2."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response
from fastapi.security.utils import get_authorization_scheme_param
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_field.task2_gateway.auth import (
    AuthenticationError,
    TokenClaims,
    development_signing_key,
    verify_token,
)
from ai_field.task2_gateway.protocol import JSONRPCRequest, ToolCallParams, jsonrpc_error

logger = logging.getLogger(__name__)

AUTHENTICATION_ERROR = -32000
UNAUTHORIZED_TOOL_CALL = -32001
DOWNSTREAM_ERROR = -32002


class GatewaySettings(BaseModel):
    """Environment-backed gateway configuration."""

    model_config = ConfigDict(strict=True)

    jwt_secret: str = Field(min_length=32, repr=False)
    downstream_url: str
    downstream_timeout_seconds: float = Field(gt=0)

    @classmethod
    def from_environment(cls) -> GatewaySettings:
        return cls(
            jwt_secret=development_signing_key(),
            downstream_url=os.getenv("TASK2_DOWNSTREAM_URL", "http://127.0.0.1:9001/mcp"),
            downstream_timeout_seconds=float(os.getenv("TASK2_DOWNSTREAM_TIMEOUT_SECONDS", "2.0")),
        )


def _authentication_response(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content=jsonrpc_error(None, AUTHENTICATION_ERROR, message),
        headers={"WWW-Authenticate": "Bearer"},
    )


def _authenticate(authorization: str | None, secret: str) -> TokenClaims:
    scheme, token = get_authorization_scheme_param(authorization or "")
    if scheme.lower() != "bearer" or not token:
        raise AuthenticationError("Bearer authentication required")
    return verify_token(token, secret)


def _validation_issues(error: ValidationError) -> list[dict[str, str]]:
    return [
        {
            "field": ".".join(str(part) for part in issue["loc"]),
            "message": issue["msg"],
            "type": issue["type"],
        }
        for issue in error.errors(include_input=False, include_url=False)
    ]


def create_app(settings: GatewaySettings | None = None) -> FastAPI:
    """Create a gateway app; environment configuration is loaded at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved = settings or GatewaySettings.from_environment()
        app.state.settings = resolved
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(resolved.downstream_timeout_seconds)
        ) as client:
            app.state.downstream_client = client
            yield

    gateway = FastAPI(title="Task 2 MCP Security Gateway", lifespan=lifespan)

    @gateway.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @gateway.post("/mcp")
    async def proxy_mcp(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> Response:
        resolved: GatewaySettings = request.app.state.settings
        try:
            claims = _authenticate(authorization, resolved.jwt_secret)
        except AuthenticationError as error:
            return _authentication_response(str(error))

        raw_body = await request.body()
        try:
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse(
                status_code=400,
                content=jsonrpc_error(None, -32700, "Parse error"),
            )

        # Batches and notifications are deliberately unsupported. Rejecting the
        # complete object before routing prevents an admin call from hiding in one.
        if not isinstance(payload, dict) or "id" not in payload:
            return JSONResponse(
                status_code=400,
                content=jsonrpc_error(None, -32600, "Invalid Request"),
            )

        try:
            rpc_request = JSONRPCRequest.model_validate(payload)
        except ValidationError as error:
            return JSONResponse(
                status_code=400,
                content=jsonrpc_error(
                    None,
                    -32600,
                    "Invalid Request",
                    data={"issues": _validation_issues(error)},
                ),
            )

        if rpc_request.method not in {"tools/list", "tools/call"}:
            return JSONResponse(content=jsonrpc_error(rpc_request.id, -32601, "Method not found"))

        if rpc_request.method == "tools/call":
            try:
                call_params = ToolCallParams.model_validate(rpc_request.params)
            except ValidationError as error:
                return JSONResponse(
                    content=jsonrpc_error(
                        rpc_request.id,
                        -32602,
                        "Invalid params",
                        data={"issues": _validation_issues(error)},
                    )
                )

            if call_params.name.startswith("admin_") and claims.role != "admin":
                logger.warning(
                    "Blocked unauthorized admin tool call for subject %s",
                    claims.sub,
                )
                return JSONResponse(
                    content=jsonrpc_error(
                        rpc_request.id,
                        UNAUTHORIZED_TOOL_CALL,
                        "Unauthorized Tool Call",
                    )
                )

        client: httpx.AsyncClient = request.app.state.downstream_client
        try:
            upstream = await client.post(
                resolved.downstream_url,
                content=raw_body,
                headers={"Content-Type": "application/json"},
            )
        except httpx.TimeoutException:
            logger.warning("Downstream MCP request timed out")
            return JSONResponse(
                status_code=504,
                content=jsonrpc_error(
                    rpc_request.id,
                    DOWNSTREAM_ERROR,
                    "Downstream service unavailable",
                ),
            )
        except httpx.RequestError:
            logger.warning("Downstream MCP connection failed")
            return JSONResponse(
                status_code=502,
                content=jsonrpc_error(
                    rpc_request.id,
                    DOWNSTREAM_ERROR,
                    "Downstream service unavailable",
                ),
            )

        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    return gateway


app = create_app()
