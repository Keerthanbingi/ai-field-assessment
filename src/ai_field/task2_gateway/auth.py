"""JWT creation and verification for the Task 2 development gateway."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
from jwt import InvalidTokenError
from pydantic import BaseModel, ConfigDict, ValidationError

Role = Literal["admin", "viewer"]
JWT_ALGORITHM = "HS256"
JWT_ISSUER = "ai-field-assessment"
JWT_AUDIENCE = "mcp-security-gateway"


class AuthenticationError(Exception):
    """Raised when a bearer token cannot be trusted."""


class TokenClaims(BaseModel):
    """Claims trusted only after cryptographic JWT verification."""

    model_config = ConfigDict(strict=True, extra="ignore")

    sub: str
    role: Role
    exp: int
    iat: int
    iss: str
    aud: str


def development_signing_key() -> str:
    """Load the development-only signing key without providing an unsafe default."""
    secret = os.getenv("TASK2_JWT_SECRET")
    if secret is None or len(secret) < 32:
        raise RuntimeError("TASK2_JWT_SECRET must contain at least 32 characters")
    return secret


def create_development_token(
    role: Role,
    *,
    subject: str | None = None,
    lifetime: timedelta = timedelta(hours=1),
    secret: str | None = None,
) -> str:
    """Create a local HS256 token for tests and manual development only."""
    signing_key = secret if secret is not None else development_signing_key()
    if len(signing_key) < 32:
        raise ValueError("JWT signing key must contain at least 32 characters")

    now = datetime.now(UTC)
    payload = {
        "sub": subject or f"sample-{role}",
        "role": role,
        "iat": now,
        "exp": now + lifetime,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
    }
    return jwt.encode(payload, signing_key, algorithm=JWT_ALGORITHM)


def verify_token(token: str, secret: str) -> TokenClaims:
    """Verify signature and required claims before returning an authorized role."""
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["sub", "role", "iat", "exp", "iss", "aud"]},
        )
        return TokenClaims.model_validate(payload)
    except (InvalidTokenError, ValidationError) as error:
        raise AuthenticationError("Invalid bearer token") from error
