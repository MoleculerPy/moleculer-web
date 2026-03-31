"""Shared pytest fixtures for moleculerpy-web tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_broker() -> MagicMock:
    """Mock MoleculerPy broker with required interface."""
    broker = MagicMock()
    broker.node_id = "test-node-1"
    broker.call = AsyncMock(return_value={"result": "ok"})
    broker.get_logger = MagicMock(return_value=MagicMock())
    return broker


@pytest.fixture
def mock_broker_call(mock_broker: MagicMock) -> AsyncMock:
    """Direct access to mock broker.call for assertions."""
    return mock_broker.call


@pytest.fixture
def sample_route_config() -> dict[str, Any]:
    """Sample route configuration for testing."""
    return {
        "path": "/",
        "mappingPolicy": "restrict",
        "aliases": {
            "GET /users": "users.list",
            "GET /users/{id}": "users.get",
            "POST /users": "users.create",
            "PUT /users/{id}": "users.update",
            "DELETE /users/{id}": "users.remove",
        },
    }


@pytest.fixture
def sample_gateway_settings(sample_route_config: dict[str, Any]) -> dict[str, Any]:
    """Sample gateway settings for testing."""
    return {
        "port": 3000,
        "ip": "127.0.0.1",
        "path": "/api",
        "routes": [sample_route_config],
    }
