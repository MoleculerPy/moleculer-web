# MoleculerPy Web

HTTP API Gateway for MoleculerPy — maps HTTP requests to Moleculer service actions.

Python port of [moleculer-web](https://github.com/moleculerjs/moleculer-web).

## Status: Alpha (v0.14.7a1)

Phase 1 — Core Gateway: HTTP server, routing, aliases, error handling.

## Quick Start

```bash
pip install moleculerpy-web
```

```python
from moleculerpy import ServiceBroker
from moleculerpy_web import ApiGatewayService

broker = ServiceBroker(node_id="gateway-1")

class Gateway(ApiGatewayService):
    name = "api"
    settings = {
        "port": 3000,
        "path": "/api",
        "routes": [{
            "path": "/",
            "aliases": {
                "GET /users": "users.list",
                "GET /users/{id}": "users.get",
                "POST /users": "users.create",
            }
        }]
    }

broker.create_service(Gateway)
broker.start()
```

## Features (Phase 1)

- HTTP server via Starlette (ASGI) + uvicorn
- Route aliases: `"GET /users/{id}" → "users.get"`
- Path parameter extraction (`:id` and `{id}` syntax)
- JSON body parsing + query parameter merging
- Moleculer error → HTTP status code mapping
- Mapping policy: `restrict` (default, secure) / `all`

## Architecture

```
HTTP Request → Starlette (ASGI) → AliasResolver → broker.call() → JSON Response
```

See [ADR-001](https://github.com/MoleculerPy/moleculer-web/wiki/ADR-001) for HTTP library decision (Starlette chosen over aiohttp).

## License

MIT
