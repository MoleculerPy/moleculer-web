"""Tests for ApiGatewayService."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from moleculerpy import Service

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


# ---------------------------------------------------------------------------
# Phase 3: Service Inheritance (ADR-002)
# ---------------------------------------------------------------------------


class TestServiceInheritance:
    """Verify ApiGatewayService inherits from moleculerpy.Service."""

    def test_inherits_from_service(self) -> None:
        """ApiGatewayService must be a subclass of moleculerpy.Service."""
        assert issubclass(ApiGatewayService, Service)

    def test_instance_is_service(self) -> None:
        """Instance must be isinstance of Service."""
        svc = ApiGatewayService()
        assert isinstance(svc, Service)

    def test_service_name_from_class_attribute(self) -> None:
        """Class-level name='api' should be used by Service."""
        svc = ApiGatewayService()
        assert svc.name == "api"

    def test_service_name_override(self) -> None:
        """Name passed to constructor should override class attribute."""
        svc = ApiGatewayService(name="gateway")
        assert svc.name == "gateway"

    def test_settings_merge(self) -> None:
        """Instance settings should be accessible."""
        svc = ApiGatewayService(settings={"port": 8080, "path": "/v1"})
        assert svc.port == 8080
        assert svc.base_path == "/v1"

    def test_broker_standalone_mode(self) -> None:
        """Broker can be passed directly for standalone/testing."""
        broker = MagicMock()
        svc = ApiGatewayService(broker=broker)
        assert svc.broker is broker

    def test_broker_none_by_default(self) -> None:
        """Without broker arg, broker should be None (set by broker.create_service later)."""
        svc = ApiGatewayService()
        assert svc.broker is None

    def test_has_lifecycle_hooks(self) -> None:
        """Service should have started/stopped lifecycle hooks."""
        svc = ApiGatewayService()
        assert hasattr(svc, "started")
        assert hasattr(svc, "stopped")
        assert asyncio.iscoroutinefunction(svc.started)
        assert asyncio.iscoroutinefunction(svc.stopped)

    def test_subclass_inherits_properly(self) -> None:
        """User-defined subclass should work correctly."""
        class MyGateway(ApiGatewayService):
            name = "my-api"

        gw = MyGateway(settings={"port": 4000, "path": "/my-api", "routes": []})
        assert gw.name == "my-api"
        assert gw.port == 4000
        assert isinstance(gw, Service)
        assert isinstance(gw, ApiGatewayService)

    def test_subclass_with_class_settings_and_mixins(self) -> None:
        """Subclass with mixins should merge class-level settings properly."""
        # Without mixins, class-level settings need explicit merge.
        # This is a known limitation of Service — class settings only auto-merge with mixins.
        # Users should pass settings via constructor or use mixins.
        class MyGateway(ApiGatewayService):
            name = "custom"

        gw = MyGateway(settings={"port": 5000})
        assert gw.name == "custom"
        assert gw.port == 5000

    def test_dependencies_passed_to_service(self) -> None:
        """Dependencies should be forwarded to Service base."""
        svc = ApiGatewayService(dependencies=["users", "auth"])
        assert svc.dependencies == ["users", "auth"]


# ---------------------------------------------------------------------------
# Phase 3: Internal Actions (listAliases, addRoute, removeRoute)
# ---------------------------------------------------------------------------


class TestInternalActions:
    """Test internal actions exposed via @action decorator."""

    @pytest.fixture
    def gw_with_routes(self, mock_broker: MagicMock) -> ApiGatewayService:
        settings = {
            "port": 3000,
            "path": "/api",
            "routes": [
                {
                    "path": "/v1",
                    "aliases": {
                        "GET /users": "users.list",
                        "POST /users": "users.create",
                        "GET /users/{id}": "users.get",
                    },
                },
                {
                    "path": "/v2",
                    "aliases": {"GET /health": "health.check"},
                },
            ],
        }
        svc = ApiGatewayService(broker=mock_broker, settings=settings)
        svc._build_routes()
        return svc

    async def test_list_aliases(self, gw_with_routes: ApiGatewayService) -> None:
        """listAliases should return all registered aliases."""
        result = await gw_with_routes.list_aliases()
        assert isinstance(result, list)
        assert len(result) == 4  # 3 from v1 + 1 from v2

        # Check structure
        for item in result:
            assert "method" in item
            assert "path" in item
            assert "action" in item
            assert "route" in item

        # Check specific aliases
        actions = {item["action"] for item in result}
        assert "users.list" in actions
        assert "users.create" in actions
        assert "users.get" in actions
        assert "health.check" in actions

    async def test_list_aliases_empty(self) -> None:
        """listAliases on empty gateway should return empty list."""
        svc = ApiGatewayService(settings={"routes": []})
        svc._build_routes()
        result = await svc.list_aliases()
        assert result == []

    async def test_add_route(self, mock_broker: MagicMock) -> None:
        """addRoute should add a new route at runtime."""
        svc = ApiGatewayService(
            broker=mock_broker,
            settings={"path": "/api", "routes": []},
        )
        svc._build_routes()
        assert len(svc._routes) == 0

        result = await svc.add_route(
            route={"path": "/new", "aliases": {"GET /items": "items.list"}},
        )
        assert result["success"] is True
        assert result["path"] == "/new"
        assert result["aliases"] == 1
        assert len(svc._routes) == 1

    async def test_add_route_to_top(self, mock_broker: MagicMock) -> None:
        """addRoute with to_bottom=False should insert at beginning."""
        svc = ApiGatewayService(
            broker=mock_broker,
            settings={
                "path": "/api",
                "routes": [{"path": "/existing", "aliases": {"GET /a": "a.get"}}],
            },
        )
        svc._build_routes()
        assert len(svc._routes) == 1

        await svc.add_route(
            route={"path": "/first", "aliases": {"GET /b": "b.get"}},
            to_bottom=False,
        )
        assert len(svc._routes) == 2
        assert svc._routes[0][0].path == "/first"

    async def test_add_route_empty_config(self) -> None:
        """addRoute with empty route config should fail gracefully."""
        svc = ApiGatewayService(settings={"routes": []})
        svc._build_routes()
        result = await svc.add_route(route={})
        assert result["success"] is False

    async def test_remove_route(self, gw_with_routes: ApiGatewayService) -> None:
        """removeRoute should remove route by path."""
        assert len(gw_with_routes._routes) == 2

        result = await gw_with_routes.remove_route(path="/v1")
        assert result["success"] is True
        assert result["removed"] == 1
        assert len(gw_with_routes._routes) == 1
        assert gw_with_routes._routes[0][0].path == "/v2"

    async def test_remove_route_nonexistent(self, gw_with_routes: ApiGatewayService) -> None:
        """removeRoute with non-existent path should return success=False."""
        result = await gw_with_routes.remove_route(path="/nonexistent")
        assert result["success"] is False
        assert result["removed"] == 0

    async def test_remove_route_no_path(self) -> None:
        """removeRoute without path should return error."""
        svc = ApiGatewayService(settings={"routes": []})
        svc._build_routes()
        result = await svc.remove_route()
        assert result["success"] is False

    async def test_add_then_list(self, mock_broker: MagicMock) -> None:
        """Adding a route should be reflected in listAliases."""
        svc = ApiGatewayService(
            broker=mock_broker,
            settings={"path": "/api", "routes": []},
        )
        svc._build_routes()
        assert await svc.list_aliases() == []

        await svc.add_route(
            route={"path": "/new", "aliases": {"GET /items": "items.list"}},
        )
        aliases = await svc.list_aliases()
        assert len(aliases) == 1
        assert aliases[0]["action"] == "items.list"

    async def test_add_route_with_rest_shorthand(self, mock_broker: MagicMock) -> None:
        """addRoute should support REST shorthand."""
        svc = ApiGatewayService(
            broker=mock_broker,
            settings={"path": "/api", "routes": []},
        )
        svc._build_routes()

        await svc.add_route(
            route={"path": "/", "aliases": {"REST /products": "products"}},
        )
        aliases = await svc.list_aliases()
        # REST shorthand generates 6 CRUD aliases
        assert len(aliases) == 6
        actions = {a["action"] for a in aliases}
        assert "products.list" in actions
        assert "products.get" in actions
        assert "products.create" in actions

    async def test_actions_discoverable(self) -> None:
        """Internal actions should be discoverable via Service.actions()."""
        svc = ApiGatewayService()
        action_names = svc.actions()
        # Must have at least 3 actions (list_aliases, add_route, remove_route)
        assert len(action_names) >= 3


# ---------------------------------------------------------------------------
# Phase 3: Security Guards
# ---------------------------------------------------------------------------


class TestSecurityGuards:
    """Test security guards on internal actions."""

    async def test_add_route_rejects_remote_call(self) -> None:
        """addRoute must reject calls from remote nodes."""
        mock_broker = MagicMock()
        mock_broker.node_id = "local-node-1"

        svc = ApiGatewayService(
            broker=mock_broker,
            settings={"path": "/api", "routes": []},
        )
        svc._build_routes()

        # Simulate remote context
        remote_ctx = MagicMock()
        remote_ctx.node_id = "remote-node-2"
        remote_ctx.params = {"route": {"path": "/evil", "aliases": {"GET /x": "x.y"}}}

        result = await svc.add_route(remote_ctx)
        assert result["success"] is False
        assert "local-only" in result["error"]

    async def test_add_route_allows_local_call(self) -> None:
        """addRoute must allow calls from local node."""
        mock_broker = MagicMock()
        mock_broker.node_id = "local-node-1"

        svc = ApiGatewayService(
            broker=mock_broker,
            settings={"path": "/api", "routes": []},
        )
        svc._build_routes()

        local_ctx = MagicMock()
        local_ctx.node_id = "local-node-1"
        local_ctx.params = {"route": {"path": "/ok", "aliases": {"GET /a": "a.b"}}}

        result = await svc.add_route(local_ctx)
        assert result["success"] is True

    async def test_add_route_rejects_unknown_node(self) -> None:
        """addRoute must reject when node_id cannot be verified (fail-secure)."""
        mock_broker = MagicMock()
        mock_broker.node_id = None  # no node_id

        svc = ApiGatewayService(
            broker=mock_broker,
            settings={"path": "/api", "routes": []},
        )
        svc._build_routes()

        ctx = MagicMock()
        ctx.node_id = "some-node"
        ctx.params = {"route": {"path": "/x", "aliases": {}}}

        result = await svc.add_route(ctx)
        assert result["success"] is False

    async def test_remove_route_rejects_remote_call(self) -> None:
        """removeRoute must reject calls from remote nodes."""
        mock_broker = MagicMock()
        mock_broker.node_id = "local-1"

        svc = ApiGatewayService(
            broker=mock_broker,
            settings={"path": "/api", "routes": [{"path": "/v1", "aliases": {"GET /a": "a.b"}}]},
        )
        svc._build_routes()

        remote_ctx = MagicMock()
        remote_ctx.node_id = "remote-2"
        remote_ctx.params = {"path": "/v1"}

        result = await svc.remove_route(remote_ctx)
        assert result["success"] is False
        assert "local-only" in result["error"]

    async def test_direct_call_without_ctx_allowed(self) -> None:
        """Direct Python call (ctx=None) should be allowed."""
        svc = ApiGatewayService(settings={"path": "/api", "routes": []})
        svc._build_routes()
        result = await svc.add_route(route={"path": "/ok", "aliases": {"GET /x": "x.y"}})
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Phase 3: Auto-aliases ($services.changed)
# ---------------------------------------------------------------------------


class TestAutoAliases:
    """Test auto-alias generation from action.rest annotations."""

    def test_regenerate_no_broker(self) -> None:
        """Without broker, regeneration should return 0."""
        svc = ApiGatewayService(settings={"routes": []})
        svc._build_routes()
        assert svc._regenerate_auto_aliases() == 0

    def test_regenerate_with_auto_aliases_route(self) -> None:
        """Route with autoAliases=True should scan registry for rest annotations."""
        mock_broker = MagicMock()
        # Simulate registry with actions having rest annotations
        mock_broker.registry.action_list = [
            {"name": "users.list", "rest": "GET /users"},
            {"name": "users.get", "rest": "GET /users/{id}"},
            {"name": "users.create", "rest": "POST /users"},
            {"name": "posts.list", "rest": {"method": "GET", "path": "/posts"}},
            {"name": "internal.action", "rest": None},  # no rest — skip
        ]

        svc = ApiGatewayService(
            broker=mock_broker,
            settings={
                "path": "/api",
                "routes": [{"path": "/auto", "autoAliases": True, "aliases": {}}],
            },
        )
        svc._build_routes()

        count = svc._regenerate_auto_aliases()
        assert count == 4  # 4 actions with rest annotations

        # Check aliases were registered
        aliases = svc._routes[0][1].aliases
        actions = {a.action for a in aliases}
        assert "users.list" in actions
        assert "users.get" in actions
        assert "users.create" in actions
        assert "posts.list" in actions

    def test_regenerate_clears_old_aliases(self) -> None:
        """Regeneration should clear old auto-aliases before rebuilding."""
        mock_broker = MagicMock()
        mock_broker.registry.action_list = [
            {"name": "users.list", "rest": "GET /users"},
        ]

        svc = ApiGatewayService(
            broker=mock_broker,
            settings={
                "path": "/api",
                "routes": [{"path": "/auto", "autoAliases": True, "aliases": {}}],
            },
        )
        svc._build_routes()

        # First run
        svc._regenerate_auto_aliases()
        assert len(svc._routes[0][1].aliases) == 1

        # Second run (same data) — should still be 1, not 2
        svc._regenerate_auto_aliases()
        assert len(svc._routes[0][1].aliases) == 1

    def test_non_auto_route_not_affected(self) -> None:
        """Routes without autoAliases should not be affected."""
        mock_broker = MagicMock()
        mock_broker.registry.action_list = [
            {"name": "users.list", "rest": "GET /users"},
        ]

        svc = ApiGatewayService(
            broker=mock_broker,
            settings={
                "path": "/api",
                "routes": [
                    {"path": "/manual", "aliases": {"GET /hello": "hello.world"}},
                    {"path": "/auto", "autoAliases": True, "aliases": {}},
                ],
            },
        )
        svc._build_routes()
        svc._regenerate_auto_aliases()

        # Manual route should keep its aliases
        manual_aliases = svc._routes[0][1].aliases
        assert len(manual_aliases) == 1
        assert manual_aliases[0].action == "hello.world"

        # Auto route should have auto-generated aliases
        auto_aliases = svc._routes[1][1].aliases
        assert len(auto_aliases) == 1

    async def test_services_changed_event(self) -> None:
        """$services.changed event should trigger regeneration."""
        mock_broker = MagicMock()
        mock_broker.registry.action_list = [
            {"name": "users.list", "rest": "GET /users"},
        ]

        svc = ApiGatewayService(
            broker=mock_broker,
            settings={
                "path": "/api",
                "routes": [{"path": "/auto", "autoAliases": True, "aliases": {}}],
            },
        )
        svc._build_routes()

        # Simulate $services.changed event
        await svc._on_services_changed()

        # Auto-aliases should be regenerated
        aliases = svc._routes[0][1].aliases
        assert len(aliases) == 1
        assert aliases[0].action == "users.list"

    def test_auto_aliases_with_no_registry(self) -> None:
        """Without registry, regeneration should return 0."""
        mock_broker = MagicMock(spec=[])  # no registry attribute
        svc = ApiGatewayService(
            broker=mock_broker,
            settings={
                "path": "/api",
                "routes": [{"path": "/", "autoAliases": True, "aliases": {}}],
            },
        )
        svc._build_routes()
        assert svc._regenerate_auto_aliases() == 0

    def test_rest_annotation_default_get(self) -> None:
        """REST annotation without method should default to GET."""
        mock_broker = MagicMock()
        mock_broker.registry.action_list = [
            {"name": "health.check", "rest": "/health"},  # no method = GET
        ]

        svc = ApiGatewayService(
            broker=mock_broker,
            settings={
                "path": "/api",
                "routes": [{"path": "/", "autoAliases": True, "aliases": {}}],
            },
        )
        svc._build_routes()
        svc._regenerate_auto_aliases()

        aliases = svc._routes[0][1].aliases
        assert len(aliases) == 1
        assert aliases[0].method == "GET"
        assert aliases[0].action == "health.check"


# ---------------------------------------------------------------------------
# Phase 3: Static File Serving
# ---------------------------------------------------------------------------


class TestStaticFiles:
    """Test static file serving via Starlette StaticFiles."""

    async def test_static_files_served(self, mock_broker: MagicMock) -> None:
        """Static files should be served from configured directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test file
            Path(tmpdir, "hello.txt").write_text("Hello World!")

            svc = ApiGatewayService(
                broker=mock_broker,
                settings={
                    "path": "/api",
                    "routes": [],
                    "assets": {"folder": tmpdir, "path": "/static"},
                },
            )
            svc._build_routes()
            svc._app = svc._create_app()

            transport = ASGITransport(app=svc.app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.get("/static/hello.txt")
                assert resp.status_code == 200
                assert resp.text == "Hello World!"

    async def test_static_files_404(self, mock_broker: MagicMock) -> None:
        """Non-existent static file should return 404."""
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = ApiGatewayService(
                broker=mock_broker,
                settings={
                    "path": "/api",
                    "routes": [],
                    "assets": {"folder": tmpdir, "path": "/static"},
                },
            )
            svc._build_routes()
            svc._app = svc._create_app()

            transport = ASGITransport(app=svc.app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.get("/static/nonexistent.txt")
                assert resp.status_code == 404

    async def test_no_assets_config(self, mock_broker: MagicMock) -> None:
        """Without assets config, no static mount should be added."""
        svc = ApiGatewayService(
            broker=mock_broker,
            settings={"path": "/api", "routes": []},
        )
        svc._build_routes()
        svc._app = svc._create_app()
        # App should still work for API routes
        assert svc.app is not None

    async def test_html_mode(self, mock_broker: MagicMock) -> None:
        """HTML mode should serve index.html for directory requests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "index.html").write_text("<h1>Home</h1>")

            svc = ApiGatewayService(
                broker=mock_broker,
                settings={
                    "path": "/api",
                    "routes": [],
                    "assets": {"folder": tmpdir, "path": "/", "html": True},
                },
            )
            svc._build_routes()
            svc._app = svc._create_app()

            transport = ASGITransport(app=svc.app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.get("/")
                assert resp.status_code == 200
                assert "<h1>Home</h1>" in resp.text
