# Runbook — SLO Latency p99 burn rate

**Triggered by** : `IrisPyServiceLatencyP99SLO` page or ticket alert.

**SLO** : 99% of HTTP requests complete in < 500ms (p99) over 30d.

## Symptoms

Same multi-window multi-burn-rate pattern as availability — page on fast
burn (1h/6h), ticket on slow burn (1d/3d). Translation : a measurable
chunk of requests are now taking > 500ms, faster than the SLO allows.

## First 5 minutes (page only)

1. **Open the SLO Overview dashboard** : the latency SLO gauge should be
   amber/red. The burn rate timeseries should show the spike start time.
2. **Open the Latency Breakdown dashboard** (when wired — see TASKS) :
   identify which endpoint(s) slowed down.
3. **Open Tempo** : trace search with `duration > 500ms` filter, last
   15 min. Find a representative slow trace.
4. **Look at the trace span breakdown** : where is the time spent ?
   - DB query span dominant → DB issue (slow query, lock contention).
   - Kafka request-reply span dominant → broker latency.
   - HTTP outbound (to Auth0, etc) span dominant → upstream slow.
   - GC pause / suspension between spans → Python heap pressure.

## Common root causes

| Trace pattern | Likely cause | Investigation |
|---|---|---|
| Single slow `db.query` span | Missing index, stale stats, lock contention | `EXPLAIN ANALYZE` the query, check `pg_stat_activity` |
| All requests slower by ~constant ms | DB pool exhausted, queue forming | SQLAlchemy pool metrics : `active`, `pending` |
| Kafka `request-reply` span > 500ms | Consumer rebalance, broker overload | `kafka-consumer-groups.sh --describe` |
| Sawtooth pattern in p99 | Major GC pauses | JVM `gc_pause_seconds` metric, consider Python GC tuning |
| Outbound HTTP > 500ms | Auth0 / external dep slow | check that dep's status page |
| Cold-start spikes after deploy | Container warming up | startupProbe failureThreshold |

## Recovery actions

- **Restart pod** if GC death-spiral : forces a clean heap.
- **Add an index** if a slow query is identified (only after testing in staging).
- **Scale out** if load-induced : `kubectl scale ... --replicas=N`.
- **Open circuit breaker** on the slow downstream if it's blocking
  request threads.
- **Switch to read replica** for read-heavy queries (if available).

## Post-incident

Same cadence as availability runbook : budget impact calculation, post-mortem,
regression test, SLO tuning, runbook update.

## See also

- [SLA promise](../slo/sla.md)
- [SLO definition](../slo/slo.yaml)
- [Google SRE Workbook ch. 5 — Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)
