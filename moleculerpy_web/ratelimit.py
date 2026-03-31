"""Rate limiting for moleculerpy-web API Gateway.

Provides Node.js moleculer-web compatible rate limiting with
pluggable stores (memory, Redis) and configurable key extraction.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from starlette.requests import Request


def default_key_extractor(request: Request) -> str | None:
    """Extract rate limit key from request (default: client IP).

    Checks headers in order: X-Forwarded-For, X-Real-IP, client.host.
    Returns None to skip rate limiting for this request.

    Args:
        request: Incoming HTTP request.

    Returns:
        Client identifier string, or None to skip rate limiting.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    if request.client:
        return request.client.host
    return None


@dataclass
class RateLimitConfig:
    """Rate limit configuration (Node.js moleculer-web compatible).

    Attributes:
        window: Time window in seconds (default 60).
        limit: Max requests per window (default 30).
        headers: Send X-Rate-Limit-* response headers.
        key: Function to extract rate limit key from request.
    """

    window: float = 60.0
    limit: int = 30
    headers: bool = False
    key: Callable[[Request], str | None] = field(default_factory=lambda: default_key_extractor)


class RateLimitStore(Protocol):
    """Abstract rate limit store (can be swapped for Redis)."""

    async def increment(self, key: str) -> int:
        """Increment counter for key, return new count."""
        ...

    @property
    def reset_time(self) -> float:
        """Unix timestamp of next reset."""
        ...

    async def start(self) -> None:
        """Start the store."""
        ...

    async def stop(self) -> None:
        """Stop the store."""
        ...


class MemoryStore:
    """In-memory rate limit store with automatic periodic reset.

    Thread-safe for asyncio (single event loop).
    Compatible with Node.js moleculer-web memory-store.js.
    """

    def __init__(self, window: float) -> None:
        """Initialize memory store.

        Args:
            window: Reset interval in seconds.
        """
        self._hits: dict[str, int] = {}
        self._window = window
        self._reset_time = time.time() + window
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the periodic reset loop."""
        if self._task is None:
            self._task = asyncio.create_task(self._reset_loop())

    async def _reset_loop(self) -> None:
        """Periodically clear all counters."""
        try:
            while True:
                await asyncio.sleep(self._window)
                self._hits.clear()
                self._reset_time = time.time() + self._window
        except asyncio.CancelledError:
            pass

    async def increment(self, key: str) -> int:
        """Increment counter for key, return new count.

        Args:
            key: Rate limit key (e.g., client IP).

        Returns:
            New hit count for the key.
        """
        count = self._hits.get(key, 0) + 1
        self._hits[key] = count
        return count

    @property
    def reset_time(self) -> float:
        """Unix timestamp of next reset."""
        return self._reset_time

    async def stop(self) -> None:
        """Stop the reset loop."""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
