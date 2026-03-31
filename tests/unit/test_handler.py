"""Tests for moleculerpy_web.handler — request handling pipeline."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route
from starlette.testclient import TestClient

from moleculerpy_web.alias import AliasResolver
from moleculerpy_web.errors import GatewayError, NotFoundError
from moleculerpy_web.handler import build_response, create_error_response, handle_request

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_broker() -> MagicMock:
    broker = MagicMock()
    broker.call = AsyncMock(return_value={"id": 1, "name": "John"})
    return broker


@pytest.fixture
def alias_resolver() -> AliasResolver:
    resolver = AliasResolver()
    resolver.add_alias("GET", "/users", "users.list")
    resolver.add_alias("GET", "/users/{id}", "users.get")
    resolver.add_alias("POST", "/users", "users.create")
    resolver.add_alias("DELETE", "/users/{id}", "users.delete")
    return resolver


def _make_app(
    broker: Any,
    alias_resolver: AliasResolver,
    *,
    mapping_policy: str = "restrict",
    base_path: str = "/api",
    route_path: str = "/",
) -> Starlette:
    """Create a minimal Starlette app wrapping handle_request."""

    async def catch_all(request: Request) -> Any:
        try:
            return await handle_request(
                request,
                broker=broker,
                alias_resolver=alias_resolver,
                route_path=route_path,
                mapping_policy=mapping_policy,
                base_path=base_path,
            )
        except GatewayError as e:
            return await create_error_response(e)

    return Starlette(
        routes=[
            Route("/{path:path}", catch_all, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
        ],
    )


def _client(
    broker: Any,
    alias_resolver: AliasResolver,
    **kwargs: Any,
) -> TestClient:
    app = _make_app(broker, alias_resolver, **kwargs)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# build_response
# ---------------------------------------------------------------------------


class TestBuildResponse:
    def test_dict_returns_json(self) -> None:
        resp = build_response({"ok": True})
        assert resp.status_code == 200
        assert resp.media_type == "application/json"

    def test_str_returns_json(self) -> None:
        resp = build_response("hello")
        assert resp.status_code == 200
        assert resp.body == b'"hello"'

    def test_bytes_returns_octet_stream(self) -> None:
        resp = build_response(b"\x00\x01")
        assert resp.status_code == 200
        assert resp.media_type == "application/octet-stream"

    def test_none_returns_204(self) -> None:
        resp = build_response(None)
        assert resp.status_code == 204

    def test_custom_status_code(self) -> None:
        resp = build_response({"created": True}, status_code=201)
        assert resp.status_code == 201

    def test_custom_headers(self) -> None:
        resp = build_response("ok", headers={"X-Custom": "yes"})
        assert resp.headers.get("x-custom") == "yes"


# ---------------------------------------------------------------------------
# create_error_response
# ---------------------------------------------------------------------------


class TestCreateErrorResponse:
    @pytest.mark.asyncio
    async def test_returns_json_with_status(self) -> None:
        err = NotFoundError("not here")
        resp = await create_error_response(err)
        assert resp.status_code == 404
        assert resp.media_type == "application/json"


# ---------------------------------------------------------------------------
# handle_request — happy paths
# ---------------------------------------------------------------------------


class TestHandleRequestHappy:
    def test_get_with_query_params(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        client = _client(mock_broker, alias_resolver)
        resp = client.get("/api/users", params={"limit": "10"})
        assert resp.status_code == 200
        mock_broker.call.assert_called_once()
        call_args = mock_broker.call.call_args
        assert call_args[0][0] == "users.list"
        assert call_args[0][1]["limit"] == "10"

    def test_post_with_json_body(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        mock_broker.call = AsyncMock(return_value={"id": 2, "name": "Jane"})
        client = _client(mock_broker, alias_resolver)
        resp = client.post("/api/users", json={"name": "Jane", "email": "jane@test.com"})
        assert resp.status_code == 200
        call_args = mock_broker.call.call_args
        assert call_args[0][1]["name"] == "Jane"
        assert call_args[0][1]["email"] == "jane@test.com"

    def test_get_with_path_params(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        mock_broker.call = AsyncMock(return_value={"id": 42, "name": "Alice"})
        client = _client(mock_broker, alias_resolver)
        resp = client.get("/api/users/42")
        assert resp.status_code == 200
        call_args = mock_broker.call.call_args
        assert call_args[0][0] == "users.get"
        assert call_args[0][1]["id"] == "42"

    def test_path_and_query_params_merge(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        client = _client(mock_broker, alias_resolver)
        resp = client.get("/api/users/5", params={"fields": "name,email"})
        assert resp.status_code == 200
        call_args = mock_broker.call.call_args
        params = call_args[0][1]
        assert params["id"] == "5"
        assert params["fields"] == "name,email"


# ---------------------------------------------------------------------------
# handle_request — response types
# ---------------------------------------------------------------------------


class TestHandleRequestResponseTypes:
    def test_dict_result_json(self, mock_broker: MagicMock, alias_resolver: AliasResolver) -> None:
        mock_broker.call = AsyncMock(return_value={"status": "ok"})
        client = _client(mock_broker, alias_resolver)
        resp = client.get("/api/users")
        assert resp.json() == {"status": "ok"}

    def test_str_result_json(self, mock_broker: MagicMock, alias_resolver: AliasResolver) -> None:
        mock_broker.call = AsyncMock(return_value="plain text response")
        client = _client(mock_broker, alias_resolver)
        resp = client.get("/api/users")
        assert resp.json() == "plain text response"

    def test_bytes_result_octet_stream(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        mock_broker.call = AsyncMock(return_value=b"\xde\xad")
        client = _client(mock_broker, alias_resolver)
        resp = client.get("/api/users")
        assert resp.content == b"\xde\xad"

    def test_none_result_204(self, mock_broker: MagicMock, alias_resolver: AliasResolver) -> None:
        mock_broker.call = AsyncMock(return_value=None)
        client = _client(mock_broker, alias_resolver)
        resp = client.delete("/api/users/1")
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# handle_request — error handling
# ---------------------------------------------------------------------------


class TestHandleRequestErrors:
    def test_unknown_route_restrict_404(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        client = _client(mock_broker, alias_resolver, mapping_policy="restrict")
        resp = client.get("/api/nonexistent")
        assert resp.status_code == 404
        body = resp.json()
        assert body["name"] == "NotFoundError"
        assert body["type"] == "NOT_FOUND"
        mock_broker.call.assert_not_called()

    def test_unknown_route_all_derives_action(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        mock_broker.call = AsyncMock(return_value={"derived": True})
        client = _client(mock_broker, alias_resolver, mapping_policy="all")
        resp = client.get("/api/posts/recent")
        assert resp.status_code == 200
        call_args = mock_broker.call.call_args
        assert call_args[0][0] == "posts.recent"

    def test_broker_generic_exception_500(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        mock_broker.call = AsyncMock(side_effect=RuntimeError("something broke"))
        client = _client(mock_broker, alias_resolver)
        resp = client.get("/api/users")
        assert resp.status_code == 500
        body = resp.json()
        assert body["name"] == "InternalServerError"
        assert body["code"] == 500
        assert body["type"] == "INTERNAL_SERVER_ERROR"
        assert body["message"] == "Internal server error"

    def test_broker_gateway_error_passthrough(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        from moleculerpy_web.errors import UnprocessableEntityError

        mock_broker.call = AsyncMock(side_effect=UnprocessableEntityError("bad data"))
        client = _client(mock_broker, alias_resolver)
        resp = client.get("/api/users")
        assert resp.status_code == 422
        body = resp.json()
        assert body["name"] == "UnprocessableEntityError"

    def test_error_response_format_matches_nodejs(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """Error response must have: name, message, code, type, data."""
        mock_broker.call = AsyncMock(side_effect=RuntimeError("fail"))
        client = _client(mock_broker, alias_resolver)
        resp = client.get("/api/users")
        body = resp.json()
        assert set(body.keys()) == {"name", "message", "code", "type", "data"}


# ---------------------------------------------------------------------------
# handle_request — MoleculerError mapping
# ---------------------------------------------------------------------------


class TestMoleculerErrorMapping:
    def test_service_not_found_maps_to_404(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        from moleculerpy.errors import ServiceNotFoundError

        mock_broker.call = AsyncMock(side_effect=ServiceNotFoundError("users.list"))
        client = _client(mock_broker, alias_resolver)
        resp = client.get("/api/users")
        assert resp.status_code == 404

    def test_validation_error_maps_to_422(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        from moleculerpy.errors import ValidationError

        mock_broker.call = AsyncMock(side_effect=ValidationError("invalid params"))
        client = _client(mock_broker, alias_resolver)
        resp = client.get("/api/users")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# handle_request — edge cases
# ---------------------------------------------------------------------------


class TestHandleRequestEdgeCases:
    def test_delete_with_body_merges_params(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        mock_broker.call = AsyncMock(return_value=None)
        client = _client(mock_broker, alias_resolver)
        resp = client.request("DELETE", "/api/users/7", json={"reason": "spam"})
        assert resp.status_code == 204
        call_args = mock_broker.call.call_args
        params = call_args[0][1]
        assert params["id"] == "7"
        assert params["reason"] == "spam"

    def test_path_params_override_query(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """Node.js compat: path params win over query params."""
        client = _client(mock_broker, alias_resolver)
        client.get("/api/users/5", params={"id": "override"})
        call_args = mock_broker.call.call_args
        assert call_args[0][1]["id"] == "5"  # path wins (Node.js: alias params > query > body)

    def test_query_overrides_body(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """Node.js compat: query params win over body params."""
        mock_broker.call = AsyncMock(return_value={"ok": True})
        client = _client(mock_broker, alias_resolver)
        client.post("/api/users", params={"name": "from_query"}, json={"name": "from_body"})
        call_args = mock_broker.call.call_args
        assert call_args[0][1]["name"] == "from_query"  # query wins (Node.js: query > body)


# ---------------------------------------------------------------------------
# handle_request — ctx.meta passthrough
# ---------------------------------------------------------------------------


class TestCtxMetaPassthrough:
    def test_ctx_meta_status_code_override(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """Action sets $statusCode via ctx.meta -> response uses that status."""

        async def call_with_meta(
            action: str, params: Any, meta: dict[str, Any] | None = None, **kw: Any
        ) -> dict[str, Any]:
            if meta is not None:
                meta["$statusCode"] = 201
            return {"created": True}

        mock_broker.call = AsyncMock(side_effect=call_with_meta)
        client = _client(mock_broker, alias_resolver)
        resp = client.post("/api/users", json={"name": "Jane"})
        assert resp.status_code == 201
        assert resp.json() == {"created": True}

    def test_ctx_meta_response_headers(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """Action sets $responseHeaders via ctx.meta -> response includes headers."""

        async def call_with_headers(
            action: str, params: Any, meta: dict[str, Any] | None = None, **kw: Any
        ) -> dict[str, Any]:
            if meta is not None:
                meta["$responseHeaders"] = {"X-Custom": "val"}
            return {"ok": True}

        mock_broker.call = AsyncMock(side_effect=call_with_headers)
        client = _client(mock_broker, alias_resolver)
        resp = client.get("/api/users")
        assert resp.status_code == 200
        assert resp.headers.get("x-custom") == "val"

    def test_ctx_meta_redirect(self, mock_broker: MagicMock, alias_resolver: AliasResolver) -> None:
        """Action sets $statusCode=302 + $location -> redirect response."""

        async def call_with_redirect(
            action: str, params: Any, meta: dict[str, Any] | None = None, **kw: Any
        ) -> dict[str, Any]:
            if meta is not None:
                meta["$statusCode"] = 302
                meta["$location"] = "/new"
            return {}

        mock_broker.call = AsyncMock(side_effect=call_with_redirect)
        client = _client(mock_broker, alias_resolver, base_path="/api")
        resp = client.get("/api/users", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers.get("location") == "/new"


# ---------------------------------------------------------------------------
# handle_request — SSRF protection
# ---------------------------------------------------------------------------


class TestSSRFProtection:
    def test_mapping_all_blocks_dollar_actions(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """mapping_policy='all' must block $node.actions and similar."""
        client = _client(mock_broker, alias_resolver, mapping_policy="all")
        resp = client.get("/api/$node/actions")
        assert resp.status_code == 404
        mock_broker.call.assert_not_called()

    def test_mapping_all_blocks_invalid_format(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """mapping_policy='all' with single-segment path -> 404 (no dot in action)."""
        client = _client(mock_broker, alias_resolver, mapping_policy="all")
        resp = client.get("/api/")
        assert resp.status_code == 404
        mock_broker.call.assert_not_called()

    def test_mapping_all_allows_valid_action(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """mapping_policy='all' allows valid service.action format."""
        mock_broker.call = AsyncMock(return_value={"ok": True})
        client = _client(mock_broker, alias_resolver, mapping_policy="all")
        resp = client.get("/api/posts/recent")
        assert resp.status_code == 200
        call_args = mock_broker.call.call_args
        assert call_args[0][0] == "posts.recent"
