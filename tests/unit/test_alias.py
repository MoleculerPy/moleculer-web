"""Tests for moleculerpy_web.alias module."""

from __future__ import annotations

from moleculerpy_web.alias import (
    AliasResolver,
    colon_to_brace,
    generate_rest_aliases,
    is_rest_shorthand,
    parse_rest_shorthand,
)


class TestColonToBrace:
    """Tests for colon_to_brace helper."""

    def test_single_param(self) -> None:
        assert colon_to_brace("/users/:id") == "/users/{id}"

    def test_multiple_params(self) -> None:
        assert colon_to_brace("/posts/:userId/comments/:commentId") == (
            "/posts/{userId}/comments/{commentId}"
        )

    def test_no_params(self) -> None:
        assert colon_to_brace("/health") == "/health"

    def test_mixed_with_brace(self) -> None:
        assert colon_to_brace("/users/:id/{action}") == "/users/{id}/{action}"

    def test_underscore_param(self) -> None:
        assert colon_to_brace("/items/:item_id") == "/items/{item_id}"


class TestAliasResolverBasic:
    """Basic alias resolution tests."""

    def test_simple_param(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/users/{id}", "users.get")
        result = resolver.resolve("GET", "/users/42")
        assert result is not None
        assert result.action == "users.get"
        assert result.params == {"id": "42"}

    def test_multiple_params(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/posts/{userId}/{postId}", "posts.get")
        result = resolver.resolve("GET", "/posts/5/99")
        assert result is not None
        assert result.action == "posts.get"
        assert result.params == {"userId": "5", "postId": "99"}

    def test_static_path(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/health", "health.check")
        result = resolver.resolve("GET", "/health")
        assert result is not None
        assert result.action == "health.check"
        assert result.params == {}


class TestMethodMatching:
    """HTTP method matching tests."""

    def test_method_mismatch(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/users/{id}", "users.get")
        assert resolver.resolve("POST", "/users/42") is None

    def test_wildcard_method(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("*", "/health", "health.check")
        assert resolver.resolve("GET", "/health") is not None
        assert resolver.resolve("POST", "/health") is not None
        assert resolver.resolve("DELETE", "/health") is not None

    def test_case_insensitive_method(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("get", "/users/{id}", "users.get")
        result = resolver.resolve("GET", "/users/1")
        assert result is not None
        assert result.action == "users.get"


class TestNoMatch:
    """Tests for non-matching scenarios."""

    def test_unknown_path(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/users/{id}", "users.get")
        assert resolver.resolve("GET", "/posts/42") is None

    def test_empty_resolver(self) -> None:
        resolver = AliasResolver()
        assert resolver.resolve("GET", "/anything") is None

    def test_partial_path_no_match(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/users/{id}", "users.get")
        assert resolver.resolve("GET", "/users/42/extra") is None


class TestColonSyntax:
    """Tests for Express-style :param syntax."""

    def test_colon_param(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/users/:id", "users.get")
        result = resolver.resolve("GET", "/users/42")
        assert result is not None
        assert result.params == {"id": "42"}

    def test_mixed_colon_and_brace(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/posts/:userId/{postId}", "posts.get")
        result = resolver.resolve("GET", "/posts/5/99")
        assert result is not None
        assert result.params == {"userId": "5", "postId": "99"}


class TestPriority:
    """Tests for alias resolution priority (first-match wins)."""

    def test_static_before_param(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/users/me", "users.me")
        resolver.add_alias("GET", "/users/{id}", "users.get")
        result = resolver.resolve("GET", "/users/me")
        assert result is not None
        assert result.action == "users.me"

    def test_param_if_no_static(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/users/me", "users.me")
        resolver.add_alias("GET", "/users/{id}", "users.get")
        result = resolver.resolve("GET", "/users/42")
        assert result is not None
        assert result.action == "users.get"


class TestURLDecoding:
    """Tests for URL-encoded path parameters."""

    def test_space_encoding(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/users/{name}", "users.get")
        result = resolver.resolve("GET", "/users/John%20Doe")
        assert result is not None
        assert result.params == {"name": "John Doe"}

    def test_special_chars(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/search/{query}", "search.run")
        result = resolver.resolve("GET", "/search/hello%26world")
        assert result is not None
        assert result.params == {"query": "hello&world"}


class TestPathParam:
    """Tests for {param:path} greedy matching."""

    def test_path_param(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/files/{path:path}", "files.get")
        result = resolver.resolve("GET", "/files/a/b/c")
        assert result is not None
        assert result.params == {"path": "a/b/c"}

    def test_path_param_single_segment(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/files/{path:path}", "files.get")
        result = resolver.resolve("GET", "/files/readme.txt")
        assert result is not None
        assert result.params == {"path": "readme.txt"}


class TestTrailingSlash:
    """Tests for trailing slash handling."""

    def test_with_trailing_slash(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/users/{id}", "users.get")
        result = resolver.resolve("GET", "/users/42/")
        assert result is not None
        assert result.params == {"id": "42"}

    def test_without_trailing_slash(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/users/{id}", "users.get")
        result = resolver.resolve("GET", "/users/42")
        assert result is not None
        assert result.params == {"id": "42"}


class TestUnicode:
    """Tests for unicode path parameters."""

    def test_unicode_path_param(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/users/{name}", "users.get")
        match = resolver.resolve("GET", "/users/%D0%98%D0%B2%D0%B0%D0%BD")
        assert match is not None
        assert match.params["name"] == "Иван"

    def test_unicode_in_path_literal(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/каталог/{id}", "catalog.get")
        match = resolver.resolve("GET", "/каталог/42")
        assert match is not None
        assert match.params["id"] == "42"

    def test_spaces_in_path_param(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/search/{query}", "search.exec")
        match = resolver.resolve("GET", "/search/hello%20world")
        assert match is not None
        assert match.params["query"] == "hello world"


class TestAliasMatchFields:
    """Tests for AliasMatch dataclass fields."""

    def test_alias_field(self) -> None:
        resolver = AliasResolver()
        resolver.add_alias("GET", "/users/:id", "users.get")
        result = resolver.resolve("GET", "/users/42")
        assert result is not None
        assert result.alias == "/users/{id}"
        assert result.action == "users.get"
        assert result.params == {"id": "42"}


class TestRESTShorthand:
    """Tests for REST shorthand alias generation."""

    def test_rest_all_6_routes(self) -> None:
        result = generate_rest_aliases("/users", "users")
        assert len(result) == 6
        assert result == {
            "GET /users": "users.list",
            "GET /users/{id}": "users.get",
            "POST /users": "users.create",
            "PUT /users/{id}": "users.update",
            "PATCH /users/{id}": "users.patch",
            "DELETE /users/{id}": "users.remove",
        }

    def test_rest_with_only(self) -> None:
        result = generate_rest_aliases("/users", {"action": "users", "only": ["list", "get"]})
        assert len(result) == 2
        assert result == {
            "GET /users": "users.list",
            "GET /users/{id}": "users.get",
        }

    def test_rest_with_except(self) -> None:
        result = generate_rest_aliases("/users", {"action": "users", "except": ["remove"]})
        assert len(result) == 5
        assert "DELETE /users/{id}" not in result
        assert "GET /users" in result

    def test_rest_with_only_and_except(self) -> None:
        result = generate_rest_aliases(
            "/users",
            {"action": "users", "only": ["list", "get", "create"], "except": ["create"]},
        )
        assert len(result) == 2
        assert result == {
            "GET /users": "users.list",
            "GET /users/{id}": "users.get",
        }

    def test_rest_dict_config(self) -> None:
        result = generate_rest_aliases("/products", {"action": "products", "only": ["list"]})
        assert result == {"GET /products": "products.list"}

    def test_rest_trailing_slash(self) -> None:
        result = generate_rest_aliases("/users/", "users")
        result_no_slash = generate_rest_aliases("/users", "users")
        assert result == result_no_slash

    def test_rest_action_names(self) -> None:
        result = generate_rest_aliases("/items", "items")
        actions = sorted(result.values())
        assert actions == [
            "items.create",
            "items.get",
            "items.list",
            "items.patch",
            "items.remove",
            "items.update",
        ]

    def test_rest_path_params(self) -> None:
        result = generate_rest_aliases("/orders", "orders")
        paths_with_id = [k for k in result if "{id}" in k]
        assert len(paths_with_id) == 4  # get, update, patch, remove

    def test_is_rest_shorthand(self) -> None:
        assert is_rest_shorthand("REST /users") is True
        assert is_rest_shorthand("  REST /users") is True
        assert is_rest_shorthand("GET /users") is False
        assert is_rest_shorthand("RESTORE /users") is False
        assert is_rest_shorthand("") is False

    def test_parse_rest_shorthand(self) -> None:
        assert parse_rest_shorthand("REST /users") == "/users"
        assert parse_rest_shorthand("  REST  /products ") == "/products"
        assert parse_rest_shorthand("REST /api/v1/items") == "/api/v1/items"
