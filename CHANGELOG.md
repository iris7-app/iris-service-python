# Changelog

All notable changes to **iris-service-python** — Python sibling of
[`iris-service-java`](https://gitlab.com/iris-7/iris-service-java),
FastAPI + Pydantic v2 + SQLAlchemy 2.x async.

Format : a lightly-formatted summary per `stable-py-vX.Y.Z` tag.
For commit-level granularity between tags :

```bash
git log --oneline stable-py-v0.6.1..stable-py-v0.6.2
```

## [stable-py-v0.6.2] — 2026-04-26

**Documentation + observability polish wave.**

### Highlights

- **6 SLO runbooks** : 3 in this repo (`docs/runbooks/slo-{availability,
  latency,enrichment}.md`) — unblocks Alertmanager 404s on alert receivers.
- **README.fr.md** fully synced with EN (TL;DR for hiring managers + SLO
  badges + Sloth in tech stack + industrial onboarding framing).
- **mkdocs `docs/index.md`** refreshed : Sloth + 12 ADRs + cross-repo
  navigation + senior-architect outcomes.
- **"What this proves for senior backend architect" matrix** added to
  README (8 rows : type discipline, async-first, test rigor, arch
  boundaries, security, observability, tooling, Java parity).
- **Renovate consolidated** via shared base preset + `bin/ship/renovate-sync.sh`
  (4 repos aligned on common config + repo-specific groups preserved).
- **Shared submodule** bumped (3 new Grafana dashboards : SLO breakdown
  by endpoint, latency heatmap, Apdex + SLO review cadence doc + renovate
  base file).
- **CHANGELOG + CONTRIBUTING + SECURITY + CODEOWNERS** added (this
  release).

## [stable-py-v0.6.1] — 2026-04-25

**Industrial Python wave + Sloth SLO + 5 new ADRs.**

### Highlights

- **Blue homogeneous icon** (radar arcs were yellow → match Python blue).
- **README rewrite** : hiring TL;DR + SLO badge + Sloth in tech stack +
  industrial Customer onboarding framing.
- **3 SLOs as code** via Sloth :
  - Availability 99% / 30d (432 min budget)
  - Latency p99 < 500ms / 99% / 30d
  - Customer enrichment success 99.5% / 30d
- **Generated PrometheusRule** + `wrap-as-prometheusrule.py` script
  (Sloth K8s wrapper bridge).
- **5 new ADRs** : 0008 async-first architecture, 0009 uv as package
  manager, 0010 SQLAlchemy 2.x async, 0011 hypothesis property-based
  testing, 0012 SLO-as-code via Sloth.
- **BSD-3-Clause LICENSE**.
- **TASKS.md** SLO + README polish backlog refreshed.

## [stable-py-v0.6.0] — 2026-04-25

**Industrial Python practices baseline (ADR-0007).**

### Highlights

- **ADR-0007 — Industrial Python best practices** : 13-decision baseline
  document covering type system (mypy strict + Pydantic v2), linting
  (ruff comprehensive ruleset), tests (pytest + property-based hypothesis),
  architecture (import-linter), CI/CD discipline.
- **Type system maximised** : `mypy --strict` on 41 source files ;
  `Final[T]` for module constants ; `Literal["access","refresh"]` for
  token type discriminator ; `TypeAlias` (PEP 695 `type` keyword) for
  complex types.
- **Coverage 83.55% → 90.21%** (127 unit tests, was 98).
  `--cov-fail-under=90` blocking gate.
- **8 hypothesis property-based tests** (JWT round-trip, DTO bounds,
  LIFO buffer invariant) — found 2 real bugs in test code during
  authoring.
- **import-linter** : 4 architectural contracts (config-leaf,
  db↔kafka indep, integration adapters indep, observability-leaf).
- **pytest-benchmark** : 6 hot-path microbenchmarks (JWT 9µs, bcrypt 280ms).
- **Pydantic models** for `Todo` + `OllamaResponse` (was `dict[str, Any]`
  aliases).
- **pip-audit CVE gate** : 3 CVEs caught + fixed during dev (pytest 9.0.3,
  fastapi 0.136.1, starlette 1.0.0).
- **mutmut** installed + configured (CI blocked on upstream bug).
- **kafka_client integration tests** (5 new) via testcontainers.
- **Renovate** : Python flavor with FastAPI/Pydantic/SQLAlchemy/OTel groups.

## [stable-v0.5.0] — earlier

Initial structured releases. See `git log stable-v0.1.0..stable-v0.5.0`
for details.

## [stable-v0.1.0] — 2026-04-15

Initial Python mirror of the Java backend. FastAPI + Pydantic v2 + SQLAlchemy
2.x async + Kafka request-reply + Redis ring buffer + OpenTelemetry +
JWT auth + structured logging.

[stable-py-v0.6.2]: https://gitlab.com/iris-7/iris-service-python/-/tags/stable-py-v0.6.2
[stable-py-v0.6.1]: https://gitlab.com/iris-7/iris-service-python/-/tags/stable-py-v0.6.1
[stable-py-v0.6.0]: https://gitlab.com/iris-7/iris-service-python/-/tags/stable-py-v0.6.0
[stable-v0.5.0]: https://gitlab.com/iris-7/iris-service-python/-/tags/stable-v0.5.0
[stable-v0.1.0]: https://gitlab.com/iris-7/iris-service-python/-/tags/stable-v0.1.0
