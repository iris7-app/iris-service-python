"""Cross-cutting HTTP middleware (request-id, structured logging, rate-limit, CORS).

Composed via ``register_middleware(app)`` from ``app.py`` — registration
order is the inverse of execution order in Starlette / ASGI :
- last registered runs first on the request,
- first registered runs first on the response.

So the order in ``register_middleware`` is :
1. CORS (innermost — runs last on request, first on response)
2. RequestIdMiddleware (assigns / extracts X-Request-ID)
3. PrometheusMiddleware (records HTTP metrics with the request-id in tags)
4. SlowAPI rate-limit (outermost — checks quota first, before any work)
"""
