"""Deterministic synthetic rates for local development and provider outages.

Every value this produces is tagged ``Freshness.SYNTHETIC`` and carries the
source "SYNTHETIC", which the API and the UI surface as an explicit warning
banner. That labelling is the entire point: a plausible-looking wrong rate that
is not flagged is worse than no rate at all.

The walk is seeded from the rate key, so the same key always yields the same
history. Charts stay stable across reloads instead of jittering.
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import date, timedelta

from . import catalog
from .models import Freshness, RateObservation, RateQuote, RateSeries

#: Illustrative anchor levels. Not market data.
ANCHORS: dict[str, float] = {
    "mortgage_30y": 0.0630,
    "mortgage_15y": 0.0562,
    "fed_funds": 0.0390,
    "prime_rate": 0.0700,
    "sofr": 0.0388,
    "treasury_3m": 0.0392,
    "treasury_1y": 0.0371,
    "treasury_2y": 0.0364,
    "treasury_5y": 0.0383,
    "treasury_10y": 0.0421,
    "treasury_30y": 0.0478,
    "inflation_expectation_10y": 0.0232,
}

DEFAULT_ANCHOR = 0.05
#: Daily standard deviation of the walk, in rate points.
VOLATILITY = 0.00035


def _seed(key: str) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)


class MockProvider:
    name = "SYNTHETIC"

    def supports(self, key: str) -> bool:
        return key in catalog.CATALOG

    def _walk(self, key: str, days: int) -> list[float]:
        """Mean-reverting random walk around the anchor level."""
        rng = random.Random(_seed(key))
        anchor = ANCHORS.get(key, DEFAULT_ANCHOR)
        value = anchor
        out = []
        for i in range(days):
            # Pull back toward the anchor so long histories do not drift away.
            value += (anchor - value) * 0.02 + rng.gauss(0, VOLATILITY)
            # A gentle seasonal wave keeps charts from looking like pure noise.
            out.append(max(0.0001, value + 0.0009 * math.sin(i / 45.0)))
        return out

    async def fetch_latest(self, key: str) -> RateQuote:
        d = catalog.get(key)
        value = self._walk(key, 400)[-1]
        return RateQuote(
            key=d.key, label=d.label, value=round(value, 6), as_of=date.today(),
            source=self.name, series_id=f"SYNTHETIC:{d.series_id}",
            freshness=Freshness.SYNTHETIC, citation_url=None,
        )

    async def fetch_series(
        self, key: str, start: date | None = None, end: date | None = None
    ) -> RateSeries:
        d = catalog.get(key)
        end = end or date.today()
        start = start or (end - timedelta(days=365))
        days = max(1, (end - start).days + 1)
        values = self._walk(key, days)
        observations = [
            RateObservation(as_of=start + timedelta(days=i), value=round(v, 6))
            for i, v in enumerate(values)
        ]
        return RateSeries(
            key=d.key, label=d.label, source=self.name,
            series_id=f"SYNTHETIC:{d.series_id}", observations=observations,
            freshness=Freshness.SYNTHETIC, citation_url=None,
        )
