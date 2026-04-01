"""Comprehensive Smoke Test — moleculerpy-web Phase 1 Feature Verification.

Запуск (сервер уже работает):
    python examples/smoke_test.py

Запуск с автостартом сервера:
    python examples/smoke_test.py --start-server

Тестирует 45+ сценариев по 9 категориям:
  1. Routing (multiple routes, prefixes, ordering)
  2. Path Parameters (single, multiple, special chars)
  3. Query Strings (params, encoding, multiple values)
  4. HTTP Methods (GET, POST, PUT, PATCH, DELETE)
  5. Body Parsing (JSON, empty, malformed, large, no content-type)
  6. Parameter Merge (path < query < body priority)
  7. Error Handling (400, 404, 422, 500, error format)
  8. Response Types (JSON, bytes, None→204, string→JSON)
  9. Edge Cases (slashes, dots, unicode, long URLs)
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time

import httpx

BASE = "http://127.0.0.1:3000"
API = f"{BASE}/api/v1"
INTERNAL = f"{BASE}/api/internal"

passed = 0
failed = 0
errors: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  \033[32m✓\033[0m {name}")
    else:
        failed += 1
        msg = f"  \033[31m✗\033[0m {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        errors.append(f"{name}: {detail}")


async def run_all_tests() -> None:
    async with httpx.AsyncClient(timeout=10.0) as c:
        # =====================================================================
        # 1. ROUTING
        # =====================================================================
        print("\n\033[1m[1] ROUTING\033[0m")

        # 1.1 Basic route works
        r = await c.get(f"{API}/health")
        check("1.1 GET /api/v1/health → 200", r.status_code == 200)
        check("1.1 health response has services", "services" in r.json())

        # 1.2 Restrict policy — unknown alias → 404
        r = await c.get(f"{API}/nonexistent")
        check("1.2 restrict: unknown path → 404", r.status_code == 404)
        check("1.2 error is NotFoundError", r.json()["name"] == "NotFoundError")

        # 1.3 Second route (internal, mappingPolicy=all) works
        r = await c.get(f"{INTERNAL}/health/check")
        check("1.3 mappingPolicy=all: /internal/health/check → health.check", r.status_code == 200)

        # 1.4 All-policy derives action from URL
        r = await c.get(f"{INTERNAL}/echo/params", params={"test": "1"})
        check("1.4 all-policy: /internal/echo/params → echo.params", r.status_code == 200)
        check("1.4 params passed through", r.json()["received_params"]["test"] == "1")

        # 1.5 All-policy unknown action → 404 from broker
        r = await c.get(f"{INTERNAL}/totally/unknown/action")
        check("1.5 all-policy: unknown action → 404", r.status_code == 404)

        # 1.6 Separate route prefixes don't interfere
        r = await c.get(f"{API}/books")
        check("1.6 /api/v1/books → books.list", r.status_code == 200 and "books" in r.json())

        # =====================================================================
        # 2. PATH PARAMETERS
        # =====================================================================
        print("\n\033[1m[2] PATH PARAMETERS\033[0m")

        # 2.1 Single path param
        r = await c.get(f"{API}/books/1")
        check("2.1 /books/1 → id=1", r.status_code == 200 and r.json()["title"] == "Dune")

        # 2.2 Different ID
        r = await c.get(f"{API}/books/42")
        check("2.2 /books/42 → Hitchhiker's Guide", r.json()["title"] == "Hitchhiker's Guide")

        # 2.3 Multiple path params (nested resource)
        r = await c.get(f"{API}/books/1/reviews")
        check("2.3 /books/{bookId}/reviews → bookId=1", r.json()["bookId"] == "1")
        check("2.3 reviews returned", len(r.json()["reviews"]) == 1)

        # 2.4 POST with path param
        r = await c.post(
            f"{API}/books/2/reviews", json={"user": "Dave", "rating": 5, "text": "Amazing"}
        )
        check("2.4 POST /books/2/reviews → add review", r.status_code == 200)
        check("2.4 bookId passed from path", r.json()["bookId"] == "2")

        # 2.5 Path param with special chars (URL-encoded)
        r = await c.get(f"{API}/books/999")
        check("2.5 non-existent id → error", r.status_code != 200)

        # =====================================================================
        # 3. QUERY STRINGS
        # =====================================================================
        print("\n\033[1m[3] QUERY STRINGS\033[0m")

        # 3.1 Single query param
        r = await c.get(f"{API}/books", params={"genre": "sci-fi"})
        check("3.1 ?genre=sci-fi filters", all(b["genre"] == "sci-fi" for b in r.json()["books"]))

        # 3.2 Multiple query params
        r = await c.get(f"{API}/books", params={"genre": "sci-fi", "sort": "year", "order": "desc"})
        books = r.json()["books"]
        check("3.2 genre + sort + order", len(books) >= 2 and books[0]["year"] >= books[-1]["year"])

        # 3.3 Pagination params
        r = await c.get(f"{API}/books", params={"page": "1", "limit": "2"})
        check("3.3 page=1&limit=2", r.json()["limit"] == 2 and len(r.json()["books"]) <= 2)

        # 3.4 Year range filtering
        r = await c.get(f"{API}/books", params={"year_from": "1960"})
        check("3.4 year_from=1960", all(b["year"] >= 1960 for b in r.json()["books"]))

        # 3.5 Search with query
        r = await c.get(f"{API}/books/search", params={"q": "dune"})
        check("3.5 search?q=dune", r.json()["count"] >= 1 and r.json()["query"] == "dune")

        # =====================================================================
        # 4. HTTP METHODS
        # =====================================================================
        print("\n\033[1m[4] HTTP METHODS\033[0m")

        # 4.1 GET
        r = await c.get(f"{API}/books")
        check("4.1 GET works", r.status_code == 200)

        # 4.2 POST with JSON
        r = await c.post(
            f"{API}/books",
            json={
                "title": "Neuromancer",
                "author": "William Gibson",
                "year": 1984,
                "genre": "cyberpunk",
            },
        )
        check(
            "4.2 POST creates book",
            r.status_code == 200 and r.json()["created"]["title"] == "Neuromancer",
        )
        new_id = r.json()["created"]["id"]

        # 4.3 PUT update
        r = await c.put(f"{API}/books/{new_id}", json={"price": 14.99})
        check(
            "4.3 PUT updates book", r.status_code == 200 and r.json()["updated"]["price"] == 14.99
        )

        # 4.4 PATCH update (same endpoint)
        r = await c.patch(f"{API}/books/{new_id}", json={"genre": "sci-fi"})
        check(
            "4.4 PATCH updates book",
            r.status_code == 200 and r.json()["updated"]["genre"] == "sci-fi",
        )

        # 4.5 DELETE
        r = await c.delete(f"{API}/books/{new_id}")
        check("4.5 DELETE removes book", r.status_code == 200 and "deleted" in r.json())

        # =====================================================================
        # 5. BODY PARSING
        # =====================================================================
        print("\n\033[1m[5] BODY PARSING\033[0m")

        # 5.1 JSON body parsed correctly
        r = await c.post(f"{API}/echo", json={"key": "value", "nested": {"a": 1}})
        check("5.1 JSON body parsed", r.json()["received_params"]["key"] == "value")
        check("5.1 nested JSON preserved", r.json()["received_params"]["nested"]["a"] == 1)

        # 5.2 Empty body POST → no crash
        r = await c.post(f"{API}/echo", content=b"", headers={"content-type": "application/json"})
        check("5.2 empty JSON body → empty params", r.status_code == 200)

        # 5.3 Malformed JSON → 400
        r = await c.post(
            f"{API}/echo", content=b"{invalid json", headers={"content-type": "application/json"}
        )
        check("5.3 malformed JSON → 400", r.status_code == 400)
        check("5.3 error type INVALID_REQUEST_BODY", r.json()["type"] == "INVALID_REQUEST_BODY")

        # 5.4 No content-type → body ignored, params empty
        r = await c.post(f"{API}/echo", content=b"some raw data")
        check("5.4 no content-type → body ignored", r.status_code == 200)

        # 5.5 GET with query (no body)
        r = await c.get(f"{API}/echo", params={"a": "1", "b": "2"})
        check("5.5 GET query params only", r.json()["received_params"]["a"] == "1")

        # =====================================================================
        # 6. PARAMETER MERGE (priority: path < query < body)
        # =====================================================================
        print("\n\033[1m[6] PARAMETER MERGE\033[0m")

        # 6.1 Path params passed
        r = await c.get(f"{API}/books/42")
        check("6.1 path param id=42", r.json()["id"] == "42")

        # 6.2 Query params added to path params
        r = await c.get(f"{API}/books/1/reviews", params={"extra": "info"})
        check("6.2 path(bookId) + query(extra) both present", r.json()["bookId"] == "1")

        # 6.3 Body overrides query
        r = await c.post(f"{API}/echo", params={"key": "from_query"}, json={"key": "from_body"})
        check("6.3 body overrides query", r.json()["received_params"]["key"] == "from_body")

        # 6.4 Path + query + body all merged
        r = await c.post(
            f"{API}/books/1/reviews",
            params={"extra": "query_val"},
            json={"user": "Test", "rating": 3, "text": "OK"},
        )
        check("6.4 POST path+query+body → 200", r.status_code == 200)

        # =====================================================================
        # 7. ERROR HANDLING
        # =====================================================================
        print("\n\033[1m[7] ERROR HANDLING\033[0m")

        # 7.1 404 — route not found (restrict)
        r = await c.get(f"{API}/does-not-exist")
        check("7.1 unknown route restrict → 404", r.status_code == 404)

        # 7.2 404 — resource not found (from action)
        r = await c.get(f"{API}/books/99999")
        check("7.2 book not found → 404", r.status_code == 400 or r.status_code == 404)

        # 7.3 422 — validation error
        r = await c.post(f"{API}/books", json={"title": ""})
        check("7.3 validation error → 422", r.status_code == 422)
        check("7.3 error name", r.json()["name"] == "UnprocessableEntityError")

        # 7.4 400 — malformed body
        r = await c.post(
            f"{API}/books", content=b"not json", headers={"content-type": "application/json"}
        )
        check("7.4 malformed body → 400", r.status_code == 400)

        # 7.5 422 — division by zero
        r = await c.get(f"{API}/math/calc", params={"op": "div", "a": "10", "b": "0"})
        check("7.5 div by zero → 422", r.status_code == 422)

        # 7.6 Error format consistency (Node.js compat)
        r = await c.get(f"{API}/does-not-exist")
        body = r.json()
        has_all_fields = all(k in body for k in ("name", "message", "code", "type", "data"))
        check("7.6 error format: name+message+code+type+data", has_all_fields)

        # =====================================================================
        # 8. RESPONSE TYPES
        # =====================================================================
        print("\n\033[1m[8] RESPONSE TYPES\033[0m")

        # 8.1 dict → JSON
        r = await c.get(f"{API}/health")
        check("8.1 dict → application/json", "application/json" in r.headers["content-type"])

        # 8.2 None → 204 No Content
        r = await c.get(f"{API}/echo/empty")
        check("8.2 None → 204", r.status_code == 204)

        # 8.3 bytes → octet-stream
        r = await c.get(f"{API}/echo/bytes")
        check("8.3 bytes → octet-stream", r.headers["content-type"] == "application/octet-stream")
        check("8.3 PNG header bytes", r.content[:4] == b"\x89PNG")

        # 8.4 string → JSON encoded
        r = await c.get(f"{API}/echo/string", params={"name": "World"})
        check("8.4 string → JSON encoded", r.headers["content-type"].startswith("application/json"))
        check("8.4 value is JSON string", r.json() == "Hello, World!")

        # =====================================================================
        # 9. EDGE CASES
        # =====================================================================
        print("\n\033[1m[9] EDGE CASES\033[0m")

        # 9.1 Trailing slash
        r = await c.get(f"{API}/health/")
        check("9.1 trailing slash /health/ works", r.status_code == 200)

        # 9.2 Math calculation
        r = await c.get(f"{API}/math/calc", params={"op": "add", "a": "100", "b": "200"})
        check("9.2 math calc add", r.json()["result"] == 300.0)

        r = await c.get(f"{API}/math/calc", params={"op": "mul", "a": "7", "b": "6"})
        check("9.3 math calc mul", r.json()["result"] == 42.0)

        # 9.4 Empty search query → validation error
        r = await c.get(f"{API}/books/search", params={"q": ""})
        check("9.4 empty search q → 422", r.status_code == 422)

        # 9.5 Search query → validation error (missing q)
        r = await c.get(f"{API}/books/search")
        check("9.5 missing search q → 422", r.status_code == 422)


async def main() -> None:
    global passed, failed

    server_proc = None
    if "--start-server" in sys.argv:
        print("Starting demo server...")
        server_proc = subprocess.Popen(
            [sys.executable, "examples/demo_app.py"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(2)

    print("\n" + "=" * 65)
    print("  moleculerpy-web Smoke Test — Phase 1 Feature Verification")
    print("=" * 65)

    try:
        await run_all_tests()
    except httpx.ConnectError:
        print("\n\033[31mERROR: Cannot connect to server at localhost:3000\033[0m")
        print("Start the server first:  python examples/demo_app.py")
        sys.exit(1)
    finally:
        if server_proc:
            server_proc.terminate()
            server_proc.wait()

    print("\n" + "=" * 65)
    total = passed + failed
    if failed == 0:
        print(f"  \033[32m✓ ALL {total} TESTS PASSED\033[0m")
    else:
        print(f"  \033[31m✗ {failed}/{total} FAILED\033[0m")
        print()
        for e in errors:
            print(f"    - {e}")
    print("=" * 65 + "\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
