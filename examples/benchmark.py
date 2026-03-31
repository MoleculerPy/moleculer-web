"""Simple benchmark for moleculerpy-web gateway.

Usage:
    python examples/benchmark.py           # Mock broker (pure gateway speed)
    python examples/benchmark.py --real    # Real NATS broker

NFR-001: >= 5,000 req/sec on simple GET
"""

from __future__ import annotations

import asyncio
import sys
import time

import httpx

from moleculerpy_web import ApiGatewayService


async def run_benchmark(
    base_url: str,
    n_requests: int = 5000,
    concurrency: int = 50,
) -> float:
    """Run benchmark: n_requests total, concurrency parallel."""

    async def single_request(client: httpx.AsyncClient, i: int) -> float:
        start = time.perf_counter()
        r = await client.get(f"{base_url}/api/v1/health")
        elapsed = time.perf_counter() - start
        assert r.status_code == 200, f"Request {i} failed: {r.status_code}"
        return elapsed

    print(f"\nBenchmark: {n_requests} requests, concurrency={concurrency}")
    print(f"Target: {base_url}/api/v1/health")
    print()

    latencies: list[float] = []
    errors = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Warmup
        for _ in range(10):
            await client.get(f"{base_url}/api/v1/health")

        start_time = time.perf_counter()

        # Run in batches of `concurrency`
        for batch_start in range(0, n_requests, concurrency):
            batch_size = min(concurrency, n_requests - batch_start)
            tasks = [
                single_request(client, batch_start + i)
                for i in range(batch_size)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    errors += 1
                else:
                    latencies.append(r)

        total_time = time.perf_counter() - start_time

    # Stats
    latencies.sort()
    rps = len(latencies) / total_time
    avg_ms = (sum(latencies) / len(latencies)) * 1000
    p50 = latencies[len(latencies) // 2] * 1000
    p95 = latencies[int(len(latencies) * 0.95)] * 1000
    p99 = latencies[int(len(latencies) * 0.99)] * 1000
    min_ms = latencies[0] * 1000
    max_ms = latencies[-1] * 1000

    print("Results:")
    print(f"  Requests:     {len(latencies)} OK, {errors} errors")
    print(f"  Total time:   {total_time:.2f}s")
    print(f"  Throughput:   {rps:.0f} req/sec")
    print(f"  Latency avg:  {avg_ms:.2f}ms")
    print(f"  Latency p50:  {p50:.2f}ms")
    print(f"  Latency p95:  {p95:.2f}ms")
    print(f"  Latency p99:  {p99:.2f}ms")
    print(f"  Latency min:  {min_ms:.2f}ms")
    print(f"  Latency max:  {max_ms:.2f}ms")
    print()

    nfr_pass = rps >= 5000
    status = "PASS" if nfr_pass else "FAIL"
    print(f"  NFR-001 (>= 5,000 req/sec): {status} ({rps:.0f})")

    return rps


async def run_with_mock_server() -> None:
    """Start mock server and benchmark."""
    from unittest.mock import AsyncMock, MagicMock

    import uvicorn

    broker = MagicMock()
    broker.call = AsyncMock(
        return_value={
            "status": "ok",
            "services": ["test"],
            "uptime": time.time(),
        }
    )

    gateway = ApiGatewayService(
        broker=broker,
        settings={
            "port": 3001,
            "ip": "127.0.0.1",
            "path": "/api",
            "routes": [
                {
                    "path": "/v1",
                    "aliases": {"GET /health": "health.check"},
                }
            ],
        },
    )
    gateway._build_routes()
    gateway._app = gateway._create_app()

    config = uvicorn.Config(
        gateway.app, host="127.0.0.1", port=3001, log_level="error"
    )
    server = uvicorn.Server(config)

    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(1)  # Wait for server

    try:
        await run_benchmark("http://127.0.0.1:3001")
    finally:
        server.should_exit = True
        await server_task


async def main() -> None:
    if "--real" in sys.argv:
        print("Benchmarking REAL server at localhost:3000...")
        await run_benchmark("http://127.0.0.1:3000")
    else:
        print("Benchmarking with MOCK broker (pure gateway overhead)...")
        await run_with_mock_server()


if __name__ == "__main__":
    asyncio.run(main())
