"""The single entry point the backend and the agent tools both call.

Resolution order for any rate, best first:

1. a cache entry still inside its TTL          -> CACHED
2. the live provider                           -> LIVE
3. a retained cache entry past its TTL         -> STALE
4. the synthetic provider, if permitted        -> SYNTHETIC

Nothing here raises on a provider outage unless synthetic data is disabled and
there is no cached value at all, so a FRED hiccup degrades the answer's
provenance rather than breaking the page.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import date

from . import catalog
from .cache import TTLCache
from .fred import FredProvider
from .mock import MockProvider
from .models import Freshness, RateFeedError, RateQuote, RateSeries

logger = logging.getLogger(__name__)


class RateService:
    def __init__(
        self,
        primary=None,
        fallback=None,
        allow_synthetic: bool = True,
        cache: TTLCache | None = None,
    ):
        self.primary = primary if primary is not None else FredProvider()
        self.fallback = fallback if fallback is not None else MockProvider()
        self.allow_synthetic = allow_synthetic
        self._quotes: TTLCache[RateQuote] = cache or TTLCache()
        self._series: TTLCache[RateSeries] = TTLCache(max_entries=128)

    # -- introspection ----------------------------------------------------

    @property
    def live_source_configured(self) -> bool:
        return getattr(self.primary, "configured", True)

    def status(self) -> dict:
        return {
            "primary_provider": getattr(self.primary, "name", "unknown"),
            "live_source_configured": self.live_source_configured,
            "synthetic_fallback_enabled": self.allow_synthetic,
            "cache": self._quotes.stats(),
        }

    # -- rates ------------------------------------------------------------

    async def get_rate(self, key: str) -> RateQuote:
        catalog.get(key)  # Raises KeyError for unknown keys.

        cached = self._quotes.get_fresh(key)
        if cached is not None:
            return dataclasses.replace(cached, freshness=Freshness.CACHED)

        if self.primary.supports(key) and self.live_source_configured:
            try:
                quote = await self.primary.fetch_latest(key)
                self._quotes.set(key, quote, catalog.ttl_for(key))
                return quote
            except RateFeedError as exc:
                logger.warning("live fetch failed for %s: %s", key, exc)

        stale = self._quotes.get_stale(key)
        if stale is not None:
            logger.info("serving stale value for %s", key)
            return dataclasses.replace(stale, freshness=Freshness.STALE)

        if self.allow_synthetic:
            return await self.fallback.fetch_latest(key)

        raise RateFeedError(
            f"no live, cached, or synthetic value available for {key!r}"
        )

    async def get_rates(self, keys: list[str]) -> list[RateQuote]:
        """Fetch several rates concurrently, skipping any that fail outright."""
        results = await asyncio.gather(
            *(self.get_rate(k) for k in keys), return_exceptions=True
        )
        out = []
        for key, result in zip(keys, results):
            if isinstance(result, Exception):
                logger.warning("dropping %s from board: %s", key, result)
                continue
            out.append(result)
        return out

    async def get_board(self) -> list[RateQuote]:
        """The featured rates for the UI's rate board."""
        return await self.get_rates(catalog.FEATURED_KEYS)

    # -- history ----------------------------------------------------------

    async def get_series(
        self, key: str, start: date | None = None, end: date | None = None
    ) -> RateSeries:
        catalog.get(key)
        cache_key = f"{key}:{start}:{end}"

        cached = self._series.get_fresh(cache_key)
        if cached is not None:
            return dataclasses.replace(cached, freshness=Freshness.CACHED)

        if self.primary.supports(key) and self.live_source_configured:
            try:
                series = await self.primary.fetch_series(key, start, end)
                if series.observations:
                    self._series.set(cache_key, series, catalog.ttl_for(key))
                    return series
                logger.warning("live series for %s came back empty", key)
            except RateFeedError as exc:
                logger.warning("live series fetch failed for %s: %s", key, exc)

        stale = self._series.get_stale(cache_key)
        if stale is not None:
            return dataclasses.replace(stale, freshness=Freshness.STALE)

        if self.allow_synthetic:
            return await self.fallback.fetch_series(key, start, end)

        raise RateFeedError(f"no series available for {key!r}")
