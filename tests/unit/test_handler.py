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
from moleculerpy_web.cors import CorsConfig
from moleculerpy_web.errors import GatewayError, NotFoundError
from moleculerpy_web.handler import build_response, create_error_response, handle_request
from moleculerpy_web.ratelimit import RateLimitConfig
from moleculerpy_web.route import RouteConfig
from moleculerpy_web.utils import generate_etag

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

    def test_async_generator_returns_streaming(self) -> None:
        """Async generator should produce StreamingResponse."""
        from starlette.responses import StreamingResponse

        async def gen():
            yield b"chunk1"
            yield b"chunk2"

        resp = build_response(gen())
        assert isinstance(resp, StreamingResponse)
        assert resp.status_code == 200

    def test_sync_generator_returns_streaming(self) -> None:
        """Sync generator should produce StreamingResponse."""
        from starlette.responses import StreamingResponse

        def gen():
            yield b"chunk1"
            yield b"chunk2"

        resp = build_response(gen())
        assert isinstance(resp, StreamingResponse)
        assert resp.status_code == 200

    def test_streaming_with_custom_content_type(self) -> None:
        """Streaming response should respect content_type."""
        from starlette.responses import StreamingResponse

        async def gen():
            yield b"data"

        resp = build_response(gen(), content_type="text/event-stream")
        assert isinstance(resp, StreamingResponse)
        assert resp.media_type == "text/event-stream"

    def test_list_not_treated_as_streaming(self) -> None:
        """List should return JSON, not streaming."""
        resp = build_response([1, 2, 3])
        assert resp.status_code == 200
        assert resp.media_type == "application/json"

    def test_dict_not_treated_as_streaming(self) -> None:
        """Dict should return JSON, not streaming."""
        resp = build_response({"key": "value"})
        assert resp.media_type == "application/json"

    def test_etag_added_when_enabled(self) -> None:
        """ETag header should be added when etag=True."""
        # Create mock request without If-None-Match
        mock_request = MagicMock()
        mock_request.headers = {}
        resp = build_response({"ok": True}, etag=True, request=mock_request)
        assert resp.status_code == 200
        assert "ETag" in resp.headers

    def test_etag_304_on_match(self) -> None:
        """Should return 304 when If-None-Match matches ETag."""
        import json

        data = {"ok": True}
        body = json.dumps(data, separators=(",", ":")).encode()
        etag_value = generate_etag(body)

        mock_request = MagicMock()
        mock_request.headers = {"if-none-match": etag_value}

        resp = build_response(data, etag=True, request=mock_request)
        assert resp.status_code == 304

    def test_etag_200_on_mismatch(self) -> None:
        """Should return 200 when If-None-Match doesn't match."""
        mock_request = MagicMock()
        mock_request.headers = {"if-none-match": 'W/"old-etag"'}

        resp = build_response({"ok": True}, etag=True, request=mock_request)
        assert resp.status_code == 200
        assert "ETag" in resp.headers

    def test_etag_not_added_when_disabled(self) -> None:
        """ETag should not be added when etag=False (default)."""
        resp = build_response({"ok": True})
        assert "ETag" not in resp.headers

    def test_etag_not_for_non_200(self) -> None:
        """ETag should not be calculated for non-200 responses."""
        mock_request = MagicMock()
        mock_request.headers = {}
        resp = build_response(
            {"error": "not found"}, status_code=404, etag=True, request=mock_request
        )
        assert resp.status_code == 404
        assert "ETag" not in resp.headers


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


# ---------------------------------------------------------------------------
# Helpers for Phase 2 pipeline tests
# ---------------------------------------------------------------------------


def _make_pipeline_app(
    broker: Any,
    alias_resolver: AliasResolver,
    route_config: RouteConfig,
    base_path: str = "/api",
) -> Starlette:
    """Create a Starlette app using RouteConfig-based pipeline."""
    # Shared rate limit stores dict (simulates Service instance ownership)
    rate_limit_stores: dict = {}

    async def catch_all(request: Request) -> Any:
        try:
            return await handle_request(
                request,
                broker=broker,
                alias_resolver=alias_resolver,
                route_config=route_config,
                base_path=base_path,
                rate_limit_stores=rate_limit_stores,
            )
        except GatewayError as e:
            return await create_error_response(e)

    return Starlette(
        routes=[
            Route(
                "/{path:path}",
                catch_all,
                methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
            ),
            Route(
                "/",
                catch_all,
                methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
            ),
        ],
    )


def _pipeline_client(
    broker: Any,
    alias_resolver: AliasResolver,
    route_config: RouteConfig,
    **kwargs: Any,
) -> TestClient:
    app = _make_pipeline_app(broker, alias_resolver, route_config, **kwargs)
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Phase 2: Middleware pipeline tests
# ---------------------------------------------------------------------------


class TestMiddlewarePipeline:
    def test_middleware_pipeline_executes_in_order(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """Middleware should execute in the order they are added."""
        order: list[str] = []

        async def before_call(ctx: Any, route: Any, req: Any) -> None:
            order.append("before")

        async def after_call(ctx: Any, route: Any, req: Any, data: Any) -> Any:
            order.append("after")
            return data

        config = RouteConfig(
            path="/",
            aliases={"GET /users": "users.list"},
            on_before_call=before_call,
            on_after_call=after_call,
        )
        client = _pipeline_client(mock_broker, alias_resolver, config)
        resp = client.get("/api/users")
        assert resp.status_code == 200
        assert order == ["before", "after"]

    def test_on_before_call_hook(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """onBeforeCall hook should be invoked before broker.call."""
        called = False

        async def before_call(ctx: Any, route: Any, req: Any) -> None:
            nonlocal called
            called = True
            ctx.params["injected"] = "yes"

        config = RouteConfig(
            path="/",
            aliases={"GET /users": "users.list"},
            on_before_call=before_call,
        )
        client = _pipeline_client(mock_broker, alias_resolver, config)
        client.get("/api/users")
        assert called
        call_args = mock_broker.call.call_args
        assert call_args[0][1]["injected"] == "yes"

    def test_on_after_call_hook_modifies_data(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """onAfterCall hook can modify the response data."""
        mock_broker.call = AsyncMock(return_value={"original": True})

        async def after_call(ctx: Any, route: Any, req: Any, data: Any) -> Any:
            data["modified"] = True
            return data

        config = RouteConfig(
            path="/",
            aliases={"GET /users": "users.list"},
            on_after_call=after_call,
        )
        client = _pipeline_client(mock_broker, alias_resolver, config)
        resp = client.get("/api/users")
        assert resp.json()["modified"] is True
        assert resp.json()["original"] is True

    def test_on_error_hook(self, mock_broker: MagicMock, alias_resolver: AliasResolver) -> None:
        """onError hook receives the error and can return a custom response."""
        from starlette.responses import JSONResponse

        async def on_error(req: Any, err: Any) -> Any:
            return JSONResponse({"custom_error": True, "msg": str(err)}, status_code=500)

        config = RouteConfig(
            path="/",
            aliases={"GET /users": "users.list"},
            whitelist=["nope.*"],  # Will block users.list
            on_error=on_error,
        )
        client = _pipeline_client(mock_broker, alias_resolver, config)
        resp = client.get("/api/users")
        assert resp.status_code == 500
        assert resp.json()["custom_error"] is True

    def test_whitelist_blocks_action(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """Whitelist should block actions that don't match."""
        config = RouteConfig(
            path="/",
            aliases={"GET /users": "users.list"},
            whitelist=["posts.*"],
        )
        client = _pipeline_client(mock_broker, alias_resolver, config)
        resp = client.get("/api/users")
        assert resp.status_code == 404
        mock_broker.call.assert_not_called()

    def test_whitelist_allows_matching_action(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """Whitelist should allow actions that match."""
        config = RouteConfig(
            path="/",
            aliases={"GET /users": "users.list"},
            whitelist=["users.*"],
        )
        client = _pipeline_client(mock_broker, alias_resolver, config)
        resp = client.get("/api/users")
        assert resp.status_code == 200

    def test_blacklist_blocks_action(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """Blacklist should block matching actions."""
        config = RouteConfig(
            path="/",
            aliases={"GET /users": "users.list"},
            blacklist=["users.*"],
        )
        client = _pipeline_client(mock_broker, alias_resolver, config)
        resp = client.get("/api/users")
        assert resp.status_code == 404
        mock_broker.call.assert_not_called()

    def test_authentication_sets_user(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """Authentication hook should set ctx.user and pass it in meta."""

        async def authenticate(ctx: Any, route: Any, req: Any) -> dict[str, Any]:
            return {"id": 42, "name": "Alice"}

        config = RouteConfig(
            path="/",
            aliases={"GET /users": "users.list"},
            authentication=authenticate,
        )
        client = _pipeline_client(mock_broker, alias_resolver, config)
        resp = client.get("/api/users")
        assert resp.status_code == 200
        call_args = mock_broker.call.call_args
        meta = call_args.kwargs.get("meta") or call_args[1].get("meta", {})
        assert meta["user"] == {"id": 42, "name": "Alice"}

    def test_authorization_denies(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """Authorization hook raising ForbiddenError should return 403."""
        from moleculerpy_web.errors import ForbiddenError

        async def authorize(ctx: Any, route: Any, req: Any) -> None:
            raise ForbiddenError("Not allowed")

        config = RouteConfig(
            path="/",
            aliases={"GET /users": "users.list"},
            authorization=authorize,
        )
        client = _pipeline_client(mock_broker, alias_resolver, config)
        resp = client.get("/api/users")
        assert resp.status_code == 403
        mock_broker.call.assert_not_called()

    def test_cors_preflight_returns_200(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """CORS preflight (OPTIONS + Access-Control-Request-Method) should return 200."""
        config = RouteConfig(
            path="/",
            aliases={"GET /users": "users.list"},
            cors=CorsConfig(origin="https://example.com"),
        )
        client = _pipeline_client(mock_broker, alias_resolver, config)
        resp = client.options(
            "/api/users",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers
        mock_broker.call.assert_not_called()

    def test_cors_headers_on_response(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """Normal requests should include CORS headers in response."""
        config = RouteConfig(
            path="/",
            aliases={"GET /users": "users.list"},
            cors=CorsConfig(origin="*"),
        )
        client = _pipeline_client(mock_broker, alias_resolver, config)
        resp = client.get("/api/users", headers={"Origin": "https://example.com"})
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "*"

    def test_rate_limit_headers(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """Rate limit headers should be added to the response."""
        config = RouteConfig(
            path="/",
            aliases={"GET /users": "users.list"},
            rate_limit=RateLimitConfig(window=60, limit=10, headers=True),
        )
        client = _pipeline_client(mock_broker, alias_resolver, config)
        resp = client.get("/api/users")
        assert resp.status_code == 200
        assert resp.headers.get("x-rate-limit-limit") == "10"
        assert resp.headers.get("x-rate-limit-remaining") == "9"
        assert "x-rate-limit-reset" in resp.headers

    def test_rate_limit_exceeded_429(
        self, mock_broker: MagicMock, alias_resolver: AliasResolver
    ) -> None:
        """Exceeding rate limit should return 429."""
        config = RouteConfig(
            path="/",
            aliases={"GET /users": "users.list"},
            rate_limit=RateLimitConfig(window=60, limit=2, headers=True),
        )
        client = _pipeline_client(mock_broker, alias_resolver, config)
        client.get("/api/users")  # 1
        client.get("/api/users")  # 2
        resp = client.get("/api/users")  # 3 -> exceeded
        assert resp.status_code == 429

    def test_rest_shorthand_in_service(self, mock_broker: MagicMock) -> None:
        """REST shorthand aliases should generate proper CRUD routes in service."""
        from moleculerpy_web.service import ApiGatewayService

        svc = ApiGatewayService(
            broker=mock_broker,
            settings={
                "port": 3000,
                "path": "/api",
                "routes": [
                    {
                        "path": "/",
                        "aliases": {"REST /users": "users"},
                    }
                ],
            },
        )
        svc._build_routes()
        assert len(svc._routes) == 1
        _config, resolver = svc._routes[0]
        # Should have REST CRUD routes
        assert resolver.resolve("GET", "/users") is not None
        assert resolver.resolve("GET", "/users/42") is not None
        assert resolver.resolve("POST", "/users") is not None
        assert resolver.resolve("PUT", "/users/42") is not None
        assert resolver.resolve("DELETE", "/users/42") is not None

        match = resolver.resolve("GET", "/users")
        assert match is not None
        assert match.action == "users.list"
