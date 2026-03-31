"""Tests for moleculerpy_web.middleware module."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from moleculerpy_web.middleware import (
    MiddlewareProtocol,
    NextHandler,
    RequestContext,
    compose_middleware,
)


def _make_request(method: str = "GET", path: str = "/test") -> Request:
    """Create a minimal Starlette Request for testing."""
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [],
    }
    return Request(scope)


def _make_ctx(**overrides: Any) -> RequestContext:
    """Create a RequestContext with sensible defaults."""
    defaults: dict[str, Any] = {
        "request": _make_request(),
        "action": "users.list",
        "params": {},
    }
    defaults.update(overrides)
    return RequestContext(**defaults)


class TestRequestContext:
    """Tests for RequestContext dataclass fields."""

    def test_required_fields(self) -> None:
        request = _make_request()
        ctx = RequestContext(request=request, action="users.get", params={"id": "1"})
        assert ctx.request is request
        assert ctx.action == "users.get"
        assert ctx.params == {"id": "1"}

    def test_default_fields(self) -> None:
        ctx = _make_ctx()
        assert ctx.meta == {}
        assert ctx.broker is None
        assert ctx.alias is None
        assert ctx.user is None
        assert ctx.route_config == {}

    def test_meta_is_independent_per_instance(self) -> None:
        ctx1 = _make_ctx()
        ctx2 = _make_ctx()
        ctx1.meta["key"] = "value"
        assert "key" not in ctx2.meta


class TestMiddlewareProtocol:
    """Tests for MiddlewareProtocol runtime checking."""

    def test_callable_class_satisfies_protocol(self) -> None:
        class MyMiddleware:
            async def __call__(self, ctx: RequestContext, next_handler: NextHandler) -> Response:
                return await next_handler(ctx)

        assert isinstance(MyMiddleware(), MiddlewareProtocol)

    def test_non_callable_does_not_satisfy(self) -> None:
        class NotMiddleware:
            pass

        assert not isinstance(NotMiddleware(), MiddlewareProtocol)


class TestComposeMiddleware:
    """Tests for compose_middleware function."""

    @pytest.mark.asyncio
    async def test_no_middlewares_calls_handler_directly(self) -> None:
        async def handler(ctx: RequestContext) -> Response:
            return JSONResponse({"action": ctx.action})

        composed = compose_middleware([], handler)
        ctx = _make_ctx()
        response = await composed(ctx)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_single_middleware_wraps_handler(self) -> None:
        order: list[str] = []

        class Mw:
            async def __call__(self, ctx: RequestContext, next_handler: NextHandler) -> Response:
                order.append("mw_before")
                response = await next_handler(ctx)
                order.append("mw_after")
                return response

        async def handler(ctx: RequestContext) -> Response:
            order.append("handler")
            return JSONResponse({"ok": True})

        composed = compose_middleware([Mw()], handler)
        await composed(_make_ctx())
        assert order == ["mw_before", "handler", "mw_after"]

    @pytest.mark.asyncio
    async def test_three_middlewares_correct_order(self) -> None:
        order: list[str] = []

        def make_mw(name: str) -> Any:
            class Mw:
                async def __call__(
                    self, ctx: RequestContext, next_handler: NextHandler
                ) -> Response:
                    order.append(f"{name}_before")
                    response = await next_handler(ctx)
                    order.append(f"{name}_after")
                    return response

            return Mw()

        async def handler(ctx: RequestContext) -> Response:
            order.append("handler")
            return JSONResponse({"ok": True})

        composed = compose_middleware([make_mw("1"), make_mw("2"), make_mw("3")], handler)
        await composed(_make_ctx())
        assert order == [
            "1_before",
            "2_before",
            "3_before",
            "handler",
            "3_after",
            "2_after",
            "1_after",
        ]

    @pytest.mark.asyncio
    async def test_middleware_can_short_circuit(self) -> None:
        handler_called = False

        class ShortCircuit:
            async def __call__(self, ctx: RequestContext, next_handler: NextHandler) -> Response:
                return JSONResponse({"short": True}, status_code=403)

        async def handler(ctx: RequestContext) -> Response:
            nonlocal handler_called
            handler_called = True
            return JSONResponse({"ok": True})

        composed = compose_middleware([ShortCircuit()], handler)
        response = await composed(_make_ctx())
        assert response.status_code == 403
        assert not handler_called

    @pytest.mark.asyncio
    async def test_middleware_can_modify_ctx(self) -> None:
        class InjectUser:
            async def __call__(self, ctx: RequestContext, next_handler: NextHandler) -> Response:
                ctx.user = {"id": "admin"}
                return await next_handler(ctx)

        async def handler(ctx: RequestContext) -> Response:
            return JSONResponse({"user": ctx.user})

        composed = compose_middleware([InjectUser()], handler)
        response = await composed(_make_ctx())
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_middleware_can_modify_response(self) -> None:
        class AddHeader:
            async def __call__(self, ctx: RequestContext, next_handler: NextHandler) -> Response:
                response = await next_handler(ctx)
                response.headers["X-Custom"] = "test"
                return response

        async def handler(ctx: RequestContext) -> Response:
            return JSONResponse({"ok": True})

        composed = compose_middleware([AddHeader()], handler)
        response = await composed(_make_ctx())
        assert response.headers["X-Custom"] == "test"

    @pytest.mark.asyncio
    async def test_middleware_can_catch_errors(self) -> None:
        class ErrorCatcher:
            async def __call__(self, ctx: RequestContext, next_handler: NextHandler) -> Response:
                try:
                    return await next_handler(ctx)
                except ValueError:
                    return JSONResponse({"error": "caught"}, status_code=500)

        async def handler(ctx: RequestContext) -> Response:
            raise ValueError("boom")

        composed = compose_middleware([ErrorCatcher()], handler)
        response = await composed(_make_ctx())
        assert response.status_code == 500
