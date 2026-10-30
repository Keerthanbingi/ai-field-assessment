"""Atomic on-disk SQLite rolling-window token reservations."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

Clock = Callable[[], float]


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    used_tokens: int
    requested_tokens: int
    limit_tokens: int


class SQLiteTokenRateLimiter:
    def __init__(
        self,
        database_path: str | Path,
        *,
        limit_tokens: int = 50_000,
        window_seconds: float = 60.0,
        busy_timeout_ms: int = 5_000,
        clock: Clock = time.time,
    ) -> None:
        self.database_path = Path(database_path)
        if str(database_path) == ":memory:" or self.database_path.name == "":
            raise ValueError("Task 4 requires a real on-disk SQLite path")
        if limit_tokens <= 0 or window_seconds <= 0 or busy_timeout_ms <= 0:
            raise ValueError("rate limiter settings must be positive")
        self.limit_tokens = limit_tokens
        self.window_seconds = window_seconds
        self.busy_timeout_ms = busy_timeout_ms
        self._clock = clock
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS token_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    tenant_hash TEXT NOT NULL,
                    reserved_tokens INTEGER NOT NULL CHECK (reserved_tokens > 0),
                    reserved_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_token_reservations_tenant_time
                ON token_reservations (tenant_hash, reserved_at)
                """
            )

    def _reserve_sync(
        self,
        tenant_hash: str,
        requested_tokens: int,
        reservation_id: str,
    ) -> RateLimitDecision:
        if requested_tokens <= 0:
            raise ValueError("requested token reservation must be positive")
        now = float(self._clock())
        cutoff = now - self.window_seconds
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM token_reservations WHERE reserved_at <= ?",
                (cutoff,),
            )
            row = connection.execute(
                """
                SELECT COALESCE(SUM(reserved_tokens), 0)
                FROM token_reservations
                WHERE tenant_hash = ? AND reserved_at > ?
                """,
                (tenant_hash, cutoff),
            ).fetchone()
            used = int(row[0]) if row is not None else 0
            allowed = used + requested_tokens <= self.limit_tokens
            if allowed:
                connection.execute(
                    """
                    INSERT INTO token_reservations
                        (reservation_id, tenant_hash, reserved_tokens, reserved_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (reservation_id, tenant_hash, requested_tokens, now),
                )
            connection.commit()
            return RateLimitDecision(
                allowed=allowed,
                used_tokens=used,
                requested_tokens=requested_tokens,
                limit_tokens=self.limit_tokens,
            )
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def reserve(
        self,
        tenant_hash: str,
        requested_tokens: int,
        reservation_id: str,
    ) -> RateLimitDecision:
        return await asyncio.to_thread(
            self._reserve_sync,
            tenant_hash,
            requested_tokens,
            reservation_id,
        )

    def _usage_sync(self, tenant_hash: str) -> int:
        cutoff = float(self._clock()) - self.window_seconds
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(reserved_tokens), 0)
                FROM token_reservations
                WHERE tenant_hash = ? AND reserved_at > ?
                """,
                (tenant_hash, cutoff),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    async def usage(self, tenant_hash: str) -> int:
        return await asyncio.to_thread(self._usage_sync, tenant_hash)

    def _row_count_sync(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM token_reservations").fetchone()
        return int(row[0]) if row is not None else 0

    async def row_count(self) -> int:
        return await asyncio.to_thread(self._row_count_sync)
