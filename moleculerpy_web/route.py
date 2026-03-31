from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class RouteConfig:
    """Configuration for a single API route.

    Attributes:
        path: Route path prefix (e.g., "/" or "/v2").
        mapping_policy: "restrict" = only aliases work (default, secure).
                       "all" = any action accessible via URL path.
        aliases: Mapping of alias patterns to action names.
                 e.g., {"GET /users": "users.list", "GET /users/{id}": "users.get"}
    """

    path: str = "/"
    mapping_policy: Literal["restrict", "all"] = "restrict"
    aliases: dict[str, str] = field(default_factory=dict)
    # Phase 2 fields (not implemented yet):
    # whitelist: list[str] | None = None
    # blacklist: list[str] | None = None
    # authentication: bool = False
    # authorization: bool = False


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
    """
    return RouteConfig(
        path=raw.get("path", "/"),
        mapping_policy=raw.get("mappingPolicy", raw.get("mapping_policy", "restrict")),
        aliases=raw.get("aliases", {}),
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
