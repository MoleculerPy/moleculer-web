from __future__ import annotations

import hashlib
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


def generate_etag(content: bytes) -> str:
    """Generate ETag from response content using MD5.

    Returns weak ETag (W/"...") as content may be transformed by middleware.

    Examples:
        generate_etag(b"hello") -> 'W/"5d41402abc4b2a76b9719d911017c592"'
    """
    digest = hashlib.md5(content, usedforsecurity=False).hexdigest()
    return f'W/"{digest}"'


def check_etag_match(request_etag: str, response_etag: str) -> bool:
    """Check if request If-None-Match header matches response ETag.

    Supports multiple ETags separated by commas, and wildcard '*'.

    Returns True if there's a match (304 should be sent).
    """
    if not request_etag or not response_etag:
        return False
    if request_etag.strip() == "*":
        return True
    # Parse comma-separated ETags
    for tag in request_etag.split(","):
        tag = tag.strip()
        if tag == response_etag:
            return True
        # Compare without W/ prefix for weak comparison
        tag_val = tag.removeprefix("W/")
        resp_val = response_etag.removeprefix("W/")
        if tag_val == resp_val:
            return True
    return False
