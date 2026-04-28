#!/usr/bin/env bash
# bin/demo-down.sh — tear down the local dev stack.
#
# Stops + removes containers BUT preserves the named postgres volume
# (iris-py-pgdata) so customer rows survive an `up`/`down` cycle.
# To wipe the DB too, pass `--volumes` :
#   bin/demo-down.sh --volumes

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

if [[ "${1:-}" == "--volumes" ]]; then
  echo "▶ docker compose down --volumes (DB DATA WILL BE LOST)"
  docker compose down --volumes
else
  echo "▶ docker compose down (DB data preserved in iris-py-pgdata volume)"
  docker compose down
fi

echo "✔ infra stopped"
