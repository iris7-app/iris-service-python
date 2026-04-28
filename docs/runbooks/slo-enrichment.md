# Runbook — SLO Enrichment success burn rate

**Triggered by** : `IrisPyEnrichmentSuccessSLO` page or ticket alert.

**SLO** : 99.5% of `/customers/{id}/enrich` calls succeed (no 504 timeout)
over 30d. Tightest SLO in the project — flagship business flow.

## What this measures

The Customer enrichment endpoint orchestrates a synchronous request-reply
over Kafka :
1. HTTP request arrives at `/customers/{id}/enrich`.
2. Controller publishes `CustomerEnrichRequest` to topic `customer.enrich.request`.
3. Consumer (in the same service) processes the request, publishes
   `CustomerEnrichReply` to topic `customer.enrich.reply` with the same
   correlation-id.
4. The original HTTP handler awaits the reply with a `enrich_timeout_seconds`
   timeout (default 5s).
5. If reply arrives in time → 200 with the enriched payload.
   If not → **504 Gateway Timeout** (= what this SLO counts as failure).

A 504 means : Kafka broker unreachable, consumer crashed, message lost,
or processing took > 5s.

## First 5 minutes

1. **Confirm the burn rate** on the SLO Overview dashboard.
2. **Check Kafka broker health** :
   `kubectl logs -l app=kafka --tail=50` — look for partition leadership
   issues or `Connection refused` from clients.
3. **Check the consumer group** :
   ```
   kubectl exec -it kafka-0 -- kafka-consumer-groups.sh \
     --bootstrap-server localhost:9092 \
     --group iris-enrich-handler --describe
   ```
   Look at `LAG` column — if growing, consumer can't keep up.
4. **Check the in-flight pending replies** in the EnrichmentService
   actuator endpoint (or in logs filtered by "pending_correlation_ids").
5. **Open Tempo** : trace search for `/enrich` with `error=true` —
   confirm where the 504 originates.

## Common root causes

| Symptom | Likely cause | Fix |
|---|---|---|
| Consumer LAG growing fast | Consumer is processing too slowly | Scale up : add more replicas, partition the topic |
| Consumer LAG = 0 but still 504 | Reply consumer not delivering to pending future | Restart pod (clears in-memory pending map) |
| Kafka logs `OFFSET_OUT_OF_RANGE` | Topic retention shorter than reset interval | Bump `retention.ms` on topic |
| `aiokafka.errors.KafkaTimeoutError` on producer side | Broker unreachable | Check broker pod, network policy |
| Specific customer-id always times out | Bad data triggers slow processing path | Find the offending message via Kafka tools, fix consumer logic |
| All 504 immediately after deploy | Consumer registration race | Verify the new pod's consumer is bound to the group |

## Recovery actions

- **Restart consumer pods** to force rebalance + clear in-memory state.
- **Reset consumer offset** to latest if reprocessing isn't critical
  (loses in-flight requests but unblocks the queue) :
  ```
  kafka-consumer-groups.sh --reset-offsets --group iris-enrich-handler \
    --topic customer.enrich.request --to-latest --execute
  ```
- **Scale Kafka brokers** if broker CPU is the bottleneck.
- **Tighten the timeout** temporarily (lower `enrich_timeout_seconds` to fail
  fast and shed load) — opposite of intuition but reduces resource hold.
- **Disable the endpoint** via feature flag (if Unleash wired) : returns
  503 immediately instead of 504, frees request threads.

## Post-incident

Same cadence as availability runbook PLUS :
- **Did messages get lost ?** Replay from Kafka if so.
- **Did the in-memory pending-correlation-ids map leak ?** Check the
  `EnrichmentService` cleanup logic.
- **Should we add a Redis-backed pending-replies store** to survive
  pod restarts ? Track as ADR if yes.

## See also

- [SLA promise](../slo/sla.md)
- [SLO definition](../slo/slo.yaml)
- [ADR-0004 Kafka request-reply pattern](../adr/0004-kafka-request-reply-pattern.md)
- [Apache Kafka — consumer-groups admin tool](https://kafka.apache.org/documentation/#operations_consumergroups)
