"""Core request handling for moleculerpy-web API Gateway.

Routes HTTP requests to Moleculer actions via alias resolution,
merges parameters from path/query/body, builds middleware pipeline,
and returns HTTP responses.
"""

from __future__ import annotations

import re
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from moleculerpy_web.access import BlacklistMiddleware, WhitelistMiddleware
from moleculerpy_web.alias import AliasResolver
from moleculerpy_web.cors import build_cors_headers, is_preflight
from moleculerpy_web.errors import (
    GatewayError,
    InternalServerError,
    NotFoundError,
    RateLimitExceededError,
    moleculer_error_to_http,
)
from moleculerpy_web.middleware import NextHandler, RequestContext, compose_middleware
from moleculerpy_web.parsers import parse_body
from moleculerpy_web.ratelimit import MemoryStore, RateLimitConfig, default_key_extractor
from moleculerpy_web.route import RouteConfig
from moleculerpy_web.utils import normalize_path, url_path_to_action

_VALID_ACTION_RE = re.compile(r"^[a-zA-Z0-9_]+(\.[a-zA-Z0-9_]+)+$")

try:
    from moleculerpy.errors import MoleculerError as _MoleculerError

    _HAS_MOLECULERPY = True
except ImportError:
    _HAS_MOLECULERPY = False
    _MoleculerError = None  # type: ignore[assignment, misc]

# Cache for rate limit stores (one per RateLimitConfig identity)
_rate_limit_stores: dict[int, MemoryStore] = {}


async def _get_or_create_store(config: RateLimitConfig) -> MemoryStore:
    """Get or create a MemoryStore for the given rate limit config.

    Store is started automatically on first creation (reset loop begins).
    Keyed by id(config) — safe as long as config is kept alive by RouteConfig.
    """
    key = id(config)
    if key not in _rate_limit_stores:
        store = MemoryStore(config.window)
        await store.start()
        _rate_limit_stores[key] = store
    return _rate_limit_stores[key]


def build_response(
    result: Any,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    content_type: str | None = None,
) -> Response:
    """Build HTTP response from action result.

    Args:
        result: Value returned by broker.call().
        status_code: HTTP status code for the response.
        headers: Optional extra response headers.
        content_type: Override Content-Type (from ctx.meta.$responseType).

    Returns:
        Appropriate Response subclass based on result type.
    """
    if result is None:
        return Response(status_code=204, headers=headers)
    if isinstance(result, bytes):
        return Response(
            content=result,
            status_code=status_code,
            media_type=content_type or "application/octet-stream",
            headers=headers,
        )
    # If $responseType is set and result is a string, return as-is with that type
    if content_type and isinstance(result, str):
        return Response(
            content=result,
            status_code=status_code,
            media_type=content_type,
            headers=headers,
        )
    # Default: JSON
    return JSONResponse(result, status_code=status_code, headers=headers)


async def create_error_response(error: GatewayError) -> Response:
    """Create JSON error response from GatewayError.

    Args:
        error: A GatewayError instance.

    Returns:
        JSONResponse with error details and appropriate status code.
    """
    return JSONResponse(error.to_response_dict(), status_code=error.status_code)


async def handle_request(
    request: Request,
    broker: Any,
    alias_resolver: AliasResolver,
    route_config: RouteConfig | None = None,
    base_path: str = "/api",
    *,
    # Legacy params for backward compatibility with existing tests/callers
    route_path: str | None = None,
    mapping_policy: str | None = None,
) -> Response:
    """Handle HTTP request: resolve alias -> middleware pipeline -> broker.call() -> response.

    Flow:
        1. Strip base_path + route_path prefix from request path.
        2. Resolve alias via alias_resolver.resolve(method, path).
        3. If no alias and mapping_policy == "restrict" -> 404.
        4. If no alias and mapping_policy == "all" -> derive action from URL path.
        5. Merge params: path_params + query_params + body.
        6. Build middleware chain from RouteConfig.
        7. Execute pipeline -> return response.

    Args:
        request: Incoming Starlette Request.
        broker: ServiceBroker instance (duck-typed).
        alias_resolver: AliasResolver with registered aliases.
        route_config: Full RouteConfig (preferred). If None, falls back to legacy params.
        base_path: Global gateway path prefix (e.g., "/api").
        route_path: (Legacy) Route path prefix. Ignored if route_config is provided.
        mapping_policy: (Legacy) "restrict" or "all". Ignored if route_config is provided.

    Returns:
        HTTP Response with action result or error.
    """
    # Resolve config: prefer route_config, fall back to legacy params
    if route_config is None:
        route_config = RouteConfig(
            path=route_path or "/",
            mapping_policy=mapping_policy or "restrict",  # type: ignore[arg-type]
        )

    effective_route_path = route_config.path
    effective_mapping_policy = route_config.mapping_policy

    # 1. Strip base_path + route_path prefix (with path boundary check)
    raw_path = request.url.path
    prefix = normalize_path(base_path + effective_route_path)
    if prefix == "/":
        relative_path = raw_path
    elif raw_path == prefix or raw_path.startswith(prefix + "/"):
        relative_path = raw_path[len(prefix) :] or "/"
    else:
        raise NotFoundError(f"Route not found: {request.method} {raw_path}")

    relative_path = normalize_path(relative_path)

    # CORS preflight short-circuit (before alias resolution — OPTIONS has no alias)
    if route_config.cors and is_preflight(request):
        headers = build_cors_headers(route_config.cors, request, is_preflight=True)
        return Response(status_code=200, headers=headers)

    # 2. Resolve alias
    method = request.method.upper()
    match = alias_resolver.resolve(method, relative_path)

    action_name: str | None = None

    if match:
        action_name = match.action
    elif effective_mapping_policy == "all":
        action_name = url_path_to_action(raw_path, prefix)
    else:
        raise NotFoundError(f"Route not found: {method} {raw_path}")

    # Validate action name format
    if not action_name or not _VALID_ACTION_RE.match(action_name):
        raise NotFoundError(f"Route not found: {method} {raw_path}")

    # 5. Merge params (Node.js compat: body < query < path_params)
    params: dict[str, Any] = {}
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        body = await parse_body(request)
        params.update(body)
    params.update(dict(request.query_params))
    if match:
        params.update(match.params)

    # Build RequestContext
    req_ctx = RequestContext(
        request=request,
        action=action_name,
        params=params,
        meta={},
        broker=broker,
        alias=match,
        route_config={
            "path": route_config.path,
            "mapping_policy": route_config.mapping_policy,
        },
    )

    # Build middleware chain (Node.js execution order)
    middlewares: list[Any] = []

    # onBeforeCall hook
    if route_config.on_before_call:
        _before_call = route_config.on_before_call

        class _BeforeCallMW:
            async def __call__(self, ctx: RequestContext, next_handler: NextHandler) -> Response:
                try:
                    await _before_call(ctx, route_config, ctx.request)
                except GatewayError:
                    raise
                except Exception as exc:
                    raise InternalServerError("Hook onBeforeCall failed") from exc
                return await next_handler(ctx)

        middlewares.append(_BeforeCallMW())

    # Whitelist
    if route_config.whitelist:
        middlewares.append(WhitelistMiddleware(route_config.whitelist))

    # Blacklist
    if route_config.blacklist:
        middlewares.append(BlacklistMiddleware(route_config.blacklist))

    # Authentication (before rate limit — Node.js order)
    if route_config.authentication:
        _auth_fn = route_config.authentication

        class _AuthMW:
            async def __call__(self, ctx: RequestContext, next_handler: NextHandler) -> Response:
                user = await _auth_fn(ctx, route_config, ctx.request)
                ctx.user = user
                ctx.meta["user"] = user
                return await next_handler(ctx)

        middlewares.append(_AuthMW())

    # Authorization (after authentication)
    if route_config.authorization:
        _authz_fn = route_config.authorization

        class _AuthzMW:
            async def __call__(self, ctx: RequestContext, next_handler: NextHandler) -> Response:
                await _authz_fn(ctx, route_config, ctx.request)
                return await next_handler(ctx)

        middlewares.append(_AuthzMW())

    # Rate limit (after auth — Node.js order: can use ctx.user for per-user limits)
    if route_config.rate_limit:
        _rl_config = route_config.rate_limit

        class _RateLimitMW:
            async def __call__(self, ctx: RequestContext, next_handler: NextHandler) -> Response:
                key_fn = _rl_config.key if _rl_config.key is not None else default_key_extractor
                key = key_fn(ctx.request)
                if key:
                    store = await _get_or_create_store(_rl_config)
                    count = await store.increment(key)
                    remaining = _rl_config.limit - count
                    if _rl_config.headers:
                        ctx.meta["$rateLimitHeaders"] = {
                            "X-Rate-Limit-Limit": str(_rl_config.limit),
                            "X-Rate-Limit-Remaining": str(max(0, remaining)),
                            "X-Rate-Limit-Reset": str(int(store.reset_time)),
                        }
                    if remaining < 0:
                        raise RateLimitExceededError("Rate limit exceeded")
                return await next_handler(ctx)

        middlewares.append(_RateLimitMW())

    # Terminal handler: broker.call + onAfterCall + build_response
    async def call_action(ctx: RequestContext) -> Response:
        try:
            result = await ctx.broker.call(ctx.action, ctx.params, meta=ctx.meta)
        except GatewayError as e:
            return await create_error_response(e)
        except Exception as e:
            if _HAS_MOLECULERPY and isinstance(e, _MoleculerError):
                return await create_error_response(moleculer_error_to_http(e))
            return await create_error_response(InternalServerError("Internal server error"))

        # onAfterCall hook — can modify data
        if route_config.on_after_call:
            try:
                result = await route_config.on_after_call(ctx, route_config, ctx.request, result)
            except GatewayError:
                raise
            except Exception as exc:
                raise InternalServerError("Hook onAfterCall failed") from exc

        # Build response with ctx.meta overrides
        raw_status = ctx.meta.get("$statusCode", 200)
        try:
            status_code = int(raw_status)
        except (TypeError, ValueError):
            status_code = 200
        response_headers: dict[str, str] | None = ctx.meta.get("$responseHeaders")
        response_type: str | None = ctx.meta.get("$responseType")
        location: str | None = ctx.meta.get("$location")

        # Handle redirects
        if location and isinstance(location, str):
            is_redirect = status_code == 201 or (300 <= status_code < 400 and status_code != 304)
            is_safe = not location.startswith(("http://", "https://", "//"))
            if is_redirect and is_safe:
                if status_code == 201:
                    response_headers = response_headers or {}
                    response_headers["Location"] = location
                else:
                    return RedirectResponse(
                        url=location, status_code=status_code, headers=response_headers
                    )

        return build_response(
            result,
            status_code=status_code,
            headers=response_headers,
            content_type=response_type,
        )

    # Compose and execute pipeline
    pipeline = compose_middleware(middlewares, call_action)

    try:
        response = await pipeline(req_ctx)
    except GatewayError as e:
        if route_config.on_error:
            custom_response: Response | None = await route_config.on_error(request, e)
            if custom_response is not None:
                return custom_response
        return await create_error_response(e)

    # Add CORS headers to response if configured
    if route_config.cors:
        cors_headers = build_cors_headers(route_config.cors, request, is_preflight=False)
        for k, v in cors_headers.items():
            response.headers[k] = v

    # Add rate limit headers if present
    rl_headers = req_ctx.meta.get("$rateLimitHeaders")
    if rl_headers:
        for k, v in rl_headers.items():
            response.headers[k] = v

    return response
