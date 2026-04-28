# Contributing to Iris-service-python

First : thank you. Iris-service-python is the **Python sibling** of
[`iris-service-java`](https://gitlab.com/iris-7/iris-service-java) —
contributions improve the "real-world how-to-operate-this-thing" value for
everyone who reads it later.

## Where to contribute

**GitLab is the canonical source.** Contributions happen there.

- Python service : [gitlab.com/iris-7/iris-service-python](https://gitlab.com/iris-7/iris-service-python)
- Java service : [gitlab.com/iris-7/iris-service-java](https://gitlab.com/iris-7/iris-service-java)
- UI : [gitlab.com/iris-7/iris-ui](https://gitlab.com/iris-7/iris-ui)
- Shared infra : [gitlab.com/iris-7/iris-service-shared](https://gitlab.com/iris-7/iris-service-shared)

The GitHub mirrors (`github.com/iris7-app/mirador-*`) are read-only.
Issues and MRs opened there will not be reviewed.

## Types of contributions welcome

| Type | How to start |
|---|---|
| **Bug report** | Open a [GitLab issue](https://gitlab.com/iris-7/iris-service-python/-/issues/new) with the "bug" template. Include : Python version, repro steps, expected vs actual, log excerpt with `request_id`. |
| **Security vulnerability** | **Do not open a public issue.** See [`SECURITY.md`](SECURITY.md). |
| **Documentation fix / clarification** | Open an MR directly. Docs-only MRs are merged fastest. |
| **New ADR proposal** | Open an MR adding `docs/adr/NNNN-<slug>.md` (Michael Nygard format). Discussion happens in the MR. |
| **New SLO / metric** | Edit `docs/slo/slo.yaml` + run `sloth generate` + `wrap-as-prometheusrule.py` (see [SLO docs](docs/slo/)). |
| **Performance improvement** | Add a benchmark in `tests/benchmarks/` first, then ship the change with before/after measurements in the MR. |
| **Type-safety improvement** | Tighten `mypy --strict` config OR add `Final` / `Literal` / `TypeAlias` to a previously-loose path. |
| **New library / framework adoption** | Open a discussion issue first — the choice will likely become an ADR. |

## Development setup

```bash
# Clone (with the shared infra submodule)
git clone --recurse-submodules https://gitlab.com/iris-7/iris-service-python.git
cd iris-service-python

# Install dependencies (uv handles Python 3.14 install too)
uv sync --all-extras

# Install lefthook hooks (commit-msg + pre-commit + pre-push)
lefthook install --config .config/lefthook.yml

# Run dev server (hot reload)
uv run iris-service
# or with explicit uvicorn :
uv run uvicorn iris_service.app:app --reload --port 8080
```

Full demo (Postgres + Redis + Kafka + LGTM observability stack) :

```bash
# Bring up the dev stack from the shared submodule
cd infra/shared
docker compose -f compose/dev-stack.yml up -d
cd ../..

# Run the app against it
uv run iris-service
```

## Quality bar (CI gates that must pass)

| Check | Command | Failure means |
|---|---|---|
| **Format** | `uv run ruff format --check .` | Run `uv run ruff format .` to fix |
| **Lint** | `uv run ruff check .` | Fix the rules ; only add `# noqa: <RULE>` with a dated comment explaining why |
| **Types** | `uv run mypy src` | mypy strict mode — no implicit Any, no untyped defs |
| **Tests** | `uv run pytest` | 127 tests, coverage ≥ 90% (gate via `--cov-fail-under=90`) |
| **Architecture** | `uv run lint-imports --config .importlinter` | 4 contracts must hold (config-leaf, db↔kafka indep, etc) |
| **CVE scan** | `uv run pip-audit --ignore-vuln CVE-2026-3219` | Bump the affected dep, OR add a dated `--ignore-vuln` exit-ticket |
| **Conventional commit** | lefthook commit-msg hook | Use `feat(scope): subject ≤ 72 chars` format |

## Conventional Commits

Every commit message MUST match :

```
<type>(<optional-scope>)!?: <subject ≤ 72 chars>

[optional body]
[optional footer]
```

`type` ∈ `feat | fix | docs | style | refactor | perf | test | build | ci | chore | revert`.

The `commit-msg` lefthook hook enforces this.

## ADR (Architecture Decision Record) workflow

For any non-trivial trade-off (new library, new pattern, deprecation) :

1. Add `docs/adr/NNNN-<short-slug>.md` (next free number).
2. Use [Michael Nygard's template](https://github.com/joelparkerhenderson/architecture-decision-record/blob/main/locales/en/templates/decision-record-template-by-michael-nygard/index.md) :
   Title / Status / Context / Decision / Consequences / See also.
3. Reference the ADR inline in code comments + commit messages
   (`per ADR-NNNN`).
4. Mark a previous ADR `Superseded by ADR-NNNN` when relevant.

## Testing philosophy

- **Unit tests** : fast, no I/O. Use mocks / fakes / `aiosqlite` in-memory
  for DB. Coverage gate ≥ 90%.
- **Property-based** (`hypothesis`) : security-critical paths only
  (JWT round-trip, DTO bounds, LIFO buffer). See ADR-0011.
- **Integration tests** : real Postgres / Kafka / Redis via testcontainers.
  Marked `@pytest.mark.integration`, NOT in the default `pytest` run.
- **Benchmarks** : hot paths only (JWT verify, bcrypt). Marked
  `@pytest.mark.benchmarks`, NOT in the default `pytest` run.

## Code style

- `ruff format` (Black-compatible) for formatting.
- `ruff check` with comprehensive ruleset (E + W + F + I + B + C90 + N + UP + S + RUF).
- mypy `strict = true` + Pydantic v2 plugin.
- Type hints maximised : `Final` for constants, `Literal` for fixed-set strings,
  `TypeAlias` (PEP 695 `type`) for complex types, `Protocol` for duck-typed
  interfaces. See ADR-0007 §1.

## Pull request review

- One reviewer's approval needed (per CODEOWNERS).
- Auto-merge ARMED on green pipeline (`glab mr merge --auto-merge`) is preferred
  over manual merging — keeps the workflow tight.
- Delete branch on merge : `--remove-source-branch=false` ONLY for `dev`
  (which is the permanent working branch ; never delete dev).

## See also

- [README](README.md) — what the project is + quick start
- [SECURITY](SECURITY.md) — vulnerability disclosure
- [ADR index](docs/adr/) — 12 records (architectural decisions)
- [SLO definitions](docs/slo/slo.yaml) + [SLA](docs/slo/sla.md)
- [Java sibling CONTRIBUTING.md](https://gitlab.com/iris-7/iris-service-java/-/blob/main/CONTRIBUTING.md) — same patterns, different stack
