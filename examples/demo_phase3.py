"""Phase 3 Demo App — Comprehensive verification of all Phase 3 features.

Tests: Service Inheritance, Internal Actions, Auto-aliases, Streaming,
File Upload, Static Files, ETag/304.

Usage:
    # Start NATS first: docker run -p 4222:4222 nats
    python examples/demo_phase3.py

    # Or run smoke test only (no NATS required):
    python examples/demo_phase3.py --smoke
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
from moleculerpy import Service

from moleculerpy_web.service import ApiGatewayService
from moleculerpy_web.utils import generate_etag


# ---------------------------------------------------------------------------
# Smoke test (no NATS required)
# ---------------------------------------------------------------------------


async def smoke_test() -> None:
    """Verify ALL Phase 3 features with mock broker."""
    print("=" * 60)
    print("Phase 3 Smoke Test — All Features")
    print("=" * 60)

    passed = 0
    failed = 0

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  PASS  {name}")
        else:
            failed += 1
            print(f"  FAIL  {name} — {detail}")

    # --- 1. Service Inheritance ---
    print("\n--- 1. Service Inheritance ---")

    check(
        "ApiGatewayService inherits Service",
        issubclass(ApiGatewayService, Service),
    )

    svc = ApiGatewayService()
    check("Instance is Service", isinstance(svc, Service))
    check("Default name is 'api'", svc.name == "api")

    svc2 = ApiGatewayService(name="gateway", settings={"port": 4000})
    check("Name override works", svc2.name == "gateway")
    check("Settings override works", svc2.port == 4000)

    broker = MagicMock()
    svc3 = ApiGatewayService(broker=broker)
    check("Broker standalone mode", svc3.broker is broker)

    # Subclass
    class MyGateway(ApiGatewayService):
        name = "my-api"

    gw = MyGateway(settings={"port": 5000})
    check("Subclass works", gw.name == "my-api" and gw.port == 5000)
    check("Subclass isinstance", isinstance(gw, Service))

    # --- 2. Internal Actions ---
    print("\n--- 2. Internal Actions ---")

    mock_broker = MagicMock()
    mock_broker.call = AsyncMock(return_value={"ok": True})

    svc = ApiGatewayService(
        broker=mock_broker,
        settings={
            "path": "/api",
            "routes": [
                {
                    "path": "/v1",
                    "aliases": {
                        "GET /users": "users.list",
                        "POST /users": "users.create",
                    },
                }
            ],
        },
    )
    svc._build_routes()

    # listAliases
    aliases = await svc.list_aliases()
    check("listAliases returns list", isinstance(aliases, list))
    check("listAliases count", len(aliases) == 2, f"got {len(aliases)}")
    actions = {a["action"] for a in aliases}
    check("listAliases contains users.list", "users.list" in actions)

    # addRoute
    result = await svc.add_route(
        route={"path": "/v2", "aliases": {"GET /items": "items.list"}},
    )
    check("addRoute success", result["success"] is True)
    check("addRoute path", result["path"] == "/v2")

    aliases_after = await svc.list_aliases()
    check("addRoute reflected in listAliases", len(aliases_after) == 3, f"got {len(aliases_after)}")

    # removeRoute
    result = await svc.remove_route(path="/v2")
    check("removeRoute success", result["success"] is True)
    check("removeRoute count", result["removed"] == 1)

    aliases_final = await svc.list_aliases()
    check("removeRoute reflected", len(aliases_final) == 2, f"got {len(aliases_final)}")

    # --- 3. Auto-aliases ---
    print("\n--- 3. Auto-aliases ---")

    mock_broker2 = MagicMock()
    mock_broker2.registry.action_list = [
        {"name": "users.list", "rest": "GET /users"},
        {"name": "users.get", "rest": "GET /users/{id}"},
        {"name": "products.list", "rest": {"method": "GET", "path": "/products"}},
    ]

    auto_svc = ApiGatewayService(
        broker=mock_broker2,
        settings={
            "path": "/api",
            "routes": [{"path": "/auto", "autoAliases": True, "aliases": {}}],
        },
    )
    auto_svc._build_routes()

    count = auto_svc._regenerate_auto_aliases()
    check("Auto-aliases generated", count == 3, f"got {count}")

    auto_aliases = auto_svc._routes[0][1].aliases
    auto_actions = {a.action for a in auto_aliases}
    check("Auto-alias users.list", "users.list" in auto_actions)
    check("Auto-alias products.list", "products.list" in auto_actions)

    # $services.changed event
    await auto_svc._on_services_changed()
    check("$services.changed re-generates", len(auto_svc._routes[0][1].aliases) == 3)

    # --- 4. Streaming ---
    print("\n--- 4. Streaming Responses ---")

    from moleculerpy_web.handler import build_response
    from starlette.responses import StreamingResponse

    async def async_gen():
        yield b"chunk1"
        yield b"chunk2"

    resp = build_response(async_gen())
    check("Async generator -> StreamingResponse", isinstance(resp, StreamingResponse))

    def sync_gen():
        yield b"data1"
        yield b"data2"

    resp2 = build_response(sync_gen())
    check("Sync generator -> StreamingResponse", isinstance(resp2, StreamingResponse))

    # Non-streaming types
    resp3 = build_response({"key": "value"})
    check("Dict -> JSONResponse (not streaming)", not isinstance(resp3, StreamingResponse))

    resp4 = build_response([1, 2, 3])
    check("List -> JSONResponse (not streaming)", not isinstance(resp4, StreamingResponse))

    # --- 5. File Upload (multipart) ---
    print("\n--- 5. File Upload (multipart) ---")

    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient
    from moleculerpy_web.parsers import parse_body
    import base64

    async def echo(request: Request) -> JSONResponse:
        result = await parse_body(request)
        # Convert bytes to base64 for JSON
        for k, v in result.items():
            if isinstance(v, dict) and "data" in v and isinstance(v["data"], bytes):
                v["data"] = base64.b64encode(v["data"]).decode()
        return JSONResponse(result)

    test_app = Starlette(routes=[Route("/upload", echo, methods=["POST"])])
    client = TestClient(test_app)

    resp = client.post(
        "/upload",
        data={"name": "test"},
        files={"file": ("hello.txt", b"Hello World!", "text/plain")},
    )
    check("Multipart text field", resp.json().get("name") == "test")
    check("Multipart file uploaded", resp.json().get("file", {}).get("filename") == "hello.txt")
    check("Multipart file size", resp.json().get("file", {}).get("size") == 12)

    # --- 6. Static Files ---
    print("\n--- 6. Static Files ---")

    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "test.txt").write_text("Static content!")
        Path(tmpdir, "index.html").write_text("<h1>Home</h1>")

        static_svc = ApiGatewayService(
            broker=MagicMock(),
            settings={
                "path": "/api",
                "routes": [],
                "assets": {"folder": tmpdir, "path": "/static"},
            },
        )
        static_svc._build_routes()
        static_svc._app = static_svc._create_app()

        transport = httpx.ASGITransport(app=static_svc.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/static/test.txt")
            check("Static file served", r.status_code == 200 and r.text == "Static content!")

            r2 = await c.get("/static/nonexistent.txt")
            check("Static 404 for missing", r2.status_code == 404)

    # --- 7. ETag + 304 ---
    print("\n--- 7. ETag + Conditional GET ---")

    data = {"users": [1, 2, 3]}
    body = json.dumps(data, separators=(",", ":")).encode()
    etag = generate_etag(body)
    check("ETag format W/\"...\"", etag.startswith('W/"') and etag.endswith('"'))

    # ETag with mock request
    mock_req = MagicMock()
    mock_req.headers = {}
    resp = build_response(data, etag=True, request=mock_req)
    check("ETag header added", "ETag" in resp.headers)
    check("ETag response 200", resp.status_code == 200)

    # 304 Not Modified
    mock_req2 = MagicMock()
    mock_req2.headers = {"if-none-match": resp.headers["ETag"]}
    resp2 = build_response(data, etag=True, request=mock_req2)
    check("304 on matching ETag", resp2.status_code == 304)

    # Mismatched ETag
    mock_req3 = MagicMock()
    mock_req3.headers = {"if-none-match": 'W/"old-hash"'}
    resp3 = build_response(data, etag=True, request=mock_req3)
    check("200 on mismatched ETag", resp3.status_code == 200)

    # --- 8. E2E: Full Gateway Pipeline ---
    print("\n--- 8. Full Gateway Pipeline ---")

    mock_broker3 = MagicMock()
    mock_broker3.call = AsyncMock(return_value={"id": 1, "name": "Alice"})

    gw = ApiGatewayService(
        broker=mock_broker3,
        settings={
            "path": "/api",
            "routes": [
                {
                    "path": "/",
                    "aliases": {
                        "GET /users": "users.list",
                        "GET /users/{id}": "users.get",
                        "POST /users": "users.create",
                    },
                    "etag": True,
                }
            ],
        },
    )
    gw._build_routes()
    gw._app = gw._create_app()

    transport = httpx.ASGITransport(app=gw.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # GET
        r = await c.get("/api/users")
        check("GET /api/users 200", r.status_code == 200)
        check("GET response body", r.json() == {"id": 1, "name": "Alice"})
        check("GET has ETag", "ETag" in r.headers or "etag" in r.headers)

        # 304 with ETag
        etag_val = r.headers.get("ETag", r.headers.get("etag", ""))
        if etag_val:
            r2 = await c.get("/api/users", headers={"If-None-Match": etag_val})
            check("304 conditional GET", r2.status_code == 304)

        # POST
        r3 = await c.post("/api/users", json={"name": "Bob"})
        check("POST /api/users 200", r3.status_code == 200)

        # 404
        r4 = await c.get("/api/unknown")
        check("404 for unknown", r4.status_code == 404)

    # --- Summary ---
    print("\n" + "=" * 60)
    total = passed + failed
    print(f"Results: {passed}/{total} passed, {failed} failed")
    if failed == 0:
        print("ALL PHASE 3 FEATURES VERIFIED!")
    else:
        print(f"WARNING: {failed} test(s) failed!")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    if "--smoke" in sys.argv or True:  # Default to smoke test
        success = asyncio.run(smoke_test())
        sys.exit(0 if success else 1)
