"""HTTP API Gateway Service for MoleculerPy.

Maps HTTP requests to Moleculer service actions via Starlette (ASGI).
Inherits from moleculerpy.Service for full broker integration:
actions, events, lifecycle, registry.

ADR-002: ApiGatewayService extends moleculerpy.Service
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

import uvicorn
from moleculerpy import Service, action, event
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from moleculerpy_web.access import check_blacklist, check_whitelist
from moleculerpy_web.alias import (
    AliasResolver,
    generate_rest_aliases,
    is_rest_shorthand,
    parse_rest_shorthand,
)
from moleculerpy_web.errors import GatewayError, NotFoundError
from moleculerpy_web.handler import create_error_response, handle_request
from moleculerpy_web.route import RouteConfig, parse_route_config
from moleculerpy_web.utils import normalize_path, parse_alias_pattern

# Compiled regex for valid Moleculer action names (shared with handler.py)
_VALID_ACTION_RE = re.compile(r"^[a-zA-Z0-9_]+(\.[a-zA-Z0-9_]+)+$")


def _build_resolver(route_config: RouteConfig) -> AliasResolver:
    """Build an AliasResolver from a RouteConfig's aliases.

    Handles REST shorthand and action config dicts.
    Extracted to avoid duplication between _build_routes and add_route.
    """
    resolver = AliasResolver()
    for alias_pattern, action_or_config in route_config.aliases.items():
        if is_rest_shorthand(alias_pattern):
            rest_path = parse_rest_shorthand(alias_pattern)
            rest_aliases = generate_rest_aliases(rest_path, action_or_config)
            for rest_pattern, rest_action in rest_aliases.items():
                method, path = parse_alias_pattern(rest_pattern)
                resolver.add_alias(method, normalize_path(path), rest_action)
        else:
            method, path = parse_alias_pattern(alias_pattern)
            act = (
                action_or_config
                if isinstance(action_or_config, str)
                else action_or_config.get("action", "")
            )
            resolver.add_alias(method, normalize_path(path), act)
    return resolver


class ApiGatewayService(Service):
    """HTTP API Gateway Service for MoleculerPy.

    Maps HTTP requests to Moleculer service actions via Starlette (ASGI).
    Inherits from moleculerpy.Service for full broker integration.

    Usage with broker:
        class Gateway(ApiGatewayService):
            name = "api"
            settings = {
                "port": 3000,
                "routes": [{"path": "/api", "aliases": {"GET /users": "users.list"}}]
            }

        broker = ServiceBroker()
        broker.create_service(Gateway)
        await broker.start()

    Usage standalone (testing):
        gateway = ApiGatewayService(broker=mock_broker, settings={...})
        await gateway.started()
    """

    name: str = "api"

    def __init__(
        self,
        broker: Any = None,
        *,
        name: str | None = None,
        settings: dict[str, Any] | None = None,
        dependencies: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        # Initialize Service base class (name, settings, dependencies merge)
        super().__init__(
            name=name or kwargs.get("name"),
            settings=settings or kwargs.get("settings"),
            dependencies=dependencies,
        )

        # Standalone mode: broker passed directly (testing, Phase 1-2 compat)
        # With broker.create_service(): broker is set by broker after __init__
        if broker is not None:
            self.broker = broker

        # Gateway-specific state
        self._app: Starlette | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._server: uvicorn.Server | None = None
        self._routes: list[tuple[RouteConfig, AliasResolver]] = []

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
            resolver = _build_resolver(route_config)
            self._routes.append((route_config, resolver))

    def _create_app(self) -> Starlette:
        """Create Starlette ASGI application."""

        async def catch_all(request: Request) -> Response:
            return await self._handle(request)

        routes: list[Route | Mount] = []

        # Static file serving (configured via settings.assets)
        assets = self.settings.get("assets")
        if isinstance(assets, dict) and "folder" in assets:
            folder = os.path.realpath(assets["folder"])
            url_path = assets.get("path", "/assets")
            html_mode = assets.get("html", False)
            if os.path.isdir(folder):
                routes.append(
                    Mount(
                        url_path,
                        app=StaticFiles(directory=folder, html=html_mode),
                        name="static",
                    )
                )

        # Catch-all routes for API handling
        all_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]
        routes.extend(
            [
                Route("/{path:path}", catch_all, methods=all_methods),
                Route("/", catch_all, methods=all_methods),
            ]
        )

        app = Starlette(routes=routes)
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
                    route_config=route_config,
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

    # --- Internal Actions (Phase 3) ---

    @action()
    async def list_aliases(self, ctx: Any = None) -> list[dict[str, Any]]:
        """List all registered route aliases.

        Returns a list of dicts with method, path, action for each alias.
        Callable as: broker.call("api.listAliases")
        """
        result: list[dict[str, Any]] = []
        for route_config, resolver in self._routes:
            route_path = route_config.path
            for alias in resolver.aliases:
                result.append(
                    {
                        "method": alias.method,
                        "path": f"{route_path}{alias.path}",
                        "action": alias.action,
                        "route": route_path,
                    }
                )
        return result

    def _is_local_call(self, ctx: Any) -> bool:
        """Check if ctx originates from the local node (default-deny)."""
        if ctx is None:
            return True  # Direct Python call (no broker context) — allowed
        local_node = getattr(getattr(self, "broker", None), "node_id", None)
        caller_node = getattr(ctx, "node_id", None)
        if not local_node or not caller_node:
            return False  # Cannot verify locality — deny (fail-secure)
        return bool(caller_node == local_node)

    @staticmethod
    def _extract_params(ctx: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Extract params from ctx (broker call) or kwargs (direct call)."""
        if ctx is not None and hasattr(ctx, "params") and isinstance(ctx.params, dict):
            return ctx.params
        return kwargs

    @action()
    async def add_route(self, ctx: Any = None, **params: Any) -> dict[str, Any]:
        """Dynamically add a route at runtime.

        Security: Only callable from local node (default-deny).

        Params:
            route: dict — route config (path, aliases, etc.)
            to_bottom: bool — add to end of routes list (default True)

        Callable as: broker.call("api.addRoute", {"route": {...}})
        """
        if not self._is_local_call(ctx):
            return {"success": False, "error": "addRoute is local-only"}

        params = self._extract_params(ctx, params)
        route_raw = params.get("route", {})
        to_bottom = params.get("to_bottom", True)

        if not route_raw:
            return {"success": False, "error": "route config required"}

        route_config = parse_route_config(route_raw) if isinstance(route_raw, dict) else route_raw
        resolver = _build_resolver(route_config)

        if to_bottom:
            self._routes.append((route_config, resolver))
        else:
            self._routes.insert(0, (route_config, resolver))

        return {"success": True, "path": route_config.path, "aliases": len(resolver.aliases)}

    @action()
    async def remove_route(self, ctx: Any = None, **params: Any) -> dict[str, Any]:
        """Remove a route by path.

        Security: Only callable from local node (default-deny).

        Callable as: broker.call("api.removeRoute", {"path": "/api"})
        """
        if not self._is_local_call(ctx):
            return {"success": False, "error": "removeRoute is local-only"}

        params = self._extract_params(ctx, params)
        target_path = params.get("path", "")

        if not target_path:
            return {"success": False, "error": "path required"}

        before = len(self._routes)
        self._routes = [(rc, res) for rc, res in self._routes if rc.path != target_path]
        removed = before - len(self._routes)

        return {"success": removed > 0, "removed": removed}

    # --- Auto-aliases (Phase 3) ---

    def _regenerate_auto_aliases(self) -> int:
        """Regenerate aliases for routes with autoAliases=True.

        Scans broker registry for actions with 'rest' annotation and creates
        aliases automatically. Like Node.js moleculer-web $services.changed.

        Returns:
            Number of auto-generated aliases.
        """
        if self.broker is None:
            return 0

        count = 0
        for route_config, resolver in self._routes:
            if not route_config.auto_aliases:
                continue

            # Clear existing auto-generated aliases
            resolver.clear()

            # Scan broker registry for actions with rest annotations
            registry = getattr(self.broker, "registry", None)
            if registry is None:
                continue

            action_list = getattr(registry, "action_list", None)
            if action_list is None:
                continue

            for act_item in action_list:
                rest = None
                # Action may have rest annotation as string or dict
                if isinstance(act_item, dict):
                    rest = act_item.get("rest")
                    act_name = act_item.get("name", "")
                elif hasattr(act_item, "rest"):
                    rest = act_item.rest
                    act_name = getattr(act_item, "name", "")
                else:
                    continue

                if rest is None:
                    continue

                if isinstance(rest, str):
                    # "GET /users" or "/users" (default GET)
                    parts = rest.strip().split(None, 1)
                    if len(parts) == 2:
                        method, path = parts[0].upper(), parts[1]
                    else:
                        method, path = "GET", parts[0]
                elif isinstance(rest, dict):
                    method = rest.get("method", "GET").upper()
                    path = rest.get("path", "")
                else:
                    continue

                if not (path and act_name):
                    continue

                # Security: validate action name (OWASP A01)
                if not _VALID_ACTION_RE.match(act_name):
                    continue

                # Security: apply whitelist/blacklist filters (OWASP A01)
                if route_config.whitelist or route_config.blacklist:
                    if route_config.whitelist:
                        if not check_whitelist(act_name, route_config.whitelist):
                            continue
                    if route_config.blacklist:
                        if check_blacklist(act_name, route_config.blacklist):
                            continue

                # Validate path doesn't contain traversal
                if ".." in path or "\x00" in path:
                    continue

                resolver.add_alias(method, normalize_path(path), act_name)
                count += 1

        return count

    @event(name="$services.changed")
    async def _on_services_changed(self, ctx: Any = None, **kwargs: Any) -> None:
        """Handle $services.changed event — regenerate auto-aliases.

        Called when services are registered/removed in the broker.
        Debounce is handled by the caller (broker emits at most once per batch).
        """
        self._regenerate_auto_aliases()
