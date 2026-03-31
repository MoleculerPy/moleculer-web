"""Tests for moleculerpy_web.ratelimit module."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from moleculerpy_web.ratelimit import (
    MemoryStore,
    RateLimitConfig,
    default_key_extractor,
)


def _make_request(
    headers: dict[str, str] | None = None,
    client_host: str | None = "127.0.0.1",
) -> Any:
    """Create a minimal mock request for testing."""
    from unittest.mock import MagicMock

    from starlette.requests import Request

    req = MagicMock(spec=Request)
    req.headers = headers or {}
    if client_host:
        req.client = MagicMock()
        req.client.host = client_host
    else:
        req.client = None
    return req


# --- MemoryStore tests ---


@pytest.mark.asyncio
async def test_memory_store_increment_counts() -> None:
    """MemoryStore.increment counts correctly for the same key."""
    store = MemoryStore(window=60.0)
    assert await store.increment("ip1") == 1
    assert await store.increment("ip1") == 2
    assert await store.increment("ip1") == 3


@pytest.mark.asyncio
async def test_memory_store_increment_independent_keys() -> None:
    """Different keys have independent counters."""
    store = MemoryStore(window=60.0)
    assert await store.increment("ip1") == 1
    assert await store.increment("ip2") == 1
    assert await store.increment("ip1") == 2
    assert await store.increment("ip2") == 2


@pytest.mark.asyncio
async def test_memory_store_reset_time() -> None:
    """reset_time is set to now + window."""
    before = time.time()
    store = MemoryStore(window=30.0)
    after = time.time()
    assert before + 30.0 <= store.reset_time <= after + 30.0


@pytest.mark.asyncio
async def test_memory_store_start_stop() -> None:
    """Start creates a task, stop cancels it."""
    store = MemoryStore(window=0.05)
    await store.start()
    assert store._task is not None
    assert not store._task.done()

    await store.increment("k")
    assert await store.increment("k") == 2

    # Wait for reset
    await asyncio.sleep(0.1)
    # After reset, counter should be cleared
    assert await store.increment("k") == 1

    await store.stop()
    assert store._task is None


@pytest.mark.asyncio
async def test_memory_store_stop_idempotent() -> None:
    """Calling stop when not started is safe."""
    store = MemoryStore(window=60.0)
    await store.stop()  # Should not raise


# --- default_key_extractor tests ---


def test_key_extractor_x_forwarded_for() -> None:
    """X-Forwarded-For header is preferred."""
    req = _make_request(headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"})
    assert default_key_extractor(req) == "1.2.3.4"


def test_key_extractor_x_real_ip() -> None:
    """X-Real-IP is used when no X-Forwarded-For."""
    req = _make_request(headers={"x-real-ip": "10.0.0.1"})
    assert default_key_extractor(req) == "10.0.0.1"


def test_key_extractor_client_host_fallback() -> None:
    """Falls back to client.host."""
    req = _make_request(headers={}, client_host="192.168.1.1")
    assert default_key_extractor(req) == "192.168.1.1"


def test_key_extractor_no_client_returns_none() -> None:
    """Returns None when no client info available."""
    req = _make_request(headers={}, client_host=None)
    assert default_key_extractor(req) is None


# --- RateLimitConfig tests ---


def test_ratelimit_config_defaults() -> None:
    """RateLimitConfig has correct defaults."""
    cfg = RateLimitConfig()
    assert cfg.window == 60.0
    assert cfg.limit == 30
    assert cfg.headers is False
    assert cfg.key is default_key_extractor
