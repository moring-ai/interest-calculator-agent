"""Tests for the rate feed, focused on provenance and degradation.

These run `asyncio.run` inside synchronous tests rather than depending on
pytest-asyncio, and never touch the network: the live provider is always a
stub, so a CI box with no FRED key still exercises every path.
"""

import asyncio
import time
from datetime import date

import pytest

from rate_feed import (
    Freshness, FredProvider, MockProvider, RateFeedError, RateObservation,
    RateQuote, RateSeries, RateService, TTLCache, catalog,
)


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# Stub providers
# --------------------------------------------------------------------------

class StubLive:
    """Stands in for FRED. Configurable to fail on demand."""

    name = "STUB"
    configured = True

    def __init__(self, value=0.0658, fail=False):
        self.value = value
        self.fail = fail
        self.calls = 0

    def supports(self, key):
        return True

    async def fetch_latest(self, key):
        self.calls += 1
        if self.fail:
            raise RateFeedError("upstream down")
        d = catalog.get(key)
        return RateQuote(
            key=key, label=d.label, value=self.value, as_of=date(2026, 8, 20),
            source=self.name, series_id=d.series_id, freshness=Freshness.LIVE,
        )

    async def fetch_series(self, key, start=None, end=None):
        self.calls += 1
        if self.fail:
            raise RateFeedError("upstream down")
        d = catalog.get(key)
        return RateSeries(
            key=key, label=d.label, source=self.name, series_id=d.series_id,
            observations=[RateObservation(as_of=date(2026, 8, 20), value=self.value)],
            freshness=Freshness.LIVE,
        )


def service(**kw):
    kw.setdefault("primary", StubLive())
    kw.setdefault("fallback", MockProvider())
    return RateService(**kw)


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------

def test_every_featured_key_exists_in_the_catalog():
    for key in catalog.FEATURED_KEYS:
        assert key in catalog.CATALOG


def test_every_definition_has_a_known_category():
    for d in catalog.CATALOG.values():
        assert d.category in catalog.CATEGORIES


def test_unknown_key_is_rejected_with_a_helpful_message():
    with pytest.raises(KeyError, match="unknown rate"):
        catalog.get("mortgage_42y")


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def test_fresh_entry_is_returned_then_expires_but_is_retained():
    c = TTLCache()
    c.set("k", "v", ttl=0.05)
    assert c.get_fresh("k") == "v"
    time.sleep(0.06)
    assert c.get_fresh("k") is None      # expired
    assert c.get_stale("k") == "v"       # but still retained for fallback


def test_cache_evicts_the_oldest_entry_when_full():
    c = TTLCache(max_entries=2)
    c.set("a", 1, 60); c.set("b", 2, 60); c.set("c", 3, 60)
    assert c.get_stale("a") is None
    assert c.get_stale("c") == 3


# --------------------------------------------------------------------------
# Service resolution order
# --------------------------------------------------------------------------

def test_live_provider_is_used_and_marked_live():
    svc = service()
    q = run(svc.get_rate("mortgage_30y"))
    assert q.freshness is Freshness.LIVE
    assert q.value == 0.0658
    assert q.percent == 6.58


def test_second_call_is_served_from_cache_without_hitting_upstream():
    live = StubLive()
    svc = service(primary=live)
    run(svc.get_rate("mortgage_30y"))
    q = run(svc.get_rate("mortgage_30y"))
    assert live.calls == 1
    assert q.freshness is Freshness.CACHED


def test_stale_cache_is_preferred_over_synthetic_when_upstream_fails():
    live = StubLive()
    svc = service(primary=live)
    run(svc.get_rate("mortgage_30y"))          # seed the cache
    svc._quotes.set("mortgage_30y", svc._quotes.get_stale("mortgage_30y"), ttl=-1)
    live.fail = True

    q = run(svc.get_rate("mortgage_30y"))
    assert q.freshness is Freshness.STALE
    assert q.value == 0.0658                    # the last real number, not synthetic
    assert q.source == "STUB"


def test_synthetic_is_the_last_resort_and_is_labelled():
    svc = service(primary=StubLive(fail=True))
    q = run(svc.get_rate("mortgage_30y"))
    assert q.freshness is Freshness.SYNTHETIC
    assert q.source == "SYNTHETIC"
    assert "SYNTHETIC" in q.series_id


def test_synthetic_can_be_disabled_so_failures_are_loud():
    svc = service(primary=StubLive(fail=True), allow_synthetic=False)
    with pytest.raises(RateFeedError, match="no live, cached, or synthetic"):
        run(svc.get_rate("mortgage_30y"))


def test_missing_api_key_skips_the_live_provider_entirely():
    """With no FRED key the service must degrade, not raise."""
    svc = RateService(primary=FredProvider(api_key=""), fallback=MockProvider())
    assert svc.live_source_configured is False
    q = run(svc.get_rate("mortgage_30y"))
    assert q.freshness is Freshness.SYNTHETIC


def test_board_returns_every_featured_rate():
    svc = service()
    board = run(svc.get_board())
    assert [q.key for q in board] == catalog.FEATURED_KEYS


def test_board_drops_rates_that_fail_rather_than_failing_whole_board():
    class Flaky(StubLive):
        async def fetch_latest(self, key):
            if key == "mortgage_15y":
                raise RateFeedError("just this one")
            return await super().fetch_latest(key)

    svc = RateService(primary=Flaky(), fallback=MockProvider(), allow_synthetic=False)
    board = run(svc.get_board())
    keys = [q.key for q in board]
    assert "mortgage_15y" not in keys
    assert "mortgage_30y" in keys


def test_empty_live_series_falls_back_instead_of_returning_nothing():
    class Empty(StubLive):
        async def fetch_series(self, key, start=None, end=None):
            d = catalog.get(key)
            return RateSeries(key=key, label=d.label, source=self.name,
                              series_id=d.series_id, observations=[])

    svc = RateService(primary=Empty(), fallback=MockProvider())
    s = run(svc.get_series("treasury_10y"))
    assert s.observations
    assert s.freshness is Freshness.SYNTHETIC


# --------------------------------------------------------------------------
# Serialization -- what the frontend actually consumes
# --------------------------------------------------------------------------

def test_quote_serializes_with_full_provenance():
    q = run(service().get_rate("mortgage_30y"))
    d = q.to_dict()
    for field in ("key", "label", "value", "percent", "as_of", "source",
                  "series_id", "freshness", "fetched_at"):
        assert field in d, f"{field} missing from serialized quote"
    assert d["as_of"] == "2026-08-20"


def test_percent_conversion_is_a_display_concern_only():
    q = run(service(primary=StubLive(value=0.0421)).get_rate("treasury_10y"))
    assert q.value == 0.0421      # decimal fraction internally
    assert q.percent == 4.21      # percent for display


# --------------------------------------------------------------------------
# Synthetic provider
# --------------------------------------------------------------------------

def test_synthetic_series_is_stable_across_calls():
    m = MockProvider()
    a = run(m.fetch_series("mortgage_30y", date(2025, 1, 1), date(2025, 6, 1)))
    b = run(m.fetch_series("mortgage_30y", date(2025, 1, 1), date(2025, 6, 1)))
    assert [o.value for o in a.observations] == [o.value for o in b.observations]


def test_synthetic_rates_stay_in_a_plausible_range():
    m = MockProvider()
    s = run(m.fetch_series("mortgage_30y", date(2024, 1, 1), date(2026, 1, 1)))
    values = [o.value for o in s.observations]
    assert all(0.01 < v < 0.15 for v in values), "synthetic mortgage rate left a sane band"


# --------------------------------------------------------------------------
# FRED parsing
# --------------------------------------------------------------------------

def test_fred_drops_missing_and_malformed_observations():
    rows = FredProvider._parse_rows([
        {"date": "2026-08-14", "value": "6.58"},
        {"date": "2026-08-13", "value": "."},      # holiday placeholder
        {"date": "2026-08-12", "value": ""},
        {"date": "2026-08-11", "value": "bad"},
        {"date": "2026-08-10", "value": "6.61"},
    ])
    assert [o.value for o in rows] == [0.0658, 0.0661]


def test_fred_requires_a_key_before_making_requests():
    with pytest.raises(RateFeedError, match="FRED_API_KEY"):
        run(FredProvider(api_key="")._get("series/observations", {}))
