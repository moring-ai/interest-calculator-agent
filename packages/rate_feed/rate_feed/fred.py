"""Federal Reserve Economic Data (FRED) provider.

FRED is the primary source: it publishes the Freddie Mac mortgage averages, the
effective fed funds rate, and the full Treasury constant-maturity curve, all
under one API. It needs a free API key from
https://fredaccount.stlouisfed.org/apikeys.

FRED quotes rates as percentage numbers ("6.58"). This module converts to the
decimal fractions the rest of the system uses (0.0658) at the boundary, so no
downstream code has to remember which convention it is holding.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime

import httpx

from . import catalog
from .models import Freshness, RateFeedError, RateObservation, RateQuote, RateSeries

logger = logging.getLogger(__name__)

API_ROOT = "https://api.stlouisfed.org/fred"
API_KEY_VAR = "FRED_API_KEY"

#: Environment variables naming a CA bundle, in the order they are consulted.
#: httpx trusts certifi's bundle by default and ignores these, which breaks on
#: any network that terminates TLS -- a corporate proxy or a ZTNA agent -- with
#: CERTIFICATE_VERIFY_FAILED. Honouring them here means setting the standard
#: variable is enough, with no code change and no disabling of verification.
CA_BUNDLE_VARS = ("RATE_FEED_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")


def _default_verify() -> str | bool:
    """Resolve a CA bundle path from the environment, else default trust."""
    for var in CA_BUNDLE_VARS:
        path = os.environ.get(var, "").strip()
        if path and os.path.isfile(path):
            logger.info("using CA bundle from %s", var)
            return path
    return True

#: FRED writes a lone period where an observation is missing (market holidays,
#: series gaps). Those rows are dropped rather than parsed as zero -- a 0% rate
#: would silently poison every projection built on it.
MISSING = "."


class FredProvider:
    name = "FRED"

    def __init__(
        self,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
        verify: str | bool | None = None,
    ):
        self.api_key = api_key or os.environ.get(API_KEY_VAR, "").strip()
        self._client = client
        self._timeout = timeout
        self._verify = _default_verify() if verify is None else verify

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def supports(self, key: str) -> bool:
        return key in catalog.CATALOG and catalog.get(key).provider == "fred"

    async def _get(self, path: str, params: dict) -> dict:
        if not self.configured:
            raise RateFeedError(
                f"{API_KEY_VAR} is not set. Get a free key at "
                "https://fredaccount.stlouisfed.org/apikeys"
            )
        query = {**params, "api_key": self.api_key, "file_type": "json"}
        client = self._client or httpx.AsyncClient(
            timeout=self._timeout, verify=self._verify
        )
        try:
            resp = await client.get(f"{API_ROOT}/{path}", params=query)
            if resp.status_code == 400:
                raise RateFeedError(f"FRED rejected the request: {resp.text[:200]}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise RateFeedError(f"FRED request failed: {exc}") from exc
        finally:
            if self._client is None:
                await client.aclose()

    @staticmethod
    def _parse_rows(rows: list[dict]) -> list[RateObservation]:
        out = []
        for row in rows:
            raw = (row.get("value") or "").strip()
            if not raw or raw == MISSING:
                continue
            try:
                out.append(
                    RateObservation(
                        as_of=datetime.strptime(row["date"], "%Y-%m-%d").date(),
                        value=float(raw) / 100.0,   # percent -> decimal fraction
                    )
                )
            except (ValueError, KeyError):
                logger.warning("skipping unparseable FRED row: %r", row)
        return out

    async def fetch_latest(self, key: str) -> RateQuote:
        """Most recent published observation for a rate."""
        d = catalog.get(key)
        # Ask for a short descending window rather than a single row: the very
        # latest row is often a placeholder "." on a holiday or before the
        # weekly release lands, and we want the last real number.
        data = await self._get(
            "series/observations",
            {"series_id": d.series_id, "sort_order": "desc", "limit": 10},
        )
        rows = self._parse_rows(data.get("observations", []))
        if not rows:
            raise RateFeedError(f"FRED returned no usable observations for {d.series_id}")
        newest = rows[0]
        return RateQuote(
            key=d.key, label=d.label, value=newest.value, as_of=newest.as_of,
            source=self.name, series_id=d.series_id,
            freshness=Freshness.LIVE, citation_url=d.citation_url,
        )

    async def fetch_series(
        self, key: str, start: date | None = None, end: date | None = None
    ) -> RateSeries:
        """Historical observations, oldest first, for charting."""
        d = catalog.get(key)
        params: dict[str, str] = {"series_id": d.series_id, "sort_order": "asc"}
        if start:
            params["observation_start"] = start.isoformat()
        if end:
            params["observation_end"] = end.isoformat()

        data = await self._get("series/observations", params)
        rows = self._parse_rows(data.get("observations", []))
        return RateSeries(
            key=d.key, label=d.label, source=self.name, series_id=d.series_id,
            observations=rows, freshness=Freshness.LIVE,
            citation_url=d.citation_url,
        )
