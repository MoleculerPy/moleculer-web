"""End-to-end integration tests for Phase 2 features.

Tests REST shorthand, hooks pipeline, authentication/authorization,
CORS, rate limiting, whitelist/blacklist, and URL-encoded body
through the full ApiGatewayService → Starlette → handler pipeline.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from moleculerpy_web.errors import ForbiddenError
from moleculerpy_web.middleware import RequestContext
from moleculerpy_web.route import RouteConfig
from moleculerpy_web.service import ApiGatewayService

try:
    import multipart  # noqa: F401

    _HAS_MULTIPART = True
except ImportError:
    _HAS_MULTIPART = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gateway(broker: MagicMock, routes: list[dict[str, Any]]) -> ApiGatewayService:
    """Build a gateway with given routes, ready for httpx testing."""
    svc = ApiGatewayService(
        broker=broker,
        settings={"port": 3000, "path": "/api", "routes": routes},
    )
    svc._build_routes()
    svc._app = svc._create_app()
    return svc


def _transport(svc: ApiGatewayService) -> ASGITransport:
    return ASGITransport(app=svc.app)


# ---------------------------------------------------------------------------
# 1. REST Shorthand
# ---------------------------------------------------------------------------


class TestRestShorthand:
    """REST shorthand aliases generate all CRUD routes through the full pipeline."""

    async def test_rest_shorthand_all_crud(self, mock_broker: MagicMock) -> None:
        """'REST /users': 'users' generates all 6 CRUD routes."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(mock_broker, [{"path": "/", "aliases": {"REST /users": "users"}}])

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            # list
            resp = await c.get("/api/users")
            assert resp.status_code == 200
            assert mock_broker.call.call_args[0][0] == "users.list"
            mock_broker.call.reset_mock()

            # get
            resp = await c.get("/api/users/42")
            assert resp.status_code == 200
            assert mock_broker.call.call_args[0][0] == "users.get"
            assert mock_broker.call.call_args[0][1]["id"] == "42"
            mock_broker.call.reset_mock()

            # create
            resp = await c.post("/api/users", json={"name": "Alice"})
            assert resp.status_code == 200
            assert mock_broker.call.call_args[0][0] == "users.create"
            mock_broker.call.reset_mock()

            # update
            resp = await c.put("/api/users/1", json={"name": "Bob"})
            assert resp.status_code == 200
            assert mock_broker.call.call_args[0][0] == "users.update"
            mock_broker.call.reset_mock()

            # patch
            resp = await c.patch("/api/users/1", json={"name": "Carol"})
            assert resp.status_code == 200
            assert mock_broker.call.call_args[0][0] == "users.patch"
            mock_broker.call.reset_mock()

            # remove
            resp = await c.delete("/api/users/1")
            assert resp.status_code == 200
            assert mock_broker.call.call_args[0][0] == "users.remove"

    async def test_rest_shorthand_only(self, mock_broker: MagicMock) -> None:
        """REST with only: ['list', 'get'] produces exactly 2 routes."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {
                        "REST /users": {"action": "users", "only": ["list", "get"]},
                    },
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            assert (await c.get("/api/users")).status_code == 200
            assert (await c.get("/api/users/1")).status_code == 200
            assert (await c.post("/api/users", json={})).status_code == 404
            assert (await c.delete("/api/users/1")).status_code == 404

    async def test_rest_shorthand_except(self, mock_broker: MagicMock) -> None:
        """REST with except: ['remove'] produces 5 routes, DELETE 404."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {
                        "REST /users": {"action": "users", "except": ["remove"]},
                    },
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            assert (await c.get("/api/users")).status_code == 200
            assert (await c.get("/api/users/1")).status_code == 200
            assert (await c.post("/api/users", json={})).status_code == 200
            assert (await c.put("/api/users/1", json={})).status_code == 200
            assert (await c.patch("/api/users/1", json={})).status_code == 200
            assert (await c.delete("/api/users/1")).status_code == 404

    async def test_rest_plus_explicit_aliases(self, mock_broker: MagicMock) -> None:
        """REST shorthand and explicit aliases coexist on the same route."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {
                        "REST /users": "users",
                        "GET /health": "health.check",
                    },
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/health")
            assert resp.status_code == 200
            assert mock_broker.call.call_args[0][0] == "health.check"

            mock_broker.call.reset_mock()
            resp = await c.get("/api/users")
            assert resp.status_code == 200
            assert mock_broker.call.call_args[0][0] == "users.list"

    async def test_rest_nested_resource_path(self, mock_broker: MagicMock) -> None:
        """REST shorthand with nested prefix /v1/users works correctly."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(
            mock_broker,
            [{"path": "/v1", "aliases": {"REST /users": "users"}}],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/v1/users")
            assert resp.status_code == 200
            assert mock_broker.call.call_args[0][0] == "users.list"

            mock_broker.call.reset_mock()
            resp = await c.get("/api/v1/users/5")
            assert resp.status_code == 200
            assert mock_broker.call.call_args[0][0] == "users.get"
            assert mock_broker.call.call_args[0][1]["id"] == "5"


# ---------------------------------------------------------------------------
# 2. Hooks Pipeline
# ---------------------------------------------------------------------------


class TestHooksPipeline:
    """onBeforeCall / onAfterCall / onError hooks execute through the pipeline."""

    async def test_on_before_call_modifies_params(self, mock_broker: MagicMock) -> None:
        """onBeforeCall can modify ctx.params before broker.call."""
        mock_broker.call.return_value = {"ok": True}

        async def before_call(ctx: RequestContext, route: RouteConfig, req: Request) -> None:
            ctx.params["injected"] = "value"

        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "onBeforeCall": before_call,
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            await c.get("/api/test")
            call_params = mock_broker.call.call_args[0][1]
            assert call_params["injected"] == "value"

    async def test_on_after_call_transforms_response(self, mock_broker: MagicMock) -> None:
        """onAfterCall can transform data before client receives it."""
        mock_broker.call.return_value = {"raw": "data"}

        async def after_call(
            ctx: RequestContext, route: RouteConfig, req: Request, data: Any
        ) -> Any:
            return {"transformed": True, "original": data}

        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "onAfterCall": after_call,
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/test")
            assert resp.json()["transformed"] is True
            assert resp.json()["original"] == {"raw": "data"}

    async def test_before_and_after_call_order(self, mock_broker: MagicMock) -> None:
        """Hooks execute in order: onBeforeCall -> action -> onAfterCall."""
        order: list[str] = []

        async def before_call(ctx: RequestContext, route: RouteConfig, req: Request) -> None:
            order.append("before")

        async def after_call(
            ctx: RequestContext, route: RouteConfig, req: Request, data: Any
        ) -> Any:
            order.append("after")
            return data

        async def mock_call(action: str, params: Any, meta: Any = None) -> dict[str, str]:
            order.append("action")
            return {"ok": "yes"}

        mock_broker.call = AsyncMock(side_effect=mock_call)

        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "onBeforeCall": before_call,
                    "onAfterCall": after_call,
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            await c.get("/api/test")
            assert order == ["before", "action", "after"]

    async def test_on_error_handles_exception(self, mock_broker: MagicMock) -> None:
        """onError hook can return a custom error response."""
        from starlette.responses import JSONResponse, Response

        mock_broker.call.return_value = {"ok": True}

        async def authorize(ctx: RequestContext, route: RouteConfig, req: Request) -> None:
            raise ForbiddenError("denied by authz")

        async def on_error(req: Request, error: Any) -> Response:
            return JSONResponse({"custom": "error", "msg": str(error)}, status_code=403)

        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "authorization": authorize,
                    "onError": on_error,
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/test")
            assert resp.status_code == 403
            assert resp.json()["custom"] == "error"

    async def test_hook_accesses_request_headers(self, mock_broker: MagicMock) -> None:
        """onBeforeCall can read request headers (e.g., Authorization)."""
        mock_broker.call.return_value = {"ok": True}

        async def before_call(ctx: RequestContext, route: RouteConfig, req: Request) -> None:
            token = req.headers.get("authorization", "")
            ctx.meta["token"] = token

        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "onBeforeCall": before_call,
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            await c.get("/api/test", headers={"Authorization": "Bearer abc123"})
            call_meta = mock_broker.call.call_args[1]["meta"]
            assert call_meta["token"] == "Bearer abc123"


# ---------------------------------------------------------------------------
# 3. Authentication + Authorization
# ---------------------------------------------------------------------------


class TestAuthenticationAuthorization:
    """Authentication and authorization hooks through the full pipeline."""

    async def test_authentication_sets_user(self, mock_broker: MagicMock) -> None:
        """Authentication sets ctx.user which is passed to broker.call via meta."""
        mock_broker.call.return_value = {"ok": True}

        async def authenticate(ctx: RequestContext, route: RouteConfig, req: Request) -> Any:
            return {"id": 1, "name": "Admin"}

        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "authentication": authenticate,
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/test")
            assert resp.status_code == 200
            call_meta = mock_broker.call.call_args[1]["meta"]
            assert call_meta["user"]["id"] == 1
            assert call_meta["user"]["name"] == "Admin"

    async def test_authentication_anonymous(self, mock_broker: MagicMock) -> None:
        """Authentication returning None (anonymous) continues processing."""
        mock_broker.call.return_value = {"ok": True}

        async def authenticate(ctx: RequestContext, route: RouteConfig, req: Request) -> Any:
            return None

        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "authentication": authenticate,
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/test")
            assert resp.status_code == 200

    async def test_authentication_raises_error(self, mock_broker: MagicMock) -> None:
        """Authentication raising ForbiddenError returns 403."""
        mock_broker.call.return_value = {"ok": True}

        async def authenticate(ctx: RequestContext, route: RouteConfig, req: Request) -> Any:
            raise ForbiddenError("Invalid token")

        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "authentication": authenticate,
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/test")
            assert resp.status_code == 403
            assert resp.json()["name"] == "ForbiddenError"

    async def test_authorization_passes(self, mock_broker: MagicMock) -> None:
        """Authorization passes -> action executes normally."""
        mock_broker.call.return_value = {"ok": True}

        async def authenticate(ctx: RequestContext, route: RouteConfig, req: Request) -> Any:
            return {"id": 1, "role": "admin"}

        async def authorize(ctx: RequestContext, route: RouteConfig, req: Request) -> None:
            pass  # Allow all

        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "authentication": authenticate,
                    "authorization": authorize,
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/test")
            assert resp.status_code == 200

    async def test_authorization_raises_forbidden(self, mock_broker: MagicMock) -> None:
        """Authorization raising ForbiddenError returns 403."""
        mock_broker.call.return_value = {"ok": True}

        async def authenticate(ctx: RequestContext, route: RouteConfig, req: Request) -> Any:
            return {"id": 2, "role": "guest"}

        async def authorize(ctx: RequestContext, route: RouteConfig, req: Request) -> None:
            if ctx.user and ctx.user.get("role") != "admin":
                raise ForbiddenError("Admin access required")

        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "authentication": authenticate,
                    "authorization": authorize,
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/test")
            assert resp.status_code == 403
            assert resp.json()["name"] == "ForbiddenError"


# ---------------------------------------------------------------------------
# 4. CORS
# ---------------------------------------------------------------------------


class TestCors:
    """CORS headers through the full gateway pipeline."""

    async def test_preflight_options_returns_cors_headers(self, mock_broker: MagicMock) -> None:
        """OPTIONS preflight returns 200 with CORS headers."""
        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "cors": {
                        "origin": "https://example.com",
                        "methods": ["GET", "POST"],
                    },
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.options(
                "/api/test",
                headers={
                    "Origin": "https://example.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert resp.status_code == 200
            assert resp.headers["access-control-allow-origin"] == "https://example.com"
            assert "GET" in resp.headers["access-control-allow-methods"]

    async def test_normal_get_with_origin(self, mock_broker: MagicMock) -> None:
        """Normal GET with Origin header gets CORS headers in response."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "cors": {"origin": "https://example.com"},
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/test", headers={"Origin": "https://example.com"})
            assert resp.status_code == 200
            assert resp.headers["access-control-allow-origin"] == "https://example.com"

    async def test_disallowed_origin_no_cors_headers(self, mock_broker: MagicMock) -> None:
        """Disallowed origin gets no CORS headers in response."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "cors": {"origin": "https://example.com"},
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/test", headers={"Origin": "https://evil.com"})
            assert resp.status_code == 200
            assert "access-control-allow-origin" not in resp.headers

    async def test_credentials_enabled(self, mock_broker: MagicMock) -> None:
        """Credentials: true adds Access-Control-Allow-Credentials header."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "cors": {"origin": "https://example.com", "credentials": True},
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/test", headers={"Origin": "https://example.com"})
            assert resp.headers["access-control-allow-credentials"] == "true"

    async def test_custom_allowed_headers_in_preflight(self, mock_broker: MagicMock) -> None:
        """Custom allowed_headers appear in preflight response."""
        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "cors": {
                        "origin": "*",
                        "allowedHeaders": ["X-Custom", "Authorization"],
                    },
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.options(
                "/api/test",
                headers={
                    "Origin": "https://example.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert resp.status_code == 200
            allowed = resp.headers["access-control-allow-headers"]
            assert "X-Custom" in allowed
            assert "Authorization" in allowed


# ---------------------------------------------------------------------------
# 5. Rate Limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    """Rate limiting through the full gateway pipeline."""

    async def test_under_limit_returns_200(self, mock_broker: MagicMock) -> None:
        """Requests under the limit succeed with 200."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "rateLimit": {"window": 60, "limit": 5, "headers": True},
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/test")
            assert resp.status_code == 200

    async def test_over_limit_returns_429(self, mock_broker: MagicMock) -> None:
        """Requests over the limit return 429 RateLimitExceeded."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "rateLimit": {"window": 60, "limit": 2, "headers": True},
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            assert (await c.get("/api/test")).status_code == 200
            assert (await c.get("/api/test")).status_code == 200
            resp = await c.get("/api/test")
            assert resp.status_code == 429
            assert resp.json()["name"] == "RateLimitExceededError"

    async def test_rate_limit_headers_present(self, mock_broker: MagicMock) -> None:
        """Rate limit headers are present when headers=True."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "rateLimit": {"window": 60, "limit": 10, "headers": True},
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/test")
            assert resp.status_code == 200
            assert "x-rate-limit-limit" in resp.headers
            assert "x-rate-limit-remaining" in resp.headers
            assert "x-rate-limit-reset" in resp.headers
            assert resp.headers["x-rate-limit-limit"] == "10"

    async def test_different_keys_independent_limits(self, mock_broker: MagicMock) -> None:
        """Different IPs (keys) have independent rate limit counters."""
        mock_broker.call.return_value = {"ok": True}

        def custom_key(request: Request) -> str | None:
            return request.headers.get("x-client-id")

        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /test": "svc.action"},
                    "rateLimit": {"window": 60, "limit": 1, "headers": False, "key": custom_key},
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            # Client A: first request OK
            resp = await c.get("/api/test", headers={"X-Client-Id": "client-a"})
            assert resp.status_code == 200

            # Client A: second request blocked
            resp = await c.get("/api/test", headers={"X-Client-Id": "client-a"})
            assert resp.status_code == 429

            # Client B: first request OK (independent counter)
            resp = await c.get("/api/test", headers={"X-Client-Id": "client-b"})
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 6. Whitelist / Blacklist
# ---------------------------------------------------------------------------


class TestWhitelistBlacklist:
    """Whitelist and blacklist access control through the full pipeline."""

    async def test_whitelist_allows_matching(self, mock_broker: MagicMock) -> None:
        """Whitelisted action passes through."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /users": "users.list"},
                    "whitelist": ["users.*"],
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/users")
            assert resp.status_code == 200

    async def test_whitelist_blocks_non_matching(self, mock_broker: MagicMock) -> None:
        """Non-whitelisted action returns 404."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /admin": "admin.panel"},
                    "whitelist": ["users.*"],
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/admin")
            assert resp.status_code == 404

    async def test_blacklist_blocks_matching(self, mock_broker: MagicMock) -> None:
        """Blacklisted action returns 404."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {"GET /admin": "admin.delete"},
                    "blacklist": ["admin.*"],
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/admin")
            assert resp.status_code == 404

    async def test_whitelist_and_blacklist_combined(self, mock_broker: MagicMock) -> None:
        """Whitelist + blacklist: allowed action passes, blocked action rejected."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(
            mock_broker,
            [
                {
                    "path": "/",
                    "aliases": {
                        "GET /users": "users.list",
                        "GET /danger": "users.dangerousAction",
                    },
                    "whitelist": ["users.*"],
                    "blacklist": ["users.dangerous*"],
                }
            ],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.get("/api/users")
            assert resp.status_code == 200

            resp = await c.get("/api/danger")
            assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 7. URL-encoded Body
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_MULTIPART, reason="python-multipart not installed")
class TestUrlEncodedBody:
    """application/x-www-form-urlencoded body parsing through the full pipeline."""

    async def test_form_urlencoded_params_parsed(self, mock_broker: MagicMock) -> None:
        """POST with application/x-www-form-urlencoded parses params correctly."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(
            mock_broker,
            [{"path": "/", "aliases": {"POST /submit": "form.submit"}}],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.post(
                "/api/submit",
                data={"name": "Alice", "email": "alice@example.com"},
            )
            assert resp.status_code == 200
            call_params = mock_broker.call.call_args[0][1]
            assert call_params["name"] == "Alice"
            assert call_params["email"] == "alice@example.com"

    async def test_empty_form_body(self, mock_broker: MagicMock) -> None:
        """POST with empty form body produces empty params."""
        mock_broker.call.return_value = {"ok": True}
        svc = _make_gateway(
            mock_broker,
            [{"path": "/", "aliases": {"POST /submit": "form.submit"}}],
        )

        async with AsyncClient(transport=_transport(svc), base_url="http://test") as c:
            resp = await c.post(
                "/api/submit",
                content=b"",
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
            assert resp.status_code == 200
            call_params = mock_broker.call.call_args[0][1]
            assert call_params == {}
