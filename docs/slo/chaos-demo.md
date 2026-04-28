# Chaos-driven SLO demo

> Show the observability story end-to-end in 3 minutes : trigger a
> failure mode, watch the SLO budget burn in Grafana, observe the
> alert fire (in dev, Alertmanager logs the page locally).

## The 3 chaos endpoints

The Python service ships 3 deliberate-failure endpoints in
`src/iris_service/customer/diagnostic_router.py` (no auth, demo-only) :

| Endpoint | Symptom | SLO it burns |
|---|---|---|
| `GET /customers/diagnostic/slow-query?seconds=N` | Sleeps N seconds in DB query | **Latency p99** (every call > 500ms) |
| `GET /customers/diagnostic/db-failure` | Returns 500 from intentionally broken SQL | **Availability** (5xx rate) |
| `GET /customers/diagnostic/kafka-timeout` | Returns 504 immediately | **Enrichment success** (504 rate on `*enrich*`) |

Each endpoint is a single HTTP call — no Kafka cluster needed, no
manual fault injection in postgres. The 504 handler in
`kafka-timeout` is fully synthetic so the demo runs against an
in-memory test stack just as well as a full LGTM stack.

## How to run the demo

### Option 1 — local LGTM stack

```bash
# 1. Start the dev stack (Postgres + Kafka + Redis + LGTM observability)
docker compose -f infra/shared/compose/dev-stack.yml up -d

# 2. Start the FastAPI service against it
uv run uvicorn iris_service.app:app --reload --port 8000

# 3. Open Grafana → SLO Breakdown by Endpoint
open http://localhost:3001/d/mirador-py-slo-breakdown-by-endpoint

# 4. Pick your scenario from another terminal :
#    Latency burn (5s slow-query, 30 calls)
for _ in $(seq 1 30); do
  curl -s "http://localhost:8000/customers/diagnostic/slow-query?seconds=5" > /dev/null
done

#    Availability burn (5xx burst, 50 calls)
for _ in $(seq 1 50); do
  curl -s "http://localhost:8000/customers/diagnostic/db-failure" > /dev/null
done

#    Enrichment burn (504 burst, 20 calls)
for _ in $(seq 1 20); do
  curl -s "http://localhost:8000/customers/diagnostic/kafka-timeout" > /dev/null
done
```

### Option 2 — single curl + dashboard reload

If you only want to verify the wiring (annotation + metric path),
1 call is enough — Grafana's 30s refresh + the chaos annotation
will surface the marker.

## What you'll see

1. **Vertical line annotation** appears in the breakdown dashboard
   panels (orange / red / grey depending on scenario) — same time
   the chaos endpoint was hit.
2. **The corresponding panel spikes** :
   - slow-query → "p99 latency" panel jumps to ~5s for the
     `/customers/diagnostic/slow-query` row.
   - db-failure → "5xx error ratio" panel goes red on the
     `/customers/diagnostic/db-failure` row.
   - kafka-timeout → "Budget burn contribution" panel shows the
     `*kafka-timeout*` row spike.
3. **Open the SLO Overview dashboard** (uid : `mirador-slo-overview`) —
   the corresponding burn-rate timeseries shows the spike inside the
   1h fast-burn window.
4. **Within ~1 minute** (Sloth's `mwmbr` short window) the
   `IrisPyServiceLatencyP99SLO` / `Availability` / `Enrichment`
   alert state flips to firing in Alertmanager.

## What the demo proves (for a senior architect interview)

- **End-to-end observability path** : metric → SLI → SLO → burn rate
  → alert → runbook. Not just "we have Grafana" but "we have an
  acted-upon error budget".
- **Symmetry across stacks** : same 3 chaos endpoints exist on Java
  (`iris-service-java`) — proves the SLO contract is portable, not
  language-specific.
- **Multi-window multi-burn-rate (MWMBR)** : the chaos burst triggers
  the 1h `× 14.4 fast-burn` window first, then if sustained, the 6h
  `× 6 sustained` window. Demonstrates Google SRE Workbook ch. 5
  alerting hygiene over naive threshold alerts.

## Cleanup

The synthetic chaos doesn't leave state — no rows in the DB, no
messages in Kafka. Stop the burst, wait 30 days for the SLO window
to roll the burn out (or restart Prometheus to clear the recording
rules state in dev).

## See also

- [SLO Overview dashboard](https://gitlab.com/iris-7/iris-service-shared/-/blob/main/infra/observability/grafana/dashboards-lgtm/slo-overview.json) — the overview gauge + burn-rate timeseries.
- [SLO Breakdown by Endpoint](https://gitlab.com/iris-7/iris-service-python/-/blob/main/infra/observability/grafana-dashboards/slo-breakdown-by-endpoint.json) — annotated dashboard for this demo.
- [Latency Heatmap](https://gitlab.com/iris-7/iris-service-python/-/blob/main/infra/observability/grafana-dashboards/latency-heatmap.json) — see the slow-query effect on tail-latency.
- [Apdex](https://gitlab.com/iris-7/iris-service-python/-/blob/main/infra/observability/grafana-dashboards/apdex.json) — single-number user-satisfaction score.
- [SLO Review Cadence](review-cadence.md) — when + how to revisit SLO targets.
- [diagnostic_router.py](https://gitlab.com/iris-7/iris-service-python/-/blob/main/src/iris_service/customer/diagnostic_router.py) — the 3 endpoint implementations.
- [Google SRE Workbook ch. 5 — Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/) — multi-window burn-rate rationale.
