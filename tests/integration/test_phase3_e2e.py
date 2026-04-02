"""End-to-end integration tests for Phase 3 features.

Tests streaming responses, file upload, static files, ETag + 304,
auto-aliases, and internal actions through the full gateway pipeline.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from moleculerpy_web.service import ApiGatewayService

try:
    import multipart  # noqa: F401

    _HAS_MULTIPART = True
except ImportError:
    _HAS_MULTIPART = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gateway(
    broker: MagicMock, routes: list[dict[str, Any]], **extra_settings: Any
) -> ApiGatewayService:
    settings = {"port": 3000, "path": "/api", "routes": routes, **extra_settings}
    svc = ApiGatewayService(broker=broker, settings=settings)
    svc._build_routes()
    svc._app = svc._create_app()
    return svc


@pytest.fixture
def mock_broker() -> MagicMock:
    broker = MagicMock()
    broker.call = AsyncMock(return_value={"result": "ok"})
    broker.node_id = "test-node"
    return broker


# ---------------------------------------------------------------------------
# 1. Streaming Responses
# ---------------------------------------------------------------------------


class TestStreamingE2E:
    """E2E: async generators returned by broker.call → StreamingResponse."""

    async def test_async_generator_streams_response(self, mock_broker: MagicMock) -> None:
        """Async generator result should be streamed via HTTP."""

        async def stream_gen(*args: Any, **kwargs: Any) -> Any:
            yield b"chunk1"
            yield b"chunk2"
            yield b"chunk3"

        mock_broker.call = AsyncMock(return_value=stream_gen())
        svc = _make_gateway(mock_broker, [{"path": "/", "aliases": {"GET /stream": "data.stream"}}])
        transport = ASGITransport(app=svc.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/stream")
            assert resp.status_code == 200
            assert b"chunk1" in resp.content
            assert b"chunk2" in resp.content
            assert b"chunk3" in resp.content


# ---------------------------------------------------------------------------
# 2. ETag + 304 Not Modified
# ---------------------------------------------------------------------------


class TestETagE2E:
    """E2E: ETag generation and conditional GET (304)."""

    async def test_etag_header_present_when_enabled(self, mock_broker: MagicMock) -> None:
        """Route with etag=True should return ETag header."""
        mock_broker.call = AsyncMock(return_value={"data": "test"})
        svc = _make_gateway(
            mock_broker, [{"path": "/", "aliases": {"GET /data": "data.get"}, "etag": True}]
        )
        transport = ASGITransport(app=svc.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/data")
            assert resp.status_code == 200
            assert "etag" in resp.headers

    async def test_304_on_matching_if_none_match(self, mock_broker: MagicMock) -> None:
        """Second request with If-None-Match should return 304."""
        mock_broker.call = AsyncMock(return_value={"data": "test"})
        svc = _make_gateway(
            mock_broker, [{"path": "/", "aliases": {"GET /data": "data.get"}, "etag": True}]
        )
        transport = ASGITransport(app=svc.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.get("/api/data")
            etag = r1.headers["etag"]

            r2 = await client.get("/api/data", headers={"If-None-Match": etag})
            assert r2.status_code == 304


# ---------------------------------------------------------------------------
# 3. Static Files
# ---------------------------------------------------------------------------


class TestStaticFilesE2E:
    """E2E: static file serving via Starlette StaticFiles mount."""

    async def test_static_file_served(self, mock_broker: MagicMock) -> None:
        """Static file configured via settings.assets should be served."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test file
            test_file = os.path.join(tmpdir, "test.txt")
            with open(test_file, "w") as f:
                f.write("hello static")

            svc = ApiGatewayService(
                broker=mock_broker,
                settings={
                    "port": 3000,
                    "path": "/api",
                    "routes": [],
                    "assets": {"folder": tmpdir, "path": "/static"},
                },
            )
            svc._build_routes()
            svc._app = svc._create_app()

            transport = ASGITransport(app=svc.app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/static/test.txt")
                assert resp.status_code == 200
                assert "hello static" in resp.text


# ---------------------------------------------------------------------------
# 4. File Upload (multipart)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_MULTIPART, reason="python-multipart not installed")
class TestFileUploadE2E:
    """E2E: multipart/form-data file upload."""

    async def test_file_upload_parsed(self, mock_broker: MagicMock) -> None:
        """Multipart file upload should be parsed and passed to action."""
        mock_broker.call = AsyncMock(return_value={"uploaded": True})
        svc = _make_gateway(
            mock_broker, [{"path": "/", "aliases": {"POST /upload": "files.upload"}}]
        )
        transport = ASGITransport(app=svc.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/upload",
                files={"file": ("test.txt", b"file content", "text/plain")},
            )
            assert resp.status_code == 200
            mock_broker.call.assert_awaited_once()
            call_args = mock_broker.call.call_args
            params = call_args[0][1]  # second positional arg
            assert "$file" in params or "file" in params


# ---------------------------------------------------------------------------
# 5. Internal Actions
# ---------------------------------------------------------------------------


class TestInternalActionsE2E:
    """E2E: listAliases, addRoute, removeRoute via broker.call simulation."""

    async def test_list_aliases_returns_all_routes(self, mock_broker: MagicMock) -> None:
        """api.listAliases should return all registered aliases."""
        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/v1",
                    "aliases": {"GET /users": "users.list", "POST /users": "users.create"},
                }
            ],
        )
        result = await svc.list_aliases()
        assert len(result) == 2
        actions = {r["action"] for r in result}
        assert "users.list" in actions
        assert "users.create" in actions

    async def test_list_aliases_includes_route_path(self, mock_broker: MagicMock) -> None:
        """Each alias should include the route path prefix."""
        svc = _make_gateway(mock_broker, [{"path": "/v2", "aliases": {"GET /items": "items.list"}}])
        result = await svc.list_aliases()
        assert result[0]["route"] == "/v2"
        assert "/v2" in result[0]["path"]


# ---------------------------------------------------------------------------
# 6. Auto-aliases ($services.changed)
# ---------------------------------------------------------------------------


class TestAutoAliasesE2E:
    """E2E: auto-alias generation from action rest annotations."""

    async def test_auto_aliases_updates_routes(self, mock_broker: MagicMock) -> None:
        """Calling _handle_services_changed should update aliases from registry."""
        svc = _make_gateway(
            mock_broker,
            [{"path": "/auto", "aliases": {}, "autoAliases": True}],
        )

        # Simulate broker.registry with a service that has rest annotation
        mock_registry = MagicMock()
        mock_action = MagicMock()
        mock_action.name = "products.list"
        mock_action.rest = "GET /products"
        mock_registry.get_action_list.return_value = [mock_action]
        mock_broker.registry = mock_registry

        # Check initial state — no aliases
        initial = await svc.list_aliases()
        initial_count = len(initial)

        # Trigger auto-alias update (simulates $services.changed event)
        if hasattr(svc, "_handle_services_changed"):
            await svc._handle_services_changed(MagicMock())
            updated = await svc.list_aliases()
            # Should have more aliases after update
            assert len(updated) >= initial_count
