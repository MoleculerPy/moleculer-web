# MoleculerPy Web — HTTP API Gateway

**HTTP API Gateway for MoleculerPy microservices.**

**Depends on**: `moleculerpy>=0.14.6`, `starlette>=0.40`, `uvicorn>=0.30`

## Quick Start

```bash
# ВАЖНО: использовать Python 3.12+ venv (НЕ системный Python 3.9)
VENV=/Users/explosovebit/Work/MoleculerPy/.venv/bin/python
$VENV -m pip install -e ".[dev]"
$VENV -m pytest                  # Run tests (277 tests, 95% coverage)
$VENV -m mypy moleculerpy_web/  # Type check (strict)
$VENV -m ruff check moleculerpy_web/ # Lint

# Или активировать venv:
source /Users/explosovebit/Work/MoleculerPy/.venv/bin/activate
pytest && mypy moleculerpy_web/ && ruff check moleculerpy_web/
```

## Architecture

| Module | File | Purpose |
|---|---|---|
| ApiGatewayService | service.py | HTTP server lifecycle (89 LOC) |
| Request Handler | handler.py | Pipeline: alias → middleware → broker.call (81 LOC) |
| Middleware | middleware.py | RequestContext + compose (97 LOC) |
| AliasResolver | alias.py | Path matching + REST shorthand (70 LOC) |
| Errors | errors.py | HTTP error classes + mapping (67 LOC) |
| CORS | cors.py | Origin check + headers (142 LOC) |
| Rate Limit | ratelimit.py | MemoryStore + config (138 LOC) |
| Access | access.py | Whitelist/blacklist (132 LOC) |

## Performance

- 11,425 req/sec (mock broker)
- 10,900 req/sec (real NATS)
- p50 latency: 4.3ms

## Git Workflow

### Branches: main ← dev ← feat/*

```bash
git checkout dev && git pull origin dev
git checkout -b feat/task-name
git add file1 file2
git commit -m "feat(module): what was done"
git push origin feat/task-name -u
gh pr create --base dev
gh pr merge --merge --delete-branch=false
```

### Commits: `type(module): description`
Types: feat, fix, docs, test, refactor, chore

### Forbidden
- `git push --force`, `git reset --hard`, `git add .`
- Direct commits to main/dev

## Methodology: Route → Shape → Code → Evidence

1. **Route** — Tactical / Standard / Deep / Critical
2. **Shape** — PRD/RFC/ADR before coding (Standard+)
3. **Code** — every function = test immediately, `pytest` required
4. **Evidence** — confirm the result

## Enforcement Hooks

5 hooks in `.claude/hooks/`:

| Hook | Checks |
|---|---|
| forge-safety | Blocks dangerous commands |
| pr-todo-check | P0 checkboxes before PR |
| commit-test-check | Tests for new `def` |
| pre-code-check | Active PRD before coding |
| pre-commit-health | Blind spots |
