"""Real Smoke Test — moleculerpy-web + NATS + real ServiceBroker.

Тестирует реальные сценарии:
  1. CRUD операции (products)
  2. Inter-service calls (orders → products.get)
  3. Query filtering, sorting, pagination
  4. Validation errors (missing fields)
  5. Not found errors (bad IDs)
  6. Business logic errors (insufficient stock)
  7. Analytics (cross-service aggregation)
  8. Slow actions (timeout behavior)
  9. Mapping policy (restrict vs all)
  10. Edge cases (empty body, malformed JSON, etc.)

Запуск (сервер должен работать):
    python examples/smoke_test_real.py

Автозапуск сервера:
    python examples/smoke_test_real.py --start-server
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time

import httpx

BASE = "http://127.0.0.1:3000"
API = f"{BASE}/api/v1"
DEBUG = f"{BASE}/api/debug"

passed = 0
failed = 0
errors: list[str] = []
timings: list[tuple[str, float]] = []


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


async def timed_request(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: object
) -> httpx.Response:
    """Execute request and track timing."""
    start = time.perf_counter()
    r = await client.request(method, url, **kwargs)
    elapsed = (time.perf_counter() - start) * 1000
    timings.append((f"{method} {url.replace(BASE, '')}", elapsed))
    return r


async def run_all_tests() -> None:
    async with httpx.AsyncClient(timeout=15.0) as c:
        # =================================================================
        print("\n\033[1m[1] HEALTH & CONNECTIVITY\033[0m")
        # =================================================================

        r = await timed_request(c, "GET", f"{API}/health")
        check("1.1 health check → 200", r.status_code == 200)
        check("1.1 has node ID", "node" in r.json())
        check("1.1 has services list", len(r.json().get("services", [])) == 3)

        # =================================================================
        print("\n\033[1m[2] PRODUCTS — CRUD\033[0m")
        # =================================================================

        # List all products
        r = await timed_request(c, "GET", f"{API}/products")
        check("2.1 list products → 200", r.status_code == 200)
        check("2.1 has 5 seed products", r.json()["total"] == 5)

        # Get single product
        r = await timed_request(c, "GET", f"{API}/products/1")
        check("2.2 get product #1", r.status_code == 200 and r.json()["name"] == "MacBook Pro 16")

        # Create product
        r = await timed_request(
            c,
            "POST",
            f"{API}/products",
            json={
                "name": "HomePod Mini",
                "price": 99.0,
                "category": "audio",
                "stock": 200,
            },
        )
        check("2.3 create product → 200", r.status_code == 200)
        new_id = r.json().get("id")
        check("2.3 has new ID", new_id is not None)

        # Update product
        r = await timed_request(c, "PUT", f"{API}/products/{new_id}", json={"price": 89.0})
        check("2.4 update price → 200", r.status_code == 200 and r.json()["price"] == 89.0)

        # Delete product
        r = await timed_request(c, "DELETE", f"{API}/products/{new_id}")
        check("2.5 delete product → 200", r.status_code == 200)
        check("2.5 deleted data returned", "deleted" in r.json())

        # Verify deleted
        r = await timed_request(c, "GET", f"{API}/products/{new_id}")
        check("2.6 deleted product → 404", r.status_code in (400, 404))

        # =================================================================
        print("\n\033[1m[3] QUERY FILTERING & PAGINATION\033[0m")
        # =================================================================

        # Filter by category
        r = await timed_request(c, "GET", f"{API}/products", params={"category": "audio"})
        check(
            "3.1 filter category=audio", all(p["category"] == "audio" for p in r.json()["products"])
        )

        # Price range
        r = await timed_request(
            c, "GET", f"{API}/products", params={"min_price": "500", "max_price": "1000"}
        )
        products = r.json()["products"]
        check("3.2 price range 500-1000", all(500 <= p["price"] <= 1000 for p in products))

        # Sort by price
        r = await timed_request(c, "GET", f"{API}/products", params={"sort": "price"})
        prices = [p["price"] for p in r.json()["products"]]
        check("3.3 sort by price asc", prices == sorted(prices))

        # Pagination
        r = await timed_request(c, "GET", f"{API}/products", params={"page": "1", "limit": "2"})
        check("3.4 page=1 limit=2", len(r.json()["products"]) == 2)
        check("3.4 total still correct", r.json()["total"] == 5)

        r = await timed_request(c, "GET", f"{API}/products", params={"page": "3", "limit": "2"})
        check("3.5 page=3 limit=2 → last page", len(r.json()["products"]) == 1)

        # Search
        r = await timed_request(c, "GET", f"{API}/products/search", params={"q": "pro"})
        check("3.6 search 'pro' finds results", r.json()["count"] >= 2)

        # =================================================================
        print("\n\033[1m[4] ORDERS — INTER-SERVICE CALLS\033[0m")
        # =================================================================

        # Create order (calls products.get internally)
        r = await timed_request(c, "POST", f"{API}/orders", json={"productId": "1", "quantity": 2})
        check("4.1 create order → 200", r.status_code == 200)
        check(
            "4.1 has productName from products.get", r.json().get("productName") == "MacBook Pro 16"
        )
        check("4.1 total = price * quantity", r.json()["total"] == 2499.0 * 2)
        order_id = r.json().get("id")

        # Get order
        r = await timed_request(c, "GET", f"{API}/orders/{order_id}")
        check("4.2 get order by ID", r.status_code == 200 and r.json()["status"] == "pending")

        # List orders
        r = await timed_request(c, "GET", f"{API}/orders")
        check("4.3 list orders", r.json()["total"] >= 1)

        # Order for non-existent product → error from inter-service call
        r = await timed_request(
            c, "POST", f"{API}/orders", json={"productId": "99999", "quantity": 1}
        )
        check("4.4 order bad product → error", r.status_code != 200)

        # Order with insufficient stock
        r = await timed_request(
            c, "POST", f"{API}/orders", json={"productId": "1", "quantity": 99999}
        )
        check("4.5 insufficient stock → error", r.status_code != 200)

        # =================================================================
        print("\n\033[1m[5] ANALYTICS — CROSS-SERVICE AGGREGATION\033[0m")
        # =================================================================

        r = await timed_request(c, "GET", f"{API}/analytics/summary")
        check("5.1 summary → 200", r.status_code == 200)
        check("5.1 has totalProducts", r.json().get("totalProducts", 0) >= 5)
        check("5.1 has totalOrders", "totalOrders" in r.json())

        # Slow report (tests async delay)
        r = await timed_request(c, "GET", f"{API}/analytics/report", params={"delay": "0.5"})
        check("5.2 slow report (0.5s) → 200", r.status_code == 200)
        check("5.2 report generated", r.json()["report"] == "generated")

        # =================================================================
        print("\n\033[1m[6] VALIDATION ERRORS\033[0m")
        # =================================================================

        # Missing name
        r = await timed_request(c, "POST", f"{API}/products", json={"price": 100})
        check("6.1 no name → 422", r.status_code == 422)
        check(
            "6.1 error is UnprocessableEntityError", r.json()["name"] == "UnprocessableEntityError"
        )

        # Missing price
        r = await timed_request(c, "POST", f"{API}/products", json={"name": "Test"})
        check("6.2 no price → 422", r.status_code == 422)

        # Empty search
        r = await timed_request(c, "GET", f"{API}/products/search", params={"q": ""})
        check("6.3 empty search q → 422", r.status_code == 422)

        # Missing order productId
        r = await timed_request(c, "POST", f"{API}/orders", json={"quantity": 1})
        check("6.4 order no productId → 422", r.status_code == 422)

        # Invalid quantity
        r = await timed_request(c, "POST", f"{API}/orders", json={"productId": "1", "quantity": 0})
        check("6.5 order qty=0 → 422", r.status_code == 422)

        # =================================================================
        print("\n\033[1m[7] ERROR HANDLING & FORMAT\033[0m")
        # =================================================================

        # 404 — route not found
        r = await timed_request(c, "GET", f"{API}/nonexistent")
        check("7.1 unknown route → 404", r.status_code == 404)

        # 404 — resource not found
        r = await timed_request(c, "GET", f"{API}/products/99999")
        check("7.2 bad product ID → error", r.status_code != 200)

        # Malformed JSON
        r = await timed_request(
            c,
            "POST",
            f"{API}/products",
            content=b"{bad json",
            headers={"content-type": "application/json"},
        )
        check("7.3 malformed JSON → 400", r.status_code == 400)
        check("7.3 type INVALID_REQUEST_BODY", r.json()["type"] == "INVALID_REQUEST_BODY")

        # Error format consistency
        r = await timed_request(c, "GET", f"{API}/nonexistent")
        body = r.json()
        check(
            "7.4 error has all fields",
            all(k in body for k in ("name", "message", "code", "type", "data")),
        )

        # No content-type → body ignored
        r = await timed_request(c, "POST", f"{API}/products", content=b"raw data")
        check("7.5 no content-type → validation error (no name)", r.status_code == 422)

        # =================================================================
        print("\n\033[1m[8] MAPPING POLICY\033[0m")
        # =================================================================

        # Restrict — unknown path with no matching alias pattern
        r = await timed_request(c, "GET", f"{API}/totally/unknown/deep/path")
        check("8.1 restrict: /totally/unknown/deep/path → 404", r.status_code == 404)

        # All — derive action from URL
        r = await timed_request(c, "GET", f"{DEBUG}/analytics/health")
        check("8.2 all: /debug/analytics/health → analytics.health", r.status_code == 200)

        r = await timed_request(c, "GET", f"{DEBUG}/products/list")
        check("8.3 all: /debug/products/list → products.list", r.status_code == 200)

        # All — unknown action → 404 from broker
        r = await timed_request(c, "GET", f"{DEBUG}/nonexistent/action")
        check("8.4 all: unknown action → 404", r.status_code == 404)

        # =================================================================
        print("\n\033[1m[9] EDGE CASES & RESPONSE TYPES\033[0m")
        # =================================================================

        # Trailing slash
        r = await timed_request(c, "GET", f"{API}/health/")
        check("9.1 trailing slash works", r.status_code == 200)

        # Content-Type is JSON
        r = await timed_request(c, "GET", f"{API}/products")
        check(
            "9.2 Content-Type: application/json",
            "application/json" in r.headers.get("content-type", ""),
        )

        # Empty body POST
        r = await timed_request(
            c, "POST", f"{API}/products", content=b"", headers={"content-type": "application/json"}
        )
        check("9.3 empty JSON body → validation (no name)", r.status_code == 422)

        # Multiple orders (workflow test)
        r1 = await timed_request(c, "POST", f"{API}/orders", json={"productId": "2", "quantity": 1})
        r2 = await timed_request(c, "POST", f"{API}/orders", json={"productId": "3", "quantity": 3})
        check("9.4 multiple orders created", r1.status_code == 200 and r2.status_code == 200)

        # Summary reflects new orders
        r = await timed_request(c, "GET", f"{API}/analytics/summary")
        check("9.5 summary reflects orders", r.json()["totalOrders"] >= 3)


async def main() -> None:
    global passed, failed

    server_proc = None
    if "--start-server" in sys.argv:
        print("Starting real demo server (NATS)...")
        server_proc = subprocess.Popen(
            [sys.executable, "examples/demo_real.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        # Wait for server to be ready
        for _i in range(20):
            try:
                async with httpx.AsyncClient(timeout=1.0) as c:
                    r = await c.get(f"{API}/health")
                    if r.status_code == 200:
                        break
            except (httpx.ConnectError, httpx.ReadTimeout):
                pass
            time.sleep(0.5)
        else:
            print("\033[31mERROR: Server did not start in 10s\033[0m")
            if server_proc:
                out = server_proc.stdout.read().decode() if server_proc.stdout else ""
                print(out[-500:] if len(out) > 500 else out)
                server_proc.terminate()
            sys.exit(1)

    print("\n" + "=" * 65)
    print("  moleculerpy-web REAL Smoke Test")
    print("  Broker: MoleculerPy + NATS (localhost:4222)")
    print("=" * 65)

    try:
        await run_all_tests()
    except httpx.ConnectError:
        print("\n\033[31mERROR: Cannot connect to localhost:3000\033[0m")
        print("Start: python examples/demo_real.py")
        sys.exit(1)
    finally:
        if server_proc:
            server_proc.terminate()
            server_proc.wait()

    # Timing report
    print("\n\033[1m[TIMING]\033[0m")
    timings.sort(key=lambda t: t[1], reverse=True)
    for name, ms in timings[:10]:
        bar = "█" * int(ms / 10) if ms < 500 else "█" * 50
        color = "\033[32m" if ms < 50 else "\033[33m" if ms < 200 else "\033[31m"
        print(f"  {color}{ms:7.1f}ms\033[0m {bar} {name}")

    avg = sum(t[1] for t in timings) / len(timings) if timings else 0
    p50 = sorted(t[1] for t in timings)[len(timings) // 2] if timings else 0
    p99 = sorted(t[1] for t in timings)[int(len(timings) * 0.99)] if timings else 0

    print(
        f"\n  avg: {avg:.1f}ms | p50: {p50:.1f}ms | p99: {p99:.1f}ms | total requests: {len(timings)}"
    )

    # Summary
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
