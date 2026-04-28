"""Unit tests for :mod:`iris_service.mcp.actuator` builders."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from iris_service.config.settings import Settings
from iris_service.mcp.actuator import (
    REDACTED_VALUE,
    build_env_snapshot,
    build_health_snapshot,
    build_info_block,
    build_openapi,
    is_secret_key,
    redact_value,
)
from iris_service.mcp.dtos import OpenApiSummary

# ── Redaction ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key,is_secret",
    [
        ("db.password", True),
        ("api_secret_key", True),
        ("KEYCLOAK_TOKEN", True),
        ("credential_provider", True),
        ("DB.PASSWORD", True),
        ("server_host", False),
        ("dev_mode", False),
        ("port", False),
        ("kafka.bootstrap_servers", False),
    ],
)
def test_is_secret_key_classification(key: str, is_secret: bool) -> None:
    assert is_secret_key(key) is is_secret


def test_redact_value_sentinel_for_secret() -> None:
    assert redact_value("db.password", "swordfish") == REDACTED_VALUE
    # The "0.0.0.0" literal IS the test data, not a real bind — we're
    # verifying the redaction rule preserves non-secret values verbatim.
    assert redact_value("server_host", "0.0.0.0") == "0.0.0.0"  # noqa: S104


def test_redact_value_stringifies_non_strings() -> None:
    assert redact_value("server_port", 8080) == "8080"
    assert redact_value("dev_mode", True) == "True"


# ── Env snapshot ──────────────────────────────────────────────────────────────


def test_build_env_snapshot_redacts_secrets() -> None:
    settings = Settings()
    snap = build_env_snapshot(settings)
    # JWT secret + DB password must be redacted (they live under nested keys).
    assert snap.properties["jwt.secret"] == REDACTED_VALUE
    assert snap.properties["db.password"] == REDACTED_VALUE
    # Non-secret keys are stringified verbatim.
    assert "server_host" in snap.properties
    assert snap.properties["server_host"] == settings.server_host


def test_build_env_snapshot_prefix_filter() -> None:
    settings = Settings()
    only_db = build_env_snapshot(settings, prefix="db.")
    assert all(k.startswith("db.") for k in only_db.properties)
    assert "jwt.algorithm" not in only_db.properties


# ── Info ──────────────────────────────────────────────────────────────────────


def test_build_info_block_uses_app_metadata() -> None:
    app = FastAPI(title="my-svc", version="9.9.9", description="hello")
    info = build_info_block(app)
    assert info.title == "my-svc"
    assert info.version == "9.9.9"
    assert "Runtime: CPython" in (info.description or "")
    assert "hello" in (info.description or "")


def test_build_info_block_falls_back_when_no_version() -> None:
    """An app with empty version uses the package __version__ — never empty.

    FastAPI's app constructor refuses an empty version when ``openapi_url``
    is set ; we disable OpenAPI for this test to exercise the fallback
    path inside :func:`build_info_block`.
    """
    app = FastAPI(title="x", version="", description="", openapi_url=None)
    info = build_info_block(app)
    assert info.version  # truthy ; pulled from __version__


# ── Health ────────────────────────────────────────────────────────────────────


class _StubSession:
    """Minimal stand-in for AsyncSession.execute(...) — supports happy + sad paths."""

    def __init__(self, raises: Exception | None = None) -> None:
        self._raises = raises

    async def execute(self, _stmt: object) -> None:
        if self._raises is not None:
            raise self._raises


@pytest.mark.asyncio
async def test_build_health_snapshot_up() -> None:
    snap = await build_health_snapshot(_StubSession())  # type: ignore[arg-type]
    assert snap.status == "UP"
    assert snap.components["db"].status == "UP"


@pytest.mark.asyncio
async def test_build_health_snapshot_db_down_no_details() -> None:
    snap = await build_health_snapshot(
        _StubSession(raises=OperationalError("stmt", {}, BaseException("boom"))),  # type: ignore[arg-type]
        include_details=False,
    )
    assert snap.status == "DOWN"
    assert snap.components["db"].status == "DOWN"
    # Sanitized form — no error details leak.
    assert snap.components["db"].details == {}


@pytest.mark.asyncio
async def test_build_health_snapshot_db_down_with_details() -> None:
    snap = await build_health_snapshot(
        _StubSession(raises=OperationalError("stmt", {}, BaseException("boom"))),  # type: ignore[arg-type]
        include_details=True,
    )
    assert snap.status == "DOWN"
    assert snap.components["db"].details["error"] == "OperationalError"
    # Message capped at 200 chars.
    assert len(snap.components["db"].details["message"]) <= 200


@pytest.mark.asyncio
async def test_build_health_snapshot_unknown_when_no_session() -> None:
    snap = await build_health_snapshot(None)
    assert snap.components["db"].status == "UNKNOWN"


# ── OpenAPI ───────────────────────────────────────────────────────────────────


def _seeded_app() -> FastAPI:
    app = FastAPI(title="t", version="0.1", description="")

    @app.get("/a")
    def a() -> dict[str, str]:
        return {"x": "y"}

    @app.post("/b")
    def b() -> dict[str, str]:
        return {"x": "y"}

    @app.get("/c")
    def c() -> dict[str, str]:
        return {"x": "y"}

    return app


def test_build_openapi_summary_groups_by_verb() -> None:
    app = _seeded_app()
    out = build_openapi(app, summary=True)
    assert isinstance(out, OpenApiSummary)
    assert sorted(out.paths_by_verb["get"]) == ["/a", "/c"]
    assert out.paths_by_verb["post"] == ["/b"]


def test_build_openapi_full_returns_dict() -> None:
    app = _seeded_app()
    out = build_openapi(app, summary=False)
    assert isinstance(out, dict)
    assert "paths" in out
    assert "/a" in out["paths"]
