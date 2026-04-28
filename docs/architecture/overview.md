# Architecture overview

## Component map

```
                                    ┌──────────────────┐
   POST /customers ────────────────►│ FastAPI app      │
                                    │  (uvicorn /      │
                                    │   gunicorn)      │
                                    └────────┬─────────┘
                                             │
              ┌──────────────┬───────────────┼───────────────┬────────────────┐
              ▼              ▼               ▼               ▼                ▼
        ┌──────────┐  ┌──────────┐  ┌────────────────┐  ┌─────────┐  ┌──────────────┐
        │ Postgres │  │ Redis    │  │ Kafka          │  │ Ollama  │  │ JSONPlaceholder
        │ (asyncpg)│  │ (redis-  │  │ (aiokafka)     │  │ (httpx) │  │ (httpx +     │
        │ + Alembic│  │  py)     │  │ - request-reply│  │ - LLM   │  │  tenacity    │
        │          │  │ - recent │  │ - fire-and-    │  │   bio   │  │  retry)      │
        │          │  │   buffer │  │   forget       │  │         │  │              │
        └──────────┘  └──────────┘  └────────────────┘  └─────────┘  └──────────────┘
                                             │
                                             ▼
                                       ┌──────────────────┐
                                       │ OTel Collector   │
                                       └────────┬─────────┘
                                                │
                              ┌─────────────────┼─────────────────┐
                              ▼                 ▼                 ▼
                       ┌──────────┐      ┌──────────┐      ┌──────────────┐
                       │ Tempo    │      │ Loki     │      │ Mimir +      │
                       │ (traces) │      │ (logs)   │      │ Pyroscope    │
                       └──────────┘      └──────────┘      └──────────────┘
```

Bonus dual-export to GitLab Observability (https://130289716.otel.gitlab-o11y.com:14318)
— see [ADR-0003](https://gitlab.com/iris-7/iris-service-python/-/blob/main/docs/adr/0003-observability-stack.md).

## Module map

```
src/iris_service/
├── app.py                    # FastAPI factory + lifespan (startup/shutdown)
├── api/
│   ├── actuator.py           # health/liveness/readiness/info/prometheus
│   └── quality.py            # /actuator/quality
├── auth/
│   ├── cleanup.py            # APScheduler refresh-token cron
│   ├── deps.py               # current_user FastAPI dependency
│   ├── dtos.py               # LoginRequest / TokenResponse / RefreshRequest
│   ├── jwt.py                # access + refresh token issuance
│   ├── models.py             # AppUser + RefreshToken ORM
│   ├── passwords.py          # bcrypt wrapper
│   └── router.py             # /auth/login + /auth/refresh + /me
├── customer/
│   ├── audit_router.py       # /customers/{id}/audit
│   ├── diagnostic_router.py  # /customers/diagnostic/*
│   ├── dtos.py               # Pydantic v1 + v2 DTOs
│   ├── enrichment_router.py  # /customers/{id}/{enrich,todos}
│   ├── models.py             # Customer ORM
│   ├── recent_buffer.py      # Redis LPUSH+LTRIM ring buffer
│   ├── repository.py         # async data-access layer
│   └── router.py             # CRUD endpoints
├── config/
│   └── settings.py           # Pydantic BaseSettings (env > .env > defaults)
├── db/
│   └── base.py               # SQLAlchemy async engine + session factory
├── integration/
│   ├── redis_client.py       # Redis singleton
│   └── todo_service.py       # JSONPlaceholder + tenacity retry
├── messaging/
│   ├── customer_event.py     # Kafka FAF — CustomerCreatedEvent
│   ├── dtos.py               # CustomerEnrichRequest/Reply DTOs
│   ├── enrichment.py         # request-reply broker (correlation futures)
│   └── kafka_client.py       # aiokafka producer/consumer lifecycle
├── middleware/
│   ├── logging.py            # structlog config
│   ├── request_id.py         # X-Request-ID extraction + binding
│   └── setup.py              # CORS + Prometheus + SlowAPI rate-limit
└── observability/
    └── otel.py               # OTel SDK + auto-instrumentation init
```

## Lifespan startup order

1. `configure_logging(dev_mode)` — structlog wired BEFORE any other setup
2. `init_otel(settings, app)` — best-effort, traces lost if collector down
3. DB engine (lazy via `get_engine()` on first session)
4. Redis client (lazy via `get_redis()`)
5. `start_kafka(settings.kafka)` — best-effort, /enrich returns 503 if down
6. `start_scheduler()` — APScheduler refresh-token cron
7. yield → serve requests
8. Reverse on shutdown
