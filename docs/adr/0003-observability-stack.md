# ADR-0003 : Observability stack — OpenTelemetry SDK + Prometheus + structlog

**Status** : Accepted
**Date** : 2026-04-25
**Sibling ADR** : `../iris-service/docs/adr/<observability-related>` (Java side, Micrometer-based)

## Context

The Java iris-service uses **Micrometer + OpenTelemetry agent** for metrics
+ traces, exporting via OTLP (HTTP) to the LGTM stack
(Loki + Grafana + Tempo + Mimir, single container `grafana/otel-lgtm`).

For iris-service-python we need :
- Cross-language trace correlation (a span in Python must propagate to a sibling
  Java service via W3C `traceparent` headers, and vice-versa).
- Metrics that surface in the same Grafana dashboards as the Java service
  (HTTP request rate, latency p99, error rate, JVM-equivalent runtime metrics).
- Structured logs that Loki can index without regex magic.

Python ecosystem choices :

| Concern | Options | Decision |
|---|---|---|
| Tracing | OpenTelemetry SDK ; ddtrace ; Honeycomb beeline | **OpenTelemetry SDK** — same specs as the Java agent ; vendor-neutral OTLP exporter ; the canonical Python tracing solution. |
| Metrics | OTel SDK metrics ; prometheus-client direct ; statsd | **Both** — `prometheus-client` for the `/actuator/prometheus` scrape endpoint (same contract as the Java side) AND OTel SDK metrics for the OTLP push to Mimir. |
| Auto-instrumentation | OTel auto-instrument (zero-code) ; per-library SDK packages | **Per-library** (`opentelemetry-instrumentation-fastapi`, `-sqlalchemy`, `-redis`, `-aiokafka`) — explicit wiring is easier to debug than the auto-attach magic, and we control the order. |
| Logging | structlog ; loguru ; stdlib logging + python-json-logger | **structlog** — structured-by-design ; renders JSON for production, pretty-prints for dev ; integrates cleanly with stdlib logging so 3rd-party libs (uvicorn, sqlalchemy) flow through. |

## Decision

`src/iris_service/observability/otel.py` exposes :
- `init_otel(settings, app)` — wires tracer + meter + auto-instrumentation, called from app.lifespan startup.
- `shutdown_otel()` — flushes pending spans before process exit, called from app.lifespan shutdown.

OTLP HTTP exporter targets `http://localhost:4318` by default (LGTM container's
OTel collector port), overridable via `IRIS_OTEL_ENDPOINT`.

Resource attributes :
- `service.name` = `iris-service-python` (configurable via `IRIS_OTEL_SERVICE_NAME`)
- `service.version` = `iris_service.__version__`
- `deployment.environment` = `dev` if `IRIS_DEV_MODE=true`, else `prod`

Auto-instrumentation enabled for : FastAPI, SQLAlchemy, Redis, aiokafka. Each
adds spans + attributes following OTel semantic conventions, so dashboards
built for the Java service work unchanged.

## Best-effort startup

OTel init is wrapped in `try/except` in `app.lifespan` — if the OTLP collector
is unreachable at startup the app boots anyway with a logged warning. The
BatchSpanProcessor enqueues spans in memory and drops them silently on flush
failures. **Observability is value-add, not load-bearing** : a Tempo outage
must not take down the API.

## Trace propagation

Default propagator is W3C `traceparent` (matches Spring Boot 3+ default).
Cross-service traces work bidirectionally :
- HTTP : `traceparent` header propagated via `httpx` client + injected by
  `opentelemetry-instrumentation-fastapi` server-side.
- Kafka : `traceparent` injected into message headers by
  `opentelemetry-instrumentation-aiokafka` producer + extracted by consumer.
- DB : SQLAlchemy spans nest under the parent HTTP span automatically.

## Consequences

**Pros** :
- Single Grafana / Tempo / Loki stack handles both Java + Python services.
- Cross-language traces (HTTP from UI → Python API → Kafka → Java handler)
  show as one continuous trace in Tempo.
- Vendor-neutral : swap LGTM for Datadog / Honeycomb / New Relic by changing
  the OTLP endpoint env var.

**Cons** :
- Per-library instrumentation packages drift independently (each at version
  `0.50b0` — beta status). Pinned exactly in `pyproject.toml` ; CI catches
  upgrades via renovate.
- aiokafka instrumentation is newer + less battle-tested than the JVM agent
  equivalent ; integration tests with testcontainers (Étape 9) cover
  end-to-end trace propagation.

## Alternatives considered

- **Auto-instrument zero-code** (`opentelemetry-instrument python -m iris_service.app`)
  — rejected : harder to control init order, harder to debug, requires running
  through a wrapper which complicates Docker entrypoint.
- **Datadog APM** — vendor lock-in, ignored OTLP standard for years.
- **Manual `tracing` calls everywhere** — too noisy, breaks SRP, doesn't survive refactors.

## Validation

`tests/unit/observability/test_otel.py` smoke-tests that init runs cleanly + sets
global providers without blocking on a missing collector. End-to-end trace
verification (a HTTP request produces a span in Tempo) is integration-test
territory (Étape 9, requires testcontainers LGTM stack).
