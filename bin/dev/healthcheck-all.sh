#!/usr/bin/env bash
# =============================================================================
# bin/dev/healthcheck-all.sh — one-glance status of every Iris local service.
#
# Mirror of the Java side's healthcheck-all.sh, adapted for the Python stack.
# Probes the same dev infrastructure (Postgres + Kafka + Redis + LGTM + Ollama)
# plus the Python uvicorn backend at :8080.
#
# Why this script exists : same pain as Java side — "why is the UI showing
# 'backend down'?" used to mean checking 8 things by hand. This does it in
# one pass.
#
# Usage :
#   bin/dev/healthcheck-all.sh           # human-readable table (default)
#   bin/dev/healthcheck-all.sh --json    # machine-readable for scripts
#
# Exit code : 0 if all required UP, 1 if any required DOWN.
# =============================================================================

set -uo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[0;33m'
BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

MODE="${1:-human}"

# Each entry : <label>|<probe-command>|<expected-substring>|<required>
SERVICES=(
  "Postgres (mirador app db)|docker exec mirador-py-postgres pg_isready -U mirador|accepting connections|1"
  "Kafka (mirador events)|docker exec mirador-py-kafka kafka-topics.sh --bootstrap-server localhost:9092 --list|.|1"
  "Redis (recent customers buffer)|docker exec mirador-py-redis redis-cli ping|PONG|1"
  "FastAPI backend (:8080)|curl -sSf -m 3 http://localhost:8080/actuator/health|UP|1"
  "Grafana / LGTM (:3000)|curl -sSf -m 3 http://localhost:3000/api/health|ok|0"
  "Ollama (:11434, opt-in profile=llm)|curl -sSf -m 3 http://localhost:11434/api/tags|.|0"
  "Tempo (:3200)|curl -sSf -m 3 http://localhost:3200/ready|ready|0"
)

results=()
fails=0

for entry in "${SERVICES[@]}"; do
    IFS='|' read -r label cmd expected required <<< "$entry"
    if eval "$cmd" 2>/dev/null | grep -q "$expected"; then
        status="UP"
        symbol="${GREEN}✓${NC}"
    else
        if [ "$required" = "1" ]; then
            status="DOWN"
            symbol="${RED}✗${NC}"
            fails=$((fails + 1))
        else
            status="DOWN-OPT"
            symbol="${YELLOW}○${NC}"
        fi
    fi
    results+=("$symbol $status|$label")
done

if [ "$MODE" = "--json" ]; then
    echo "{"
    for r in "${results[@]}"; do
        IFS='|' read -r status label <<< "$r"
        clean_status=$(echo "$status" | sed 's/\\033\[[0-9;]*m//g' | awk '{print $2}')
        printf '  "%s": "%s",\n' "$label" "$clean_status"
    done | sed '$ s/,$//'
    echo "}"
else
    printf "${BOLD}Iris-py healthcheck — %s${NC}\n" "$(date +%H:%M:%S)"
    printf "${DIM}Required marked with ✗ on failure ; optional with ○${NC}\n\n"
    for r in "${results[@]}"; do
        IFS='|' read -r status label <<< "$r"
        printf "  %b  %s\n" "$status" "$label"
    done
    echo
    if [ "$fails" = "0" ]; then
        printf "${GREEN}All required services UP.${NC}\n"
    else
        printf "${RED}%d required service(s) DOWN.${NC}\n" "$fails"
    fi
fi

exit $fails
