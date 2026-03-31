# Changelog

## [0.14.1a1] - 2026-03-31

### Added
- Initial alpha release — Phase 1 Core Gateway
- `ApiGatewayService` — Starlette app lifecycle in Service.started()/stopped()
- `AliasResolver` — path matching with `{param}` and `:param` support
- HTTP request handler — request → broker.call() → response pipeline
- Error mapping: MoleculerError hierarchy → HTTP status codes
- JSON body parsing + query parameter merging
- Basic configuration (port, ip, path prefix)
- Mapping policy: `restrict` (default) / `all`
