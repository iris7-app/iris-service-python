# Architecture Decision Records — `iris-service-python`

This directory captures the **why** of every architectural choice that
spans more than one file. Format follows [Michael Nygard's lightweight
ADR template](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

For **cross-cutting decisions** that bind the Java + Python repos
together (e.g. observability stack choice, Sloth for SLOs, Renovate
base preset), see the shared submodule's
[`infra/shared/docs/adr/`](https://gitlab.com/iris-7/iris-service-shared/-/tree/main/docs/adr/).

## Status snapshot

- ✨ **Accepted** : current architectural shape ; obey unless an ADR
  supersedes.
- 📝 **Proposed** : draft, awaiting review or implementation.
- 🛑 **Superseded** : kept for historical context ; the link points to
  the replacement.
- 🚧 **Experimental** : in-progress trial ; may flip to Accepted or
  Superseded based on outcome.

## Hierarchical index

| Theme | ADRs |
|---|---|
| **Stack & language** | 0001 Python stack, 0007 industrial best practices, 0008 async-first, 0009 uv as package manager |
| **Auth & security** | 0002 JWT with rotation |
| **Observability** | 0003 observability stack, 0006 structlog over stdlib, 0012 SLO-as-code via Sloth |
| **Data & messaging** | 0004 Kafka request-reply, 0010 SQLAlchemy async |
| **Config** | 0005 pydantic-settings hierarchy |
| **Testing** | 0011 hypothesis property-based testing |

## Flat index

The table below is **auto-regenerated** by
[`bin/dev/regen-adr-index.sh`](https://gitlab.com/iris-7/iris-service-python/-/blob/main/bin/dev/regen-adr-index.sh).
Do not edit between the markers — run the script after adding /
modifying an ADR (the `stability-check.sh` preflight catches drift
in CI).

<!-- ADR-INDEX:START -->
| ID | Status | Title |
|---|---|---|
| 0001 | Accepted | [Python stack choice for iris-service-python](0001-python-stack-choice.md) |
| 0002 | Accepted | [Auth — JWT access + refresh token rotation, bcrypt password hashing](0002-auth-jwt-with-rotation.md) |
| 0003 | Accepted | [Observability stack — OpenTelemetry SDK + Prometheus + structlog](0003-observability-stack.md) |
| 0004 | Accepted | [Kafka request-reply pattern via aiokafka + correlation-id futures](0004-kafka-request-reply-pattern.md) |
| 0005 | Accepted | [Configuration via Pydantic Settings — env > .env > defaults](0005-pydantic-settings-hierarchy.md) |
| 0006 | Accepted | [Structured logging via structlog (over stdlib logging alone)](0006-structlog-over-stdlib.md) |
| 0007 | Accepted | [Industrial Python best practices baseline](0007-industrial-python-best-practices.md) |
| 0008 | Accepted | [Async-first architecture (no sync mixed in)](0008-async-first-architecture.md) |
| 0009 | Accepted | [`uv` as package manager (replaces pip + setuptools + pyenv)](0009-uv-as-package-manager.md) |
| 0010 | Accepted | [SQLAlchemy 2.x async over Tortoise / Beanie / SQLModel](0010-sqlalchemy-async-over-alternatives.md) |
| 0011 | Accepted | [Hypothesis property-based testing on selected paths](0011-hypothesis-property-based-testing.md) |
| 0012 | Accepted | [SLO/SLA-as-code via Sloth (Python side)](0012-slo-as-code-via-sloth.md) |
| 0013 | Accepted | [`stability-check.sh` six-section progressive design](0013-stability-check-six-section-design.md) |
| 0014 | Accepted | [Test coverage : 90 % floor + Hypothesis property tests on selected paths](0014-coverage-floor-and-property-based-testing.md) |
<!-- ADR-INDEX:END -->

## Adding a new ADR

1. Pick the next 4-digit ID (look at the existing files).
2. Copy `0000-template.md` if present, otherwise reuse the closest
   recent ADR as a structural model.
3. File name : `NNNN-<kebab-case-title>.md` (e.g. `0013-kafka-exactly-once.md`).
4. First line of the file : `# ADR-NNNN — <Title>`.
5. Include a `- Status: <Proposed|Accepted|Superseded|Experimental>`
   bullet near the top so the index regenerator picks it up.
6. Run `bin/dev/regen-adr-index.sh --in-place` to refresh this README's
   flat-index table.
7. Commit the ADR + the regenerated README in the same commit.

## See also

- [`https://gitlab.com/iris-7/iris-service-python/-/blob/main/bin/dev/regen-adr-index.sh`](https://gitlab.com/iris-7/iris-service-python/-/blob/main/bin/dev/regen-adr-index.sh) — regenerator
- [`https://gitlab.com/iris-7/iris-service-python/-/blob/main/bin/dev/stability-check.sh`](https://gitlab.com/iris-7/iris-service-python/-/blob/main/bin/dev/stability-check.sh) — preflight (catches drift)
- [shared/docs/adr/](https://gitlab.com/iris-7/iris-service-shared/-/tree/main/docs/adr/) — cross-cutting ADRs
