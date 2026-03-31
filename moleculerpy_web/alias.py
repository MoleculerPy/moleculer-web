"""Alias resolution for moleculerpy-web API Gateway.

Maps HTTP method + path patterns to Moleculer action names,
extracting path parameters along the way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import unquote

# Pattern to convert Express-style :param to {param} (only after /)
_COLON_PARAM_RE = re.compile(r"(?<=/):([a-zA-Z_][a-zA-Z0-9_]*)")

# Pattern to find {param} or {param:path} placeholders
_BRACE_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)(?::path)?\}")

# Pattern to replace {param:path} with greedy regex
_PATH_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*):path\}")

# Pattern to replace {param} with non-greedy segment regex
_SEGMENT_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def colon_to_brace(path: str) -> str:
    """Convert Express-style :param to {param} in path.

    Examples:
        >>> colon_to_brace("/users/:id")
        '/users/{id}'
        >>> colon_to_brace("/posts/:userId/comments/:commentId")
        '/posts/{userId}/comments/{commentId}'
    """
    return _COLON_PARAM_RE.sub(r"{\1}", path)


def _path_to_regex(path: str) -> tuple[re.Pattern[str], list[str]]:
    """Convert path with {param} placeholders to compiled regex + param names.

    ``{param}`` matches a single path segment (``[^/]+``).
    ``{param:path}`` matches the rest of the path (greedy ``.+``).

    Returns:
        Tuple of compiled regex pattern and ordered list of parameter names.
    """
    param_names = [m.group(1) for m in _BRACE_PARAM_RE.finditer(path)]

    # Two-pass approach: replace params with placeholders, escape literals, restore
    _PH_PATH = "\x00PATH_PARAM_{}\x00"
    _PH_SEG = "\x00SEG_PARAM_{}\x00"

    temp = path
    path_params: list[str] = []
    seg_params: list[str] = []

    # Pass 1: replace {param:path} with placeholders
    for m in _PATH_PARAM_RE.finditer(temp):
        path_params.append(m.group(1))
    for i, name in enumerate(path_params):
        temp = temp.replace(f"{{{name}:path}}", _PH_PATH.format(i), 1)

    # Replace {param} with placeholders
    for m in _SEGMENT_PARAM_RE.finditer(temp):
        seg_params.append(m.group(1))
    for i, name in enumerate(seg_params):
        temp = temp.replace(f"{{{name}}}", _PH_SEG.format(i), 1)

    # Pass 2: escape literal parts
    temp = re.escape(temp)

    # Pass 3: restore capture groups
    for i, name in enumerate(path_params):
        temp = temp.replace(re.escape(_PH_PATH.format(i)), f"(?P<{name}>.+)")
    for i, name in enumerate(seg_params):
        temp = temp.replace(re.escape(_PH_SEG.format(i)), f"(?P<{name}>[^/]+)")

    # Allow optional trailing slash
    pattern = f"^{temp}/?$"
    return re.compile(pattern), param_names


@dataclass(slots=True)
class Alias:
    """A single route alias mapping HTTP method + path to a Moleculer action."""

    method: str
    path: str
    action: str
    regex: re.Pattern[str]
    param_names: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AliasMatch:
    """Result of a successful alias resolution."""

    action: str
    params: dict[str, str]
    alias: str


class AliasResolver:
    """Resolves HTTP requests to Moleculer actions via registered aliases."""

    def __init__(self) -> None:
        self._aliases: list[Alias] = []

    def add_alias(self, method: str, path: str, action: str) -> None:
        """Register a new alias.

        Args:
            method: HTTP method (GET, POST, etc.) or ``*`` for all methods.
            path: URL pattern with optional ``{param}`` or ``:param`` placeholders.
            action: Moleculer action name to invoke.
        """
        normalized = colon_to_brace(path)
        regex, param_names = _path_to_regex(normalized)
        alias = Alias(
            method=method.upper(),
            path=normalized,
            action=action,
            regex=regex,
            param_names=param_names,
        )
        self._aliases.append(alias)

    def resolve(self, method: str, path: str) -> AliasMatch | None:
        """Resolve an HTTP request to a matching alias.

        Args:
            method: HTTP method of the incoming request.
            path: URL path of the incoming request.

        Returns:
            An ``AliasMatch`` if a matching alias is found, otherwise ``None``.
        """
        upper_method = method.upper()
        for alias in self._aliases:
            if alias.method != "*" and alias.method != upper_method:
                continue
            match = alias.regex.match(path)
            if match:
                # Determine which params are :path type (allow /)
                path_param_names = {m.group(1) for m in _PATH_PARAM_RE.finditer(alias.path)}
                params = {}
                for name, value in match.groupdict().items():
                    if value is None:
                        continue
                    decoded = unquote(value)
                    # Strip / from non-path params to prevent path traversal via %2F
                    if name not in path_param_names:
                        decoded = decoded.replace("/", "")
                    params[name] = decoded
                return AliasMatch(
                    action=alias.action,
                    params=params,
                    alias=alias.path,
                )
        return None
