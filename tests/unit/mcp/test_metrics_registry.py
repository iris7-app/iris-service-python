"""Unit tests for :mod:`iris_service.mcp.metrics_registry`."""

from __future__ import annotations

import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from iris_service.mcp.metrics_registry import (
    DEFAULT_CACHE_MAXSIZE,
    DEFAULT_CACHE_TTL_SECONDS,
    MetricsRegistryReader,
    _classify,
    _matches_tags,
)


@pytest.fixture
def registry() -> CollectorRegistry:
    """Isolated registry — avoids interference across tests."""
    return CollectorRegistry()


def test_classify_canonical_kinds() -> None:
    for k in ("counter", "gauge", "histogram", "summary"):
        assert _classify(k) == k


def test_classify_collapses_unknown() -> None:
    assert _classify("info") == "untyped"
    assert _classify("stateset") == "untyped"
    assert _classify("anything-not-on-list") == "untyped"


def test_matches_tags_subset_match() -> None:
    sample = {"path": "/o", "method": "GET", "status": "200"}
    assert _matches_tags(sample, {"path": "/o"})  # subset OK
    assert _matches_tags(sample, {"method": "GET", "status": "200"})  # multi-key
    assert _matches_tags(sample, {})  # empty filter matches all
    assert not _matches_tags(sample, {"path": "/x"})  # mismatch
    assert not _matches_tags(sample, {"unknown_key": "v"})  # missing key


def test_list_samples_empty_when_registry_empty(registry: CollectorRegistry) -> None:
    reader = MetricsRegistryReader(registry)
    assert reader.list_samples() == []


def test_list_samples_returns_counter(registry: CollectorRegistry) -> None:
    c = Counter("test_total", "demo counter", registry=registry)
    c.inc()
    c.inc()
    reader = MetricsRegistryReader(registry)
    snaps = reader.list_samples()
    # prometheus_client emits a *_total + *_created sample for each Counter.
    by_name = {s.name: s for s in snaps}
    assert "test_total" in by_name
    assert by_name["test_total"].value == 2.0
    assert by_name["test_total"].type == "counter"


def test_list_samples_filters_by_name(registry: CollectorRegistry) -> None:
    Counter("alpha_total", "a", registry=registry).inc()
    Counter("beta_total", "b", registry=registry).inc()
    reader = MetricsRegistryReader(registry)
    only_alpha = reader.list_samples(name_filter="alpha")
    names = {s.name for s in only_alpha}
    assert "alpha_total" in names
    # beta_total must NOT appear.
    assert all("beta" not in n for n in names)


def test_list_samples_filters_by_tags(registry: CollectorRegistry) -> None:
    g = Gauge("requests", "demo gauge", labelnames=("path", "method"), registry=registry)
    g.labels(path="/orders", method="GET").set(10)
    g.labels(path="/orders", method="POST").set(3)
    g.labels(path="/products", method="GET").set(7)
    reader = MetricsRegistryReader(registry)
    only_orders_get = reader.list_samples(tags_filter={"path": "/orders", "method": "GET"})
    assert len(only_orders_get) == 1
    assert only_orders_get[0].value == 10


def test_caching_returns_same_list(registry: CollectorRegistry) -> None:
    Counter("cache_test_total", "c", registry=registry).inc()
    reader = MetricsRegistryReader(registry, ttl_seconds=60.0)  # long TTL
    first = reader.list_samples()
    second = reader.list_samples()
    # Same underlying list object means cache hit.
    assert first is second


def test_cache_key_order_insensitive(registry: CollectorRegistry) -> None:
    g = Gauge("k_test", "x", labelnames=("a", "b"), registry=registry)
    g.labels(a="1", b="2").set(1)
    reader = MetricsRegistryReader(registry, ttl_seconds=60.0)
    first = reader.list_samples(tags_filter={"a": "1", "b": "2"})
    second = reader.list_samples(tags_filter={"b": "2", "a": "1"})
    assert first is second


def test_cache_clear(registry: CollectorRegistry) -> None:
    Counter("clear_test_total", "x", registry=registry).inc()
    reader = MetricsRegistryReader(registry, ttl_seconds=60.0)
    first = reader.list_samples()
    reader.clear_cache()
    second = reader.list_samples()
    # Different list objects after clear (even if contents identical).
    assert first is not second


def test_cache_ttl_expires(registry: CollectorRegistry) -> None:
    Counter("ttl_test_total", "x", registry=registry).inc()
    reader = MetricsRegistryReader(registry, ttl_seconds=0.05)
    first = reader.list_samples()
    time.sleep(0.1)
    second = reader.list_samples()
    assert first is not second


def test_histogram_classified_as_histogram(registry: CollectorRegistry) -> None:
    h = Histogram("h_test", "x", registry=registry)
    h.observe(0.5)
    reader = MetricsRegistryReader(registry)
    snaps = reader.list_samples(name_filter="h_test")
    types = {s.type for s in snaps}
    assert "histogram" in types


def test_default_constants_sanity() -> None:
    """Sanity : the public defaults match the doc claims (5s, 128 entries)."""
    assert DEFAULT_CACHE_TTL_SECONDS == 5.0
    assert DEFAULT_CACHE_MAXSIZE == 128


@given(
    name_filter=st.one_of(st.none(), st.text(min_size=0, max_size=20)),
    tags_filter=st.one_of(
        st.none(),
        st.dictionaries(
            keys=st.text(min_size=1, max_size=10),
            values=st.text(min_size=0, max_size=10),
            max_size=3,
        ),
    ),
)
@settings(max_examples=30, deadline=None)
def test_list_samples_never_raises_on_arbitrary_filters(
    name_filter: str | None,
    tags_filter: dict[str, str] | None,
) -> None:
    """Property : any filter combination returns a list, never raises.

    Light Hypothesis pass — the production code shouldn't fault on
    arbitrary string inputs (the LLM might pass odd strings ; we don't
    want a crash, just an empty result).
    """
    registry = CollectorRegistry()
    Counter("prop_total", "x", registry=registry).inc()
    reader = MetricsRegistryReader(registry)
    out = reader.list_samples(name_filter=name_filter, tags_filter=tags_filter)
    assert isinstance(out, list)
