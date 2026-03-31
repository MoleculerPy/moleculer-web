from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from moleculerpy_web.cors import CorsConfig
from moleculerpy_web.ratelimit import RateLimitConfig


@dataclass
class RouteConfig:
    """Configuration for a single API route.

    Attributes:
        path: Route path prefix (e.g., "/" or "/v2").
        mapping_policy: "restrict" = only aliases work (default, secure).
                       "all" = any action accessible via URL path.
        aliases: Mapping of alias patterns to action names or config dicts.
        whitelist: Action patterns allowed (fnmatch or regex).
        blacklist: Action patterns blocked (fnmatch or regex).
        on_before_call: Hook called before broker.call.
        on_after_call: Hook called after broker.call, can modify result.
        on_error: Hook called on error, can return custom response.
        authentication: Hook to authenticate request, returns user.
        authorization: Hook to authorize request.
        cors: CORS configuration for this route.
        rate_limit: Rate limiting configuration for this route.
        body_parsers: Body parser configuration.
    """

    path: str = "/"
    mapping_policy: Literal["restrict", "all"] = "restrict"
    aliases: dict[str, str | dict[str, Any]] = field(default_factory=dict)
    whitelist: list[str | re.Pattern[str]] | None = None
    blacklist: list[str | re.Pattern[str]] | None = None
    on_before_call: Callable[..., Awaitable[None]] | None = None
    on_after_call: Callable[..., Awaitable[Any]] | None = None
    on_error: Callable[..., Awaitable[Any]] | None = None
    authentication: Callable[..., Awaitable[Any]] | None = None
    authorization: Callable[..., Awaitable[None]] | None = None
    cors: CorsConfig | None = None
    rate_limit: RateLimitConfig | None = None
    body_parsers: dict[str, bool | dict[str, Any]] | None = None


@dataclass
class GatewaySettings:
    """Full gateway service settings.

    Attributes:
        port: HTTP server port.
        ip: Bind address.
        path: Global path prefix for all routes.
        routes: List of route configurations.
        log_request_params: Log level for request params (None to disable).
        log_response_data: Log level for response data (None to disable).
    """

    port: int = 3000
    ip: str = "0.0.0.0"
    path: str = "/api"
    routes: list[RouteConfig] = field(default_factory=list)
    log_request_params: str | None = "debug"
    log_response_data: str | None = None


def parse_route_config(raw: dict[str, Any]) -> RouteConfig:
    """Parse a raw dict into RouteConfig.

    Accepts both camelCase (Node.js compat) and snake_case:
        {"mappingPolicy": "all"} -> RouteConfig(mapping_policy="all")
        {"mapping_policy": "all"} -> RouteConfig(mapping_policy="all")
        {"onBeforeCall": fn} -> RouteConfig(on_before_call=fn)
        {"rateLimit": {...}} -> RouteConfig(rate_limit=RateLimitConfig(...))
        {"cors": {...}} -> RouteConfig(cors=CorsConfig(...))
    """
    # Parse cors: dict -> CorsConfig, pass through if already CorsConfig
    raw_cors = raw.get("cors")
    cors: CorsConfig | None = None
    if isinstance(raw_cors, dict):
        cors = CorsConfig(
            origin=raw_cors.get("origin", "*"),
            methods=raw_cors.get("methods", ["GET", "HEAD", "PUT", "PATCH", "POST", "DELETE"]),
            credentials=raw_cors.get("credentials", False),
            exposed_headers=raw_cors.get("exposedHeaders", raw_cors.get("exposed_headers")),
            allowed_headers=raw_cors.get("allowedHeaders", raw_cors.get("allowed_headers")),
            max_age=raw_cors.get("maxAge", raw_cors.get("max_age")),
        )
    elif isinstance(raw_cors, CorsConfig):
        cors = raw_cors

    # Parse rate_limit: dict -> RateLimitConfig
    raw_rl = raw.get("rateLimit", raw.get("rate_limit"))
    rate_limit: RateLimitConfig | None = None
    if isinstance(raw_rl, dict):
        rate_limit = RateLimitConfig(
            window=raw_rl.get("window", 60.0),
            limit=raw_rl.get("limit", 30),
            headers=raw_rl.get("headers", False),
        )
        if "key" in raw_rl:
            rate_limit.key = raw_rl["key"]
    elif isinstance(raw_rl, RateLimitConfig):
        rate_limit = raw_rl

    return RouteConfig(
        path=raw.get("path", "/"),
        mapping_policy=raw.get("mappingPolicy", raw.get("mapping_policy", "restrict")),
        aliases=raw.get("aliases", {}),
        whitelist=raw.get("whitelist"),
        blacklist=raw.get("blacklist"),
        on_before_call=raw.get("onBeforeCall", raw.get("on_before_call")),
        on_after_call=raw.get("onAfterCall", raw.get("on_after_call")),
        on_error=raw.get("onError", raw.get("on_error")),
        authentication=raw.get("authentication"),
        authorization=raw.get("authorization"),
        cors=cors,
        rate_limit=rate_limit,
        body_parsers=raw.get("bodyParsers", raw.get("body_parsers")),
    )


def parse_gateway_settings(raw: dict[str, Any]) -> GatewaySettings:
    """Parse raw settings dict into GatewaySettings."""
    routes = [parse_route_config(r) for r in raw.get("routes", [])]
    return GatewaySettings(
        port=raw.get("port", 3000),
        ip=raw.get("ip", "0.0.0.0"),
        path=raw.get("path", "/api"),
        routes=routes,
        log_request_params=raw.get("logRequestParams", raw.get("log_request_params", "debug")),
        log_response_data=raw.get("logResponseData", raw.get("log_response_data")),
    )
