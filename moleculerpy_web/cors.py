"""Route-level CORS for moleculerpy-web API Gateway.

Provides Node.js moleculer-web compatible CORS handling at the route level,
not as Starlette middleware. Supports wildcard origins, origin lists,
callable checks, preflight detection, and full header generation.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable
from dataclasses import dataclass, field

from starlette.requests import Request


@dataclass
class CorsConfig:
    """CORS configuration (Node.js moleculer-web compatible).

    Attributes:
        origin: Allowed origins. "*" for any, string for exact/wildcard,
                list for multiple, callable for custom check.
        methods: Allowed HTTP methods.
        credentials: Allow credentials (cookies, auth headers).
        exposed_headers: Headers accessible to JavaScript.
        allowed_headers: Headers allowed in requests (None = echo request headers).
        max_age: Preflight cache duration in seconds.
    """

    origin: str | list[str] | Callable[[str], bool] | None = "*"
    methods: list[str] = field(
        default_factory=lambda: ["GET", "HEAD", "PUT", "PATCH", "POST", "DELETE"]
    )
    credentials: bool = False
    exposed_headers: list[str] | None = None
    allowed_headers: list[str] | None = None
    max_age: int | None = None


def check_origin(
    request_origin: str,
    allowed: str | list[str] | Callable[[str], bool] | None,
) -> bool:
    """Check if request origin is allowed.

    Supports:
        - "*": any origin
        - Exact string: "https://example.com"
        - Wildcard string: "https://*.example.com" (fnmatch)
        - List of strings: ["https://a.com", "https://b.com"]
        - Callable: custom function (origin) -> bool

    Args:
        request_origin: The Origin header value from the request.
        allowed: Allowed origin specification.

    Returns:
        True if the origin is allowed.
    """
    if allowed is None or allowed == "*":
        return True
    if callable(allowed) and not isinstance(allowed, (str, list)):
        return allowed(request_origin)
    if isinstance(allowed, str):
        if "*" in allowed or "?" in allowed:
            return fnmatch.fnmatch(request_origin, allowed)
        return request_origin == allowed
    if isinstance(allowed, list):
        return any(check_origin(request_origin, a) for a in allowed)
    return False


def build_cors_headers(
    config: CorsConfig,
    request: Request,
    is_preflight: bool = False,
) -> dict[str, str]:
    """Build CORS response headers from config.

    Args:
        config: CORS configuration.
        request: Incoming HTTP request.
        is_preflight: True for OPTIONS preflight requests.

    Returns:
        Dict of CORS response headers.
    """
    headers: dict[str, str] = {}
    origin = request.headers.get("origin", "")

    if not origin:
        return headers

    # Access-Control-Allow-Origin
    if config.origin == "*":
        headers["Access-Control-Allow-Origin"] = "*"
    elif check_origin(origin, config.origin):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"
    else:
        return {}  # Origin not allowed — return no CORS headers

    # Access-Control-Allow-Credentials
    if config.credentials:
        headers["Access-Control-Allow-Credentials"] = "true"

    # Access-Control-Expose-Headers
    if config.exposed_headers:
        headers["Access-Control-Expose-Headers"] = ", ".join(config.exposed_headers)

    # Preflight-only headers
    if is_preflight:
        # Access-Control-Allow-Methods
        headers["Access-Control-Allow-Methods"] = ", ".join(config.methods)

        # Access-Control-Allow-Headers
        if config.allowed_headers:
            headers["Access-Control-Allow-Headers"] = ", ".join(config.allowed_headers)
        else:
            # Echo request headers
            req_headers = request.headers.get("access-control-request-headers", "")
            if req_headers:
                headers["Access-Control-Allow-Headers"] = req_headers

        # Access-Control-Max-Age
        if config.max_age is not None:
            headers["Access-Control-Max-Age"] = str(config.max_age)

    return headers


def is_preflight(request: Request) -> bool:
    """Check if request is a CORS preflight (OPTIONS + Access-Control-Request-Method).

    Args:
        request: Incoming HTTP request.

    Returns:
        True if this is a CORS preflight request.
    """
    return request.method == "OPTIONS" and "access-control-request-method" in request.headers
