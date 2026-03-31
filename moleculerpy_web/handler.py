"""Core request handling for moleculerpy-web API Gateway.

Routes HTTP requests to Moleculer actions via alias resolution,
merges parameters from path/query/body, and builds HTTP responses.
"""

from __future__ import annotations

import re
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from moleculerpy_web.alias import AliasResolver
from moleculerpy_web.errors import (
    GatewayError,
    InternalServerError,
    NotFoundError,
    moleculer_error_to_http,
)
from moleculerpy_web.parsers import parse_body
from moleculerpy_web.utils import normalize_path, url_path_to_action

_VALID_ACTION_RE = re.compile(r"^[a-zA-Z0-9_]+(\.[a-zA-Z0-9_]+)+$")

try:
    from moleculerpy.errors import MoleculerError as _MoleculerError

    _HAS_MOLECULERPY = True
except ImportError:
    _HAS_MOLECULERPY = False
    _MoleculerError = None  # type: ignore[assignment, misc]


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
    route_path: str = "/",
    mapping_policy: str = "restrict",
    base_path: str = "/api",
) -> Response:
    """Handle HTTP request: resolve alias -> merge params -> broker.call() -> response.

    Flow:
        1. Strip base_path + route_path prefix from request path.
        2. Resolve alias via alias_resolver.resolve(method, path).
        3. If no alias and mapping_policy == "restrict" -> 404.
        4. If no alias and mapping_policy == "all" -> derive action from URL path.
        5. Merge params: path_params + query_params + body.
        6. Call broker.call(action, params).
        7. Build response from result.

    Args:
        request: Incoming Starlette Request.
        broker: ServiceBroker instance (duck-typed).
        alias_resolver: AliasResolver with registered aliases.
        route_path: Route path prefix (e.g., "/" or "/v2").
        mapping_policy: "restrict" (only aliases) or "all" (any action via URL).
        base_path: Global gateway path prefix (e.g., "/api").

    Returns:
        HTTP Response with action result or error.
    """
    # 1. Strip base_path + route_path prefix (with path boundary check)
    raw_path = request.url.path
    prefix = normalize_path(base_path + route_path)
    if prefix == "/":
        relative_path = raw_path
    elif raw_path == prefix or raw_path.startswith(prefix + "/"):
        # Exact match or prefix followed by / — correct boundary
        relative_path = raw_path[len(prefix) :] or "/"
    else:
        # Path does not match prefix at all (e.g., /outside/posts vs /api)
        # or /apiary starts with /api but is NOT under /api/
        raise NotFoundError(f"Route not found: {request.method} {raw_path}")

    relative_path = normalize_path(relative_path)

    # 2. Resolve alias
    method = request.method.upper()
    match = alias_resolver.resolve(method, relative_path)

    action_name: str | None = None

    if match:
        action_name = match.action
    elif mapping_policy == "all":
        # 4. Derive action from URL path
        action_name = url_path_to_action(raw_path, prefix)
    else:
        # 3. mapping_policy == "restrict" and no alias -> raise (not return)
        # This allows service._handle to distinguish route-not-found from action 404
        raise NotFoundError(f"Route not found: {method} {raw_path}")

    # Validate action name format: must be "service.action" with alphanumeric segments
    # This also blocks $-prefixed internal actions since $ is not in the allowed charset
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

    # 6. Call broker with shared meta dict for ctx.meta passthrough
    meta: dict[str, Any] = {}
    try:
        result = await broker.call(action_name, params, meta=meta)
    except GatewayError as e:
        return await create_error_response(e)
    except Exception as e:
        if _HAS_MOLECULERPY and isinstance(e, _MoleculerError):
            gateway_err = moleculer_error_to_http(e)
            return await create_error_response(gateway_err)
        # Unknown error -> 500 (don't leak internal details)
        return await create_error_response(InternalServerError("Internal server error"))

    # 7. Build response with ctx.meta overrides
    raw_status = meta.get("$statusCode", 200)
    try:
        status_code = int(raw_status)
    except (TypeError, ValueError):
        status_code = 200
    response_headers: dict[str, str] | None = meta.get("$responseHeaders")
    response_type: str | None = meta.get("$responseType")
    location: str | None = meta.get("$location")

    # Handle redirects: 201 + Location (REST create) or 300-399 (standard redirects)
    # Only relative URLs allowed (open redirect protection)
    if location and isinstance(location, str):
        is_redirect = status_code == 201 or (300 <= status_code < 400 and status_code != 304)
        is_safe = not location.startswith(("http://", "https://", "//"))
        if is_redirect and is_safe:
            if status_code == 201:
                # 201 Created: set Location header but still return body
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
