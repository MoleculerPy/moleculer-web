from __future__ import annotations

import posixpath
import re


def normalize_path(path: str) -> str:
    """Normalize URL path: resolve ./.., ensure leading /, remove trailing /, collapse //.

    Examples:
        normalize_path("api/users/") -> "/api/users"
        normalize_path("//api///users//") -> "/api/users"
        normalize_path("/") -> "/"
        normalize_path("") -> "/"
        normalize_path("/api/../admin") -> "/admin"
        normalize_path("/api/./users") -> "/api/users"
    """
    if not path or path == "/":
        return "/"
    # Resolve .. and . segments
    path = posixpath.normpath(path)
    # Collapse multiple slashes into one
    path = re.sub(r"/+", "/", path)
    # Ensure leading slash
    if not path.startswith("/"):
        path = "/" + path
    # Remove trailing slash
    path = path.rstrip("/")
    return path or "/"


def parse_alias_pattern(pattern: str) -> tuple[str, str]:
    """Parse alias pattern string into (method, path).

    Examples:
        parse_alias_pattern("GET /users") -> ("GET", "/users")
        parse_alias_pattern("POST /users/{id}") -> ("POST", "/users/{id}")
        parse_alias_pattern("/health") -> ("*", "/health")
    """
    parts = pattern.strip().split(None, 1)
    if len(parts) == 2 and parts[0].upper() == parts[0] and not parts[0].startswith("/"):
        return parts[0].upper(), parts[1]
    # No method specified — treat entire pattern as path
    return "*", pattern.strip()


def url_path_to_action(path: str, prefix: str) -> str:
    """Convert URL path to Moleculer action name (for mappingPolicy="all").

    Examples:
        url_path_to_action("/api/users/list", "/api") -> "users.list"
        url_path_to_action("/api/v1/users/get", "/api/v1") -> "users.get"
    """
    # Remove prefix
    if prefix and path.startswith(prefix):
        path = path[len(prefix) :]
    # Strip leading/trailing slashes and convert / to .
    path = path.strip("/")
    return path.replace("/", ".")
