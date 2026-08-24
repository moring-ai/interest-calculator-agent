"""The catalog of rates Interest Calculator knows how to fetch.

Keys are stable, human-meaningful identifiers ("mortgage_30y") rather than the
upstream series codes, so a provider can be swapped without changing the API,
the agent's tool schema, or the frontend.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RateDefinition:
    key: str
    label: str
    series_id: str          # Upstream identifier, e.g. a FRED series.
    provider: str           # Which provider resolves this key.
    category: str           # Groups rates in the UI.
    description: str
    citation_url: str | None = None
    #: Roughly how often upstream publishes. Drives cache TTL and the
    #: "as of" wording, since a weekly mortgage average being four days old is
    #: normal while a daily treasury yield being four days old is not.
    cadence: str = "daily"


FRED_BASE = "https://fred.stlouisfed.org/series/"


def _fred(key, label, series, category, description, cadence="daily") -> RateDefinition:
    return RateDefinition(
        key=key, label=label, series_id=series, provider="fred",
        category=category, description=description, cadence=cadence,
        citation_url=f"{FRED_BASE}{series}",
    )


CATALOG: dict[str, RateDefinition] = {d.key: d for d in [
    # ---- Mortgages -------------------------------------------------------
    _fred("mortgage_30y", "30-Year Fixed Mortgage", "MORTGAGE30US", "mortgage",
          "Freddie Mac national average rate for a 30-year fixed mortgage.",
          cadence="weekly"),
    _fred("mortgage_15y", "15-Year Fixed Mortgage", "MORTGAGE15US", "mortgage",
          "Freddie Mac national average rate for a 15-year fixed mortgage.",
          cadence="weekly"),

    # ---- Policy & benchmark ---------------------------------------------
    _fred("fed_funds", "Federal Funds Rate", "DFF", "benchmark",
          "The Federal Reserve's effective overnight policy rate. Savings and "
          "money market yields track this closely."),
    _fred("prime_rate", "Bank Prime Loan Rate", "DPRIME", "benchmark",
          "The rate banks charge their most creditworthy customers; HELOCs and "
          "credit cards are usually quoted as prime plus a margin."),
    _fred("sofr", "SOFR", "SOFR", "benchmark",
          "Secured Overnight Financing Rate, the benchmark for floating-rate "
          "business and commercial loans."),

    # ---- Treasury curve --------------------------------------------------
    _fred("treasury_3m", "3-Month Treasury", "DGS3MO", "treasury",
          "3-month Treasury constant maturity yield."),
    _fred("treasury_1y", "1-Year Treasury", "DGS1", "treasury",
          "1-year Treasury constant maturity yield."),
    _fred("treasury_2y", "2-Year Treasury", "DGS2", "treasury",
          "2-year Treasury constant maturity yield."),
    _fred("treasury_5y", "5-Year Treasury", "DGS5", "treasury",
          "5-year Treasury constant maturity yield."),
    _fred("treasury_10y", "10-Year Treasury", "DGS10", "treasury",
          "10-year Treasury constant maturity yield, the reference long rate "
          "that mortgage pricing follows."),
    _fred("treasury_30y", "30-Year Treasury", "DGS30", "treasury",
          "30-year Treasury constant maturity yield."),

    # ---- Inflation -------------------------------------------------------
    _fred("inflation_expectation_10y", "10-Year Breakeven Inflation", "T10YIE",
          "inflation",
          "Market-implied average inflation over the next 10 years. Useful as "
          "the default assumption when converting nominal returns to real."),
]}

#: Ordered for the UI's rate board: the headline numbers first.
FEATURED_KEYS = [
    "mortgage_30y", "mortgage_15y", "fed_funds",
    "treasury_10y", "treasury_2y", "inflation_expectation_10y",
]

CATEGORIES = ["mortgage", "benchmark", "treasury", "inflation"]

#: Cache lifetimes by publication cadence. Nothing upstream updates faster than
#: daily, so an hour of staleness is invisible to a user and removes almost all
#: load from the provider.
TTL_SECONDS = {"daily": 3600, "weekly": 6 * 3600, "monthly": 12 * 3600}


def get(key: str) -> RateDefinition:
    if key not in CATALOG:
        raise KeyError(
            f"unknown rate {key!r}; known keys: {', '.join(sorted(CATALOG))}"
        )
    return CATALOG[key]


def by_category(category: str) -> list[RateDefinition]:
    return [d for d in CATALOG.values() if d.category == category]


def ttl_for(key: str) -> int:
    return TTL_SECONDS.get(get(key).cadence, 3600)
