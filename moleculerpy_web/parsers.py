"""Request body parsers for moleculerpy-web API Gateway."""

from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request

from moleculerpy_web.errors import BadRequestError, PayloadTooLargeError

#: Maximum allowed request body size in bytes (default: 1 MB).
MAX_BODY_SIZE: int = 1_048_576


async def parse_body(
    request: Request,
    max_body_size: int = MAX_BODY_SIZE,
) -> dict[str, Any]:
    """Parse request body based on content type.

    Currently supports:
        - application/json -> parsed JSON dict
        - (Phase 2: application/x-www-form-urlencoded)
        - (Phase 3: multipart/form-data)

    Returns empty dict if body is empty or content type unsupported.

    Raises:
        BadRequestError: If body exceeds max size or JSON is malformed.
    """
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        body = await request.body()
        if not body:
            return {}
        if len(body) > max_body_size:
            raise PayloadTooLargeError(
                f"Request body too large ({len(body)} bytes, max {max_body_size})",
            )
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            raise BadRequestError(
                f"Invalid JSON body: {e}",
                type="INVALID_REQUEST_BODY",
            ) from e
        # Only dict bodies can be merged into params. Arrays/scalars are rejected.
        if not isinstance(parsed, dict):
            raise BadRequestError(
                f"JSON body must be an object, got {type(parsed).__name__}",
                type="INVALID_REQUEST_BODY",
            )
        return parsed

    if "application/x-www-form-urlencoded" in content_type:
        body = await request.body()
        if not body:
            return {}
        if len(body) > max_body_size:
            raise PayloadTooLargeError(
                f"Request body too large ({len(body)} bytes, max {max_body_size})",
            )
        form_data = await request.form()
        result = dict(form_data)
        await form_data.close()
        return result

    return {}
