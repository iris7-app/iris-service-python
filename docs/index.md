# Iris Service Python

Python mirror of [`iris-service-java`](https://gitlab.com/iris-7/iris-service-java)
— FastAPI + Pydantic v2 + SQLAlchemy 2.x async + Kafka + Redis + OpenTelemetry +
**Sloth-defined SLOs**.

> **What this project demonstrates mastery of**
>
> _A 30-second skim of the central themes of current backend mastery — each axis
> is verified at every `stable-py-v*` tag. Source of truth for what "this rev guarantees" :
> `git show stable-py-vX.Y.Z`._
>
> - 🤖 **AI** — FastMCP server (Anthropic `mcp[cli]≥1.27`) + streamable-http transport at `/mcp` + 14 in-process tools mirroring the Java backend + audit log per tool call + idempotency + role-based authz.
> - 🔒 **Security** — JWT HS256 (15 min, refresh-token rotation) + X-API-Key middleware + RBAC + DNS-rebinding host guard + env-var redaction + pip-audit hard gate.
> - 🧠 **Functional** — Customer onboarding & enrichment (JSONPlaceholder + Ollama LLM bio) + Order / Product / OrderLine domain (6 invariants, 8 Hypothesis property tests) + Kafka audit + diagnostic endpoints.
> - ☁️ **Infrastructure & Cloud** — Docker debian-slim 412 MB + GKE deploy + Workload Identity Federation + Postgres asyncpg + Kafka aiokafka + Redis async.
> - 📊 **Observability** — OpenTelemetry → LGTM + starlette-prometheus + 3 SLOs as code via Sloth + multi-burn-rate alerting + 4 dashboards + 3 runbooks.
> - ✅ **Quality** — `pytest --cov-fail-under=90` blocking gate (~308 tests, 94.59 % coverage) + `mypy --strict` + `ruff` + `import-linter` + Hypothesis + Testcontainers.
> - 🔄 **CI/CD** — GitLab CI 9 jobs + compat matrix Py 3.12 / 3.13 / 3.14 + Conventional Commits + auto-merge + pip-audit hard gate + Renovate weekly + GitHub mirror push.
> - 🏛 **Architecture** — Feature-slicing + per-method MCP `@tool` (ADR-0062 produces-vs-accesses) + polyrepo flat α submodules + Clean Code 7 non-negotiables.
> - 🛠 **DevX** — `uv` (Astral, 100× faster than pip) + Lefthook + `bin/dev/api-smoke.sh` + scheduled tasks for dated TODOs + Conventional Commits CI template (shared via `infra/common/`).

## TL;DR for hiring managers (60 sec read)

- **Polyrepo demonstrator** : Python implementation of the same industrial backend
  served by [`iris-service-java`](https://gitlab.com/iris-7/iris-service-java).
  Shared infra + observability + CI templates via the
  [`iris-service-shared`](https://gitlab.com/iris-7/iris-service-shared)
  git submodule.
- **mypy --strict on 41 source files** : Final / Literal / TypeAlias / PEP 695,
  no implicit `Any`, no untyped defs.
- **Coverage 90.21%** with `--cov-fail-under=90` hard gate ; 127 unit tests +
  Hypothesis property-based + 5 kafka_client integration tests via Testcontainers.
- **SLO/SLA-as-code** via Sloth : 3 SLOs (availability 99% / latency p99 < 500ms /
  enrichment 99.5%) over 30d + multi-burn-rate alerting + Grafana dashboard.
- **pip-audit hard gate** : 3 CVEs caught + fixed during dev (pytest 9.0.3,
  fastapi 0.136.1, starlette 1.0.0).

The default branch targets **Python 3.14**. The compat matrix in CI also
builds + tests green on **3.12 + 3.13** from the same source — conservative
production target = 3.12 (oldest with PEP 695 + ergonomic Final/Literal).

For the comprehensive description (tech stack table, endpoint list, MCP tool
catalogue, full senior-architect matrix, sibling-projects map, deep dives), see the
[main README](https://gitlab.com/iris-7/iris-service-python/-/blob/main/README.md).

## Quick links

- 📚 [API Reference](reference/app.md) — auto-generated from docstrings
- 🏗 [Architecture overview](architecture/overview.md)
- 📋 [ADRs](architecture/adrs.md) (12 records — stack, auth, Kafka, observability, settings, logging, industrial practices, async-first, uv, SQLAlchemy, hypothesis, Sloth SLO)
- 📊 [SLO definitions](https://gitlab.com/iris-7/iris-service-python/-/blob/main/docs/slo/slo.yaml) + [SLA promise](https://gitlab.com/iris-7/iris-service-python/-/blob/main/docs/slo/sla.md)
- 📖 [Runbooks](https://gitlab.com/iris-7/iris-service-python/-/tree/main/docs/runbooks)
- 🛠 [README (full)](https://gitlab.com/iris-7/iris-service-python/-/blob/main/README.md)
- 🔬 [Java sibling](https://gitlab.com/iris-7/iris-service-java) | [UI](https://gitlab.com/iris-7/iris-ui) | [shared infra](https://gitlab.com/iris-7/iris-service-shared)

## Quick start

```bash
# Install dependencies
uv sync --all-extras

# Run dev server (hot reload)
uv run iris-service

# Or via the bin script
bin/run.sh

# Full demo (postgres + redis + kafka + LGTM + app)
bin/demo-up.sh
```

See the [main README](https://gitlab.com/iris-7/iris-service-python/-/blob/main/README.md)
for the full setup guide, the tech stack table, the endpoint list, and the
[14-tool MCP catalogue](https://gitlab.com/iris-7/iris-service-python/-/blob/main/README.md#ai-integration-via-mcp).
</content>
</invoke>