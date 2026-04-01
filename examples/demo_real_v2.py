"""Real Demo v2: Phase 2 Features — hooks, auth, CORS, rate limit, REST shorthand.

3 microservices + HTTP Gateway on real NATS.
Demonstrates ALL Phase 2 features in a realistic scenario.

Запуск:
    python examples/demo_real_v2.py

Тесты:
    python examples/smoke_test_v2.py
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from moleculerpy import Broker, Context, Service, action
from moleculerpy.settings import Settings

from moleculerpy_web import ApiGatewayService
from moleculerpy_web.errors import ForbiddenError, UnauthorizedError


# ---------------------------------------------------------------------------
# Service 1: UsersService — CRUD via REST shorthand
# ---------------------------------------------------------------------------

class UsersService(Service):
    name = "users"

    def __init__(self) -> None:
        super().__init__(self.name)
        self._db: dict[str, dict[str, Any]] = {
            "1": {"id": "1", "name": "Alice", "email": "alice@test.com", "role": "admin"},
            "2": {"id": "2", "name": "Bob", "email": "bob@test.com", "role": "user"},
            "3": {"id": "3", "name": "Carol", "email": "carol@test.com", "role": "user"},
        }
        self._next_id = 4

    @action()
    async def list(self, ctx: Context) -> dict[str, Any]:
        return {"users": list(self._db.values()), "total": len(self._db)}

    @action()
    async def get(self, ctx: Context) -> dict[str, Any]:
        uid = ctx.params.get("id", "")
        user = self._db.get(uid)
        if not user:
            from moleculerpy.errors import MoleculerClientError
            raise MoleculerClientError(f"User #{uid} not found", code=404)
        return user

    @action()
    async def create(self, ctx: Context) -> dict[str, Any]:
        name = ctx.params.get("name")
        if not name:
            from moleculerpy.errors import ValidationError
            raise ValidationError("Name is required")
        new_id = str(self._next_id)
        self._next_id += 1
        user = {"id": new_id, "name": name, "email": ctx.params.get("email", ""), "role": "user"}
        self._db[new_id] = user
        return user

    @action()
    async def update(self, ctx: Context) -> dict[str, Any]:
        uid = ctx.params.get("id", "")
        user = self._db.get(uid)
        if not user:
            from moleculerpy.errors import MoleculerClientError
            raise MoleculerClientError(f"User #{uid} not found", code=404)
        for k in ("name", "email", "role"):
            if k in ctx.params:
                user[k] = ctx.params[k]
        return user

    @action()
    async def patch(self, ctx: Context) -> dict[str, Any]:
        return await self.update(ctx)

    @action()
    async def remove(self, ctx: Context) -> dict[str, Any]:
        uid = ctx.params.get("id", "")
        user = self._db.pop(uid, None)
        if not user:
            from moleculerpy.errors import MoleculerClientError
            raise MoleculerClientError(f"User #{uid} not found", code=404)
        return {"deleted": user}


# ---------------------------------------------------------------------------
# Service 2: ProductsService
# ---------------------------------------------------------------------------

class ProductsService(Service):
    name = "products"

    def __init__(self) -> None:
        super().__init__(self.name)
        self._db: dict[str, dict[str, Any]] = {
            "1": {"id": "1", "name": "Laptop", "price": 999.0},
            "2": {"id": "2", "name": "Phone", "price": 599.0},
        }

    @action()
    async def list(self, ctx: Context) -> dict[str, Any]:
        return {"products": list(self._db.values())}

    @action()
    async def get(self, ctx: Context) -> dict[str, Any]:
        pid = ctx.params.get("id", "")
        product = self._db.get(pid)
        if not product:
            from moleculerpy.errors import MoleculerClientError
            raise MoleculerClientError(f"Product #{pid} not found", code=404)
        return product


# ---------------------------------------------------------------------------
# Service 3: AdminService — protected by auth
# ---------------------------------------------------------------------------

class AdminService(Service):
    name = "admin"

    def __init__(self) -> None:
        super().__init__(self.name)

    @action()
    async def stats(self, ctx: Context) -> dict[str, Any]:
        user = ctx.meta.get("user")
        return {
            "requestedBy": user.get("name") if user else "anonymous",
            "uptime": time.time(),
            "services": 3,
        }

    @action()
    async def danger(self, ctx: Context) -> dict[str, Any]:
        return {"action": "dangerous operation", "executed": True}


# ---------------------------------------------------------------------------
# Auth functions
# ---------------------------------------------------------------------------

async def authenticate(ctx: Any, route: Any, request: Any) -> dict[str, Any] | None:
    """Simple token-based auth. Header: Authorization: Bearer <user_id>."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None  # Anonymous
    token = auth_header[7:]
    # Fake token = user ID
    fake_users = {
        "admin-token": {"id": "1", "name": "Alice", "role": "admin"},
        "user-token": {"id": "2", "name": "Bob", "role": "user"},
    }
    user = fake_users.get(token)
    if not user:
        raise UnauthorizedError(f"Invalid token: {token}")
    return user


async def authorize_admin(ctx: Any, route: Any, request: Any) -> None:
    """Only admin role allowed."""
    if not ctx.user:
        raise ForbiddenError("Authentication required")
    if ctx.user.get("role") != "admin":
        raise ForbiddenError(f"Admin role required, got: {ctx.user.get('role')}")


# ---------------------------------------------------------------------------
# Hook functions
# ---------------------------------------------------------------------------

_request_log: list[dict[str, Any]] = []


async def log_before_call(ctx: Any, route: Any, request: Any) -> None:
    """Log every request."""
    _request_log.append({
        "action": ctx.action,
        "method": request.method,
        "path": str(request.url.path),
        "time": time.time(),
    })


async def transform_response(ctx: Any, route: Any, request: Any, data: Any) -> Any:
    """Add metadata to every response."""
    if isinstance(data, dict):
        data["_gateway"] = {"node": "gateway-v2", "timestamp": time.time()}
    return data


# ---------------------------------------------------------------------------
# Gateway Configuration
# ---------------------------------------------------------------------------

def create_gateway(broker: Broker) -> ApiGatewayService:
    return ApiGatewayService(
        broker=broker,
        settings={
            "port": 3000,
            "ip": "127.0.0.1",
            "path": "/api",
            "routes": [
                # Route 1: Public API — REST shorthand + CORS + rate limit
                {
                    "path": "/v1",
                    "mappingPolicy": "restrict",
                    "aliases": {
                        "REST /users": "users",       # 6 CRUD routes!
                        "REST /products": {"action": "products", "only": ["list", "get"]},
                    },
                    "whitelist": ["users.*", "products.*"],
                    "cors": {
                        "origin": "*",
                        "methods": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                        "credentials": False,
                    },
                    "rateLimit": {
                        "window": 60,
                        "limit": 100,
                        "headers": True,
                    },
                    "onBeforeCall": log_before_call,
                    "onAfterCall": transform_response,
                },
                # Route 2: Admin API — auth required
                {
                    "path": "/admin",
                    "mappingPolicy": "restrict",
                    "aliases": {
                        "GET /stats": "admin.stats",
                        "POST /danger": "admin.danger",
                    },
                    "authentication": authenticate,
                    "authorization": authorize_admin,
                    "onBeforeCall": log_before_call,
                },
                # Route 3: Public health (no auth)
                {
                    "path": "/",
                    "aliases": {
                        "GET /health": "admin.stats",
                    },
                },
            ],
        },
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    settings = Settings(transporter="nats://localhost:4222", log_level="INFO", request_timeout=10.0)
    broker = Broker("gateway-v2", settings=settings)

    await broker.register(UsersService())
    await broker.register(ProductsService())
    await broker.register(AdminService())
    await broker.start()

    gateway = create_gateway(broker)
    gateway._build_routes()
    gateway._app = gateway._create_app()

    print("\n" + "=" * 70)
    print("  moleculerpy-web v2 Demo — Phase 2 Features on NATS")
    print("=" * 70)
    print(f"  http://127.0.0.1:3000/api/...")
    print()
    print("  Features demonstrated:")
    print("    REST shorthand:  /api/v1/users (6 CRUD routes)")
    print("    CORS:            Origin: * on /v1 routes")
    print("    Rate limiting:   100 req/60s with headers")
    print("    Hooks:           onBeforeCall (logging) + onAfterCall (metadata)")
    print("    Auth:            /api/admin/* requires Bearer token")
    print("    Authorization:   admin role required for /admin/*")
    print("    Whitelist:       users.* and products.* only on /v1")
    print()
    print("  Quick test:")
    print('    curl "localhost:3000/api/v1/users"                    # REST list')
    print('    curl "localhost:3000/api/v1/users/1"                  # REST get')
    print('    curl -X POST localhost:3000/api/v1/users \\')
    print('      -H "Content-Type: application/json" -d \'{"name":"Dave"}\'')
    print('    curl "localhost:3000/api/admin/stats" \\')
    print('      -H "Authorization: Bearer admin-token"              # Auth OK')
    print('    curl "localhost:3000/api/admin/stats"                  # 403 No auth')
    print('    curl "localhost:3000/api/admin/stats" \\')
    print('      -H "Authorization: Bearer user-token"               # 403 Not admin')
    print()
    print("  Smoke test: python examples/smoke_test_v2.py")
    print("  Press Ctrl+C to stop")
    print("=" * 70 + "\n")

    import uvicorn
    config = uvicorn.Config(gateway.app, host="127.0.0.1", port=3000, log_level="warning")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await broker.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown.")
