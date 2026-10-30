"""API-key verification and irreversible tenant identifiers for Task 4."""

from __future__ import annotations

import hashlib
import hmac


class AuthenticationFailed(Exception):
    """Raised when a presented tenant API key is not configured."""


class TenantAuthenticator:
    def __init__(self, tenant_keys: dict[str, str], fingerprint_secret: str) -> None:
        if not tenant_keys:
            raise ValueError("at least one tenant API key is required")
        if len(fingerprint_secret) < 32:
            raise ValueError("tenant fingerprint secret must contain at least 32 characters")
        self._tenant_keys = dict(tenant_keys)
        self._fingerprint_secret = fingerprint_secret.encode()

    def authenticate(self, presented_key: str | None) -> str:
        if not presented_key:
            raise AuthenticationFailed
        matched_tenant: str | None = None
        for tenant_id, expected_key in self._tenant_keys.items():
            if hmac.compare_digest(presented_key, expected_key):
                matched_tenant = tenant_id
        if matched_tenant is None:
            raise AuthenticationFailed
        return hmac.new(
            self._fingerprint_secret,
            matched_tenant.encode(),
            hashlib.sha256,
        ).hexdigest()
