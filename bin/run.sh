#!/usr/bin/env bash
# bin/run.sh — start the FastAPI app locally with uvicorn + hot-reload.
#
# Assumes the dev stack (postgres, redis, kafka, lgtm) is already up via
# `bin/demo-up.sh`. If you want the full demo flow with infra start +
# migrations + app, use `bin/demo-up.sh` instead.
#
# Hot-reload is enabled via IRIS_DEV_MODE=true (read by app.run() →
# uvicorn.run(reload=...)). Edits to src/ trigger a restart in <1s.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Source .env if present so IRIS_* vars get exported (uv handles this
# automatically for `uv run` scripts but explicit > implicit when running
# from a shell that doesn't know about pydantic-settings).
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

echo "▶ starting iris-service-python on http://${IRIS_SERVER_HOST:-0.0.0.0}:${IRIS_SERVER_PORT:-8080}"
echo "  (Ctrl-C to stop ; hot-reload is ON via IRIS_DEV_MODE)"
echo ""

exec uv run iris-service
