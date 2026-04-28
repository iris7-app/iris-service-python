# syntax=docker/dockerfile:1.7
# Multi-stage build : (1) build venv with uv, (2) thin runtime image.
# Mirrors iris-service Java's `build-jar` + `Dockerfile` 2-stage pattern.

# ── Stage 1 : Build (with uv) ────────────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

WORKDIR /app

# Copy dependency files first for layer caching.
# README.md is referenced by pyproject.toml (`readme = "README.md"`) so uv
# build needs it present before `--no-install-project` would even resolve.
COPY pyproject.toml uv.lock* README.md ./

# Install dependencies into .venv ; --frozen mirrors `mvn ci`-style deterministic
# builds (fails if uv.lock missing / out of date).
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Copy app source AFTER deps are installed (better layer caching)
COPY src/ ./src/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ── Stage 2 : Runtime (slim-bookworm, no uv) ───────────────────────────────
# Tried alpine 2026-04-25 (would save ~130 MB) but pydantic_core's Rust
# binary copied from the bookworm builder is glibc-only — runtime crashes
# with `ModuleNotFoundError: pydantic_core._pydantic_core`. To go alpine
# would require building deps INSIDE alpine (single-stage or alpine
# builder), which defeats the multi-stage caching benefit. Sticking with
# slim-bookworm for now ; revisit when uv ships musl wheels for all our
# Rust-extension deps (pydantic_core, cryptography, bcrypt).
FROM python:3.14-slim-bookworm AS runtime

# Non-root user (Dockle CIS-DI-0001 + matches Java mirror's spring user)
RUN groupadd --system --gid 1001 iris \
 && useradd  --system --uid 1001 --gid iris --shell /usr/sbin/nologin iris

WORKDIR /app

# Copy venv from builder + app source
COPY --from=builder --chown=iris:iris /app/.venv /app/.venv
COPY --from=builder --chown=iris:iris /app/src /app/src

# PATH the venv binaries
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Health probe baked-in for k8s liveness/readiness
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/actuator/health/liveness').read()" || exit 1

USER iris

EXPOSE 8080

# Production : gunicorn with uvicorn workers (= Spring Boot's embedded Tomcat)
# Worker count tuned to (2 * CPU + 1) but capped at 4 for the demo.
CMD ["gunicorn", \
     "--bind=0.0.0.0:8080", \
     "--workers=4", \
     "--worker-class=uvicorn.workers.UvicornWorker", \
     "--access-logfile=-", \
     "--error-logfile=-", \
     "iris_service.app:app"]
