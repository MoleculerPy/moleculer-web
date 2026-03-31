"""Whitelist/blacklist access control for moleculerpy-web API Gateway.

Provides pattern-based action filtering using fnmatch wildcards and regex,
plus middleware classes for integrating access control into the pipeline.
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from starlette.responses import Response

from moleculerpy_web.errors import NotFoundError

if TYPE_CHECKING:
    from moleculerpy_web.middleware import NextHandler, RequestContext


def matches_pattern(action: str, pattern: str | re.Pattern[str]) -> bool:
    """Check if action name matches a single pattern.

    String patterns use fnmatch (shell-style wildcards: *, ?).
    Regex patterns use re.search.

    Args:
        action: Fully qualified action name (e.g., "users.list").
        pattern: fnmatch string pattern or compiled regex.

    Returns:
        True if the action matches the pattern.

    Examples:
        >>> matches_pattern("users.list", "users.*")
        True
        >>> matches_pattern("admin.delete", "users.*")
        False
        >>> matches_pattern("users.list", re.compile(r"^users\\."))
        True
    """
    if isinstance(pattern, re.Pattern):
        return pattern.search(action) is not None
    return fnmatch.fnmatch(action, pattern)


def check_whitelist(action: str, patterns: Sequence[str | re.Pattern[str]]) -> bool:
    """Check if action is allowed by whitelist patterns.

    Args:
        action: Fully qualified action name.
        patterns: List of allowed patterns.

    Returns:
        True if action matches ANY pattern in the whitelist.
    """
    return any(matches_pattern(action, p) for p in patterns)


def check_blacklist(action: str, patterns: Sequence[str | re.Pattern[str]]) -> bool:
    """Check if action is blocked by blacklist patterns.

    Args:
        action: Fully qualified action name.
        patterns: List of blocked patterns.

    Returns:
        True if action matches ANY pattern in the blacklist.
    """
    return any(matches_pattern(action, p) for p in patterns)


class WhitelistMiddleware:
    """Middleware that checks action against whitelist patterns.

    If action doesn't match whitelist, raises NotFoundError
    (same as Node.js -- looks like action doesn't exist).

    Args:
        patterns: List of allowed action patterns (fnmatch or regex).
    """

    def __init__(self, patterns: Sequence[str | re.Pattern[str]]) -> None:
        self._patterns = patterns

    async def __call__(self, ctx: RequestContext, next_handler: NextHandler) -> Response:
        """Check action against whitelist, then proceed or reject.

        Args:
            ctx: Request context with resolved action name.
            next_handler: Next middleware or terminal handler.

        Returns:
            Response from downstream handler.

        Raises:
            NotFoundError: If action is not in the whitelist.
        """
        if not check_whitelist(ctx.action, self._patterns):
            raise NotFoundError(f"Action '{ctx.action}' is not in the whitelist")
        return await next_handler(ctx)


class BlacklistMiddleware:
    """Middleware that checks action against blacklist patterns.

    If action matches blacklist, raises NotFoundError.

    Args:
        patterns: List of blocked action patterns (fnmatch or regex).
    """

    def __init__(self, patterns: Sequence[str | re.Pattern[str]]) -> None:
        self._patterns = patterns

    async def __call__(self, ctx: RequestContext, next_handler: NextHandler) -> Response:
        """Check action against blacklist, then proceed or reject.

        Args:
            ctx: Request context with resolved action name.
            next_handler: Next middleware or terminal handler.

        Returns:
            Response from downstream handler.

        Raises:
            NotFoundError: If action is blocked by the blacklist.
        """
        if check_blacklist(ctx.action, self._patterns):
            raise NotFoundError(f"Action '{ctx.action}' is blocked by blacklist")
        return await next_handler(ctx)
