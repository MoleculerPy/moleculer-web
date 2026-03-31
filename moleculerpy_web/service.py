"""HTTP API Gateway Service for MoleculerPy.

Maps HTTP requests to Moleculer service actions via Starlette (ASGI).
Provides lifecycle management (started/stopped) compatible with
the MoleculerPy Service interface.
"""

from __future__ import annotations

import asyncio
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from moleculerpy_web.alias import AliasResolver
from moleculerpy_web.errors import GatewayError, NotFoundError
from moleculerpy_web.handler import create_error_response, handle_request
from moleculerpy_web.route import RouteConfig, parse_route_config
from moleculerpy_web.utils import normalize_path, parse_alias_pattern


class ApiGatewayService:
    """HTTP API Gateway Service for MoleculerPy.

    Maps HTTP requests to Moleculer service actions via Starlette (ASGI).

    Usage:
        class Gateway(ApiGatewayService):
            name = "api"
            settings = {
                "port": 3000,
                "path": "/api",
                "routes": [{"path": "/", "aliases": {"GET /users": "users.list"}}]
            }

    NOTE: This is NOT a moleculerpy.Service subclass yet — that integration
    comes when we wire it into the broker. For Phase 1, it's standalone with
    a compatible interface (started/stopped lifecycle).
    """

    name: str = "api"

    def __init__(self, broker: Any = None, **kwargs: Any) -> None:
        self.broker = broker
        self.settings: dict[str, Any] = kwargs.get("settings", {})
        self._app: Starlette | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._server: uvicorn.Server | None = None
        self._routes: list[tuple[RouteConfig, AliasResolver]] = []
        if "name" in kwargs:
            self.name = kwargs["name"]

    @property
    def port(self) -> int:
        """HTTP server port."""
        port: int = self.settings.get("port", 3000)
        return port

    @property
    def ip(self) -> str:
        """Bind address."""
        ip: str = self.settings.get("ip", "0.0.0.0")
        return ip

    @property
    def base_path(self) -> str:
        """Global path prefix for all routes."""
        path: str = self.settings.get("path", "/api")
        return path

    def _build_routes(self) -> None:
        """Parse route configs and build alias resolvers."""
        self._routes.clear()
        raw_routes = self.settings.get("routes", [])
        for raw in raw_routes:
            route_config = parse_route_config(raw) if isinstance(raw, dict) else raw
            resolver = AliasResolver()
            for alias_pattern, action in route_config.aliases.items():
                method, path = parse_alias_pattern(alias_pattern)
                normalized = normalize_path(path)
                resolver.add_alias(method, normalized, action)
            self._routes.append((route_config, resolver))

    def _create_app(self) -> Starlette:
        """Create Starlette ASGI application."""

        async def catch_all(request: Request) -> Response:
            return await self._handle(request)

        app = Starlette(
            routes=[
                Route(
                    "/{path:path}",
                    catch_all,
                    methods=[
                        "GET",
                        "POST",
                        "PUT",
                        "PATCH",
                        "DELETE",
                        "OPTIONS",
                        "HEAD",
                    ],
                ),
                Route(
                    "/",
                    catch_all,
                    methods=[
                        "GET",
                        "POST",
                        "PUT",
                        "PATCH",
                        "DELETE",
                        "OPTIONS",
                        "HEAD",
                    ],
                ),
            ],
        )
        return app

    async def _handle(self, request: Request) -> Response:
        """Route HTTP request to appropriate handler.

        NotFoundError raised by handle_request means "route not matched" —
        try next route. Any other error or a successful response (even 404
        from an action) is returned immediately.
        """
        for route_config, resolver in self._routes:
            try:
                return await handle_request(
                    request=request,
                    broker=self.broker,
                    alias_resolver=resolver,
                    route_path=route_config.path,
                    mapping_policy=route_config.mapping_policy,
                    base_path=self.base_path,
                )
            except NotFoundError:
                continue  # Route not matched — try next
            except GatewayError as e:
                return await create_error_response(e)

        # No route matched
        err = NotFoundError(f"No route found for {request.method} {request.url.path}")
        return await create_error_response(err)

    async def started(self) -> None:
        """Lifecycle: called when broker starts. Creates Starlette app + uvicorn server.

        Waits for the server to bind successfully before returning.
        If bind fails, the error propagates immediately (not as a background crash).
        """
        self._build_routes()
        self._app = self._create_app()

        config = uvicorn.Config(
            self._app,
            host=self.ip,
            port=self.port,
            log_level="info",
            access_log=False,
        )
        server = uvicorn.Server(config)
        self._server = server

        # Start server in background task
        self._server_task = asyncio.create_task(
            server.serve(),
            name=f"moleculerpy:{self.name}:serve",
        )

        # Wait for server to actually bind (or fail) before returning
        # uvicorn.Server.started is set True after successful bind
        for _ in range(50):  # up to 5 seconds
            if server.started:
                return
            # If the task already failed, re-raise immediately
            if self._server_task.done():
                self._server_task.result()  # raises the bind error
                return
            await asyncio.sleep(0.1)

        # Loop exhausted — server did not start in time
        raise RuntimeError(f"API Gateway failed to start within 5s on {self.ip}:{self.port}")

    async def stopped(self) -> None:
        """Lifecycle: called when broker stops. Graceful HTTP server shutdown."""
        if self._server is not None:
            self._server.should_exit = True
        if self._server_task is not None:
            try:
                await asyncio.wait_for(self._server_task, timeout=5.0)
            except TimeoutError:
                self._server_task.cancel()
            self._server_task = None

    @property
    def app(self) -> Starlette | None:
        """Access Starlette app for testing (httpx.AsyncClient)."""
        return self._app
