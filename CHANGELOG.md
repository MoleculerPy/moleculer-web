# Changelog

## [0.1.0] - 2026-04-03

### Fixed — Phase 4: Stable Release
- **Rate limit stores**: moved from module-level global to per-service instance
  - Key changed from `id(config)` to `(route_path, window, limit)` for deterministic identity
  - `stopped()` now calls `store.stop()` on all stores (fixes asyncio task leak)
  - `handle_request()` raises `InternalServerError` if `rate_limit_stores` not provided
- **VALID_ACTION_RE**: deduplicated from handler.py + service.py into utils.py
- **README**: fixed Quick Start (internal API → broker.create_service pattern), test count 277→365

### Changed
- Examples consolidated: 10 files → 4 (basic_gateway, benchmark, demo, smoke_test)
- Development Status classifier: Alpha → Beta

### Tests
- 365 tests (was 348), 93% coverage
- Added: store lifecycle, key isolation, instance isolation, VALID_ACTION_RE security boundary

## [0.1.0b1] - 2026-04-02

### Added — Phase 3: Stable Release
- **Service Inheritance**: `ApiGatewayService` now extends `moleculerpy.Service` (ADR-002)
  - Full broker integration: registry, lifecycle, mixins support
  - `broker.register(gateway)` standard pattern
- **Internal Actions**: `api.listAliases`, `api.addRoute`, `api.removeRoute`
  - Dynamic route management at runtime
  - Local-node-only security guard (default-deny)
- **Auto-aliases**: `$services.changed` event handler
  - Automatic alias generation from action `rest` annotations
  - Whitelist/blacklist filtering during generation
  - Action name regex validation
- **Streaming Responses**: async/sync generators → `StreamingResponse`
- **File Upload**: multipart/form-data parsing via python-multipart
  - Filename sanitization (path traversal, null bytes, length limit)
  - File count limit (MAX_FILES_PER_REQUEST=50)
  - Content-Length pre-check before reading body
- **Static File Serving**: Starlette `StaticFiles` mount via `settings.assets`
  - `os.path.realpath` resolution for security
- **ETag + Conditional GET**: `generate_etag()` + 304 Not Modified
  - Weak ETag (W/"...") with MD5 (usedforsecurity=False)
  - If-None-Match header support with multi-value parsing
- **New exports**: `build_response`, `parse_body`, `parse_multipart`, `generate_etag`, `check_etag_match`

### Security
- CRLF injection prevention in `$responseHeaders`
- Open redirect allowlist (only relative `/`-prefixed paths)
- Path traversal prevention in multipart filenames (`ntpath.basename` + `os.path.basename`)
- Auto-aliases action name validation + whitelist/blacklist enforcement
- Static files path traversal blocked (Starlette + realpath)
- Content-Length pre-check to prevent DoS via oversized bodies
- Extracted `_build_resolver()` helper to eliminate code duplication
- Default-deny `_is_local_call()` guard for addRoute/removeRoute

### Tests
- 348 tests (71 new), 93% coverage
- 45-point real NATS E2E verification (features + security + edge cases)
- Security guard tests: remote rejection, fail-secure, filename sanitization

## [0.1.0a1] - 2026-04-01

### Added
- Initial alpha release — Phase 1+2 Core Gateway + Features
- `ApiGatewayService` — Starlette app lifecycle in Service.started()/stopped()
- `AliasResolver` — path matching with `{param}` and `:param` support
- HTTP request handler — request → broker.call() → response pipeline
- Error mapping: MoleculerError hierarchy → HTTP status codes
- JSON body parsing + query parameter merging
- Basic configuration (port, ip, path prefix)
- Mapping policy: `restrict` (default) / `all`
- Hook system: onBeforeCall, onAfterCall, onError
- Authentication + Authorization hooks
- Whitelist/blacklist actions
- CORS middleware
- Rate limiting (MemoryStore, per-IP)
- REST shorthand (`"REST /users"` → 6 CRUD aliases)
- 277 tests, 94% coverage
