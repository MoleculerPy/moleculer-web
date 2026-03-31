from __future__ import annotations

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from moleculerpy_web.parsers import parse_body


async def _echo_body(request: Request) -> JSONResponse:
    """Test endpoint that echoes parsed body back."""
    from moleculerpy_web.errors import GatewayError

    try:
        result = await parse_body(request)
    except GatewayError as e:
        return JSONResponse(e.to_response_dict(), status_code=e.status_code)
    return JSONResponse({"parsed": result})


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
