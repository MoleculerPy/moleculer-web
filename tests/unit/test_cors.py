"""Tests for moleculerpy_web.cors module."""

from __future__ import annotations

from unittest.mock import MagicMock

from starlette.requests import Request

from moleculerpy_web.cors import (
    CorsConfig,
    build_cors_headers,
    check_origin,
    is_preflight,
)


def _make_request(
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> Request:
    """Create a minimal mock request for CORS testing."""
    req = MagicMock(spec=Request)
    req.method = method
    req.headers = headers or {}
    return req


# --- check_origin tests ---


def test_check_origin_wildcard() -> None:
    """Wildcard '*' allows any origin."""
    assert check_origin("https://example.com", "*") is True


def test_check_origin_none_allows_all() -> None:
    """None allows any origin."""
    assert check_origin("https://example.com", None) is True


def test_check_origin_exact_match() -> None:
    """Exact string match."""
    assert check_origin("https://example.com", "https://example.com") is True


def test_check_origin_exact_mismatch() -> None:
    """Exact string mismatch."""
    assert check_origin("https://other.com", "https://example.com") is False


def test_check_origin_wildcard_pattern() -> None:
    """Wildcard pattern matching with fnmatch."""
    assert check_origin("https://sub.example.com", "https://*.example.com") is True
    assert check_origin("https://other.com", "https://*.example.com") is False


def test_check_origin_list() -> None:
    """List of allowed origins."""
    allowed = ["https://a.com", "https://b.com"]
    assert check_origin("https://a.com", allowed) is True
    assert check_origin("https://b.com", allowed) is True
    assert check_origin("https://c.com", allowed) is False


def test_check_origin_callable() -> None:
    """Custom callable check."""

    def checker(o: str) -> bool:
        return o.endswith(".example.com")

    assert check_origin("https://sub.example.com", checker) is True
    assert check_origin("https://other.com", checker) is False


# --- build_cors_headers tests ---


def test_build_headers_wildcard_origin() -> None:
    """Wildcard origin returns Access-Control-Allow-Origin: *."""
    config = CorsConfig(origin="*")
    req = _make_request(headers={"origin": "https://example.com"})
    headers = build_cors_headers(config, req)
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert "Vary" not in headers


def test_build_headers_specific_origin_with_vary() -> None:
    """Specific origin sets Vary: Origin."""
    config = CorsConfig(origin="https://example.com")
    req = _make_request(headers={"origin": "https://example.com"})
    headers = build_cors_headers(config, req)
    assert headers["Access-Control-Allow-Origin"] == "https://example.com"
    assert headers["Vary"] == "Origin"


def test_build_headers_credentials() -> None:
    """Credentials flag sets Access-Control-Allow-Credentials."""
    config = CorsConfig(credentials=True)
    req = _make_request(headers={"origin": "https://example.com"})
    headers = build_cors_headers(config, req)
    assert headers["Access-Control-Allow-Credentials"] == "true"


def test_build_headers_exposed_headers() -> None:
    """Exposed headers are set."""
    config = CorsConfig(exposed_headers=["X-Custom", "X-Request-Id"])
    req = _make_request(headers={"origin": "https://example.com"})
    headers = build_cors_headers(config, req)
    assert headers["Access-Control-Expose-Headers"] == "X-Custom, X-Request-Id"


def test_build_headers_preflight_full() -> None:
    """Preflight includes methods, allowed headers, and max-age."""
    config = CorsConfig(
        methods=["GET", "POST"],
        allowed_headers=["Content-Type", "Authorization"],
        max_age=3600,
    )
    req = _make_request(headers={"origin": "https://example.com"})
    headers = build_cors_headers(config, req, is_preflight=True)
    assert headers["Access-Control-Allow-Methods"] == "GET, POST"
    assert headers["Access-Control-Allow-Headers"] == "Content-Type, Authorization"
    assert headers["Access-Control-Max-Age"] == "3600"


def test_build_headers_preflight_echo_request_headers() -> None:
    """Preflight echoes request headers when allowed_headers is None."""
    config = CorsConfig(allowed_headers=None)
    req = _make_request(
        headers={
            "origin": "https://example.com",
            "access-control-request-headers": "X-Custom, Authorization",
        }
    )
    headers = build_cors_headers(config, req, is_preflight=True)
    assert headers["Access-Control-Allow-Headers"] == "X-Custom, Authorization"


def test_build_headers_no_origin_returns_empty() -> None:
    """No origin header returns empty dict."""
    config = CorsConfig()
    req = _make_request(headers={})
    headers = build_cors_headers(config, req)
    assert headers == {}


def test_build_headers_disallowed_origin_returns_empty() -> None:
    """Disallowed origin returns empty dict."""
    config = CorsConfig(origin="https://allowed.com")
    req = _make_request(headers={"origin": "https://evil.com"})
    headers = build_cors_headers(config, req)
    assert headers == {}


# --- is_preflight tests ---


def test_is_preflight_true() -> None:
    """OPTIONS with Access-Control-Request-Method is preflight."""
    req = _make_request(
        method="OPTIONS",
        headers={"access-control-request-method": "POST"},
    )
    assert is_preflight(req) is True


def test_is_preflight_false_for_get() -> None:
    """GET is not preflight."""
    req = _make_request(method="GET", headers={})
    assert is_preflight(req) is False


def test_is_preflight_false_options_without_request_method() -> None:
    """OPTIONS without Access-Control-Request-Method is not preflight."""
    req = _make_request(method="OPTIONS", headers={})
    assert is_preflight(req) is False


# --- CorsConfig tests ---


def test_cors_config_defaults() -> None:
    """CorsConfig has correct defaults."""
    cfg = CorsConfig()
    assert cfg.origin == "*"
    assert cfg.methods == ["GET", "HEAD", "PUT", "PATCH", "POST", "DELETE"]
    assert cfg.credentials is False
    assert cfg.exposed_headers is None
    assert cfg.allowed_headers is None
    assert cfg.max_age is None
