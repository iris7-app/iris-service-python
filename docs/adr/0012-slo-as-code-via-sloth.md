# ADR-0012 : SLO/SLA-as-code via Sloth (Python side)

**Status** : Accepted
**Date** : 2026-04-25
**Cross-cutting** : See [`iris-service-shared/docs/adr/0058-slo-sla-with-sloth.md`](https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/adr/0058-slo-sla-with-sloth.md)
for the cross-stack design rationale (Sloth vs Pyrra vs OpenSLO, multi-window
multi-burn-rate alerting pattern, dashboard structure). This ADR captures
the Python-specific implementation.
**Sibling** : `iris-service-java/docs/slo/slo.yaml` (Java SLO definitions
with Micrometer metric names).

## Context

Per ADR-0007 §"selected paths" + ADR-0058 (shared), Iris adopted Sloth
for declarative SLO definitions. Both Java + Python services need their own
`slo.yaml` because :
- Metric names differ : Java uses Micrometer's
  `http_server_requests_seconds_*` ; Python uses starlette-prometheus's
  `starlette_requests_total` + `starlette_request_duration_seconds_*`.
- Endpoints differ : `/customers/{id}/enrich` exists on both but the route
  template format differs (`uri` label on Java, `path_template` on Python).

## Decision

Three SLOs defined in `docs/slo/slo.yaml` for the Python service, mirroring
Java :

| SLO | Target | Window | Source metric |
|---|---|---|---|
| Availability (non-5xx) | 99.0% | 30 days | `starlette_requests_total{status_code=~"5.."}` |
| Latency p99 | 99.0% < 500ms | 30 days | `starlette_request_duration_seconds_bucket{le="0.5"}` |
| Enrichment success (no 504) | 99.5% | 30 days | `starlette_requests_total{path_template=~".*enrich.*",status_code="504"}` |

**Generation pipeline** (matches Java side, parallel scripts) :

```
docs/slo/slo.yaml
        │  sloth generate -i slo.yaml -o /tmp/mirador-py-slo-rules.yaml
        ▼
/tmp/mirador-py-slo-rules.yaml (Sloth raw output)
        │  python3 docs/slo/wrap-as-prometheusrule.py
        ▼
iris-service-shared/deploy/kubernetes/observability-prom/mirador-py-slo.yaml
        │  (PrometheusRule CRD with `release: prometheus-stack` label)
        ▼
        kube-prometheus-stack operator picks up + Prometheus loads rules
```

Both Java + Python PrometheusRules land in the SAME shared submodule
directory. Operator's `ruleSelector` picks BOTH up — same Prometheus
instance evaluates both services' SLOs.

## Consequences

**Pros** :
- **Java/Python parity** : same 3 SLOs, same targets, same Grafana dashboard
  (filtered by `sloth_service` label). Demonstrates that SLOs are
  service-agnostic — the discipline transfers across stacks.
- **starlette-prometheus exporter** is automatic — labelled metrics emerge
  from the FastAPI middleware without per-route instrumentation.
- **45 recording rules + 6 alerts generated per service** from ~100 lines
  of YAML. Manual would be 300+ lines per service of error-prone PromQL.
- **Multi-window multi-burn-rate alerting** (Google SRE Workbook ch. 5)
  comes for free with Sloth — page on 1h × 14.4× fast burn or 6h × 6× slow
  burn ; ticket on 1d × 3× or 3d × 1× drift.

**Cons** :
- **Two SLO YAMLs to maintain** : Java + Python diverge if an endpoint is
  added on one side. Mitigation : a future `bin/ship/slo-parity-check.sh`
  could diff the two — backlog item.
- **starlette-prometheus is a separate library** (not part of FastAPI). Its
  metric names are stable but not Micrometer-compatible — can't use the
  same Java SLO YAML.
- **`path_template` cardinality** : starlette-prometheus by default uses
  the route TEMPLATE (`/customers/{id}/enrich`) not the full path
  (`/customers/42/enrich`). Good for cardinality, but means the SLO can't
  be sliced by customer-id. That's by design.

**Alternatives considered** :

| Tool | Why not |
|---|---|
| **prometheus_client** + manual rules | Hand-written, 5× more code, no multi-burn-rate template |
| **OpenSLO + Pyrra** | Operator-based runtime evaluation ; we don't run Pyrra in cluster, Sloth's CLI generation suffices |
| **Per-endpoint SLOs** | Cardinality explosion, premature optimisation for a 3-endpoint demo |

## Validation

- `sloth generate -i docs/slo/slo.yaml -o /tmp/mirador-py-slo-rules.yaml`
  → 9 groups, 45 recording rules, 6 alerts.
- `python3 docs/slo/wrap-as-prometheusrule.py` produces
  `mirador-py-slo.yaml` with the K8s CRD wrapper.
- `kubectl apply -f mirador-py-slo.yaml --dry-run=server` validates against
  kube-prometheus-stack's PrometheusRule schema.
- Grafana dashboard `slo-overview` displays both Java + Python SLOs side-
  by-side, filtered by `sloth_service`.

## See also

- ADR-0007 : Industrial Python practices §13 (perf benchmarks complement
  SLO observability)
- ADR-0058 (shared) : SLO/SLA with Sloth — cross-cutting design
- [Sloth specs/v1](https://sloth.dev/specs/v1/)
- [Google SRE Workbook ch. 5 — Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)
- [starlette-prometheus](https://github.com/perdy/starlette-prometheus)
