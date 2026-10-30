"""Real HTTP tests for the Task 2 security gateway and downstream mock."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest

from ai_field.task2_gateway.auth import JWT_AUDIENCE, JWT_ISSUER, create_development_token

from .conftest import TEST_SECRET, GatewayStack


def authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def rpc_request(
    request_id: str | int,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return payload


async def reset_downstream(client: httpx.AsyncClient, stack: GatewayStack) -> None:
    response = await client.post(f"{stack.downstream_url}/__test__/reset")
    assert response.status_code == 200


async def downstream_count(client: httpx.AsyncClient, stack: GatewayStack) -> int:
    response = await client.get(f"{stack.downstream_url}/__test__/requests")
    response.raise_for_status()
    return int(response.json()["count"])


async def downstream_log(client: httpx.AsyncClient, stack: GatewayStack) -> dict[str, Any]:
    response = await client.get(f"{stack.downstream_url}/__test__/requests")
    response.raise_for_status()
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["viewer", "admin"])
async def test_authenticated_roles_can_list_tools(
    gateway_stack: GatewayStack,
    role: str,
) -> None:
    token = create_development_token(role, secret=TEST_SECRET)  # type: ignore[arg-type]
    async with httpx.AsyncClient() as client:
        request = rpc_request(f"list-{role}", "tools/list", {})
        direct = await client.post(f"{gateway_stack.downstream_url}/mcp", json=request)
        await reset_downstream(client, gateway_stack)
        before = await downstream_count(client, gateway_stack)
        response = await client.post(
            f"{gateway_stack.gateway_url}/mcp",
            json=request,
            headers=authorization(token),
        )
        assert response.status_code == 200
        assert response.json() == direct.json()
        body = response.json()
        assert [tool["name"] for tool in body["result"]["tools"]] == [
            "get_customer_record",
            "admin_reset_key",
        ]
        after = await downstream_count(client, gateway_stack)
        assert (before, after) == (0, 1)
        assert (await downstream_log(client, gateway_stack))["invocations"] == [
            {
                "id": f"list-{role}",
                "method": "tools/list",
                "params": {},
                "tool": None,
            }
        ]


@pytest.mark.asyncio
async def test_bearer_scheme_is_case_insensitive_and_forwarded(
    gateway_stack: GatewayStack,
    viewer_token: str,
) -> None:
    async with httpx.AsyncClient() as client:
        await reset_downstream(client, gateway_stack)
        before = await downstream_count(client, gateway_stack)
        response = await client.post(
            f"{gateway_stack.gateway_url}/mcp",
            json=rpc_request("lower-bearer", "tools/list", {}),
            headers={"Authorization": f"bearer {viewer_token}"},
        )
        after = await downstream_count(client, gateway_stack)
    assert response.status_code == 200
    assert response.json()["id"] == "lower-bearer"
    assert (before, after) == (0, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("header", "message"),
    [
        ("Basic abc", "Bearer authentication required"),
        ("Bearer", "Bearer authentication required"),
        ("Bearer abc def", "Invalid bearer token"),
    ],
)
async def test_malformed_authorization_headers_rejected_before_forwarding(
    gateway_stack: GatewayStack,
    header: str,
    message: str,
) -> None:
    async with httpx.AsyncClient() as client:
        await reset_downstream(client, gateway_stack)
        before = await downstream_count(client, gateway_stack)
        response = await client.post(
            f"{gateway_stack.gateway_url}/mcp",
            json=rpc_request("bad-auth-header", "tools/list", {}),
            headers={"Authorization": header},
        )
        after = await downstream_count(client, gateway_stack)
    assert response.status_code == 401
    assert response.json()["error"] == {
        "code": -32000,
        "message": message,
    }
    assert before == after


@pytest.mark.asyncio
async def test_viewer_can_call_regular_tool(
    gateway_stack: GatewayStack,
    viewer_token: str,
) -> None:
    request = rpc_request(
        "viewer-customer",
        "tools/call",
        {"name": "get_customer_record", "arguments": {"customer_id": "CUST-12345"}},
    )
    async with httpx.AsyncClient() as client:
        await reset_downstream(client, gateway_stack)
        before = await downstream_count(client, gateway_stack)
        response = await client.post(
            f"{gateway_stack.gateway_url}/mcp",
            json=request,
            headers=authorization(viewer_token),
        )
        assert response.status_code == 200
        assert response.json()["result"]["structuredContent"]["customer_id"] == "CUST-12345"
        after = await downstream_count(client, gateway_stack)
        assert (before, after) == (0, 1)
        assert (await downstream_log(client, gateway_stack))["invocations"][0]["tool"] == (
            "get_customer_record"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    ["admin_", "admin_reset_key", "admin_rotate_secret", "admin__nested"],
)
async def test_every_viewer_admin_prefix_call_is_blocked_locally_and_preserves_id(
    gateway_stack: GatewayStack,
    viewer_token: str,
    tool_name: str,
) -> None:
    request_id = f"blocked-{tool_name}"
    request = rpc_request(
        request_id,
        "tools/call",
        {"name": tool_name, "arguments": {}},
    )
    async with httpx.AsyncClient() as client:
        await reset_downstream(client, gateway_stack)
        before = await downstream_count(client, gateway_stack)
        response = await client.post(
            f"{gateway_stack.gateway_url}/mcp",
            json=request,
            headers=authorization(viewer_token),
        )
        assert response.status_code == 200
        after = await downstream_count(client, gateway_stack)
        assert response.json() == {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32001, "message": "Unauthorized Tool Call"},
        }
        assert before == after


@pytest.mark.asyncio
async def test_admin_can_call_admin_tool(
    gateway_stack: GatewayStack,
    admin_token: str,
) -> None:
    request = rpc_request(
        44,
        "tools/call",
        {"name": "admin_reset_key", "arguments": {}},
    )
    async with httpx.AsyncClient() as client:
        await reset_downstream(client, gateway_stack)
        before = await downstream_count(client, gateway_stack)
        response = await client.post(
            f"{gateway_stack.gateway_url}/mcp",
            json=request,
            headers=authorization(admin_token),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == 44
        assert body["result"]["structuredContent"] == {
            "tool": "admin_reset_key",
            "status": "simulated",
        }
        after = await downstream_count(client, gateway_stack)
        assert (before, after) == (0, 1)
        assert (await downstream_log(client, gateway_stack))["invocations"][0]["tool"] == (
            "admin_reset_key"
        )


@pytest.mark.asyncio
async def test_forwarding_preserves_request_and_response_semantics(
    gateway_stack: GatewayStack,
    viewer_token: str,
) -> None:
    request = rpc_request(
        "semantic-id",
        "tools/call",
        {
            "name": "get_customer_record",
            "arguments": {"customer_id": "CUST-00001"},
        },
    )
    async with httpx.AsyncClient() as client:
        direct = await client.post(f"{gateway_stack.downstream_url}/mcp", json=request)
        await reset_downstream(client, gateway_stack)
        proxied = await client.post(
            f"{gateway_stack.gateway_url}/mcp",
            json=request,
            headers=authorization(viewer_token),
        )
        invocation_log = (
            await client.get(f"{gateway_stack.downstream_url}/__test__/requests")
        ).json()
    assert proxied.status_code == direct.status_code
    assert proxied.json() == direct.json()
    assert invocation_log == {
        "count": 1,
        "invocations": [
            {
                "id": "semantic-id",
                "method": "tools/call",
                "params": request["params"],
                "tool": "get_customer_record",
            }
        ],
    }


@pytest.mark.asyncio
async def test_missing_token_rejected_before_forwarding(gateway_stack: GatewayStack) -> None:
    async with httpx.AsyncClient() as client:
        await reset_downstream(client, gateway_stack)
        before = await downstream_count(client, gateway_stack)
        response = await client.post(
            f"{gateway_stack.gateway_url}/mcp",
            json=rpc_request(50, "tools/list", {}),
        )
        assert response.status_code == 401
        assert response.json()["error"] == {
            "code": -32000,
            "message": "Bearer authentication required",
        }
        after = await downstream_count(client, gateway_stack)
        assert before == after


def _replace_payload_role_without_resigning(token: str, role: str) -> str:
    header, payload, signature = token.split(".")
    decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    decoded["role"] = role
    forged_payload = base64.urlsafe_b64encode(
        json.dumps(decoded, separators=(",", ":")).encode()
    ).rstrip(b"=")
    return f"{header}.{forged_payload.decode()}.{signature}"


def invalid_tokens() -> list[Any]:
    now = datetime.now(UTC)
    expired = create_development_token(
        "viewer",
        lifetime=timedelta(seconds=-1),
        secret=TEST_SECRET,
    )
    valid = create_development_token("viewer", secret=TEST_SECRET)
    header, payload, signature = valid.split(".")
    replacement = "A" if signature[0] != "A" else "B"
    tampered = f"{header}.{payload}.{replacement}{signature[1:]}"
    unsigned = jwt.encode(
        {
            "sub": "unsigned-viewer",
            "role": "viewer",
            "iat": now,
            "exp": now + timedelta(hours=1),
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
        },
        key="",
        algorithm="none",
    )
    unsupported_role = jwt.encode(
        {
            "sub": "signed-superadmin",
            "role": "superadmin",
            "iat": now,
            "exp": now + timedelta(hours=1),
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
        },
        key=TEST_SECRET,
        algorithm="HS256",
    )
    missing_role = jwt.encode(
        {
            "sub": "signed-missing-role",
            "iat": now,
            "exp": now + timedelta(hours=1),
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
        },
        key=TEST_SECRET,
        algorithm="HS256",
    )
    attacker_signed_admin = jwt.encode(
        {
            "sub": "forged-admin",
            "role": "admin",
            "iat": now,
            "exp": now + timedelta(hours=1),
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
        },
        key="attacker-controlled-secret-that-is-long-enough",
        algorithm="HS256",
    )
    modified_viewer = _replace_payload_role_without_resigning(valid, "admin")
    return [
        pytest.param("not-a-jwt", id="malformed"),
        pytest.param(expired, id="expired"),
        pytest.param(tampered, id="tampered-signature"),
        pytest.param(unsigned, id="unsigned"),
        pytest.param(unsupported_role, id="unsupported-role"),
        pytest.param(missing_role, id="missing-role"),
        pytest.param(attacker_signed_admin, id="attacker-signed-admin"),
        pytest.param(modified_viewer, id="viewer-payload-changed-to-admin"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("token", invalid_tokens())
async def test_untrusted_token_variants_rejected_before_forwarding(
    gateway_stack: GatewayStack,
    token: str,
) -> None:
    async with httpx.AsyncClient() as client:
        await reset_downstream(client, gateway_stack)
        before = await downstream_count(client, gateway_stack)
        response = await client.post(
            f"{gateway_stack.gateway_url}/mcp",
            json=rpc_request(51, "tools/list", {}),
            headers=authorization(token),
        )
        assert response.status_code == 401
        assert response.json()["error"] == {
            "code": -32000,
            "message": "Invalid bearer token",
        }
        assert TEST_SECRET not in response.text
        after = await downstream_count(client, gateway_stack)
        assert before == after


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [None, {}, {"arguments": {}}, {"name": ""}, {"name": None}],
    ids=["null", "empty-object", "missing-name", "empty-name", "null-name"],
)
async def test_malformed_tool_call_params_rejected_without_forwarding(
    gateway_stack: GatewayStack,
    viewer_token: str,
    params: Any,
) -> None:
    async with httpx.AsyncClient() as client:
        await reset_downstream(client, gateway_stack)
        before = await downstream_count(client, gateway_stack)
        response = await client.post(
            f"{gateway_stack.gateway_url}/mcp",
            json={"jsonrpc": "2.0", "id": 52, "method": "tools/call", "params": params},
            headers=authorization(viewer_token),
        )
        after = await downstream_count(client, gateway_stack)
        assert response.status_code == 200
        assert response.json()["id"] == 52
        assert response.json()["error"]["code"] == -32602
        assert before == after
        assert TEST_SECRET not in response.text
        assert gateway_stack.downstream_url not in response.text
        assert "Traceback" not in response.text


@pytest.mark.asyncio
async def test_malformed_json_rejected_without_forwarding(
    gateway_stack: GatewayStack,
    viewer_token: str,
) -> None:
    async with httpx.AsyncClient() as client:
        await reset_downstream(client, gateway_stack)
        before = await downstream_count(client, gateway_stack)
        response = await client.post(
            f"{gateway_stack.gateway_url}/mcp",
            content=b"{not-json",
            headers={**authorization(viewer_token), "Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert response.json() == {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "Parse error"},
        }
        after = await downstream_count(client, gateway_stack)
        assert before == after


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"jsonrpc": "1.0", "id": 54, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 54, "params": {}},
        {"jsonrpc": "2.0", "id": 54, "method": 123, "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 54,
            "method": "tools/list",
            "params": {},
            "unexpected": True,
        },
    ],
    ids=["wrong-version", "missing-method", "non-string-method", "extra-envelope-field"],
)
async def test_malformed_jsonrpc_envelopes_rejected_without_forwarding(
    gateway_stack: GatewayStack,
    viewer_token: str,
    payload: dict[str, Any],
) -> None:
    async with httpx.AsyncClient() as client:
        await reset_downstream(client, gateway_stack)
        before = await downstream_count(client, gateway_stack)
        response = await client.post(
            f"{gateway_stack.gateway_url}/mcp",
            json=payload,
            headers=authorization(viewer_token),
        )
        after = await downstream_count(client, gateway_stack)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32600
    assert before == after


@pytest.mark.asyncio
async def test_downstream_connection_failure_is_sanitized(
    broken_gateway_url: str,
    viewer_token: str,
) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{broken_gateway_url}/mcp",
            json=rpc_request(53, "tools/list", {}),
            headers=authorization(viewer_token),
        )
    assert response.status_code == 502
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": 53,
        "error": {"code": -32002, "message": "Downstream service unavailable"},
    }
    assert "127.0.0.1" not in response.text
    assert TEST_SECRET not in response.text
    assert "Traceback" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        [
            rpc_request(60, "tools/list", {}),
            rpc_request(
                61,
                "tools/call",
                {"name": "admin_reset_key", "arguments": {}},
            ),
        ],
        {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "admin_reset_key", "arguments": {}},
        },
    ],
)
async def test_batches_and_notifications_cannot_bypass_authorization(
    gateway_stack: GatewayStack,
    viewer_token: str,
    payload: Any,
) -> None:
    async with httpx.AsyncClient() as client:
        await reset_downstream(client, gateway_stack)
        before = await downstream_count(client, gateway_stack)
        response = await client.post(
            f"{gateway_stack.gateway_url}/mcp",
            json=payload,
            headers=authorization(viewer_token),
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32600
        after = await downstream_count(client, gateway_stack)
        assert before == after


@pytest.mark.asyncio
async def test_admin_prefix_is_case_sensitive(
    gateway_stack: GatewayStack,
    viewer_token: str,
) -> None:
    async with httpx.AsyncClient() as client:
        await reset_downstream(client, gateway_stack)
        before = await downstream_count(client, gateway_stack)
        response = await client.post(
            f"{gateway_stack.gateway_url}/mcp",
            json=rpc_request(
                62,
                "tools/call",
                {"name": "Admin_reset_key", "arguments": {}},
            ),
            headers=authorization(viewer_token),
        )
        assert response.status_code == 200
        assert response.json()["error"]["code"] == -32602
        after = await downstream_count(client, gateway_stack)
        assert (before, after) == (0, 1)
