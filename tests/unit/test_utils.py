from __future__ import annotations

from moleculerpy_web.alias import colon_to_brace
from moleculerpy_web.utils import (
    VALID_ACTION_RE,
    check_etag_match,
    generate_etag,
    normalize_path,
    parse_alias_pattern,
    url_path_to_action,
)


class TestNormalizePath:
    def test_empty_string(self) -> None:
        assert normalize_path("") == "/"

    def test_root(self) -> None:
        assert normalize_path("/") == "/"

    def test_adds_leading_slash(self) -> None:
        assert normalize_path("api/users") == "/api/users"

    def test_removes_trailing_slash(self) -> None:
        assert normalize_path("/api/users/") == "/api/users"

    def test_collapses_double_slashes(self) -> None:
        assert normalize_path("//api///users//") == "/api/users"

    def test_already_normalized(self) -> None:
        assert normalize_path("/api/users") == "/api/users"


class TestParseAliasPattern:
    def test_get_with_path(self) -> None:
        assert parse_alias_pattern("GET /users") == ("GET", "/users")

    def test_post_with_param(self) -> None:
        assert parse_alias_pattern("POST /users/{id}") == ("POST", "/users/{id}")

    def test_no_method(self) -> None:
        assert parse_alias_pattern("/health") == ("*", "/health")

    def test_delete_method(self) -> None:
        assert parse_alias_pattern("DELETE /users/{id}") == ("DELETE", "/users/{id}")

    def test_whitespace_stripped(self) -> None:
        assert parse_alias_pattern("  GET /users  ") == ("GET", "/users")


class TestColonToBrace:
    def test_single_param(self) -> None:
        assert colon_to_brace("/users/:id") == "/users/{id}"

    def test_multiple_params(self) -> None:
        assert colon_to_brace("/posts/:userId/:postId") == "/posts/{userId}/{postId}"

    def test_already_brace(self) -> None:
        assert colon_to_brace("/users/{id}") == "/users/{id}"

    def test_no_params(self) -> None:
        assert colon_to_brace("/users/list") == "/users/list"


class TestNormalizePathTraversal:
    def test_path_traversal_blocked(self) -> None:
        assert normalize_path("/api/../admin") == "/admin"

    def test_dot_segment(self) -> None:
        assert normalize_path("/api/./users") == "/api/users"

    def test_double_dot_at_root(self) -> None:
        assert normalize_path("/../etc/passwd") == "/etc/passwd"

    def test_multiple_traversals(self) -> None:
        assert normalize_path("/a/b/../../c") == "/c"


class TestUrlPathToAction:
    def test_basic(self) -> None:
        assert url_path_to_action("/api/users/list", "/api") == "users.list"

    def test_with_nested_prefix(self) -> None:
        assert url_path_to_action("/api/v1/users/get", "/api/v1") == "users.get"

    def test_single_segment(self) -> None:
        assert url_path_to_action("/api/health", "/api") == "health"


class TestGenerateEtag:
    def test_deterministic(self) -> None:
        """Same content should produce same ETag."""
        assert generate_etag(b"hello") == generate_etag(b"hello")

    def test_different_content(self) -> None:
        """Different content should produce different ETags."""
        assert generate_etag(b"hello") != generate_etag(b"world")

    def test_weak_format(self) -> None:
        """ETag should be in W/"..." format."""
        etag = generate_etag(b"test")
        assert etag.startswith('W/"')
        assert etag.endswith('"')


class TestCheckEtagMatch:
    def test_exact_match(self) -> None:
        etag = generate_etag(b"hello")
        assert check_etag_match(etag, etag) is True

    def test_no_match(self) -> None:
        assert check_etag_match('W/"abc"', 'W/"def"') is False

    def test_wildcard(self) -> None:
        assert check_etag_match("*", 'W/"anything"') is True

    def test_empty_request_etag(self) -> None:
        assert check_etag_match("", 'W/"abc"') is False

    def test_multiple_etags(self) -> None:
        """Comma-separated ETags should be checked individually."""
        target = 'W/"abc"'
        assert check_etag_match('W/"xyz", W/"abc", W/"def"', target) is True
        assert check_etag_match('W/"xyz", W/"def"', target) is False

    def test_weak_comparison(self) -> None:
        """W/ prefix should be stripped for weak comparison."""
        assert check_etag_match('"abc"', 'W/"abc"') is True
        assert check_etag_match('W/"abc"', '"abc"') is True


class TestValidActionRe:
    """Direct tests for VALID_ACTION_RE (security boundary, OWASP A01)."""

    def test_valid_service_action(self) -> None:
        assert VALID_ACTION_RE.match("users.list") is not None

    def test_valid_versioned(self) -> None:
        assert VALID_ACTION_RE.match("v1.math.add") is not None

    def test_valid_with_underscores(self) -> None:
        assert VALID_ACTION_RE.match("my_service.my_action") is not None

    def test_rejects_dollar_prefix(self) -> None:
        assert VALID_ACTION_RE.match("$node.actions") is None

    def test_rejects_no_dot(self) -> None:
        assert VALID_ACTION_RE.match("users") is None

    def test_rejects_empty(self) -> None:
        assert VALID_ACTION_RE.match("") is None

    def test_rejects_path_traversal(self) -> None:
        assert VALID_ACTION_RE.match("../etc/passwd") is None

    def test_rejects_trailing_dot(self) -> None:
        assert VALID_ACTION_RE.match("users.") is None

    def test_rejects_leading_dot(self) -> None:
        assert VALID_ACTION_RE.match(".users.list") is None
