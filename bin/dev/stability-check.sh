#!/usr/bin/env bash
# =============================================================================
# bin/dev/stability-check.sh — multi-section preflight before tagging
# `stable-py-vX.Y.Z`.
#
# Mirror of the Java side's stability-check.sh. Adapted for Python's
# toolchain (uv + ruff + mypy + pytest + pip-audit + import-linter) so
# the same "is the repo ready for a stable tag?" question gets a consistent
# answer in both languages.
#
# Sections (each can be toggled via --skip-<name>) :
#   1. Preflight    — git state + branch + uncommitted check
#   2. Code         — ruff check + ruff format --check + mypy strict
#   3. Tests        — pytest unit + coverage threshold
#   4. Architecture — import-linter contracts (4 contracts, 0 broken)
#   5. Security     — pip-audit (CVE scan, with grandfathered ignores)
#   6. ADR drift    — bin/dev/regen-adr-index.sh --check (when README.md exists)
#
# Each section ends with a 🟢 / 🟡 / 🔴 verdict.
# Exit 0 if no 🔴, exit 1 otherwise.
#
# Usage :
#   bin/dev/stability-check.sh                # full run (~2-3 min)
#   bin/dev/stability-check.sh --fast         # skip pytest + pip-audit (the slow ones)
#   bin/dev/stability-check.sh --skip-tests   # skip just pytest
#   bin/dev/stability-check.sh --skip-security # skip just pip-audit
#   bin/dev/stability-check.sh --report       # write a report to docs/audit/stability-<date>.md
# =============================================================================

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

# ── Colors ───────────────────────────────────────────────────────────────────
G='\033[32m'; Y='\033[33m'; R='\033[31m'; B='\033[34m'; BOLD='\033[1m'; DIM='\033[2m'; N='\033[0m'
green() { printf "${G}🟢${N} %s\n" "$1"; }
amber() { printf "${Y}🟡${N} %s\n" "$1"; AMBER=$((AMBER + 1)); }
red()   { printf "${R}🔴${N} %s\n" "$1"; RED_COUNT=$((RED_COUNT + 1)); }
info()  { printf "${B}ℹ${N}  %s\n" "$1"; }
section() { printf "\n${BOLD}── %s ──${N}\n" "$1"; }

AMBER=0
RED_COUNT=0
SKIP_TESTS=0
SKIP_SECURITY=0
SKIP_ADR=0
REPORT=0

for arg in "$@"; do
    case "$arg" in
        --fast)          SKIP_TESTS=1 ; SKIP_SECURITY=1 ;;
        --skip-tests)    SKIP_TESTS=1 ;;
        --skip-security) SKIP_SECURITY=1 ;;
        --skip-adr)      SKIP_ADR=1 ;;
        --report)        REPORT=1 ;;
        --help|-h)
            sed -n '2,40p' "$0"
            exit 0
            ;;
    esac
done

REPORT_FILE=""
if [ "$REPORT" = "1" ]; then
    /bin/mkdir -p docs/audit
    REPORT_FILE="docs/audit/stability-$(date +%Y-%m-%d-%H%M).md"
    exec > >(tee "$REPORT_FILE") 2>&1
    echo "# Stability check — $(date +'%Y-%m-%d %H:%M')"
    echo ""
fi

printf "${BOLD}stability-check (python) — %s${N}\n" "$(date +'%Y-%m-%d %H:%M:%S')"
printf "${DIM}repo: %s${N}\n" "$REPO_ROOT"

# ── 1. Preflight ──
section "1. Preflight"
BRANCH=$(git branch --show-current 2>/dev/null)
if [ "$BRANCH" = "dev" ] || [ "$BRANCH" = "main" ]; then
    green "branch=$BRANCH"
else
    amber "branch=$BRANCH (working off dev is the convention)"
fi

DIRTY=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
if [ "$DIRTY" = "0" ]; then
    green "working tree clean"
else
    amber "$DIRTY uncommitted file(s) (won't be in the tag if you tag now)"
fi

if git -C infra/shared rev-parse HEAD >/dev/null 2>&1; then
    SHARED_SHA=$(git -C infra/shared rev-parse --short HEAD)
    green "infra/shared submodule pinned at $SHARED_SHA"
else
    red "infra/shared submodule not initialised (git submodule update --init)"
fi

# ── 2. Code quality ──
section "2. Code quality"
if uv run ruff check . --quiet >/dev/null 2>&1; then
    green "ruff check clean"
else
    red "ruff check FAILED — run 'uv run ruff check .'"
fi

if uv run ruff format --check . >/dev/null 2>&1; then
    green "ruff format clean"
else
    amber "ruff format would reformat — run 'uv run ruff format .'"
fi

if uv run mypy src --no-error-summary >/dev/null 2>&1; then
    green "mypy strict passes"
else
    red "mypy strict FAILED — run 'uv run mypy src'"
fi

# ── 3. Tests + coverage ──
section "3. Tests + coverage"
if [ "$SKIP_TESTS" = "1" ]; then
    info "skipped (--skip-tests / --fast)"
else
    if uv run pytest tests/unit -q 2>&1 | tail -5 | grep -qE "[0-9]+ passed"; then
        cov=$(uv run pytest tests/unit -q 2>&1 | grep -oE "Total coverage: [0-9.]+%" | tail -1 | awk '{print $3}')
        if [ -n "$cov" ]; then
            green "pytest passed, coverage $cov"
            # Extract numeric (strip %), compare to 90 % floor.
            cov_num=$(echo "$cov" | tr -d '%' | awk -F. '{print $1}')
            if [ -n "$cov_num" ] && [ "$cov_num" -lt 90 ]; then
                amber "coverage $cov below 90 % floor"
            fi
        else
            green "pytest passed (coverage unparsed)"
        fi
    else
        red "pytest FAILED — run 'uv run pytest tests/unit'"
    fi
fi

# ── 4. Architecture (import-linter) ──
section "4. Architecture (import-linter)"
if uv run lint-imports --config .importlinter --no-cache 2>/dev/null | grep -q "Contracts: 4 kept, 0 broken"; then
    green "import-linter : 4 contracts kept, 0 broken"
else
    red "import-linter contracts BROKEN — run 'uv run lint-imports --config .importlinter'"
fi

# ── 5. Security (pip-audit) ──
section "5. Security (pip-audit)"
if [ "$SKIP_SECURITY" = "1" ]; then
    info "skipped (--skip-security / --fast)"
else
    if uv run pip-audit --ignore-vuln CVE-2026-3219 2>&1 | grep -q "No known vulnerabilities found"; then
        green "pip-audit : no new CVEs (CVE-2026-3219 ignored — pip bundled, no upstream fix)"
    else
        amber "pip-audit found new vulnerabilities — run 'uv run pip-audit'"
    fi
fi

# ── 6. ADR drift ──
# Calls the COMMON regenerator via the universal submodule (factored
# 2026-04-26 into iris-common per ADR-0001 + ADR-0060). Falls back
# to the legacy infra/shared/bin/... path for the brief transition
# window when this repo's submodule SHA hasn't been bumped yet.
section "6. ADR drift"
if [ -x "infra/common/bin/dev/regen-adr-index.sh" ]; then
    ADR_REGEN="infra/common/bin/dev/regen-adr-index.sh"
elif [ -x "infra/shared/bin/dev/regen-adr-index.sh" ]; then
    ADR_REGEN="infra/shared/bin/dev/regen-adr-index.sh"
else
    ADR_REGEN=""
fi
if [ "$SKIP_ADR" = "1" ]; then
    info "skipped (--skip-adr)"
elif [ ! -f docs/adr/README.md ]; then
    info "no docs/adr/README.md — skipping drift check"
elif [ -z "$ADR_REGEN" ]; then
    amber "regenerator missing in infra/common/ AND infra/shared/ — bump submodule SHA"
elif "$ADR_REGEN" --check >/dev/null 2>&1; then
    green "ADR index in sync with docs/adr/*.md"
else
    amber "ADR index drift — run '$ADR_REGEN --in-place'"
fi

# ── Summary ──
echo ""
printf "${BOLD}── Summary ──${N}\n"
if [ "$RED_COUNT" = "0" ] && [ "$AMBER" = "0" ]; then
    printf "${G}All sections green.${N} Safe to tag stable-py-v<next>.\n"
    [ -n "$REPORT_FILE" ] && echo "Report: $REPORT_FILE"
    exit 0
elif [ "$RED_COUNT" = "0" ]; then
    printf "${Y}%d amber finding(s), 0 red.${N} Tag possible but review the warnings.\n" "$AMBER"
    [ -n "$REPORT_FILE" ] && echo "Report: $REPORT_FILE"
    exit 0
else
    printf "${R}%d red finding(s), %d amber.${N} Do NOT tag — fix the red items first.\n" "$RED_COUNT" "$AMBER"
    [ -n "$REPORT_FILE" ] && echo "Report: $REPORT_FILE"
    exit 1
fi
