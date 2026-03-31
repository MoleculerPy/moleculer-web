"""Tests for ApiGatewayService."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from moleculerpy_web.service import ApiGatewayService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def gateway_settings() -> dict:
    return {
        "port": 3000,
        "ip": "127.0.0.1",
        "path": "/api",
        "routes": [
            {
                "path": "/",
                "mappingPolicy": "restrict",
                "aliases": {
                    "GET /users": "users.list",
                    "GET /users/{id}": "users.get",
                    "POST /users": "users.create",
                },
            }
        ],
    }


@pytest.fixture
def mock_broker() -> MagicMock:
    broker = MagicMock()
    broker.call = AsyncMock(return_value={"id": 1, "name": "John"})
    return broker


@pytest.fixture
def gateway(mock_broker: MagicMock, gateway_settings: dict) -> ApiGatewayService:
    svc = ApiGatewayService(broker=mock_broker, settings=gateway_settings)
    svc._build_routes()
    svc._app = svc._create_app()
    return svc


@pytest.fixture
async def client(gateway: ApiGatewayService) -> AsyncClient:
    transport = ASGITransport(app=gateway.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Settings / properties
# ---------------------------------------------------------------------------


class TestSettings:
    def test_default_port(self) -> None:
        svc = ApiGatewayService()
        assert svc.port == 3000

    def test_default_ip(self) -> None:
        svc = ApiGatewayService()
        assert svc.ip == "0.0.0.0"

    def test_default_base_path(self) -> None:
        svc = ApiGatewayService()
        assert svc.base_path == "/api"

    def test_custom_settings(self, gateway_settings: dict) -> None:
        svc = ApiGatewayService(settings=gateway_settings)
        assert svc.port == 3000
        assert svc.ip == "127.0.0.1"
        assert svc.base_path == "/api"

    def test_name_override(self) -> None:
        svc = ApiGatewayService(name="gateway")
        assert svc.name == "gateway"

    def test_default_name(self) -> None:
        svc = ApiGatewayService()
        assert svc.name == "api"

    def test_broker_stored(self, mock_broker: MagicMock) -> None:
        svc = ApiGatewayService(broker=mock_broker)
        assert svc.broker is mock_broker


# ---------------------------------------------------------------------------
# Route building
# ---------------------------------------------------------------------------


class TestBuildRoutes:
    def test_build_routes_creates_resolvers(self, gateway: ApiGatewayService) -> None:
        assert len(gateway._routes) == 1
        route_config, resolver = gateway._routes[0]
        assert route_config.path == "/"
        assert route_config.mapping_policy == "restrict"
        # Resolver has aliases registered with relative paths
        match = resolver.resolve("GET", "/users")
        assert match is not None
        assert match.action == "users.list"

    def test_build_routes_clears_previous(self, gateway: ApiGatewayService) -> None:
        gateway._build_routes()
        assert len(gateway._routes) == 1

    def test_build_routes_empty_settings(self) -> None:
        svc = ApiGatewayService(settings={"routes": []})
        svc._build_routes()
        assert svc._routes == []

    def test_build_routes_multiple_routes(self, mock_broker: MagicMock) -> None:
        settings = {
            "path": "/api",
            "routes": [
                {
                    "path": "/v1",
                    "aliases": {"GET /health": "health.check"},
                },
                {
                    "path": "/v2",
                    "aliases": {"GET /health": "health.check.v2"},
                },
            ],
        }
        svc = ApiGatewayService(broker=mock_broker, settings=settings)
        svc._build_routes()
        assert len(svc._routes) == 2


# ---------------------------------------------------------------------------
# HTTP request handling
# ---------------------------------------------------------------------------


class TestRequestHandling:
    async def test_get_users_list(
        self,
        client: AsyncClient,
        mock_broker: MagicMock,
    ) -> None:
        resp = await client.get("/api/users")
        assert resp.status_code == 200
        assert resp.json() == {"id": 1, "name": "John"}
        mock_broker.call.assert_awaited_once_with("users.list", {}, meta={})

    async def test_get_user_by_id(
        self,
        client: AsyncClient,
        mock_broker: MagicMock,
    ) -> None:
        resp = await client.get("/api/users/42")
        assert resp.status_code == 200
        mock_broker.call.assert_awaited_once()
        call_args = mock_broker.call.call_args
        assert call_args[0][0] == "users.get"
        assert call_args[0][1]["id"] == "42"

    async def test_post_users_with_json_body(
        self,
        client: AsyncClient,
        mock_broker: MagicMock,
    ) -> None:
        body = {"name": "Alice", "email": "alice@test.com"}
        resp = await client.post(
            "/api/users",
            json=body,
        )
        assert resp.status_code == 200
        call_args = mock_broker.call.call_args
        assert call_args[0][0] == "users.create"
        assert call_args[0][1]["name"] == "Alice"
        assert call_args[0][1]["email"] == "alice@test.com"

    async def test_not_found_unknown_route(
        self,
        client: AsyncClient,
    ) -> None:
        resp = await client.get("/api/unknown")
        assert resp.status_code == 404
        data = resp.json()
        assert data["name"] == "NotFoundError"
        assert data["code"] == 404
        assert data["type"] == "NOT_FOUND"

    async def test_query_params_passed_to_action(
        self,
        client: AsyncClient,
        mock_broker: MagicMock,
    ) -> None:
        resp = await client.get("/api/users?limit=10&offset=0")
        assert resp.status_code == 200
        call_args = mock_broker.call.call_args
        assert call_args[0][1]["limit"] == "10"
        assert call_args[0][1]["offset"] == "0"


# ---------------------------------------------------------------------------
# Multiple routes
# ---------------------------------------------------------------------------


class TestMultipleRoutes:
    async def test_multiple_route_prefixes(self, mock_broker: MagicMock) -> None:
        settings = {
            "path": "/api",
            "routes": [
                {
                    "path": "/v1",
                    "aliases": {"GET /ping": "v1.ping"},
                },
                {
                    "path": "/v2",
                    "aliases": {"GET /ping": "v2.ping"},
                },
            ],
        }
        svc = ApiGatewayService(broker=mock_broker, settings=settings)
        svc._build_routes()
        svc._app = svc._create_app()

        transport = ASGITransport(app=svc.app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/v1/ping")
            assert resp.status_code == 200
            mock_broker.call.assert_awaited_with("v1.ping", {}, meta={})

            mock_broker.call.reset_mock()
            resp = await c.get("/api/v2/ping")
            assert resp.status_code == 200
            mock_broker.call.assert_awaited_with("v2.ping", {}, meta={})


# ---------------------------------------------------------------------------
# Mapping policy
# ---------------------------------------------------------------------------


class TestMappingPolicy:
    async def test_mapping_policy_all(self, mock_broker: MagicMock) -> None:
        settings = {
            "path": "/api",
            "routes": [
                {
                    "path": "/",
                    "mappingPolicy": "all",
                    "aliases": {},
                }
            ],
        }
        svc = ApiGatewayService(broker=mock_broker, settings=settings)
        svc._build_routes()
        svc._app = svc._create_app()

        transport = ASGITransport(app=svc.app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/posts/list")
            assert resp.status_code == 200
            mock_broker.call.assert_awaited_once_with("posts.list", {}, meta={})


# ---------------------------------------------------------------------------
# Error response format
# ---------------------------------------------------------------------------


class TestErrorResponseFormat:
    async def test_error_response_structure(self, client: AsyncClient) -> None:
        resp = await client.get("/api/nonexistent")
        assert resp.status_code == 404
        data = resp.json()
        assert "name" in data
        assert "message" in data
        assert "code" in data
        assert "type" in data
        assert "data" in data
        assert isinstance(data["data"], dict)


# ---------------------------------------------------------------------------
# App property
# ---------------------------------------------------------------------------


class TestAppProperty:
    def test_app_none_before_create(self) -> None:
        svc = ApiGatewayService()
        assert svc.app is None

    def test_app_available_after_create(self, gateway: ApiGatewayService) -> None:
        assert gateway.app is not None


# ---------------------------------------------------------------------------
# Lifecycle (started / stopped)
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_started_creates_app_and_server(self) -> None:
        """started() should create Starlette app and start uvicorn."""
        import unittest.mock as _mock

        import uvicorn as _real_uvicorn

        gateway = ApiGatewayService(
            broker=MagicMock(),
            settings={
                "port": 9999,
                "ip": "127.0.0.1",
                "path": "/api",
                "routes": [],
            },
        )

        mock_server = MagicMock()
        mock_server.serve = AsyncMock()

        mock_config = MagicMock()

        with (
            _mock.patch.object(_real_uvicorn, "Config", return_value=mock_config) as patched_config,
            _mock.patch.object(_real_uvicorn, "Server", return_value=mock_server) as patched_server,
        ):
            await gateway.started()

            # Verify app created
            assert gateway.app is not None
            # Verify uvicorn.Config called with right params
            patched_config.assert_called_once()
            config_call = patched_config.call_args
            assert config_call.kwargs["port"] == 9999
            assert config_call.kwargs["host"] == "127.0.0.1"
            # Verify server started
            patched_server.assert_called_once()
            # Verify task created
            assert gateway._server_task is not None

            # Cleanup
            gateway._server_task.cancel()
            try:
                await gateway._server_task
            except asyncio.CancelledError:
                pass

    async def test_stopped_signals_exit(self) -> None:
        """stopped() should signal server to exit."""
        gateway = ApiGatewayService(broker=MagicMock(), settings={"routes": []})

        mock_server = MagicMock()
        mock_server.should_exit = False
        gateway._server = mock_server

        # Create a task that completes quickly
        async def fake_serve() -> None:
            await asyncio.sleep(0.01)

        gateway._server_task = asyncio.create_task(fake_serve())

        await gateway.stopped()

        assert mock_server.should_exit is True
        assert gateway._server_task is None

    async def test_stopped_cancels_on_timeout(self) -> None:
        """stopped() should cancel task if it doesn't stop in time."""
        import unittest.mock as _mock

        gateway = ApiGatewayService(broker=MagicMock(), settings={"routes": []})

        mock_server = MagicMock()
        gateway._server = mock_server

        # Create a task that hangs
        async def hang_forever() -> None:
            await asyncio.sleep(100)

        gateway._server_task = asyncio.create_task(hang_forever())

        # Patch asyncio.wait_for to raise TimeoutError
        with _mock.patch.object(asyncio, "wait_for", side_effect=TimeoutError):
            await gateway.stopped()

        assert gateway._server_task is None


# ---------------------------------------------------------------------------
# Bug fix regression tests
# ---------------------------------------------------------------------------


class TestBugFixes:
    async def test_prefix_boundary_apiary_vs_api(self) -> None:
        """Bug #3: /apiary should NOT match /api prefix."""
        broker = MagicMock()
        broker.call = AsyncMock(return_value={"ok": True})

        gateway = ApiGatewayService(
            broker=broker,
            settings={
                "port": 3000,
                "path": "/api",
                "routes": [{"path": "/", "aliases": {"GET /users": "users.list"}}],
            },
        )
        gateway._build_routes()
        gateway._app = gateway._create_app()

        transport = ASGITransport(app=gateway.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/users")
            assert resp.status_code == 200

            resp = await client.get("/apiary/users")
            assert resp.status_code == 404

    async def test_shared_settings_not_leaked(self) -> None:
        """Bug #4: settings should be per-instance, not shared class-level."""
        gw1 = ApiGatewayService()
        gw2 = ApiGatewayService()

        gw1.settings["port"] = 9999
        assert gw2.settings.get("port") != 9999

    async def test_json_array_body_returns_400(self) -> None:
        """Bug #2: JSON array body should return 400, not crash."""
        broker = MagicMock()
        broker.call = AsyncMock(return_value={"ok": True})

        gateway = ApiGatewayService(
            broker=broker,
            settings={
                "port": 3000,
                "path": "/api",
                "routes": [{"path": "/", "aliases": {"POST /data": "data.save"}}],
            },
        )
        gateway._build_routes()
        gateway._app = gateway._create_app()

        transport = ASGITransport(app=gateway.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/data",
                content=b"[1, 2, 3]",
                headers={"content-type": "application/json"},
            )
            assert resp.status_code == 400
            assert resp.json()["type"] == "INVALID_REQUEST_BODY"

    async def test_path_outside_base_path_returns_404(self) -> None:
        """Paths outside base_path must not invoke actions even with mappingPolicy=all."""
        broker = MagicMock()
        broker.call = AsyncMock(return_value={"ok": True})

        gateway = ApiGatewayService(
            broker=broker,
            settings={
                "port": 3000,
                "path": "/api",
                "routes": [{"path": "/", "mappingPolicy": "all", "aliases": {}}],
            },
        )
        gateway._build_routes()
        gateway._app = gateway._create_app()

        transport = ASGITransport(app=gateway.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # /outside/posts is NOT under /api — must be 404
            resp = await client.get("/outside/posts")
            assert resp.status_code == 404
            broker.call.assert_not_called()

            # /foo/bar is NOT under /api — must be 404
            resp = await client.get("/foo/bar")
            assert resp.status_code == 404
            broker.call.assert_not_called()

            # /api/posts/list IS under /api — should work with all policy
            resp = await client.get("/api/posts/list")
            assert resp.status_code == 200

    async def test_started_raises_on_timeout(self) -> None:
        """started() must raise RuntimeError if server doesn't bind in time."""
        import unittest.mock as _mock

        gateway = ApiGatewayService(
            broker=MagicMock(),
            settings={"port": 3000, "path": "/api", "routes": []},
        )

        mock_server = MagicMock()
        mock_server.started = False  # never becomes True

        # serve() hangs forever (never completes, never sets started=True)
        hang_event = asyncio.Event()

        async def hang_forever() -> None:
            await hang_event.wait()  # blocks until event is set

        mock_server.serve = hang_forever

        with _mock.patch("moleculerpy_web.service.uvicorn") as mock_uvicorn:
            mock_uvicorn.Config.return_value = MagicMock()
            mock_uvicorn.Server.return_value = mock_server

            with _mock.patch("moleculerpy_web.service.asyncio.sleep", return_value=None):
                with pytest.raises(RuntimeError, match="failed to start"):
                    await gateway.started()

            # Cleanup
            hang_event.set()
            if gateway._server_task:
                gateway._server_task.cancel()
                try:
                    await gateway._server_task
                except (asyncio.CancelledError, Exception):
                    pass
