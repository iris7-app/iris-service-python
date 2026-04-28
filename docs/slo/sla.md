# Iris-service-python — SLA (Service Level Agreement)

> ⚠️ **Portfolio-demo SLA**. Mirror of the Java side's
> [`sla.md`](https://gitlab.com/iris-7/iris-service-java/-/blob/main/docs/slo/sla.md)
> with the Python service's specific metric names + endpoints. Same
> commitments, same review cadence, same architecture (Sloth + multi-
> window multi-burn-rate per ADR-0058 in the shared submodule).

## What we promise

| Indicator | Target | Window | Error budget |
|---|---|---|---|
| **Availability** (HTTP 200-499 / total) | 99.0% | 30 days | 432 min downtime/month |
| **Latency p99** (request duration < 500ms) | 99.0% | 30 days | 1% of requests can be slow |
| **Customer enrichment success** (no 504) | 99.5% | 30 days | 0.5% of /enrich calls can timeout |

## How we measure (Python-specific bits)

- **Source metrics** : FastAPI → starlette-prometheus →
  `starlette_requests_total`, `starlette_request_duration_seconds_*`
  (the metric names differ from Java's `http_server_requests_seconds_*`,
  hence the separate `slo.yaml`).
- **Recording rules** : Sloth-generated, stored in
  `iris-service-shared/deploy/kubernetes/observability-prom/mirador-py-slo.yaml`.
- **Dashboard** : same `SLO Overview — Iris` Grafana board ; both
  Java + Python burn-rate timeseries are visible side-by-side
  (filtered by `sloth_service=iris-service-python` for Python-only).

## What we DON'T cover (Python-specific)

- **uvicorn worker restarts** during normal pod lifecycle (Kubernetes
  rolling deploys) — counted as planned maintenance, not budget burn.
- **Background tasks** (refresh-token cleanup at 03:00 UTC, Kafka
  consumer loops) — these don't carry SLO commitments ; failures
  surface as logged warnings + Prometheus counters but not paged.
- **Coverage gate** (`pytest --cov-fail-under=90`) is a CI gate, not
  an SLO. Different concern : test discipline vs runtime promise.

## Review + tooling

Cadence (monthly compliance pass + quarterly target review +
post-incident root-cause loop) documented in
[`review-cadence.md`](review-cadence.md) — thin pointer to the
cross-language [shared cadence doc](https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/slo/review-cadence.md).
Hands-on demo of the burn-rate alerting in
[`chaos-demo.md`](chaos-demo.md).

## References

- [`slo.yaml`](slo.yaml) — Python SLO definitions, source-of-truth.
- [`review-cadence.md`](review-cadence.md) — review loop entry point.
- [`chaos-demo.md`](chaos-demo.md) — chaos-driven SLO demo guide.
- [ADR-0058 in shared](https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/adr/0058-slo-sla-with-sloth.md) — design decisions.
- [Java SLA](https://gitlab.com/iris-7/iris-service-java/-/blob/main/docs/slo/sla.md) — sibling service.
- ADR-0007 (industrial Python practices) — coverage/lint/security baseline that supports the SLO discipline.
