"""Request body parsers for moleculerpy-web API Gateway."""

from __future__ import annotations

import json
import ntpath
import os
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

    # Pre-check Content-Length to reject oversized requests before reading body
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_body_size:
                raise PayloadTooLargeError(
                    f"Content-Length {content_length} exceeds max {max_body_size}",
                )
        except ValueError:
            pass  # Invalid Content-Length — will be caught later

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

    if "multipart/form-data" in content_type:
        return await parse_multipart(request, max_body_size)

    return {}


def _sanitize_filename(filename: str | None) -> str | None:
    """Sanitize uploaded filename to prevent path traversal."""
    if filename is None:
        return None
    filename = filename.replace("\x00", "")
    # Handle both Unix and Windows path separators
    filename = ntpath.basename(filename)
    filename = os.path.basename(filename)
    filename = filename.lstrip(".")
    if len(filename) > 255:
        filename = filename[:255]
    return filename or "unnamed"


#: Maximum number of files per multipart request.
MAX_FILES_PER_REQUEST: int = 50


async def parse_multipart(
    request: Request,
    max_body_size: int = MAX_BODY_SIZE,
) -> dict[str, Any]:
    """Parse multipart/form-data request.

    Text fields are returned as strings. File uploads are returned as dicts
    with keys: filename, content_type, size, data (bytes).

    Requires python-multipart package (optional dependency).

    Raises:
        BadRequestError: If python-multipart is not installed.
        PayloadTooLargeError: If total size exceeds limit.
    """
    try:
        form_data = await request.form()
    except Exception as e:
        if "python-multipart" in str(e).lower() or "No install" in str(e):
            raise BadRequestError(
                "Multipart parsing requires python-multipart: pip install moleculerpy-web[multipart]",
                type="MISSING_DEPENDENCY",
            ) from e
        raise BadRequestError(f"Failed to parse multipart data: {e}") from e

    result: dict[str, Any] = {}
    total_size = 0
    file_count = 0

    try:
        for key, value in form_data.multi_items():
            if hasattr(value, "read"):
                # File upload (UploadFile)
                file_count += 1
                if file_count > MAX_FILES_PER_REQUEST:
                    raise BadRequestError(
                        f"Too many files ({file_count}, max {MAX_FILES_PER_REQUEST})",
                        type="TOO_MANY_FILES",
                    )
                upload: Any = value
                file_data = await upload.read()
                total_size += len(file_data)
                if total_size > max_body_size:
                    raise PayloadTooLargeError(
                        f"Multipart data too large ({total_size} bytes, max {max_body_size})",
                    )
                result[key] = {
                    "filename": _sanitize_filename(upload.filename),
                    "content_type": upload.content_type,
                    "size": len(file_data),
                    "data": file_data,
                }
            else:
                # Text field
                result[key] = value
    finally:
        await form_data.close()

    return result
