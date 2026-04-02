# MoleculerPy Web

[![CI](https://github.com/MoleculerPy/moleculer-web/workflows/CI/badge.svg)](https://github.com/MoleculerPy/moleculer-web/actions)
[![PyPI version](https://img.shields.io/pypi/v/moleculerpy-web.svg)](https://pypi.org/project/moleculerpy-web/)
[![Python versions](https://img.shields.io/pypi/pyversions/moleculerpy-web.svg)](https://pypi.org/project/moleculerpy-web/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

HTTP API Gateway for MoleculerPy — maps HTTP requests to Moleculer service actions via Starlette (ASGI).

Python port of [moleculer-web](https://github.com/moleculerjs/moleculer-web).

---

## Features

- **HTTP Gateway** — Starlette (ASGI) + uvicorn, deploy via any ASGI server
- **Route Aliases** — `"GET /users/{id}" → "users.get"` with `:id` and `{id}` syntax
- **REST Shorthand** — `"REST /users" → 6 CRUD routes` with `only`/`except` filters
- **Middleware Pipeline** — Onion model matching Node.js moleculer-web execution order
- **Hooks** — `onBeforeCall`, `onAfterCall`, `onError` (route-level)
- **Authentication** — Sets `ctx.meta.user` from custom auth function
- **Authorization** — Raises `ForbiddenError` on deny
- **CORS** — Route-level with origin validation (string/list/wildcard/callable)
- **Rate Limiting** — MemoryStore with async reset, `X-Rate-Limit-*` headers
- **Whitelist/Blacklist** — Action access control with fnmatch wildcards + regex
- **Error Mapping** — MoleculerError hierarchy → HTTP status codes (400-504)
- **ctx.meta Passthrough** — `$statusCode`, `$responseHeaders`, `$responseType`, `$location`
- **Service Inheritance** — `ApiGatewayService(Service)` with full broker integration
- **Internal Actions** — `api.listAliases`, `api.addRoute`, `api.removeRoute`
- **Auto-aliases** — `$services.changed` event + action `rest` annotations
- **Streaming** — Async/sync generators → `StreamingResponse`
- **File Upload** — Multipart/form-data with filename sanitization
- **Static Files** — Starlette `StaticFiles` mount via `settings.assets`
- **ETag + 304** — Conditional GET with `If-None-Match` support
- **Security** — Path traversal, SSRF, open redirect, CRLF injection, body size limits
- **Type-Safe** — Full type hints with mypy strict mode

---

## Performance

Benchmarked with Apache Bench (ab), Python 3.12:

| Scenario | Throughput | Latency |
|----------|-----------|---------|
| Simple GET (mock broker) | **11,425 req/sec** | 4.4ms |
| Real NATS + 3 services | **10,900 req/sec** | 4.5ms |
| 404 error path | **14,302 req/sec** | 3.5ms |

---

## Quick Start

### Installation

```bash
pip install moleculerpy-web
```

### Basic Gateway

```python
import asyncio
from moleculerpy import Broker
from moleculerpy.settings import Settings
from moleculerpy_web import ApiGatewayService

class Gateway(ApiGatewayService):
    name = "api"
    settings = {
        "port": 3000,
        "path": "/api",
        "routes": [{
            "path": "/v1",
            "aliases": {
                "REST /users": "users",        # 6 CRUD routes
                "GET /health": "health.check",
            },
            "cors": {"origin": "*"},
            "rateLimit": {"window": 60, "limit": 100, "headers": True},
        }]
    }

async def main():
    broker = Broker("gateway-1", settings=Settings(transporter="nats://localhost:4222"))
    broker.create_service(Gateway)
    await broker.start()    # starts gateway + uvicorn automatically
    # Gateway is now listening on http://0.0.0.0:3000/api/v1/...

asyncio.run(main())
```

### With Authentication

```python
from moleculerpy_web import ApiGatewayService
from moleculerpy_web.errors import UnauthorizedError, ForbiddenError

async def authenticate(ctx, route, request):
    token = request.headers.get("authorization", "")[7:]  # Bearer <token>
    if not token:
        return None  # Anonymous
    user = await verify_token(token)
    if not user:
        raise UnauthorizedError("Invalid token")
    return user  # Sets ctx.meta.user

async def authorize_admin(ctx, route, request):
    if not ctx.user or ctx.user.get("role") != "admin":
        raise ForbiddenError("Admin required")

gateway = ApiGatewayService(broker=broker, settings={
    "port": 3000,
    "path": "/api",
    "routes": [{
        "path": "/admin",
        "aliases": {"GET /stats": "admin.stats"},
        "authentication": authenticate,
        "authorization": authorize_admin,
    }]
})
```

---

## Route Configuration

```python
{
    "path": "/v1",                    # Route prefix
    "mappingPolicy": "restrict",      # "restrict" (default) or "all"
    "aliases": {
        "GET /users": "users.list",
        "REST /products": "products", # REST shorthand → 6 CRUD routes
        "REST /orders": {"action": "orders", "only": ["list", "get"]},
    },

    # Middleware pipeline (Node.js execution order)
    "onBeforeCall": async_function,   # Before broker.call()
    "onAfterCall": async_function,    # After broker.call(), can modify data
    "onError": async_function,        # Custom error handler

    # Access control
    "whitelist": ["users.*", "products.*"],  # fnmatch patterns
    "blacklist": ["admin.danger"],

    # Authentication
    "authentication": async_function, # Returns user object or None
    "authorization": async_function,  # Raises on deny

    # CORS
    "cors": {
        "origin": "*",               # String, list, callable, or wildcard
        "methods": ["GET", "POST", "PUT", "DELETE"],
        "credentials": False,
        "maxAge": 3600,
    },

    # Rate limiting
    "rateLimit": {
        "window": 60,                # Seconds
        "limit": 100,                # Max requests per window
        "headers": True,             # X-Rate-Limit-* headers
    },
}
```

---

## Architecture

```
HTTP Request (uvicorn)
    ↓
Starlette (ASGI)
    ↓
CORS preflight check
    ↓
AliasResolver → match route
    ↓
Middleware Pipeline (onion model):
    onBeforeCall → Whitelist → Blacklist
    → Auth → Authz → Rate Limit
    → broker.call(action, params, meta)
    → onAfterCall
    ↓
Response: ctx.meta.$statusCode, $responseHeaders, $responseType
    ↓
HTTP Response (JSON / bytes / redirect / 204)
```

### Modules

| Module | Purpose | LOC |
|--------|---------|-----|
| `service.py` | ApiGatewayService lifecycle | 89 |
| `handler.py` | Request pipeline + response | 81 |
| `middleware.py` | RequestContext + compose | 97 |
| `alias.py` | Path matching + REST shorthand | 70 |
| `errors.py` | HTTP error classes + mapping | 67 |
| `cors.py` | CORS headers + origin check | 142 |
| `ratelimit.py` | MemoryStore + rate limit | 138 |
| `access.py` | Whitelist/blacklist | 132 |
| `route.py` | RouteConfig dataclass | 77 |
| `parsers.py` | JSON + form body parsing | 22 |
| `utils.py` | Path normalization | 22 |

---

## Testing

```bash
pip install -e ".[dev]"
pytest                        # 348 tests, 93% coverage
mypy moleculerpy_web/        # 0 errors (strict mode)
ruff check moleculerpy_web/  # 0 errors
```

### Real NATS demo

```bash
# Start NATS
docker run -p 4222:4222 nats:2-alpine

# Run demo (3 microservices + gateway)
python examples/demo_real_v2.py

# Run smoke tests (31 tests on real NATS)
python examples/smoke_test_v2.py
```

---

## Node.js Compatibility

| Feature | moleculer-web | moleculerpy-web |
|---------|:---:|:---:|
| Route aliases | ✅ | ✅ |
| REST shorthand | ✅ | ✅ |
| Path params (`:id` / `{id}`) | ✅ | ✅ (both) |
| Hooks (before/after/error) | ✅ | ✅ |
| Authentication | ✅ | ✅ |
| Authorization | ✅ | ✅ |
| CORS | ✅ | ✅ |
| Rate limiting | ✅ | ✅ |
| Whitelist/blacklist | ✅ | ✅ |
| ctx.meta passthrough | ✅ | ✅ |
| Service inheritance | ✅ | ✅ |
| Internal actions (listAliases) | ✅ | ✅ |
| Auto-aliases ($services.changed) | ✅ | ✅ |
| Streaming responses | ✅ | ✅ |
| File upload (multipart) | ✅ | ✅ |
| Static file serving | ✅ | ✅ |
| ETag / conditional GET | ✅ | ✅ |
| Param merge order | body < query < path | ✅ Same |
| Error format | ✅ | ✅ Same |
| Mapping policy | default: `all` | default: `restrict` (secure) |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

See [SECURITY.md](SECURITY.md).

## License

MIT
