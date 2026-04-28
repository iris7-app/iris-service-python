# ADR-0004 : Kafka request-reply pattern via aiokafka + correlation-id futures

**Status** : Accepted
**Date** : 2026-04-25
**Sibling** : `../iris-service` (Java side, Spring Kafka ReplyingKafkaTemplate)

## Context

The Java iris-service demonstrates the **request-reply pattern over Kafka**
via Spring's `ReplyingKafkaTemplate` — a synchronous HTTP request triggers a
Kafka round-trip and blocks the thread until the reply arrives, with a
configurable timeout. It's the canonical pattern for async-fronted services
that need to surface a synchronous result (e.g. enrich-on-demand).

The Python mirror needs to :
- Same wire contract (topics `customer.enrich.request` / `customer.enrich.reply`,
  W3C `traceparent` header propagation, JSON payloads).
- Same HTTP shape (`GET /customers/{id}/enrich` returns 200 / 504 / 404).
- Survive broker outage gracefully (the rest of the API keeps serving
  CRUD ; only `/enrich` returns 503).

Python ecosystem options :

| Choice | Pros | Cons |
|---|---|---|
| **aiokafka** | Native asyncio, no thread juggling | Beta-quality OTel instrumentation (0.50b0) |
| confluent-kafka-python | Most performant client | Sync-only API, requires thread pool to bridge to async |
| kafka-python | Pure Python, simple | Sync-only, slow, abandoned-ish (no 4.x release) |
| aiomonitor + librdkafka | Hybrid | Complex wiring, two libraries to debug |

## Decision

`aiokafka` 0.12.0 — the only mature async Kafka client. Pinned exactly.

### Architecture

`iris_service/messaging/` :

- **dtos.py** — wire DTOs (`CustomerEnrichRequest`, `CustomerEnrichReply`,
  `EnrichedCustomerResponse`). Pydantic v2 with `populate_by_name=True` so
  camelCase JSON ↔ snake_case Python.
- **enrichment.py** — pure broker (`EnrichmentService`). Holds a
  `dict[correlation_id, asyncio.Future]`. Owns NO IO of its own — the
  producer is injected. Easily mocked in tests.
- **kafka_client.py** — module-level singletons : producer + 2 consumer
  tasks + `EnrichmentService` instance. `start_kafka()` wires lifespan
  startup ; `stop_kafka()` flushes on shutdown.
- **customer/enrichment_router.py** — separate router from CRUD (split by
  concern, not resource — same as Java's `CustomerEnrichmentController`).

### Request-reply flow

```
HTTP GET /customers/3/enrich
  ↓
EnrichmentService.request_reply(request, timeout=5s)
  1. Generate correlation_id = uuid4()
  2. Register asyncio.Future in self._pending[correlation_id]
  3. await producer.send_and_wait("customer.enrich.request",
        value=request.model_dump_json(),
        headers=[("correlation-id", uuid), ("reply-topic", "customer.enrich.reply")])
  4. await asyncio.wait_for(future, timeout=5s)
       ↓
       (background _consume_requests task)
       ↓
       EnrichmentService.handle_request(request, correlation_id)
         compute displayName = "Name <email>"
         await producer.send_and_wait("customer.enrich.reply",
            value=reply.model_dump_json(),
            headers=[("correlation-id", uuid)])
       ↓
       (background _consume_replies task)
       ↓
       EnrichmentService.deliver_reply(correlation_id, reply)
         self._pending[correlation_id].set_result(reply)
       ↓
  5. future resolves → request_reply returns CustomerEnrichReply
  6. HTTP handler returns 200 EnrichedCustomerResponse
```

### Why two consumer tasks (not one)

Different consumer groups :
- `customer.enrich.request` consumer uses a STABLE group_id
  (`iris-enrich-handler`) so all replicas share the load (only one
  replica processes each request).
- `customer.enrich.reply` consumer uses a UNIQUE group_id per instance
  (`iris-enrich-reply-{id}`) so EVERY replica sees EVERY reply (the
  reply must reach the specific instance with the pending future ; load
  balancing would lose replies to wrong replicas).

Same trick as Java side's `KafkaConsumerFactory` with random group.

### Timeout + cleanup

`asyncio.wait_for(future, timeout=5s)` raises `TimeoutError` (alias for
`asyncio.TimeoutError` in 3.11+). The router maps it to HTTP 504. The
`finally` block in `request_reply` removes the entry from `self._pending`
even on timeout / cancel / exception → no leaks.

### Best-effort startup

Kafka can be DOWN at app boot. `app.lifespan` wraps `start_kafka` in
try/except → logs warning, continues. The DI provider
`get_enrichment_service` raises HTTPException 503 if no service is
registered → only `/enrich` is degraded ; CRUD keeps working.

### Tests without a broker

`tests/unit/messaging/test_enrichment.py` uses `unittest.mock.AsyncMock`
to fake the producer :

```python
producer = AsyncMock()
service = EnrichmentService(producer, "req", "rep")
task = asyncio.create_task(service.request_reply(req, 2.0))
await asyncio.sleep(0)
await asyncio.sleep(0)  # give the producer.send_and_wait time to register
correlation_id = ... # extract from producer.send_and_wait call args
service.deliver_reply(correlation_id, fake_reply)
result = await task  # completes
```

10 tests cover : compute_enrichment pure function, request_reply success +
timeout + cleanup, deliver_reply silent on unknown correlation_id,
handle_request produces reply with correlation echoed.

End-to-end Kafka behaviour (real broker, real serialization, OTel trace
propagation) lives in Étape 9 with testcontainers.

## Consequences

**Pros** :
- Same wire contract as Java → bidirectional interop possible (Java
  service can be the handler, Python service can be the handler, or both).
- Pure-Python broker logic (`EnrichmentService`) is trivially testable
  without Docker.
- Best-effort startup keeps the rest of the API up when Kafka is down.

**Cons** :
- aiokafka 0.12.0 is the latest stable but moves slowly compared to
  confluent-kafka-python. If we need higher throughput later, swap the
  producer with a thread-pool wrapped confluent-kafka producer (the
  EnrichmentService interface stays unchanged).
- `_pending` dict grows during a thunk-storm if many requests arrive
  faster than they reply (memory pressure). Bounded by `enrich_timeout_seconds`
  → the `finally` cleans up entries within 5s. For higher concurrency,
  add a `BoundedSemaphore(max=1000)` around `request_reply` to bound the
  in-flight count.

## Alternatives considered

- **Single fan-out consumer for both topics** — rejected : reply messages
  for instance B would arrive at instance A which has no pending future
  → silent reply loss + future timeout on the legitimate caller.
- **gRPC** — wrong abstraction : the demo POINT is showing event-driven
  request-reply over Kafka (vs sync RPC). gRPC is a different ADR.
- **HTTP from Kafka consumer** — rejected : couples consumer-side to a
  specific URL, defeats the broker-as-decoupling-layer purpose.

## Validation

10 unit tests via AsyncMock (no broker). Integration tests via
testcontainers Kafka + 2 service instances (Étape 9) verify : real
serialization works, correlation routing across replicas works, OTel
traces propagate end-to-end.
