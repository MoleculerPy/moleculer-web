"""Core middleware abstractions for moleculerpy-web API Gateway.

Provides the middleware pipeline foundation: RequestContext, MiddlewareProtocol,
and compose_middleware for building onion-model middleware chains.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from starlette.requests import Request
from starlette.responses import Response

from moleculerpy_web.alias import AliasMatch

# Type alias for the next handler in the chain
NextHandler = Callable[["RequestContext"], Awaitable[Response]]


@dataclass
class RequestContext:
    """Context object passed through the middleware pipeline.

    Contains everything a middleware needs to process the request.
    Extensible -- Phase 3 can add fields without breaking existing middleware.

    Attributes:
        request: Starlette Request.
        action: Resolved action name (e.g., "users.get").
        params: Merged params (body < query < path_params).
        meta: Shared meta for ctx.meta passthrough.
        broker: ServiceBroker (duck-typed).
        alias: Matched alias (None if mapping_policy=all).
        user: Set by authentication middleware.
        route_config: Route config stored as dict to avoid circular import.
    """

    request: Request
    action: str
    params: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)
    broker: Any = None
    alias: AliasMatch | None = None
    user: Any | None = None
    route_config: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class MiddlewareProtocol(Protocol):
    """Protocol for gateway middleware.

    Each middleware receives the request context and a next handler.
    It can modify the context, call next(), modify the response, or short-circuit.

    Example::

        class LoggingMiddleware:
            async def __call__(self, ctx: RequestContext, next_handler: NextHandler) -> Response:
                print(f"Request: {ctx.action}")
                response = await next_handler(ctx)
                print(f"Response: {response.status_code}")
                return response
    """

    async def __call__(self, ctx: RequestContext, next_handler: NextHandler) -> Response: ...


def compose_middleware(
    middlewares: list[Any],
    handler: NextHandler,
) -> NextHandler:
    """Compose middleware chain (onion model).

    Each middleware wraps the next. Last middleware calls the terminal handler.
    Execution order: first middleware added = first to execute (outermost layer).

    Args:
        middlewares: List of middleware instances (outermost first).
        handler: Terminal handler (broker.call + response building).

    Returns:
        Composed handler that executes the full middleware chain.
    """
    result = handler
    for mw in reversed(middlewares):
        prev = result

        async def make_wrapped(
            ctx: RequestContext, _mw: Any = mw, _next: NextHandler = prev
        ) -> Response:
            resp: Response = await _mw(ctx, _next)
            return resp

        result = make_wrapped
    return result
