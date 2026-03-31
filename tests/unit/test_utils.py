from __future__ import annotations

from moleculerpy_web.alias import colon_to_brace
from moleculerpy_web.utils import (
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
