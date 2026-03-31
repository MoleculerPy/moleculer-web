"""Comprehensive Demo App — moleculerpy-web Phase 1 Feature Verification.

Реалистичное приложение: Bookstore API с несколькими сервисами.
Проверяет ВСЕ фичи Phase 1 gateway.

Запуск сервера:
    python examples/demo_app.py

Запуск тестов (в другом терминале):
    python examples/smoke_test.py

Или всё вместе (автоматически):
    python examples/smoke_test.py --start-server
"""

from __future__ import annotations

import asyncio
from typing import Any

import uvicorn

from moleculerpy_web import ApiGatewayService


# ---------------------------------------------------------------------------
# Mock Broker — имитирует реальный broker с несколькими сервисами
# ---------------------------------------------------------------------------

class BookstoreBroker:
    """Mock broker simulating a bookstore microservices cluster."""

    _books: dict[str, dict[str, Any]] = {
        "1": {"id": "1", "title": "Dune", "author": "Frank Herbert", "year": 1965, "genre": "sci-fi", "price": 12.99},
        "2": {"id": "2", "title": "1984", "author": "George Orwell", "year": 1949, "genre": "dystopia", "price": 9.99},
        "3": {"id": "3", "title": "Foundation", "author": "Isaac Asimov", "year": 1951, "genre": "sci-fi", "price": 11.50},
        "42": {"id": "42", "title": "Hitchhiker's Guide", "author": "Douglas Adams", "year": 1979, "genre": "comedy", "price": 8.99},
    }

    _authors: dict[str, dict[str, Any]] = {
        "1": {"id": "1", "name": "Frank Herbert", "country": "USA"},
        "2": {"id": "2", "name": "George Orwell", "country": "UK"},
        "3": {"id": "3", "name": "Isaac Asimov", "country": "USA"},
    }

    _reviews: dict[str, list[dict[str, Any]]] = {
        "1": [{"user": "Alice", "rating": 5, "text": "Masterpiece!"}],
        "2": [{"user": "Bob", "rating": 4, "text": "Chilling"}, {"user": "Carol", "rating": 5, "text": "Prophetic"}],
    }

    async def call(self, action: str, params: dict[str, Any] | None = None) -> Any:
        """Route action calls to service handlers."""
        params = params or {}
        handlers: dict[str, Any] = {
            # Books service
            "books.list": self._books_list,
            "books.get": self._books_get,
            "books.create": self._books_create,
            "books.update": self._books_update,
            "books.remove": self._books_remove,
            "books.search": self._books_search,
            # Authors service
            "authors.list": self._authors_list,
            "authors.get": self._authors_get,
            # Reviews service
            "reviews.list": self._reviews_list,
            "reviews.add": self._reviews_add,
            # Utility services
            "health.check": self._health_check,
            "echo.params": self._echo_params,
            "echo.raw": self._echo_raw,
            "echo.empty": self._echo_empty,
            "echo.bytes": self._echo_bytes,
            "echo.string": self._echo_string,
            "math.calc": self._math_calc,
            "slow.action": self._slow_action,
        }
        handler = handlers.get(action)
        if not handler:
            from moleculerpy.errors import ServiceNotFoundError
            raise ServiceNotFoundError(action)
        return await handler(params)

    # --- Books ---
    async def _books_list(self, params: dict[str, Any]) -> dict[str, Any]:
        books = list(self._books.values())
        # Filtering by genre
        genre = params.get("genre")
        if genre:
            books = [b for b in books if b["genre"] == genre]
        # Filtering by year range
        year_from = params.get("year_from")
        year_to = params.get("year_to")
        if year_from:
            books = [b for b in books if b["year"] >= int(year_from)]
        if year_to:
            books = [b for b in books if b["year"] <= int(year_to)]
        # Sorting
        sort = params.get("sort", "title")
        reverse = params.get("order", "asc") == "desc"
        if sort in ("title", "year", "price"):
            books = sorted(books, key=lambda b: b[sort], reverse=reverse)
        # Pagination
        page = int(params.get("page", "1"))
        limit = int(params.get("limit", "10"))
        total = len(books)
        offset = (page - 1) * limit
        books = books[offset:offset + limit]
        return {"books": books, "total": total, "page": page, "limit": limit}

    async def _books_get(self, params: dict[str, Any]) -> dict[str, Any]:
        book_id = params.get("id", "")
        book = self._books.get(book_id)
        if not book:
            from moleculerpy.errors import MoleculerClientError
            raise MoleculerClientError(f"Book #{book_id} not found", code=404)
        return book

    async def _books_create(self, params: dict[str, Any]) -> dict[str, Any]:
        # Validation
        if not params.get("title"):
            from moleculerpy.errors import ValidationError
            raise ValidationError("Title is required", field="title")
        if not params.get("author"):
            from moleculerpy.errors import ValidationError
            raise ValidationError("Author is required", field="author")
        new_id = str(max(int(k) for k in self._books) + 1)
        book = {"id": new_id, "title": params["title"], "author": params["author"],
                "year": int(params.get("year", 2024)), "genre": params.get("genre", "unknown"),
                "price": float(params.get("price", 0))}
        self._books[new_id] = book
        return {"created": book}

    async def _books_update(self, params: dict[str, Any]) -> dict[str, Any]:
        book_id = params.get("id", "")
        book = self._books.get(book_id)
        if not book:
            from moleculerpy.errors import MoleculerClientError
            raise MoleculerClientError(f"Book #{book_id} not found", code=404)
        for key in ("title", "author", "year", "genre", "price"):
            if key in params and key != "id":
                book[key] = int(params[key]) if key == "year" else (float(params[key]) if key == "price" else params[key])
        return {"updated": book}

    async def _books_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        book_id = params.get("id", "")
        book = self._books.pop(book_id, None)
        if not book:
            from moleculerpy.errors import MoleculerClientError
            raise MoleculerClientError(f"Book #{book_id} not found", code=404)
        return {"deleted": book}

    async def _books_search(self, params: dict[str, Any]) -> dict[str, Any]:
        q = params.get("q", "").lower()
        if not q:
            from moleculerpy.errors import ValidationError
            raise ValidationError("Search query 'q' is required")
        results = [b for b in self._books.values()
                   if q in b["title"].lower() or q in b["author"].lower()]
        return {"results": results, "query": q, "count": len(results)}

    # --- Authors ---
    async def _authors_list(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"authors": list(self._authors.values())}

    async def _authors_get(self, params: dict[str, Any]) -> dict[str, Any]:
        author_id = params.get("id", "")
        author = self._authors.get(author_id)
        if not author:
            from moleculerpy.errors import MoleculerClientError
            raise MoleculerClientError(f"Author #{author_id} not found", code=404)
        return author

    # --- Reviews ---
    async def _reviews_list(self, params: dict[str, Any]) -> dict[str, Any]:
        book_id = params.get("bookId", "")
        reviews = self._reviews.get(book_id, [])
        return {"reviews": reviews, "bookId": book_id, "count": len(reviews)}

    async def _reviews_add(self, params: dict[str, Any]) -> dict[str, Any]:
        book_id = params.get("bookId", "")
        if book_id not in self._books:
            from moleculerpy.errors import MoleculerClientError
            raise MoleculerClientError(f"Book #{book_id} not found", code=404)
        if not params.get("rating"):
            from moleculerpy.errors import ValidationError
            raise ValidationError("Rating is required")
        review = {"user": params.get("user", "Anonymous"),
                  "rating": int(params["rating"]),
                  "text": params.get("text", "")}
        self._reviews.setdefault(book_id, []).append(review)
        return {"added": review, "bookId": book_id}

    # --- Utility ---
    async def _health_check(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok", "version": "0.14.1a1", "services": ["books", "authors", "reviews"]}

    async def _echo_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Echo back all received params — useful for debugging param merge."""
        return {"received_params": params}

    async def _echo_raw(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"method": "echo", "params": params, "keys": list(params.keys())}

    async def _echo_empty(self, params: dict[str, Any]) -> None:
        """Return None → 204 No Content."""
        return None

    async def _echo_bytes(self, params: dict[str, Any]) -> bytes:
        """Return raw bytes."""
        return b"\x89PNG\r\n\x1a\n"  # PNG header

    async def _echo_string(self, params: dict[str, Any]) -> str:
        """Return string → should be JSON-encoded per audit fix."""
        return f"Hello, {params.get('name', 'World')}!"

    async def _math_calc(self, params: dict[str, Any]) -> dict[str, Any]:
        op = params.get("op", "add")
        a = float(params.get("a", 0))
        b = float(params.get("b", 0))
        if op == "add":
            return {"result": a + b, "op": op}
        if op == "sub":
            return {"result": a - b, "op": op}
        if op == "mul":
            return {"result": a * b, "op": op}
        if op == "div":
            if b == 0:
                from moleculerpy.errors import ValidationError
                raise ValidationError("Division by zero")
            return {"result": a / b, "op": op}
        from moleculerpy.errors import ValidationError
        raise ValidationError(f"Unknown operation: {op}")

    async def _slow_action(self, params: dict[str, Any]) -> dict[str, Any]:
        delay = float(params.get("delay", "0.1"))
        await asyncio.sleep(delay)
        return {"slept": delay}


# ---------------------------------------------------------------------------
# Gateway Configuration — multiple routes with different policies
# ---------------------------------------------------------------------------

def create_gateway() -> ApiGatewayService:
    """Create gateway with realistic multi-route configuration."""
    gateway = ApiGatewayService(
        broker=BookstoreBroker(),
        settings={
            "port": 3000,
            "ip": "127.0.0.1",
            "path": "/api",
            "routes": [
                # Route 1: Public API (restrict — only explicit aliases)
                {
                    "path": "/v1",
                    "mappingPolicy": "restrict",
                    "aliases": {
                        # Books CRUD
                        "GET /books": "books.list",
                        "GET /books/search": "books.search",
                        "GET /books/{id}": "books.get",
                        "POST /books": "books.create",
                        "PUT /books/{id}": "books.update",
                        "PATCH /books/{id}": "books.update",
                        "DELETE /books/{id}": "books.remove",
                        # Authors
                        "GET /authors": "authors.list",
                        "GET /authors/{id}": "authors.get",
                        # Reviews (nested resource)
                        "GET /books/{bookId}/reviews": "reviews.list",
                        "POST /books/{bookId}/reviews": "reviews.add",
                        # Utility
                        "GET /health": "health.check",
                        "GET /echo": "echo.params",
                        "POST /echo": "echo.params",
                        # Response types
                        "GET /echo/empty": "echo.empty",
                        "GET /echo/bytes": "echo.bytes",
                        "GET /echo/string": "echo.string",
                        # Math
                        "GET /math/calc": "math.calc",
                    },
                },
                # Route 2: Internal/debug API (all — any action via URL)
                {
                    "path": "/internal",
                    "mappingPolicy": "all",
                    "aliases": {},
                },
            ],
        },
    )
    gateway._build_routes()
    gateway._app = gateway._create_app()
    return gateway


# ---------------------------------------------------------------------------
# Run server
# ---------------------------------------------------------------------------

async def main() -> None:
    gateway = create_gateway()
    print("\n" + "=" * 65)
    print("  Bookstore API — moleculerpy-web Phase 1 Demo")
    print("=" * 65)
    print(f"  http://127.0.0.1:3000/api/v1/...")
    print()
    print("  Services: books, authors, reviews, health, echo, math")
    print("  Routes:   /api/v1 (restrict)  |  /api/internal (all)")
    print()
    print("  Run smoke tests:  python examples/smoke_test.py")
    print("  Press Ctrl+C to stop")
    print("=" * 65 + "\n")

    config = uvicorn.Config(gateway.app, host="127.0.0.1", port=3000, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown.")
