# Architecture Decision Records (ADRs)

| # | Title | Status |
|---|---|---|
| [0001](https://gitlab.com/iris-7/iris-service-python/-/blob/main/docs/adr/0001-python-stack-choice.md) | Python stack choice | Accepted |
| [0002](https://gitlab.com/iris-7/iris-service-python/-/blob/main/docs/adr/0002-auth-jwt-with-rotation.md) | Auth — JWT access + refresh with rotation | Accepted |
| [0003](https://gitlab.com/iris-7/iris-service-python/-/blob/main/docs/adr/0003-observability-stack.md) | Observability stack — OTel SDK + Prometheus + structlog | Accepted |
| [0004](https://gitlab.com/iris-7/iris-service-python/-/blob/main/docs/adr/0004-kafka-request-reply-pattern.md) | Kafka request-reply via aiokafka + correlation-id futures | Accepted |
| [0005](https://gitlab.com/iris-7/iris-service-python/-/blob/main/docs/adr/0005-pydantic-settings-hierarchy.md) | Configuration via Pydantic Settings | Accepted |
| [0006](https://gitlab.com/iris-7/iris-service-python/-/blob/main/docs/adr/0006-structlog-over-stdlib.md) | Structured logging via structlog | Accepted |

## Cross-repo ADRs

Major design decisions that span Java + Python + UI live in the
[Java repo's docs/adr/](https://gitlab.com/iris-7/iris-service/-/tree/main/docs/adr) :

- [ADR-0010](https://gitlab.com/iris-7/iris-service/-/blob/main/docs/adr/0010-otlp-push-to-collector.md) — OTLP push to Collector (Python applies the same pattern)
- [ADR-0033](https://gitlab.com/iris-7/iris-service/-/blob/main/docs/adr/0033-ten-green-runs-shield-removal.md) — 10-green-runs shield removal (CI discipline)
- [ADR-0044](https://gitlab.com/iris-7/iris-service/-/blob/main/docs/adr/0044-hexagonal-lite-port-only-when-cross-feature-coupling.md) — Hexagonal Lite (port/adapter only when cross-feature coupling emerges)
- [ADR-0054](https://gitlab.com/iris-7/iris-service/-/blob/main/docs/adr/0054-gitlab-observability-dual-export.md) — Dual-export OTLP to GitLab Observability
