"""Compound growth: savings accounts, CDs, and any contribution schedule.

Rate convention
---------------
Everything is normalized to an *effective annual rate* (EAR) first, then grown
one step at a time using ``(1 + EAR) ** (1 / steps_per_year)``. This keeps the
result exact for any mix of compounding frequency and contribution frequency
(e.g. daily-compounding account funded monthly) without needing the two to
share a period, and it matches the definition of APY that banks advertise --
which is the number consumers actually compare accounts on.

Precision
---------
The running balance is carried at full ``Decimal`` precision and quantized to
cents only when a row is emitted or a total is reported. Rounding *inside* the
loop would make the answer depend on the simulation step, so the same account
would return a different final balance depending on whether the caller asked
for a monthly or an annual chart. Contributions are the exception: those are
real deposits and are quantized to cents before they land.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from .money import D, ZERO, cents

RateKind = Literal["apy", "apr", "continuous"]
Timing = Literal["end", "begin"]


# --------------------------------------------------------------------------
# Rate conversions
# --------------------------------------------------------------------------

def apy_from_apr(apr: float, compounds_per_year: int = 12) -> float:
    """Nominal annual rate -> effective annual yield."""
    if compounds_per_year <= 0:
        raise ValueError("compounds_per_year must be positive")
    return (1.0 + apr / compounds_per_year) ** compounds_per_year - 1.0


def apr_from_apy(apy: float, compounds_per_year: int = 12) -> float:
    """Effective annual yield -> nominal annual rate."""
    if compounds_per_year <= 0:
        raise ValueError("compounds_per_year must be positive")
    if apy <= -1.0:
        raise ValueError("apy must be greater than -100%")
    return compounds_per_year * ((1.0 + apy) ** (1.0 / compounds_per_year) - 1.0)


def effective_annual_rate(
    rate: float,
    kind: RateKind = "apy",
    compounds_per_year: int = 12,
) -> float:
    """Normalize any quoted rate to an effective annual rate."""
    if kind == "apy":
        return rate
    if kind == "apr":
        return apy_from_apr(rate, compounds_per_year)
    if kind == "continuous":
        return math.exp(rate) - 1.0
    raise ValueError(f"unknown rate kind: {kind!r}")


def real_rate(nominal: float, inflation: float) -> float:
    """Fisher equation: strip inflation out of a nominal return."""
    return (1.0 + nominal) / (1.0 + inflation) - 1.0


# --------------------------------------------------------------------------
# Projection
# --------------------------------------------------------------------------

@dataclass
class PeriodRow:
    period: int
    year: float
    opening_balance: Decimal
    contribution: Decimal
    interest: Decimal
    closing_balance: Decimal
    cumulative_contributions: Decimal
    cumulative_interest: Decimal


@dataclass
class SavingsProjection:
    final_balance: Decimal
    principal: Decimal
    total_contributions: Decimal
    total_interest: Decimal
    effective_annual_rate: float
    years: float
    periods: list[PeriodRow] = field(default_factory=list)
    real_final_balance: Decimal | None = None
    inflation_rate: float | None = None

    @property
    def total_deposited(self) -> Decimal:
        """Every dollar the saver put in, principal plus contributions."""
        return self.principal + self.total_contributions

    def yearly(self) -> list[PeriodRow]:
        """One row per year boundary -- the natural resolution for charting."""
        out, seen = [], set()
        for row in self.periods:
            marker = math.floor(row.year + 1e-9)
            if row.year >= marker and marker not in seen and abs(row.year - marker) < 1e-6:
                seen.add(marker)
                out.append(row)
        if self.periods and (not out or out[-1].period != self.periods[-1].period):
            out.append(self.periods[-1])
        return out


def project_savings(
    principal: float | Decimal,
    rate: float,
    years: float,
    *,
    rate_kind: RateKind = "apy",
    compounds_per_year: int = 12,
    contribution: float | Decimal = 0,
    contributions_per_year: int = 12,
    contribution_timing: Timing = "end",
    annual_contribution_growth: float = 0.0,
    inflation_rate: float | None = None,
) -> SavingsProjection:
    """Grow a balance over time with optional recurring contributions.

    Args:
        principal: Starting balance.
        rate: Quoted rate, interpreted according to `rate_kind`.
        years: Horizon. May be fractional.
        rate_kind: "apy" (effective), "apr" (nominal), or "continuous".
        compounds_per_year: Only consulted when `rate_kind` is "apr".
        contribution: Amount added each contribution period.
        contributions_per_year: Contribution cadence; also the simulation step.
        contribution_timing: "begin" earns interest in the period it lands
            (annuity-due), "end" does not (ordinary annuity).
        annual_contribution_growth: Escalate contributions each year, e.g.
            0.03 to raise savings 3% annually.
        inflation_rate: If given, also report the inflation-adjusted balance.
    """
    if years < 0:
        raise ValueError("years must be non-negative")
    if contributions_per_year <= 0:
        raise ValueError("contributions_per_year must be positive")

    ear = effective_annual_rate(rate, rate_kind, compounds_per_year)
    if ear <= -1.0:
        raise ValueError("effective annual rate must be greater than -100%")

    steps_per_year = contributions_per_year
    total_steps = int(round(years * steps_per_year))
    step_growth = D((1.0 + ear) ** (1.0 / steps_per_year) - 1.0)

    balance = D(principal)
    base_contribution = D(contribution)
    cum_contrib = ZERO
    cum_interest = ZERO
    rows: list[PeriodRow] = []

    for step in range(1, total_steps + 1):
        opening = balance
        # Contributions escalate on whole-year boundaries.
        completed_years = (step - 1) // steps_per_year
        pmt = cents(base_contribution * D((1.0 + annual_contribution_growth) ** completed_years))

        if contribution_timing == "begin":
            balance += pmt
            interest = balance * step_growth
            balance += interest
        else:
            interest = balance * step_growth
            balance += interest + pmt

        cum_contrib += pmt
        cum_interest += interest
        rows.append(
            PeriodRow(
                period=step,
                year=step / steps_per_year,
                opening_balance=cents(opening),
                contribution=pmt,
                interest=cents(interest),
                closing_balance=cents(balance),
                cumulative_contributions=cents(cum_contrib),
                cumulative_interest=cents(cum_interest),
            )
        )

    real_balance = None
    if inflation_rate is not None:
        real_balance = cents(D(balance) / D((1.0 + inflation_rate) ** years))

    return SavingsProjection(
        final_balance=cents(balance),
        principal=cents(principal),
        total_contributions=cents(cum_contrib),
        total_interest=cents(cum_interest),
        effective_annual_rate=ear,
        years=years,
        periods=rows,
        real_final_balance=real_balance,
        inflation_rate=inflation_rate,
    )
