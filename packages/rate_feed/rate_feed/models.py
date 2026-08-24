"""Rate types carrying their own provenance.

Every rate that reaches the UI has to be able to answer "where did this come
from and how old is it?". Provenance is part of the value, not metadata bolted
on somewhere else, because the whole premise of the product is that these are
live numbers rather than something the model remembered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum


class Freshness(str, Enum):
    LIVE = "live"          # Fetched from the upstream provider just now.
    CACHED = "cached"      # Served from cache, still within its TTL.
    STALE = "stale"        # Upstream failed; this is the last good value.
    SYNTHETIC = "synthetic"  # No provider configured; illustrative only.


@dataclass(frozen=True)
class RateQuote:
    """A single observed rate, as of a specific date, from a named source."""

    key: str                 # Catalog key, e.g. "mortgage_30y".
    label: str               # Human label, e.g. "30-Year Fixed Mortgage".
    value: float             # Decimal fraction: 0.0672 means 6.72%.
    as_of: date              # The observation date, not the fetch time.
    source: str              # e.g. "FRED".
    series_id: str           # e.g. "MORTGAGE30US".
    unit: str = "percent_per_year"
    freshness: Freshness = Freshness.LIVE
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    citation_url: str | None = None

    @property
    def percent(self) -> float:
        """The rate as a percentage number, for display."""
        return round(self.value * 100, 4)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "percent": self.percent,
            "as_of": self.as_of.isoformat(),
            "source": self.source,
            "series_id": self.series_id,
            "unit": self.unit,
            "freshness": self.freshness.value,
            "fetched_at": self.fetched_at.isoformat(),
            "citation_url": self.citation_url,
        }


@dataclass(frozen=True)
class RateObservation:
    """One point in a historical series."""

    as_of: date
    value: float

    def to_dict(self) -> dict:
        return {"as_of": self.as_of.isoformat(), "value": self.value,
                "percent": round(self.value * 100, 4)}


@dataclass(frozen=True)
class RateSeries:
    """A historical run of observations for one rate, newest last."""

    key: str
    label: str
    source: str
    series_id: str
    observations: list[RateObservation]
    freshness: Freshness = Freshness.LIVE
    citation_url: str | None = None

    @property
    def latest(self) -> RateObservation | None:
        return self.observations[-1] if self.observations else None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "source": self.source,
            "series_id": self.series_id,
            "freshness": self.freshness.value,
            "citation_url": self.citation_url,
            "observations": [o.to_dict() for o in self.observations],
        }


class RateFeedError(RuntimeError):
    """Upstream provider could not satisfy the request."""
