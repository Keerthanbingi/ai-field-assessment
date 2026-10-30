"""Single-fallback asynchronous model router."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx


class ProvidersUnavailable(Exception):
    """Raised after the bounded primary/secondary policy is exhausted."""


@dataclass(frozen=True)
class RoutedCompletion:
    payload: dict[str, Any]
    provider: str


class ModelFallbackRouter:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        primary_url: str,
        secondary_url: str,
        primary_timeout_seconds: float = 3.0,
    ) -> None:
        self._client = client
        self._primary_url = primary_url
        self._secondary_url = secondary_url
        self.primary_timeout_seconds = primary_timeout_seconds

    @staticmethod
    def _safe_payload(response: httpx.Response) -> dict[str, Any] | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    async def _primary(self, payload: dict[str, Any]) -> RoutedCompletion | None:
        try:
            async with asyncio.timeout(self.primary_timeout_seconds):
                response = await self._client.post(self._primary_url, json=payload)
        except (TimeoutError, httpx.RequestError):
            return None
        if not response.is_success:
            return None
        result = self._safe_payload(response)
        if result is None:
            return None
        return RoutedCompletion(payload=result, provider="primary")

    async def _secondary(self, payload: dict[str, Any]) -> RoutedCompletion:
        try:
            response = await self._client.post(self._secondary_url, json=payload)
        except httpx.RequestError as error:
            raise ProvidersUnavailable from error
        if not response.is_success:
            raise ProvidersUnavailable
        result = self._safe_payload(response)
        if result is None:
            raise ProvidersUnavailable
        return RoutedCompletion(payload=result, provider="secondary")

    async def complete(self, payload: dict[str, Any]) -> RoutedCompletion:
        primary = await self._primary(payload)
        if primary is not None:
            return primary
        return await self._secondary(payload)
