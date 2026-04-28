#!/usr/bin/env bash
# bin/demo-up.sh — full demo bootstrap : infra + migrations + app.
#
# 1. Brings up postgres, redis, kafka, lgtm via docker compose.
# 2. Waits for postgres to be healthy.
# 3. Applies Alembic migrations to head.
# 4. Starts the FastAPI app with hot-reload (foreground — Ctrl-C stops the app
#    but leaves the infra up ; run `bin/demo-down.sh` to tear infra down too).
#
# Idempotent : re-running brings any-down services back up without losing
# postgres data (named volume iris-py-pgdata persists).

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Bring up .env from .env.example if missing — first-run convenience so the
# user doesn't get cryptic "IRIS_DB__PASSWORD missing" errors.
if [[ ! -f .env ]]; then
  echo "▶ creating .env from .env.example (first-run convenience)"
  cp .env.example .env
fi

echo "▶ docker compose up -d (postgres, redis, kafka, lgtm)"
docker compose up -d

echo "▶ waiting for postgres health..."
# Retry up to ~60s — postgres is fast but kafka pulls a 700 MB image on
# first run which can stall the docker daemon.
for i in $(seq 1 60); do
  if docker exec iris-py-postgres pg_isready -U iris -d iris &>/dev/null; then
    echo "  ✔ postgres healthy after ${i}s"
    break
  fi
  if [[ $i -eq 60 ]]; then
    echo "  ✘ postgres failed health after 60s — check 'docker compose logs postgres'"
    exit 1
  fi
  sleep 1
done

echo "▶ alembic upgrade head"
uv run alembic upgrade head

echo "▶ services up :"
echo "  • API           : http://localhost:8080         (Swagger : /docs)"
echo "  • Grafana       : http://localhost:3000         (admin / admin)"
echo "  • Postgres      : localhost:5432                (iris / iris)"
echo "  • Redis         : localhost:6379"
echo "  • Kafka         : localhost:9092"
echo ""

exec bin/run.sh
