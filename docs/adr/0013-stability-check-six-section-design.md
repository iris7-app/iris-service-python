# ADR-0013 — `stability-check.sh` six-section progressive design

**Status** : Accepted
**Date** : 2026-04-26
**Sibling project** : `../iris-service-java/bin/dev/stability-check.sh`
                       (different design — multi-repo aggregator with `bin/dev/sections/*.sh`)

## Context

The Java side ships `bin/dev/stability-check.sh` as a **multi-repo
backend stability checker** (sonar + CVE + bundle-size + ADR-drift across
svc + UI). It iterates over `$SVC_DIR` + `$UI_DIR`, sources sub-section
files in `bin/dev/sections/*.sh`, and produces a cross-cutting report.

The Python side needed its own preflight script for the same purpose
(decide if `stable-py-v*` is safe to tag) but with a **different
operational shape** :

- Python is **single-repo** (no UI sibling to aggregate over from inside
  Python's CI ; UI's stability is a separate concern).
- Python's toolchain is **uv-centric** : every check runs `uv run <tool>`.
  Java's checker invokes `mvn`, `npx`, `glab`, etc. natively.
- Python's quality bar emphasises **strict-typing + property tests +
  contract enforcement** more than Java side does at the script level.
- Python's `bin/` is intentionally lighter (~6 scripts vs Java's 80+),
  so a single self-contained 200-LOC script is preferred over the
  Java pattern of `stability-check.sh` + `sections/*.sh` modular split.

The decision : how should the Python checker be structured ?

## Decision

A **single-file 6-section progressive script** at
`bin/dev/stability-check.sh` (~200 LOC). Each section is independent
(failure in one doesn't kill the rest) ; findings are aggregated into a
single 🟢 / 🟡 / 🔴 verdict at the end. Sections, in fixed order :

| # | Section | Tool | Scope | Failure level if red |
|---|---|---|---|---|
| 1 | Preflight | `git`, submodule check | branch (`dev`/`main`), working tree clean, `infra/shared` initialised | 🟡 (amber) — non-blocking |
| 2 | Code quality | `uv run ruff check` + `ruff format --check` + `uv run mypy src` | lint clean, formatter clean, mypy strict passes | 🔴 (red) for ruff/mypy ; 🟡 for format |
| 3 | Tests + coverage | `uv run pytest tests/unit -q` | pytest passes, total coverage ≥ 90 % | 🔴 if pytest fails ; 🟡 if coverage < 90 % |
| 4 | Architecture | `uv run lint-imports --config .importlinter` | 4 contracts kept, 0 broken | 🔴 if any contract broken |
| 5 | Security | `uv run pip-audit --ignore-vuln CVE-2026-3219` | no NEW CVEs (CVE-2026-3219 grandfathered — pip-bundled, no upstream fix) | 🟡 amber (security findings warrant attention but not always blocking) |
| 6 | ADR drift | `infra/common/bin/dev/regen-adr-index.sh --check` (universal regenerator from common submodule) | flat-index table in `docs/adr/README.md` matches files | 🟡 amber + actionable fix command |

**Exit code** : 0 if no red findings (amber-only is OK), 1 if any red.

**Flags** :
- `--fast` skips sections 3 + 5 (the slow ones — pytest can take 2 min, pip-audit 30 s)
- `--skip-tests` / `--skip-security` / `--skip-adr` skip individual sections
- `--report` writes the output to `docs/audit/stability-<date>.md` for archival

## Consequences

### Positive

- **Single file readable in one pass** (~200 LOC). New contributor or
  Claude session understands the whole preflight in 2 minutes.
- **Section ordering = priority** : preflight before code (don't run
  ruff on a dirty repo), code before tests (lint failures are cheaper
  to fix than test failures), tests before architecture (a passing
  test that violates import-linter is a smaller problem than a failing
  test). The fixed order makes a habitual reader's pattern-matching
  reliable — section 4 is always the import-linter check.
- **Progressive disclosure** : each section produces 1-3 lines of output
  with a clear emoji verdict. Failed sections include the actionable
  fix command (e.g. "run 'uv run ruff check .' to see"). No need to
  scroll a 1000-line CI log.
- **Independent sections** : a failed section doesn't kill the rest.
  The maintainer sees ALL failures at once and fixes them in batches,
  rather than the iterate-rerun-iterate loop of fail-fast scripts.
- **Symmetric to Java** at the workflow level (both repos answer
  "is this commit safe to tag?" with the same "🟢/🟡/🔴 + exit code"
  contract) without forcing identical implementation.

### Negative

- **Implementation duplicated** between Java + Python (different shape,
  but same semantic). If we ever add a 7th section that's universal
  (e.g. "check `infra/common` SHA is up to date with main"), it'd need
  to be added in both files. Mitigated by : the universal pieces ARE
  in `infra/common/bin/dev/...` (e.g. regen-adr-index.sh) and called
  from both — only the orchestration logic is per-repo.
- **No multi-repo aggregation** unlike Java's checker. If we ever want
  a single command "report all 4 repos' stability", we'd need a
  separate orchestrator. Acceptable today since each repo's CI runs
  independently and the user reads MR pipelines per-repo.
- **Hard-coded Python toolchain** (`uv run ruff`, `uv run mypy`, etc.).
  If we ever change Python's package manager (away from uv), this
  script needs an audit. Mitigated by : ADR-0009 documents the uv
  choice + its expected stability.

### Neutral

- **The `--fast` mode** is the default for "quick check before commit".
  The full mode (~2-3 min) is for pre-tag preflight. The choice between
  modes is the maintainer's habit, not enforced.
- **Coverage 90 % threshold** : see ADR-0014 for the rationale + how
  it interacts with this section.
- **The `--report` mode** writes to `docs/audit/` which is gitignored
  for casual runs but committed when reported as evidence
  (e.g. before tagging a milestone).

## Alternatives considered

### Multi-file modular (Java pattern)

Split into `bin/dev/stability-check.sh` (orchestrator) + `bin/dev/sections/*.sh`
(individual checks). Rejected for Python because :
- Python's `bin/` is intentionally light (~6 scripts) — adding a
  `sections/` subdir for 6 small functions feels over-architected.
- Sourcing files adds bash complexity (managing globals, naming
  collisions). Single-file keeps it grep-friendly.
- The Java pattern made sense there because it has 12+ sections including
  cross-repo aggregation. Python has 6 single-repo sections — a flat
  function list is the natural shape.

### Replace with a `pytest`-style plugin system

Use a generic preflight framework (e.g. `pre-commit` hooks, or a custom
plugin loader). Rejected for : (a) it's a 200-LOC script, the framework
overhead would dwarf the logic, (b) every section is a thin wrapper over
an existing tool's CLI — the abstraction would just hide that.

### Use a Python script instead of bash

The checker could be `bin/dev/stability-check.py` since this IS a Python
project. Rejected for : (a) bash is faster to start (no Python interpreter
warm-up for a 5-second script), (b) it would need its own deps (or `uv
run`), (c) bash is the sibling Java + UI project's choice — keeping
Python on bash for `bin/` scripts maintains cross-repo symmetry at the
shell-script layer.

## References

- [Python stability-check.sh source](https://gitlab.com/iris-7/iris-service-python/-/blob/main/bin/dev/stability-check.sh)
- [Java's stability-check.sh + sections](https://gitlab.com/iris-7/iris-service-java/-/tree/main/bin/dev) — different design, same goal
- [common ADR-0001 — submodule pattern](https://gitlab.com/iris-7/iris-common/-/blob/main/docs/adr/0001-shared-repo-via-submodule.md)
- [ADR-0014](0014-coverage-floor-and-property-based-testing.md) — the coverage threshold this script enforces
- [ADR-0007](0007-industrial-python-best-practices.md) — broader Python quality bar
