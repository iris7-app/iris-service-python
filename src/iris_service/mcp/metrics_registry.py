"""Wrapper around the in-process ``prometheus_client`` REGISTRY.

Why this exists — the MCP ``get_metrics`` tool MUST stay HTTP-free
(constraint mirrored from ADR-0062 §"backend-LOCAL only"). It reads
the metric samples directly from the in-process REGISTRY instead of
hitting the ``/actuator/prometheus`` HTTP endpoint or a remote Mimir.

Two responsibilities :

1. **Translate** the ``prometheus_client`` ``MetricFamily`` /
   ``Sample`` objects into our typed :class:`MetricSnapshot` DTOs (the
   raw types are too generic for direct LLM exposure).
2. **Cache** the resulting list with a short TTL (5s default). LLMs
   frequently re-issue the same metrics query during a single
   reasoning step ; serving from cache for a few seconds keeps the
   reasoning latency low without staling the data meaningfully.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final, Literal

from cachetools import TTLCache  # type: ignore[import-untyped]  # cachetools ships no py.typed marker yet
from prometheus_client import REGISTRY, CollectorRegistry

from iris_service.mcp.dtos import MetricSnapshot

# Reuse the DTO's Literal so the classifier and the field stay in lockstep.
MetricKind = Literal["counter", "gauge", "histogram", "summary", "untyped"]

#: Cache TTL — short enough that the LLM sees fresh data within a
#: reasoning step, long enough that 3-4 quick re-queries hit the cache.
#: Mirrors the 5s Caffeine TTL on the Java sibling's ``query_metric``.
DEFAULT_CACHE_TTL_SECONDS: Final[float] = 5.0

#: Cache size — 128 distinct (name_filter, tags_filter) tuples should
#: comfortably cover any realistic LLM session.
DEFAULT_CACHE_MAXSIZE: Final[int] = 128


def _classify(metric_type: str) -> MetricKind:
    """Coerce ``prometheus_client`` metric types into our Literal set.

    The library returns short string names (``counter``, ``gauge``,
    ``histogram``, ``summary``, ``info``, ``stateset``, ``unknown``).
    We collapse the rare ones to ``untyped`` so the DTO Literal stays
    finite (we don't want a "stateset" value bubbling up to the LLM
    which would have no idea what it means).
    """
    if metric_type == "counter":
        return "counter"
    if metric_type == "gauge":
        return "gauge"
    if metric_type == "histogram":
        return "histogram"
    if metric_type == "summary":
        return "summary"
    return "untyped"


def _matches_tags(sample_labels: dict[str, str], filter_tags: dict[str, str]) -> bool:
    """All filter tags must be present with the exact label value.

    Subset semantics : a filter ``{"path": "/orders"}`` matches a sample
    with labels ``{"path": "/orders", "method": "GET", "status": "200"}``.
    Empty filter matches everything.
    """
    return all(sample_labels.get(k) == v for k, v in filter_tags.items())


class MetricsRegistryReader:
    """Translates the prometheus REGISTRY into MCP-friendly DTOs.

    Stateless except for the TTL cache. Constructed by the FastMCP
    wiring with the global REGISTRY by default ; tests inject an
    isolated CollectorRegistry to avoid leaking state across runs.
    """

    def __init__(
        self,
        registry: CollectorRegistry | None = None,
        *,
        ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        maxsize: int = DEFAULT_CACHE_MAXSIZE,
    ) -> None:
        # ``REGISTRY`` is the module-level default ; callers can pass an
        # isolated registry for tests so concurrent suites don't observe
        # each other's metrics.
        self._registry: CollectorRegistry = registry if registry is not None else REGISTRY
        # TTLCache is generically subscriptable at runtime but its stub-less
        # type makes mypy treat it as Any — explicit annotation keeps mypy
        # honest about what we put in / take out.
        self._cache: TTLCache[tuple[str, frozenset[tuple[str, str]]], list[MetricSnapshot]] = TTLCache(
            maxsize=maxsize, ttl=ttl_seconds
        )

    def list_samples(
        self,
        *,
        name_filter: str | None = None,
        tags_filter: dict[str, str] | None = None,
    ) -> list[MetricSnapshot]:
        """Return all samples matching optional name + tag filters.

        ``name_filter`` is a substring match (case-sensitive) — keeps the
        contract simple ; the LLM rarely needs regex granularity.
        Examples : ``"http_request"`` matches ``http_requests_total`` and
        ``http_request_duration_seconds``.

        ``tags_filter`` is a subset match — every key/value pair must be
        present on the sample's labels.

        Cached by the (name_filter, tags_filter) tuple for the configured
        TTL. Identical re-queries hit the cache instead of re-walking the
        REGISTRY.
        """
        cache_key = self._cache_key(name_filter, tags_filter)
        cached: list[MetricSnapshot] | None = self._cache.get(cache_key)
        if cached is not None:
            return cached
        snapshots = self._collect(name_filter, tags_filter or {})
        self._cache[cache_key] = snapshots
        return snapshots

    def clear_cache(self) -> None:
        """Drop all cached samples — exposed for tests."""
        self._cache.clear()

    def _collect(self, name_filter: str | None, tags_filter: dict[str, str]) -> list[MetricSnapshot]:
        """Walk the REGISTRY ; convert matching samples to DTOs."""
        snapshots: list[MetricSnapshot] = []
        timestamp = datetime.now(UTC)
        for family in self._registry.collect():
            if name_filter is not None and name_filter not in family.name:
                continue
            metric_kind = _classify(family.type)
            for sample in family.samples:
                if not _matches_tags(sample.labels, tags_filter):
                    continue
                snapshots.append(
                    MetricSnapshot(
                        name=sample.name,
                        tags=dict(sample.labels),
                        type=metric_kind,
                        value=float(sample.value),
                        timestamp=timestamp,
                    )
                )
        return snapshots

    @staticmethod
    def _cache_key(
        name_filter: str | None, tags_filter: dict[str, str] | None
    ) -> tuple[str, frozenset[tuple[str, str]]]:
        """Build a hashable cache key from the filter args.

        ``frozenset`` over the tag items makes the key order-insensitive —
        callers can pass tags in any order and still hit the cache.
        """
        return (
            name_filter or "",
            frozenset((tags_filter or {}).items()),
        )


# Module-level singleton — same lifecycle pattern as ring_buffer.
_reader_singleton: MetricsRegistryReader | None = None


def get_metrics_reader() -> MetricsRegistryReader:
    """Return the process-wide reader ; lazy-init."""
    global _reader_singleton
    if _reader_singleton is None:
        _reader_singleton = MetricsRegistryReader()
    return _reader_singleton


def set_metrics_reader(reader: MetricsRegistryReader | None) -> None:
    """Test hook — swap the singleton."""
    global _reader_singleton
    _reader_singleton = reader
