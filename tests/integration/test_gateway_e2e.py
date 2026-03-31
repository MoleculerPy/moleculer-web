"""End-to-end integration tests for moleculerpy-web API Gateway.

Verifies PRD-011 acceptance criteria (AC-1 through AC-4) and additional
integration scenarios via httpx.AsyncClient + ASGITransport.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from moleculerpy.errors import (
    RequestTimeoutError,
    ServiceNotFoundError,
    ValidationError,
)

from moleculerpy_web.service import ApiGatewayService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_broker() -> MagicMock:
    """Mock broker with async call."""
    broker = MagicMock()
    broker.call = AsyncMock(return_value={"result": "ok"})
    return broker


@pytest.fixture
async def gateway_math(mock_broker: MagicMock) -> ApiGatewayService:
    """Gateway with math service alias for AC-1."""
    mock_broker.call.return_value = {"result": 8}
    svc = ApiGatewayService(
        broker=mock_broker,
        settings={
            "port": 3000,
            "path": "/api",
            "routes": [
                {
                    "path": "/",
                    "aliases": {
                        "GET /math/add": "math.add",
                    },
                }
            ],
        },
    )
    svc._build_routes()
    svc._app = svc._create_app()
    return svc


@pytest.fixture
async def client_math(gateway_math: ApiGatewayService) -> AsyncClient:
    transport = ASGITransport(app=gateway_math.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def gateway_users(mock_broker: MagicMock) -> ApiGatewayService:
    """Gateway with full CRUD user aliases for AC-2/AC-3/AC-4."""
    mock_broker.call.return_value = {"id": 42, "name": "John"}
    svc = ApiGatewayService(
        broker=mock_broker,
        settings={
            "port": 3000,
            "path": "/api",
            "routes": [
                {
                    "path": "/",
                    "aliases": {
                        "GET /users": "users.list",
                        "GET /users/{id}": "users.get",
                        "POST /users": "users.create",
                        "PUT /users/{id}": "users.update",
                        "DELETE /users/{id}": "users.remove",
                    },
                }
            ],
        },
    )
    svc._build_routes()
    svc._app = svc._create_app()
    return svc


@pytest.fixture
async def client_users(gateway_users: ApiGatewayService) -> AsyncClient:
    transport = ASGITransport(app=gateway_users.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# AC-1: Basic REST API — GET /api/math/add?a=5&b=3
# ---------------------------------------------------------------------------


class TestAC1BasicRestApi:
    """AC-1: HTTP GET with query params calls correct action and returns result."""

    async def test_get_math_add_returns_200(self, client_math: AsyncClient) -> None:
        """GET /api/math/add?a=5&b=3 returns 200 with {result: 8}."""
        resp = await client_math.get("/api/math/add?a=5&b=3")
        assert resp.status_code == 200
        assert resp.json() == {"result": 8}

    async def test_get_math_add_calls_broker(
        self, client_math: AsyncClient, mock_broker: MagicMock
    ) -> None:
        """broker.call invoked with ('math.add', {'a': '5', 'b': '3'})."""
        await client_math.get("/api/math/add?a=5&b=3")
        mock_broker.call.assert_awaited_once_with("math.add", {"a": "5", "b": "3"}, meta={})


# ---------------------------------------------------------------------------
# AC-2: Path Parameters — GET /api/users/:id
# ---------------------------------------------------------------------------


class TestAC2PathParameters:
    """AC-2: Path parameters extracted and passed to action."""

    async def test_get_user_by_id_returns_200(self, client_users: AsyncClient) -> None:
        """GET /api/users/42 returns 200."""
        resp = await client_users.get("/api/users/42")
        assert resp.status_code == 200

    async def test_get_user_by_id_passes_params(
        self, client_users: AsyncClient, mock_broker: MagicMock
    ) -> None:
        """Action receives params {'id': '42'}."""
        await client_users.get("/api/users/42")
        call_args = mock_broker.call.call_args
        assert call_args[0][0] == "users.get"
        assert call_args[0][1]["id"] == "42"


# ---------------------------------------------------------------------------
# AC-3: POST with JSON Body
# ---------------------------------------------------------------------------


class TestAC3PostJsonBody:
    """AC-3: POST with JSON body passes all fields to action."""

    async def test_post_user_returns_200(self, client_users: AsyncClient) -> None:
        """POST /api/users with JSON body returns 200."""
        resp = await client_users.post(
            "/api/users",
            json={"name": "John", "email": "john@example.com"},
        )
        assert resp.status_code == 200

    async def test_post_user_passes_body_to_action(
        self, client_users: AsyncClient, mock_broker: MagicMock
    ) -> None:
        """Action receives params from JSON body."""
        await client_users.post(
            "/api/users",
            json={"name": "John", "email": "john@example.com"},
        )
        call_args = mock_broker.call.call_args
        assert call_args[0][0] == "users.create"
        assert call_args[0][1]["name"] == "John"
        assert call_args[0][1]["email"] == "john@example.com"


# ---------------------------------------------------------------------------
# AC-4: Error Handling — MoleculerPy errors mapped to HTTP
# ---------------------------------------------------------------------------


class TestAC4ErrorHandling:
    """AC-4: MoleculerPy errors produce correct HTTP error responses."""

    async def test_service_not_found_returns_404(
        self, client_users: AsyncClient, mock_broker: MagicMock
    ) -> None:
        """ServiceNotFoundError → 404 with NotFoundError body."""
        mock_broker.call.side_effect = ServiceNotFoundError("users.get")
        resp = await client_users.get("/api/users/42")
        assert resp.status_code == 404
        data = resp.json()
        assert data["name"] == "NotFoundError"
        assert data["code"] == 404

    async def test_validation_error_returns_422(
        self, client_users: AsyncClient, mock_broker: MagicMock
    ) -> None:
        """ValidationError → 422 with UnprocessableEntityError body."""
        mock_broker.call.side_effect = ValidationError("Invalid email")
        resp = await client_users.post(
            "/api/users",
            json={"name": "John", "email": "bad"},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["name"] == "UnprocessableEntityError"
        assert data["code"] == 422

    async def test_request_timeout_returns_504(
        self, client_users: AsyncClient, mock_broker: MagicMock
    ) -> None:
        """RequestTimeoutError → 504 with GatewayTimeoutError body."""
        mock_broker.call.side_effect = RequestTimeoutError("users.get", timeout=5.0)
        resp = await client_users.get("/api/users/42")
        assert resp.status_code == 504
        data = resp.json()
        assert data["name"] == "GatewayTimeoutError"
        assert data["code"] == 504

    async def test_unknown_error_returns_500(
        self, client_users: AsyncClient, mock_broker: MagicMock
    ) -> None:
        """Unhandled exception → 500."""
        mock_broker.call.side_effect = RuntimeError("unexpected")
        resp = await client_users.get("/api/users/42")
        assert resp.status_code == 500
        data = resp.json()
        assert data["name"] == "InternalServerError"
        assert data["code"] == 500


# ---------------------------------------------------------------------------
# 5. CRUD Workflow — all HTTP methods work
# ---------------------------------------------------------------------------


class TestCrudWorkflow:
    """Full CRUD: GET list, GET by id, POST create, PUT update, DELETE remove."""

    async def test_crud_lifecycle(self, client_users: AsyncClient, mock_broker: MagicMock) -> None:
        """All CRUD aliases resolve and call correct actions."""
        # GET list
        resp = await client_users.get("/api/users")
        assert resp.status_code == 200
        assert mock_broker.call.call_args[0][0] == "users.list"

        mock_broker.call.reset_mock()

        # POST create
        resp = await client_users.post("/api/users", json={"name": "Alice"})
        assert resp.status_code == 200
        assert mock_broker.call.call_args[0][0] == "users.create"

        mock_broker.call.reset_mock()

        # PUT update
        resp = await client_users.put("/api/users/1", json={"name": "Bob"})
        assert resp.status_code == 200
        assert mock_broker.call.call_args[0][0] == "users.update"
        assert mock_broker.call.call_args[0][1]["id"] == "1"

        mock_broker.call.reset_mock()

        # DELETE remove
        resp = await client_users.delete("/api/users/1")
        assert resp.status_code == 200
        assert mock_broker.call.call_args[0][0] == "users.remove"
        assert mock_broker.call.call_args[0][1]["id"] == "1"


# ---------------------------------------------------------------------------
# 6. Multiple Routes — /v1 and /v2
# ---------------------------------------------------------------------------


class TestMultipleRoutes:
    """Different route prefixes resolve to different actions."""

    async def test_v1_and_v2_routes(self, mock_broker: MagicMock) -> None:
        """Route /v1 and /v2 with separate aliases."""
        svc = ApiGatewayService(
            broker=mock_broker,
            settings={
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
            },
        )
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
# 7. Mapping Policy "restrict" — unknown path → 404
# ---------------------------------------------------------------------------


class TestMappingPolicyRestrict:
    """Restrict policy rejects unregistered paths."""

    async def test_unknown_path_returns_404(self, client_users: AsyncClient) -> None:
        """GET /api/unknown → 404."""
        resp = await client_users.get("/api/unknown")
        assert resp.status_code == 404
        data = resp.json()
        assert data["name"] == "NotFoundError"
        assert data["code"] == 404


# ---------------------------------------------------------------------------
# 8. Mapping Policy "all" — derive action from URL
# ---------------------------------------------------------------------------


class TestMappingPolicyAll:
    """'all' policy derives action name from URL path."""

    async def test_unknown_path_derives_action(self, mock_broker: MagicMock) -> None:
        """GET /api/posts/list → broker.call('posts.list', {})."""
        svc = ApiGatewayService(
            broker=mock_broker,
            settings={
                "path": "/api",
                "routes": [
                    {
                        "path": "/",
                        "mappingPolicy": "all",
                        "aliases": {},
                    }
                ],
            },
        )
        svc._build_routes()
        svc._app = svc._create_app()

        transport = ASGITransport(app=svc.app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/posts/list")
            assert resp.status_code == 200
            mock_broker.call.assert_awaited_once_with("posts.list", {}, meta={})


# ---------------------------------------------------------------------------
# 9. Path + Query + Body Merge
# ---------------------------------------------------------------------------


class TestParamMerge:
    """Path params, query params, and body all merge into action params."""

    async def test_path_query_body_merge(
        self, client_users: AsyncClient, mock_broker: MagicMock
    ) -> None:
        """POST /api/users/42?role=admin with body {name: John} merges all."""
        mock_broker.call.reset_mock()
        resp = await client_users.put(
            "/api/users/42?role=admin",
            json={"name": "John"},
        )
        assert resp.status_code == 200
        call_args = mock_broker.call.call_args
        params: dict[str, Any] = call_args[0][1]
        assert params["id"] == "42"
        assert params["role"] == "admin"
        assert params["name"] == "John"


# ---------------------------------------------------------------------------
# 10. Error Format Consistency
# ---------------------------------------------------------------------------


class TestErrorFormatConsistency:
    """All error responses have {name, message, code, type, data}."""

    async def test_404_error_has_all_fields(self, client_users: AsyncClient) -> None:
        """404 error response contains all required fields."""
        resp = await client_users.get("/api/nonexistent")
        data = resp.json()
        for field in ("name", "message", "code", "type", "data"):
            assert field in data, f"Missing field: {field}"
        assert isinstance(data["data"], dict)

    async def test_moleculer_error_has_all_fields(
        self, client_users: AsyncClient, mock_broker: MagicMock
    ) -> None:
        """Moleculer error mapped to HTTP has all required fields."""
        mock_broker.call.side_effect = ServiceNotFoundError("users.get")
        resp = await client_users.get("/api/users/42")
        data = resp.json()
        for field in ("name", "message", "code", "type", "data"):
            assert field in data, f"Missing field: {field}"
        assert isinstance(data["data"], dict)


# ---------------------------------------------------------------------------
# 11. Content-Type Header
# ---------------------------------------------------------------------------


class TestContentTypeHeader:
    """Success responses have application/json content type."""

    async def test_json_response_content_type(self, client_users: AsyncClient) -> None:
        """Successful JSON response has application/json content type."""
        resp = await client_users.get("/api/users")
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]

    async def test_error_response_content_type(self, client_users: AsyncClient) -> None:
        """Error response also has application/json content type."""
        resp = await client_users.get("/api/nonexistent")
        assert resp.status_code == 404
        assert "application/json" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# 12. Empty Body GET
# ---------------------------------------------------------------------------


class TestEmptyBodyGet:
    """GET requests with no body work correctly."""

    async def test_get_with_no_body_no_content_type(
        self, client_users: AsyncClient, mock_broker: MagicMock
    ) -> None:
        """GET without body does not cause parsing errors."""
        resp = await client_users.get("/api/users")
        assert resp.status_code == 200
        # Params should only contain query params (none here)
        mock_broker.call.assert_awaited_once_with("users.list", {}, meta={})
