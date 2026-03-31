"""Test concurrent request handling."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from moleculerpy_web.service import ApiGatewayService


@pytest.fixture
async def concurrent_gateway() -> tuple[ApiGatewayService, MagicMock]:
    call_count = 0

    async def slow_call(action: str, params: dict, **kw: object) -> dict:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)  # Simulate 10ms latency
        return {"action": action, "call_number": call_count}

    broker = MagicMock()
    broker.call = AsyncMock(side_effect=slow_call)

    gateway = ApiGatewayService(
        broker=broker,
        settings={
            "port": 3000,
            "path": "/api",
            "routes": [
                {
                    "path": "/",
                    "aliases": {
                        "GET /test": "test.action",
                        "GET /test/{id}": "test.get",
                    },
                }
            ],
        },
    )
    gateway._build_routes()
    gateway._app = gateway._create_app()
    return gateway, broker


class TestConcurrent:
    async def test_50_parallel_requests(
        self,
        concurrent_gateway: tuple[ApiGatewayService, MagicMock],
    ) -> None:
        """50 concurrent GET requests should all succeed."""
        gateway, broker = concurrent_gateway
        transport = ASGITransport(app=gateway.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            tasks = [client.get("/api/test", params={"i": str(i)}) for i in range(50)]
            responses = await asyncio.gather(*tasks)

        assert all(r.status_code == 200 for r in responses)
        assert len(responses) == 50
        # All requests were served (broker.call invoked 50 times)
        assert broker.call.call_count == 50

    async def test_mixed_methods_concurrent(
        self,
        concurrent_gateway: tuple[ApiGatewayService, MagicMock],
    ) -> None:
        """Mixed GET requests with different paths."""
        gateway, broker = concurrent_gateway
        transport = ASGITransport(app=gateway.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            tasks = []
            for i in range(20):
                if i % 2 == 0:
                    tasks.append(client.get(f"/api/test/{i}"))
                else:
                    tasks.append(client.get("/api/test", params={"n": str(i)}))
            responses = await asyncio.gather(*tasks)

        assert all(r.status_code == 200 for r in responses)
        assert broker.call.call_count == 20

    async def test_concurrent_with_errors(
        self,
        concurrent_gateway: tuple[ApiGatewayService, MagicMock],
    ) -> None:
        """Some requests fail, others succeed — no cross-contamination."""
        gateway, broker = concurrent_gateway

        call_num = 0

        async def flaky_call(action: str, params: dict, **kw: object) -> dict:
            nonlocal call_num
            call_num += 1
            if call_num % 3 == 0:
                from moleculerpy.errors import ServiceNotFoundError

                raise ServiceNotFoundError(action)
            return {"ok": True, "n": call_num}

        broker.call = AsyncMock(side_effect=flaky_call)

        transport = ASGITransport(app=gateway.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            tasks = [client.get("/api/test") for _ in range(30)]
            responses = await asyncio.gather(*tasks)

        ok_count = sum(1 for r in responses if r.status_code == 200)
        err_count = sum(1 for r in responses if r.status_code == 404)
        assert ok_count == 20  # 30 - 10 failures (every 3rd)
        assert err_count == 10
