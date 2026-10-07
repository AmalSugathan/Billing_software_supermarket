#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
if [ "${REQUIRE_POSTGRES_TESTS:-0}" = "1" ]; then
  uv run --locked pytest --cov=supermarket --cov-report=term-missing
else
  uv run --locked pytest -m 'not database'
fi
cd apps/web
npm ci --no-fund
npm run typecheck:api
npm run lint
npm run typecheck
npm test
npm run build
