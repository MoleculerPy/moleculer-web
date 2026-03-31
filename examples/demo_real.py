"""Real Demo: moleculerpy-web + real ServiceBroker + NATS transporter.

Bookstore API — 3 реальных микросервиса + HTTP Gateway.
Работает через NATS на localhost:4222.

Запуск:
    python examples/demo_real.py

Тесты (в другом терминале):
    python examples/smoke_test_real.py

Ctrl+C для остановки.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from moleculerpy import Broker, Context, Service, action, event
from moleculerpy.settings import Settings

from moleculerpy_web import ApiGatewayService


# ---------------------------------------------------------------------------
# Service 1: ProductsService — каталог товаров
# ---------------------------------------------------------------------------

class ProductsService(Service):
    name = "products"

    def __init__(self) -> None:
        super().__init__(self.name)
        self._next_id = 6
        # Seed data (instance-level — class-level mutables break with Service.__init__)
        self._db: dict[str, dict[str, Any]] = {
            "1": {"id": "1", "name": "MacBook Pro 16", "price": 2499.0, "category": "laptops", "stock": 15},
            "2": {"id": "2", "name": "iPhone 15 Pro", "price": 999.0, "category": "phones", "stock": 50},
            "3": {"id": "3", "name": "AirPods Pro 2", "price": 249.0, "category": "audio", "stock": 100},
            "4": {"id": "4", "name": "iPad Air", "price": 599.0, "category": "tablets", "stock": 30},
            "5": {"id": "5", "name": "Apple Watch Ultra", "price": 799.0, "category": "watches", "stock": 25},
        }

    @action()
    async def list(self, ctx: Context) -> dict[str, Any]:
        """List products with filtering and pagination."""
        products = list(self._db.values())
        # Filter
        category = ctx.params.get("category")
        if category:
            products = [p for p in products if p["category"] == category]
        min_price = ctx.params.get("min_price")
        if min_price:
            products = [p for p in products if p["price"] >= float(min_price)]
        max_price = ctx.params.get("max_price")
        if max_price:
            products = [p for p in products if p["price"] <= float(max_price)]
        # Sort
        sort_by = ctx.params.get("sort", "name")
        if sort_by in ("name", "price", "stock"):
            products = sorted(products, key=lambda p: p[sort_by])
        # Paginate
        page = int(ctx.params.get("page", "1"))
        limit = int(ctx.params.get("limit", "10"))
        total = len(products)
        start = (page - 1) * limit
        products = products[start:start + limit]
        return {"products": products, "total": total, "page": page, "limit": limit}

    @action()
    async def get(self, ctx: Context) -> dict[str, Any]:
        """Get single product by ID."""
        product_id = ctx.params.get("id", "")
        product = self._db.get(product_id)
        if not product:
            from moleculerpy.errors import MoleculerClientError
            raise MoleculerClientError(f"Product #{product_id} not found", code=404)
        return product

    @action()
    async def create(self, ctx: Context) -> dict[str, Any]:
        """Create new product."""
        name = ctx.params.get("name")
        if not name:
            from moleculerpy.errors import ValidationError
            raise ValidationError("Product name is required", field="name")
        price = ctx.params.get("price")
        if not price:
            from moleculerpy.errors import ValidationError
            raise ValidationError("Price is required", field="price")
        new_id = str(self._next_id)
        self._next_id += 1
        product = {
            "id": new_id,
            "name": name,
            "price": float(price),
            "category": ctx.params.get("category", "other"),
            "stock": int(ctx.params.get("stock", "0")),
        }
        self._db[new_id] = product
        return product

    @action()
    async def update(self, ctx: Context) -> dict[str, Any]:
        """Update product."""
        product_id = ctx.params.get("id", "")
        product = self._db.get(product_id)
        if not product:
            from moleculerpy.errors import MoleculerClientError
            raise MoleculerClientError(f"Product #{product_id} not found", code=404)
        for key in ("name", "price", "category", "stock"):
            if key in ctx.params and key != "id":
                val = ctx.params[key]
                if key == "price":
                    val = float(val)
                elif key == "stock":
                    val = int(val)
                product[key] = val
        return product

    @action()
    async def remove(self, ctx: Context) -> dict[str, Any]:
        """Delete product."""
        product_id = ctx.params.get("id", "")
        product = self._db.pop(product_id, None)
        if not product:
            from moleculerpy.errors import MoleculerClientError
            raise MoleculerClientError(f"Product #{product_id} not found", code=404)
        return {"deleted": product}

    @action()
    async def search(self, ctx: Context) -> dict[str, Any]:
        """Full-text search products."""
        q = ctx.params.get("q", "").lower()
        if not q:
            from moleculerpy.errors import ValidationError
            raise ValidationError("Search query 'q' is required")
        results = [p for p in self._db.values() if q in p["name"].lower()]
        return {"results": results, "query": q, "count": len(results)}


# ---------------------------------------------------------------------------
# Service 2: OrdersService — заказы (вызывает products для проверки)
# ---------------------------------------------------------------------------

class OrdersService(Service):
    name = "orders"

    def __init__(self) -> None:
        super().__init__(self.name)
        self._orders: dict[str, dict[str, Any]] = {}
        self._next_id: int = 1

    @action()
    async def create(self, ctx: Context) -> dict[str, Any]:
        """Create order — calls products.get to validate product exists."""
        product_id = ctx.params.get("productId")
        quantity = int(ctx.params.get("quantity", "1"))
        if not product_id:
            from moleculerpy.errors import ValidationError
            raise ValidationError("productId is required")
        if quantity < 1:
            from moleculerpy.errors import ValidationError
            raise ValidationError("Quantity must be >= 1")

        # Inter-service call: verify product exists and get price
        product = await ctx.call("products.get", {"id": product_id})

        # Check stock
        if product["stock"] < quantity:
            from moleculerpy.errors import MoleculerClientError
            raise MoleculerClientError(
                f"Insufficient stock: {product['stock']} available, {quantity} requested",
                code=409,
            )

        order_id = str(self._next_id)
        self._next_id += 1
        order = {
            "id": order_id,
            "productId": product_id,
            "productName": product["name"],
            "quantity": quantity,
            "unitPrice": product["price"],
            "total": product["price"] * quantity,
            "status": "pending",
            "createdAt": time.time(),
        }
        self._orders[order_id] = order
        return order

    @action()
    async def get(self, ctx: Context) -> dict[str, Any]:
        """Get order by ID."""
        order_id = ctx.params.get("id", "")
        order = self._orders.get(order_id)
        if not order:
            from moleculerpy.errors import MoleculerClientError
            raise MoleculerClientError(f"Order #{order_id} not found", code=404)
        return order

    @action()
    async def list(self, ctx: Context) -> dict[str, Any]:
        """List all orders."""
        return {"orders": list(self._orders.values()), "total": len(self._orders)}

    @event()
    async def product_created(self, ctx: Context) -> None:
        """React to new products being added."""
        print(f"  [orders] New product notification: {ctx.params.get('product', {}).get('name')}")


# ---------------------------------------------------------------------------
# Service 3: AnalyticsService — метрики и статистика
# ---------------------------------------------------------------------------

class AnalyticsService(Service):
    name = "analytics"

    def __init__(self) -> None:
        super().__init__(self.name)

    @action()
    async def summary(self, ctx: Context) -> dict[str, Any]:
        """Get system summary — calls multiple services."""
        # Parallel calls to multiple services
        products = await ctx.call("products.list", {})
        orders = await ctx.call("orders.list", {})
        return {
            "totalProducts": products["total"],
            "totalOrders": orders["total"],
            "timestamp": time.time(),
        }

    @action()
    async def slow_report(self, ctx: Context) -> dict[str, Any]:
        """Simulate a slow report generation."""
        delay = float(ctx.params.get("delay", "1.0"))
        await asyncio.sleep(delay)
        products = await ctx.call("products.list", {})
        return {
            "report": "generated",
            "delay": delay,
            "productCount": products["total"],
        }

    @action()
    async def health(self, ctx: Context) -> dict[str, Any]:
        """Health check."""
        return {
            "status": "ok",
            "node": ctx.node_id,
            "services": ["products", "orders", "analytics"],
            "uptime": time.time(),
        }


# ---------------------------------------------------------------------------
# Gateway Configuration
# ---------------------------------------------------------------------------

def create_gateway(broker: Broker) -> ApiGatewayService:
    """Create API Gateway with all routes."""
    return ApiGatewayService(
        broker=broker,
        settings={
            "port": 3000,
            "ip": "127.0.0.1",
            "path": "/api",
            "routes": [
                # V1 API — restrict mode (only explicit aliases)
                {
                    "path": "/v1",
                    "mappingPolicy": "restrict",
                    "aliases": {
                        # Products CRUD
                        "GET /products": "products.list",
                        "GET /products/search": "products.search",
                        "GET /products/{id}": "products.get",
                        "POST /products": "products.create",
                        "PUT /products/{id}": "products.update",
                        "DELETE /products/{id}": "products.remove",
                        # Orders
                        "POST /orders": "orders.create",
                        "GET /orders": "orders.list",
                        "GET /orders/{id}": "orders.get",
                        # Analytics
                        "GET /analytics/summary": "analytics.summary",
                        "GET /analytics/report": "analytics.slow_report",
                        "GET /health": "analytics.health",
                    },
                },
                # Debug API — all mode (any action via URL)
                {
                    "path": "/debug",
                    "mappingPolicy": "all",
                    "aliases": {},
                },
            ],
        },
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    settings = Settings(
        transporter="nats://localhost:4222",
        log_level="INFO",
        request_timeout=10.0,
    )
    broker = Broker("gateway-node", settings=settings)

    # Register services
    await broker.register(ProductsService())
    await broker.register(OrdersService())
    await broker.register(AnalyticsService())

    # Start broker (connects to NATS)
    await broker.start()

    # Create gateway
    gateway = create_gateway(broker)
    gateway._build_routes()
    gateway._app = gateway._create_app()

    print("\n" + "=" * 65)
    print("  Bookstore API — REAL MoleculerPy + NATS + HTTP Gateway")
    print("=" * 65)
    print(f"  HTTP:  http://127.0.0.1:3000/api/v1/...")
    print(f"  NATS:  nats://localhost:4222")
    print(f"  Node:  gateway-node")
    print()
    print("  Services: products (5 items), orders, analytics")
    print("  Routes:   /api/v1 (restrict)  |  /api/debug (all)")
    print()
    print("  Quick test:")
    print('    curl "localhost:3000/api/v1/products"')
    print('    curl "localhost:3000/api/v1/products/1"')
    print('    curl "localhost:3000/api/v1/products?category=laptops"')
    print('    curl -X POST localhost:3000/api/v1/orders \\')
    print('      -H "Content-Type: application/json" \\')
    print('      -d \'{"productId":"1","quantity":2}\'')
    print('    curl "localhost:3000/api/v1/analytics/summary"')
    print()
    print("  Smoke test: python examples/smoke_test_real.py")
    print("  Press Ctrl+C to stop")
    print("=" * 65 + "\n")

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
