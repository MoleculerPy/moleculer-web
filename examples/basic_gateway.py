"""Demo: moleculerpy-web API Gateway — standalone verification.

Запуск:
    python examples/basic_gateway.py

Тестирует все фичи Phase 1 без реального broker (mock).
Результат: HTTP сервер на localhost:3000 с реальными routes.
"""

from __future__ import annotations

import asyncio

import uvicorn

from moleculerpy_web import ApiGatewayService


async def main() -> None:
    """Run demo gateway with mock broker and test all features."""

    # Mock broker — имитирует broker.call()
    class MockBroker:
        async def call(self, action: str, params: dict | None = None) -> dict | str | None:
            params = params or {}
            # Math
            if action == "math.add":
                return {"result": int(params.get("a", 0)) + int(params.get("b", 0))}
            if action == "math.subtract":
                return {"result": int(params.get("a", 0)) - int(params.get("b", 0))}
            # Users
            users = {
                "1": {"id": "1", "name": "Alice", "email": "alice@test.com"},
                "42": {"id": "42", "name": "Charlie", "email": "charlie@test.com"},
            }
            if action == "users.list":
                return {"users": list(users.values()), "total": len(users)}
            if action == "users.get":
                user = users.get(params.get("id", ""))
                if not user:
                    from moleculerpy.errors import ServiceNotFoundError
                    raise ServiceNotFoundError(f"User {params.get('id')} not found")
                return user
            if action == "users.create":
                return {"created": {"id": "99", **params}}
            if action == "users.update":
                user = users.get(params.get("id", ""))
                if not user:
                    from moleculerpy.errors import ServiceNotFoundError
                    raise ServiceNotFoundError(f"User {params.get('id')} not found")
                user.update(params)
                return {"updated": user}
            if action == "users.remove":
                return {"deleted": params.get("id")}
            # Health
            if action == "health.check":
                return {"status": "ok", "version": "0.1.0a1"}

            from moleculerpy.errors import ServiceNotFoundError
            raise ServiceNotFoundError(f"Action '{action}' not found")

    # Gateway
    gateway = ApiGatewayService(
        broker=MockBroker(),
        settings={
            "port": 3000,
            "ip": "127.0.0.1",
            "path": "/api",
            "routes": [
                {
                    "path": "/",
                    "mappingPolicy": "restrict",
                    "aliases": {
                        "GET /math/add": "math.add",
                        "GET /math/subtract": "math.subtract",
                        "GET /users": "users.list",
                        "GET /users/{id}": "users.get",
                        "POST /users": "users.create",
                        "PUT /users/{id}": "users.update",
                        "DELETE /users/{id}": "users.remove",
                        "GET /health": "health.check",
                    },
                }
            ],
        },
    )

    gateway._build_routes()
    gateway._app = gateway._create_app()

    print("\n" + "=" * 60)
    print("  moleculerpy-web Demo Gateway v0.1.0a1")
    print("=" * 60)
    print("  Server: http://127.0.0.1:3000")
    print()
    print("  Test endpoints:")
    print("    curl localhost:3000/api/math/add?a=5&b=3")
    print("    curl localhost:3000/api/users")
    print("    curl localhost:3000/api/users/42")
    print('    curl -X POST localhost:3000/api/users -H "Content-Type: application/json" -d \'{"name":"John"}\'')
    print("    curl localhost:3000/api/nonexistent  # 404")
    print("    curl localhost:3000/api/health")
    print()
    print("  Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    config = uvicorn.Config(gateway.app, host="127.0.0.1", port=3000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown.")
