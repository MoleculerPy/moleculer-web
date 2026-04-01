from __future__ import annotations

from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from moleculerpy_web.parsers import _sanitize_filename, parse_body, parse_multipart


def _make_json_safe(obj: Any) -> Any:
    """Convert bytes to base64 strings for JSON serialization."""
    import base64

    if isinstance(obj, bytes):
        return base64.b64encode(obj).decode()
    if isinstance(obj, dict):
        return {k: _make_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_make_json_safe(v) for v in obj]
    return obj


async def _echo_body(request: Request) -> JSONResponse:
    """Test endpoint that echoes parsed body back."""
    from moleculerpy_web.errors import GatewayError

    try:
        result = await parse_body(request)
    except GatewayError as e:
        return JSONResponse(e.to_response_dict(), status_code=e.status_code)
    return JSONResponse({"parsed": _make_json_safe(result)})


app = Starlette(routes=[Route("/test", _echo_body, methods=["POST"])])
client = TestClient(app)


class TestParseBody:
    def test_json_body(self) -> None:
        resp = client.post(
            "/test",
            json={"name": "John", "age": 30},
        )
        assert resp.status_code == 200
        assert resp.json()["parsed"] == {"name": "John", "age": 30}

    def test_empty_json_body(self) -> None:
        resp = client.post(
            "/test",
            content=b"",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["parsed"] == {}

    def test_unsupported_content_type(self) -> None:
        resp = client.post(
            "/test",
            content=b"key=value",
            headers={"content-type": "text/plain"},
        )
        assert resp.status_code == 200
        assert resp.json()["parsed"] == {}

    def test_no_content_type(self) -> None:
        resp = client.post(
            "/test",
            content=b"some data",
        )
        assert resp.status_code == 200
        assert resp.json()["parsed"] == {}

    def test_json_array_body_rejected(self) -> None:
        """Bug #2: JSON arrays should be rejected — only objects allowed."""
        resp = client.post(
            "/test",
            content=b"[1, 2, 3]",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == "INVALID_REQUEST_BODY"

    def test_json_string_body_rejected(self) -> None:
        """JSON scalar strings should be rejected — only objects allowed."""
        resp = client.post(
            "/test",
            content=b'"just a string"',
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["type"] == "INVALID_REQUEST_BODY"


class TestMultipartParsing:
    """Test multipart/form-data parsing."""

    def test_text_fields(self) -> None:
        """Multipart text fields should be parsed as strings."""
        resp = client.post(
            "/test",
            data={"name": "Alice", "role": "admin"},
        )
        assert resp.status_code == 200
        parsed = resp.json()["parsed"]
        assert parsed["name"] == "Alice"
        assert parsed["role"] == "admin"

    def test_file_upload(self) -> None:
        """File uploads should be parsed with metadata."""
        resp = client.post(
            "/test",
            files={"avatar": ("photo.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        assert resp.status_code == 200
        parsed = resp.json()["parsed"]
        assert "avatar" in parsed
        avatar = parsed["avatar"]
        assert avatar["filename"] == "photo.png"
        assert avatar["content_type"] == "image/png"
        assert avatar["size"] == 8

    def test_mixed_fields_and_files(self) -> None:
        """Mix of text fields and file uploads."""
        resp = client.post(
            "/test",
            data={"description": "My photo"},
            files={"file": ("doc.txt", b"Hello world", "text/plain")},
        )
        assert resp.status_code == 200
        parsed = resp.json()["parsed"]
        assert parsed["description"] == "My photo"
        assert parsed["file"]["filename"] == "doc.txt"
        assert parsed["file"]["size"] == 11


class TestSanitizeFilename:
    """Test _sanitize_filename security function."""

    def test_normal_filename(self) -> None:
        assert _sanitize_filename("photo.png") == "photo.png"

    def test_none_returns_none(self) -> None:
        assert _sanitize_filename(None) is None

    def test_path_traversal(self) -> None:
        assert _sanitize_filename("../../etc/passwd") == "passwd"

    def test_windows_path_traversal(self) -> None:
        result = _sanitize_filename("..\\..\\windows\\system32\\config")
        assert ".." not in result

    def test_null_bytes_stripped(self) -> None:
        assert "\x00" not in (_sanitize_filename("evil\x00.sh") or "")

    def test_leading_dot_stripped(self) -> None:
        assert _sanitize_filename(".env") == "env"
        assert _sanitize_filename("...hidden") == "hidden"

    def test_long_filename_truncated(self) -> None:
        long_name = "a" * 300 + ".txt"
        result = _sanitize_filename(long_name)
        assert result is not None
        assert len(result) <= 255

    def test_empty_after_sanitize(self) -> None:
        """If all chars are stripped, return 'unnamed'."""
        assert _sanitize_filename("...") == "unnamed"

    def test_just_slashes(self) -> None:
        result = _sanitize_filename("///")
        assert result is not None
        assert "/" not in result
