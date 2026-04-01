"""Smoke Test v2: Phase 2 Features on real NATS.

Tests REST shorthand, hooks, auth, CORS, rate limit, whitelist.

    python examples/smoke_test_v2.py --start-server
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time

import httpx

BASE = "http://127.0.0.1:3000"
API = f"{BASE}/api/v1"
ADMIN = f"{BASE}/api/admin"

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
        # =================================================================
        print("\n\033[1m[1] REST SHORTHAND — /api/v1/users (6 CRUD routes)\033[0m")
        # =================================================================

        r = await c.get(f"{API}/users")
        check("1.1 GET /users → list", r.status_code == 200 and "users" in r.json())

        r = await c.get(f"{API}/users/1")
        check("1.2 GET /users/1 → get", r.status_code == 200 and r.json()["name"] == "Alice")

        r = await c.post(f"{API}/users", json={"name": "Dave", "email": "dave@test.com"})
        check("1.3 POST /users → create", r.status_code == 200 and r.json()["name"] == "Dave")
        dave_id = r.json().get("id")

        r = await c.put(f"{API}/users/{dave_id}", json={"name": "David"})
        check("1.4 PUT /users/{id} → update", r.status_code == 200 and r.json()["name"] == "David")

        r = await c.patch(f"{API}/users/{dave_id}", json={"email": "david@new.com"})
        check("1.5 PATCH /users/{id} → patch", r.status_code == 200)

        r = await c.delete(f"{API}/users/{dave_id}")
        check("1.6 DELETE /users/{id} → remove", r.status_code == 200 and "deleted" in r.json())

        # Products — only list and get (only filter)
        r = await c.get(f"{API}/products")
        check("1.7 GET /products → list (only)", r.status_code == 200)

        r = await c.get(f"{API}/products/1")
        check("1.8 GET /products/1 → get (only)", r.status_code == 200)

        r = await c.post(f"{API}/products", json={"name": "Tablet"})
        check("1.9 POST /products → 404 (only=list,get)", r.status_code == 404)

        # =================================================================
        print("\n\033[1m[2] HOOKS — onBeforeCall + onAfterCall\033[0m")
        # =================================================================

        r = await c.get(f"{API}/users")
        body = r.json()
        check("2.1 onAfterCall adds _gateway metadata", "_gateway" in body)
        if "_gateway" in body:
            check("2.2 _gateway has node", body["_gateway"].get("node") == "gateway-v2")
            check("2.3 _gateway has timestamp", "timestamp" in body["_gateway"])

        # =================================================================
        print("\n\033[1m[3] AUTHENTICATION + AUTHORIZATION\033[0m")
        # =================================================================

        # No auth → 403 (authorization requires user)
        r = await c.get(f"{ADMIN}/stats")
        check("3.1 admin without auth → 403", r.status_code == 403)

        # User token → 403 (not admin)
        r = await c.get(f"{ADMIN}/stats", headers={"Authorization": "Bearer user-token"})
        check("3.2 admin with user role → 403", r.status_code == 403)
        check("3.2 error is ForbiddenError", r.json().get("name") == "ForbiddenError")

        # Admin token → 200
        r = await c.get(f"{ADMIN}/stats", headers={"Authorization": "Bearer admin-token"})
        check("3.3 admin with admin role → 200", r.status_code == 200)
        check("3.3 requestedBy = Alice", r.json().get("requestedBy") == "Alice")

        # Invalid token → 401
        r = await c.get(f"{ADMIN}/stats", headers={"Authorization": "Bearer bad-token"})
        check("3.4 invalid token → 401", r.status_code == 401)

        # Health (no auth route) → 200
        r = await c.get(f"{BASE}/api/health")
        check("3.5 /health (no auth) → 200", r.status_code == 200)

        # =================================================================
        print("\n\033[1m[4] CORS\033[0m")
        # =================================================================

        # Preflight
        r = await c.options(
            f"{API}/users",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        check("4.1 OPTIONS preflight → 200", r.status_code == 200)
        check("4.1 has Allow-Origin", "access-control-allow-origin" in r.headers)

        # Normal request with Origin
        r = await c.get(f"{API}/users", headers={"Origin": "https://app.example.com"})
        check("4.2 GET with Origin → CORS headers", "access-control-allow-origin" in r.headers)

        # =================================================================
        print("\n\033[1m[5] RATE LIMITING\033[0m")
        # =================================================================

        r = await c.get(f"{API}/users")
        check("5.1 rate limit headers present", "x-rate-limit-limit" in r.headers)
        if "x-rate-limit-limit" in r.headers:
            check("5.1 limit = 100", r.headers["x-rate-limit-limit"] == "100")
            remaining = int(r.headers.get("x-rate-limit-remaining", "0"))
            check("5.1 remaining > 0", remaining > 0)

        # =================================================================
        print("\n\033[1m[6] WHITELIST\033[0m")
        # =================================================================

        # users.* and products.* are whitelisted
        r = await c.get(f"{API}/users")
        check("6.1 users.list → allowed", r.status_code == 200)

        # admin.* is NOT whitelisted on /v1 route
        # (admin is on a different route, so this just won't match aliases)

        # =================================================================
        print("\n\033[1m[7] ERROR FORMAT CONSISTENCY\033[0m")
        # =================================================================

        r = await c.get(f"{API}/nonexistent")
        body = r.json()
        check("7.1 error has name", "name" in body)
        check("7.1 error has message", "message" in body)
        check("7.1 error has code", "code" in body)
        check("7.1 error has type", "type" in body)
        check("7.1 error has data", "data" in body)


async def main() -> None:
    global passed, failed

    server_proc = None
    if "--start-server" in sys.argv:
        print("Starting v2 demo server (NATS)...")
        server_proc = subprocess.Popen(
            [sys.executable, "examples/demo_real_v2.py"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        for _ in range(20):
            try:
                async with httpx.AsyncClient(timeout=1.0) as c:
                    r = await c.get(f"{BASE}/api/health")
                    if r.status_code == 200:
                        break
            except (httpx.ConnectError, httpx.ReadTimeout):
                pass
            time.sleep(0.5)
        else:
            print("\033[31mServer did not start in 10s\033[0m")
            if server_proc and server_proc.stdout:
                print(server_proc.stdout.read().decode()[-500:])
            if server_proc:
                server_proc.terminate()
            sys.exit(1)

    print("\n" + "=" * 70)
    print("  moleculerpy-web Phase 2 Smoke Test — NATS")
    print("=" * 70)

    try:
        await run_all_tests()
    except httpx.ConnectError:
        print("\n\033[31mCannot connect to localhost:3000\033[0m")
        sys.exit(1)
    finally:
        if server_proc:
            server_proc.terminate()
            server_proc.wait()

    print("\n" + "=" * 70)
    total = passed + failed
    if failed == 0:
        print(f"  \033[32m✓ ALL {total} TESTS PASSED\033[0m")
    else:
        print(f"  \033[31m✗ {failed}/{total} FAILED\033[0m")
        for e in errors:
            print(f"    - {e}")
    print("=" * 70 + "\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
