"""Tests for moleculerpy_web.access module."""

from __future__ import annotations

import re

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from moleculerpy_web.access import (
    BlacklistMiddleware,
    WhitelistMiddleware,
    check_blacklist,
    check_whitelist,
    matches_pattern,
)
from moleculerpy_web.errors import NotFoundError
from moleculerpy_web.middleware import RequestContext


def _make_request(method: str = "GET", path: str = "/test") -> Request:
    """Create a minimal Starlette Request for testing."""
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [],
    }
    return Request(scope)


def _make_ctx(action: str = "users.list") -> RequestContext:
    """Create a RequestContext with the given action."""
    return RequestContext(
        request=_make_request(),
        action=action,
        params={},
    )


async def _ok_handler(ctx: RequestContext) -> Response:
    """Terminal handler that returns 200 OK."""
    return JSONResponse({"ok": True})


class TestMatchesPattern:
    """Tests for matches_pattern function."""

    def test_fnmatch_wildcard_matches(self) -> None:
        assert matches_pattern("users.list", "users.*") is True

    def test_fnmatch_exact_matches(self) -> None:
        assert matches_pattern("users.list", "users.list") is True

    def test_fnmatch_no_match(self) -> None:
        assert matches_pattern("admin.delete", "users.*") is False

    def test_regex_matches(self) -> None:
        assert matches_pattern("admin.delete", re.compile(r"^admin\.")) is True

    def test_regex_no_match(self) -> None:
        assert matches_pattern("users.list", re.compile(r"^admin\.")) is False

    def test_fnmatch_question_mark(self) -> None:
        assert matches_pattern("users.get", "users.g?t") is True
        assert matches_pattern("users.list", "users.g?t") is False

    def test_double_wildcard(self) -> None:
        assert matches_pattern("deep.nested.action", "*.*.*") is True


class TestCheckWhitelist:
    """Tests for check_whitelist function."""

    def test_matches_any_pattern(self) -> None:
        assert check_whitelist("users.list", ["admin.*", "users.*"]) is True

    def test_empty_list_returns_false(self) -> None:
        assert check_whitelist("users.list", []) is False

    def test_no_match_returns_false(self) -> None:
        assert check_whitelist("users.list", ["admin.*", "posts.*"]) is False

    def test_mixed_string_and_regex(self) -> None:
        patterns = ["admin.*", re.compile(r"^users\.")]
        assert check_whitelist("users.get", patterns) is True


class TestCheckBlacklist:
    """Tests for check_blacklist function."""

    def test_matches_any_pattern(self) -> None:
        assert check_blacklist("admin.delete", ["admin.*"]) is True

    def test_no_match_returns_false(self) -> None:
        assert check_blacklist("users.list", ["admin.*"]) is False


class TestWhitelistMiddleware:
    """Tests for WhitelistMiddleware."""

    @pytest.mark.asyncio
    async def test_allowed_action_passes_through(self) -> None:
        mw = WhitelistMiddleware(["users.*", "posts.*"])
        response = await mw(_make_ctx("users.list"), _ok_handler)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_blocked_action_raises_not_found(self) -> None:
        mw = WhitelistMiddleware(["users.*"])
        with pytest.raises(NotFoundError, match="not in the whitelist"):
            await mw(_make_ctx("admin.delete"), _ok_handler)

    @pytest.mark.asyncio
    async def test_empty_whitelist_blocks_all(self) -> None:
        mw = WhitelistMiddleware([])
        with pytest.raises(NotFoundError):
            await mw(_make_ctx("users.list"), _ok_handler)


class TestBlacklistMiddleware:
    """Tests for BlacklistMiddleware."""

    @pytest.mark.asyncio
    async def test_blocked_action_raises_not_found(self) -> None:
        mw = BlacklistMiddleware(["admin.*"])
        with pytest.raises(NotFoundError, match="blocked by blacklist"):
            await mw(_make_ctx("admin.delete"), _ok_handler)

    @pytest.mark.asyncio
    async def test_allowed_action_passes_through(self) -> None:
        mw = BlacklistMiddleware(["admin.*"])
        response = await mw(_make_ctx("users.list"), _ok_handler)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_empty_blacklist_allows_all(self) -> None:
        mw = BlacklistMiddleware([])
        response = await mw(_make_ctx("admin.delete"), _ok_handler)
        assert response.status_code == 200


class TestCombinedAccess:
    """Test whitelist + blacklist together in a pipeline."""

    @pytest.mark.asyncio
    async def test_whitelist_then_blacklist(self) -> None:
        """Action must pass whitelist AND not be in blacklist."""
        from moleculerpy_web.middleware import compose_middleware

        whitelist = WhitelistMiddleware(["users.*", "admin.*"])
        blacklist = BlacklistMiddleware(["admin.delete"])

        composed = compose_middleware([whitelist, blacklist], _ok_handler)

        # users.list: passes whitelist, not in blacklist -> OK
        response = await composed(_make_ctx("users.list"))
        assert response.status_code == 200

        # admin.list: passes whitelist, not in blacklist -> OK
        response = await composed(_make_ctx("admin.list"))
        assert response.status_code == 200

        # admin.delete: passes whitelist, but in blacklist -> blocked
        with pytest.raises(NotFoundError, match="blocked by blacklist"):
            await composed(_make_ctx("admin.delete"))

        # posts.list: not in whitelist -> blocked
        with pytest.raises(NotFoundError, match="not in the whitelist"):
            await composed(_make_ctx("posts.list"))
