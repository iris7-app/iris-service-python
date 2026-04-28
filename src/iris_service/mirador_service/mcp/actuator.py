"""Wrappers over the existing FastAPI actuator-equivalent endpoints.

Why not just call the routes — the MCP tool API needs typed DTOs
(:class:`HealthSnapshot`, :class:`EnvSnapshot`, :class:`OpenApiSummary`)
not raw ``dict[str, Any]``. This module reuses the IN-PROCESS logic of
the existing :mod:`iris_service.api.actuator` (see :func:`readiness`,
:func:`info`) and shapes the result.

NO HTTP self-call (would be a CORS / auth / DI wiring nightmare for
zero gain — the data lives in this same Python process). Everything
runs in the same async context as the calling tool.
"""

from __future__ import annotations

import platform
import re
import sys
from typing import Any

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from iris_service import __version__
from iris_service.config.settings import Settings
from iris_service.mcp.dtos import (
    ComponentStatus,
    EnvSnapshot,
    HealthSnapshot,
    InfoBlock,
    OpenApiSummary,
)

#: Regex for env-key redaction — same shape as Spring Boot Actuator's
#: built-in sanitizer + the Java sibling's ``EnvironmentEndpoint`` config.
#: Case-insensitive ; matches anywhere in the key. Examples that get
#: redacted : ``DB_PASSWORD``, ``api.secret_key``, ``KEYCLOAK_TOKEN``,
#: ``credential_provider``.
SECRET_KEY_PATTERN = re.compile(r"(?i).*(password|secret|token|key|credential).*")

#: Sentinel value substituted for redacted entries. Long form on purpose —
#: a careless ``grep`` for "REDACTED" in logs surfaces them all.
REDACTED_VALUE = "***REDACTED***"


def is_secret_key(key: str) -> bool:
    """Return True if the env-prop key looks secret-bearing."""
    return SECRET_KEY_PATTERN.match(key) is not None


def redact_value(key: str, value: object) -> str:
    """Stringify + redact in one shot — safe to feed any settings value.

    Stringification is deliberate : Pydantic Settings can hold ``int``,
    ``bool``, nested ``BaseSettings`` ; the LLM payload should be flat
    strings. Redaction happens AFTER stringification so we never accidentally
    leak a secret through a custom ``__repr__``.
    """
    if is_secret_key(key):
        return REDACTED_VALUE
    return str(value)


# ── Health ────────────────────────────────────────────────────────────────────


async def build_health_snapshot(
    db: AsyncSession | None,
    *,
    include_details: bool = False,
) -> HealthSnapshot:
    """Compose a HealthSnapshot from the in-process actuator probes.

    Re-implements the same DB ping as :func:`iris_service.api.actuator
    .readiness` instead of going through HTTP ; same observable behaviour,
    fewer moving parts. Redis + Kafka checks deferred to follow-up
    (the existing actuator route hasn't wired them either).

    ``include_details=False`` strips raw error strings from the DOWN path
    — the public ``get_health`` tool stays opaque about internals while
    the admin-gated ``get_health_detail`` tool gets the verbose form.
    """
    components: dict[str, ComponentStatus] = {}
    overall_up = True

    db_status, db_details = await _probe_db(db, include_details=include_details)
    components["db"] = ComponentStatus(status=db_status, details=db_details)
    if db_status != "UP":
        overall_up = False

    return HealthSnapshot(
        status="UP" if overall_up else "DOWN",
        components=components,
    )


async def _probe_db(
    db: AsyncSession | None,
    *,
    include_details: bool,
) -> tuple[str, dict[str, str]]:
    """Run ``SELECT 1`` against the DB ; return (status, details).

    ``db is None`` is treated as UNKNOWN — caller failed to inject a
    session, but the MCP tool shouldn't hard-error just because health
    can't probe (e.g. during the startup window).
    """
    if db is None:
        return "UNKNOWN", {}
    try:
        await db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        details: dict[str, str] = {}
        if include_details:
            # Verbose form for admins — short-circuit any sensitive info
            # (DB DSNs, password leakage in the error msg) by capping length.
            details = {"error": type(exc).__name__, "message": str(exc)[:200]}
        return "DOWN", details
    return "UP", {}


# ── Info ──────────────────────────────────────────────────────────────────────


def build_info_block(app: FastAPI) -> InfoBlock:
    """Compose the actuator-info DTO from FastAPI metadata + interpreter info.

    Title + version come from the FastAPI app (matches the Java sibling's
    ``info.app.name`` / ``info.app.version`` shape). Description gets the
    Python runtime info appended so a single tool call gives the LLM the
    full "what's running" picture.
    """
    runtime = f"CPython {sys.version.split()[0]} on {platform.platform()} ({platform.python_implementation()})"
    description = app.description or ""
    full_description = f"{description}\n\nRuntime: {runtime}".strip()
    return InfoBlock(
        title=app.title,
        version=app.version or __version__,
        description=full_description,
    )


# ── Env ───────────────────────────────────────────────────────────────────────


def build_env_snapshot(settings: Settings, *, prefix: str | None = None) -> EnvSnapshot:
    """Flatten Settings into a redacted property map.

    ``prefix`` filters by key ; for nested sub-settings (db, redis, kafka,
    jwt) the flattened keys use ``.`` as separator (``db.host``,
    ``jwt.algorithm``). Matches Spring Boot Actuator's display convention.

    Redaction rule applied per-key (see :func:`is_secret_key`) — even if
    a value somehow holds sensitive content under a benign-looking key,
    the standard convention is the secret-bearing key (e.g. ``db.password``)
    is the redaction trigger.
    """
    flat: dict[str, str] = {}
    for top_key, top_value in settings.model_dump().items():
        _flatten_into(flat, top_key, top_value)
    if prefix:
        flat = {k: v for k, v in flat.items() if k.startswith(prefix)}
    return EnvSnapshot(properties=flat)


def _flatten_into(out: dict[str, str], key: str, value: object) -> None:
    """Recursive flatten helper — mutates ``out`` in place.

    Dicts contribute one entry per leaf with dotted key. Lists / tuples
    are stringified as-is (LLM rarely needs index-level access ; the
    JSON-style repr is enough for human/LLM legibility).
    """
    if isinstance(value, dict):
        for sub_key, sub_value in value.items():
            _flatten_into(out, f"{key}.{sub_key}", sub_value)
        return
    out[key] = redact_value(key, value)


# ── OpenAPI ───────────────────────────────────────────────────────────────────


def build_openapi(app: FastAPI, *, summary: bool) -> OpenApiSummary | dict[str, Any]:
    """Return either a paths-only summary or the full OpenAPI 3.x dict.

    ``summary=True`` produces an :class:`OpenApiSummary` — paths grouped
    by HTTP verb. Token-economical : a 200-route service typically fits
    in ~3 KB instead of the full 50-100 KB OpenAPI doc. The LLM uses
    this as a "directory listing" before drilling into a specific
    endpoint with a follow-up REST call.

    ``summary=False`` returns the full FastAPI-generated OpenAPI dict
    as-is — suitable for an LLM doing schema-driven code generation.
    """
    spec = app.openapi()
    if not summary:
        return spec
    info_block = InfoBlock(
        title=spec.get("info", {}).get("title", app.title),
        version=spec.get("info", {}).get("version", app.version or __version__),
        description=spec.get("info", {}).get("description"),
    )
    paths_by_verb: dict[str, list[str]] = {}
    for path, methods in (spec.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for verb in methods:
            verb_lower = verb.lower()
            if verb_lower in {"parameters", "summary", "description"}:
                # OpenAPI Path Item Object can carry non-verb keys ; skip them.
                continue
            paths_by_verb.setdefault(verb_lower, []).append(path)
    for verb_paths in paths_by_verb.values():
        verb_paths.sort()
    return OpenApiSummary(info=info_block, paths_by_verb=paths_by_verb)
