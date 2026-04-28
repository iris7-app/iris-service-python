# ADR-0007 : Industrial Python best practices baseline

**Status** : Accepted
**Date** : 2026-04-25
**Sibling** : `../iris-service-java` (Java side, Spring Boot 4 — uses
compile-time type system + Spotless + Checkstyle + SpotBugs + PMD)

## Context

Python lacks Java's compile-time type checking — runtime errors that the
JVM would catch at compile (NullPointerException, ClassCastException,
method-signature mismatch) only surface in production. To compensate +
match Java mirror's "industrial-grade demo" claim, the Python project
must apply a deliberate set of practices that emulate the static-typing
+ quality-gate posture.

User directive 2026-04-25 :
> "Fait bien une bonne couverture de test car en python le code est peu
> vérifié par le compilateur"
> "Met en place les bonnes pratiques de code industriel en python"
> "Maximise les infos de décorateurs de types partout"
> "Note ces décisions dans l'adr"

This ADR captures the baseline + the per-tool decisions.

## Decisions

### 1. Type system : mypy strict + Pydantic v2 runtime + max annotations

- `mypy --strict` in CI and pre-push hook (catches : missing return types,
  untyped defs, implicit `Any`, returning `Any` from typed functions,
  unused-ignore directives).
- `pydantic.mypy` plugin so Pydantic models get full mypy support
  (validators, computed fields, model_config typed correctly).
- **Annotations everywhere**, including :
  - Module-level constants → `Final[T]` (e.g. `_COST: Final[int] = 12`)
  - Fixed-set string fields → `Literal["access", "refresh"]` (vs plain
    `str` that mypy can't narrow)
  - Function args + return types — no `def foo(x):` allowed
  - TypeAliases for complex types (`type AuditEvent = dict[str, Any]`)
  - `Protocol` for duck-typed interfaces (vs `typing.Any`)
- `from __future__ import annotations` at the top of every file (Python
  3.14 still benefits from PEP 563 deferred evaluation).

### 2. Linting : ruff comprehensive ruleset

`pyproject.toml` `[tool.ruff.lint] select = [...]` :
- `E` + `W` — pycodestyle errors / warnings
- `F` — pyflakes (unused imports, undefined names)
- `I` — isort (import order)
- `B` — flake8-bugbear (likely bugs : mutable default arg, comparison
  to None, etc.)
- `C90` — mccabe complexity ≤ 10
- `N` — pep8-naming
- `UP` — pyupgrade (modern idioms : `list[int]` over `List[int]`)
- `S` — flake8-bandit (security : weak crypto, SQL injection, etc.)
- `RUF` — ruff-specific (RUF002 ambiguous unicode, RUF100 unused noqa)

Per-file ignores : tests get S101 (assert) + S105/S106/S107 (hardcoded
"passwords" = test fixtures, not real secrets).

### 3. Test coverage : 80 % gate, target 90 %+

- `pytest-cov` enforced in CI via `--cov-fail-under=80`.
- `concurrency = ["greenlet", "thread"]` in coverage config — fixes
  FastAPI router undercounting (SQLAlchemy async uses greenlets).
- Default `pytest` excludes integration tests via `-m 'not integration'`
  for speed ; opt-in via `pytest -m integration` for testcontainers
  Postgres + Kafka.
- Branches uncovered after best-effort : lifespan startup branches +
  consumer loops + degraded fallbacks (Redis/Kafka/Ollama outage paths
  are tested via mocks but the runtime exception handlers themselves
  are hard to trigger without spinning real broken services).

### 4. Property-based testing : hypothesis (selected paths)

`hypothesis` for invariant validation on :
- Customer DTO (email validation, name length bounds)
- JWT round-trip (encode → decode → equal claims for any valid input)
- RecentCustomerBuffer ordering (LIFO invariant)

Example-based pytest tests stay primary ; hypothesis adds adversarial
input search at low marginal cost.

### 5. Architectural boundaries : import-linter

`.importlinter` config enforces hexagonal-lite layering :
- `iris_service.api.*` may import from `iris_service.{customer,auth,messaging}` (use cases)
- `iris_service.{customer,auth,messaging}.*` may import from `iris_service.{db,integration,config}` (adapters)
- `iris_service.config.*` imports nothing else from the project
- No circular deps allowed

CI fails if any import violates the contract.

### 6. Documentation : mkdocs-material + mkdocstrings

- `mkdocs build --strict` in CI on docs/ + mkdocs.yml + src/**.py changes
  (docstrings drive autodoc).
- Google-style docstrings (handled by mkdocstrings).
- ADRs in `docs/adr/` — every architectural decision recorded.
- README.md + README.fr.md — i18n + key parity rule.

### 7. Hooks : lefthook commit-msg + pre-commit + pre-push

- **commit-msg** : conventional-commits regex (subject ≤ 72 chars).
- **pre-commit** (parallel) : ruff + ruff-format + yamllint + hadolint
  + glab ci lint + kubectl kustomize + .env key parity.
- **pre-push** : pytest tests/unit + mypy strict.

Bypass via `LEFTHOOK=0` (emergency) or `--no-verify` (single commit).

### 8. Secrets : env-vars + .env.example key parity

- All secrets via `IRIS_*` env vars (`pydantic-settings` BaseSettings).
- `.env.example` committed with placeholder values, `.env` gitignored.
- Lefthook checks `.env` and `.env.example` keep the same KEYS (values
  may differ).
- No hardcoded passwords in source — `S105/S106/S107` (bandit) catches
  regressions.

### 9. Dependencies : pinned + renovate + supply-chain scanning

- Runtime deps pinned to exact versions in `pyproject.toml`
  (`fastapi==0.115.5`, `pydantic[email]>=2.11`, etc.).
- Dev deps allow `>=` for ruff/mypy (their semver is stable).
- `renovate.json` (in shared submodule) opens MRs on dependency updates.
- `gitleaks.toml` (in shared submodule) catches accidental secret leaks.
- pip-audit / safety can be added in CI for CVE checks (TODO).

### 10. CI/CD : modular GitLab pipelines + multi-arch + SonarCloud

- `.gitlab-ci/` modular includes : lint + test + quality + build + deploy + docs.
- Conventional-commits CI job validates every commit in the MR.
- SonarCloud analysis on every MR + main (custom sonar-scanner image
  shared with Java + UI repos).
- Multi-arch Docker build (amd64 + arm64) via buildx + QEMU.
- Two-tier deployment : `deploy:staging` (auto on main) +
  `deploy:prod` (manual on tag).
- Lefthook hooks installed locally → catch issues before push.

### 11. Observability : OTel SDK + structlog + prometheus

Per ADR-0003. Three signals (traces + metrics + logs) emitted via OTel
Collector → LGTM local + GitLab Observability dual-export. Structured
JSON logs in prod (Loki-ingestible). request_id auto-bound via
contextvars in middleware.

### 12. Security : JWT rotation + bcrypt 5.x + rate-limit Redis

Per ADR-0002. Access + refresh token segregation, rotation on every
refresh (revocation detection), `jti` UUID for uniqueness. Bcrypt 5.x
direct (not passlib — semi-abandoned per ADR-0002 amendment in C4
batch 2026-04-25). Rate-limit via SlowAPI with Redis backend in prod
(per Étape 12).

### 13. Performance / regression : pytest-benchmark (TODO)

Hot paths (JWT verify, password hash, repository search) deserve
benchmark gates. `pytest-benchmark` + JUnit-comparable JSON output for
CI delta tracking. Not yet implemented — TODO Étape next.

## Consequences

**Pros** :
- Compensates Python's runtime-only typing with strict mypy + Pydantic
  + comprehensive linting + coverage gate.
- Architecture boundaries enforced by tooling (import-linter), not just
  reviewer discipline.
- Onboarding cost low : `uv sync && pytest && lefthook install` and the
  full quality stack is operational.
- Same observability + security posture as Java mirror, despite different
  language ecosystem.

**Cons** :
- Slow CI : 4 jobs × Python compat matrix + integration containers + sonar
  + multi-arch build + docs = ~15 min cycle.
- Tooling sprawl : 8+ tools (ruff, mypy, pytest, hypothesis, mkdocs,
  lefthook, glab, sonar-scanner) — each a moving part to maintain.
- Some practices (mutation testing, property-based for everything) skipped
  for portfolio-demo right-sizing.

## Validation

Live numbers after the implementation batch (2026-04-25) :

- `uv run pytest -q` → **127 tests passing, coverage 90.21 %** (was 98 / 83.55 %).
- `uv run ruff check . && ruff format --check .` → All checks passed (78 files).
- `uv run mypy src` → Success on 41 source files (strict mode).
- `uv run lint-imports --config .importlinter` → 4 contracts kept, 0 broken.
- `uv run mkdocs build --strict` → Success (autodoc, ADR index, reference).
- Lefthook hooks installed + verified : commit-msg (conventional) + pre-commit
  (ruff + ruff-format + yaml + dockerfile + ci-lint + env-keys) + pre-push
  (pytest + mypy + import-linter).
- 8 property-based tests via hypothesis (JWT round-trip, DTOs, LIFO buffer).
- pytest `--cov-fail-under=90` ratchet — future regressions break the build.

## See also

- ADR-0001 : Python stack choice
- ADR-0002 : Auth — JWT + rotation + bcrypt
- ADR-0003 : Observability stack
- ADR-0004 : Kafka request-reply pattern
- ADR-0005 : Pydantic settings hierarchy
- ADR-0006 : Structlog over stdlib
- (shared) ADR-0001 : Shared infra via submodule
- (shared) ADR-0010 : OTLP push to Collector
- (shared) ADR-0057 : Polyrepo vs monorepo
