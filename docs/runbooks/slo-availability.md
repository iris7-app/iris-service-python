# Runbook — SLO Availability burn rate

**Triggered by** : `IrisPyServiceAvailabilitySLO` page or ticket alert
(see [`docs/slo/slo.yaml`](../slo/slo.yaml)).

**SLO** : 99% of HTTP requests succeed (non-5xx) over 30d. Budget = 432 min/month.

## Symptoms

The Alertmanager fired one of :
- **Page** (critical) : 1h × 14.4× fast burn → 2% budget gone in 1h, OR
  6h × 6× sustained → 5% budget in 6h.
- **Ticket** (warning) : 1d × 3× → 10% in 1d, OR 3d × 1× → 10% in 3d.

Translation : a chunk of the monthly downtime budget just got consumed
faster than the SLO target allows. If page : likely an active incident.
If ticket : slow drift, not an emergency but needs investigation.

## First 5 minutes (page only)

1. **Open the SLO Overview dashboard** :
   https://grafana.local/d/iris-slo-overview/slo-overview-iris
   (or the live one at the deployed Grafana endpoint).
2. **Confirm the burn rate** is still elevated (not a transient spike).
3. **Open Tempo trace search** with `status_code >= 500` filter,
   last 15 min — find a representative failing trace.
4. **Open Loki** with `{job="iris-service"} |= "ERROR"` last 15 min,
   look for repeated error signatures.
5. If the failures cluster on **one endpoint** : that endpoint is the
   incident root. If they spread across all endpoints : likely an
   infrastructure issue (DB / Kafka / pod) — go to step 6.

## Common root causes (sorted by past frequency on this project)

| Symptom in logs/traces | Likely cause | First check |
|---|---|---|
| `sqlalchemy.exc.DBAPIError` / `Connection refused` to Postgres | Postgres pod restart, DB pool exhaustion | `kubectl get pods -l app=postgres` + SQLAlchemy pool metrics |
| `aiokafka.errors.KafkaTimeoutError` on `customer.enrich.request` | Kafka broker unreachable | `kubectl logs -l app=kafka` + check `kafka-broker-api-versions.sh` |
| `MemoryError or OOMKilled` / OOMKilled | Python heap too small for workload | `kubectl describe pod` → "Last State: OOMKilled" |
| `503 Service Unavailable` from probes | Liveness probe failing during cold start | check `actuator/health/readiness` |
| Thread pool exhaustion (high uvicorn worker count) | Slow downstream blocking threads | check tenacity circuit-breaker state |

## Recovery actions

- **Restart the affected pod** : `kubectl rollout restart deployment/iris-service`
  (last resort — usually fixes transient state, masks root cause).
- **Scale out** if request rate spike : `kubectl scale deployment/iris-service --replicas=3`.
- **Rollback** if the incident started after a deploy :
  `kubectl rollout undo deployment/iris-service`.
- **Open a circuit breaker** manually if a downstream is hosed (use
  tenacity actuator endpoint).

## Post-incident (within 7 days)

1. **Calculate budget impact** : `(error_rate_during_incident × duration_min)`
   minutes consumed. Update the SLA document if the budget was breached.
2. **Write a short post-mortem** in `docs/post-mortems/YYYY-MM-DD-availability.md`
   following the project's PM template.
3. **Add a regression test** if the root cause was a code bug.
4. **Tighten or relax the SLO** if the breach reveals it was unrealistic.
5. **Update this runbook** if the cause was a new failure mode.

## Escalation path

- L1 (alert receiver) : on-call engineer, follow this runbook.
- L2 (after 30 min if unresolved) : team lead.
- L3 (after 1h) : architecture team + product owner notified.

## See also

- [SLA promise](../slo/sla.md)
- [SLO definition](../slo/slo.yaml)
- [ADR-0058 SLO/SLA via Sloth](https://gitlab.com/iris-7/iris-service-shared/-/blob/main/docs/adr/0058-slo-sla-with-sloth.md)
- [Google SRE Workbook ch. 9 — Incident response](https://sre.google/workbook/incident-response/)
