"""Loan amortization: mortgages, auto loans, and any fixed-payment debt.

Precision
---------
Unlike :mod:`compounding`, this module rounds to cents *inside* the loop, on
purpose. A lender posts a whole-cent interest charge to the ledger every month
and the borrower pays a whole-cent payment, so per-period rounding is what
actually happens to the balance. The final payment is trued up to clear the
loan exactly rather than leaving a few cents of rounding drift behind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .money import D, ZERO, cents


@dataclass
class AmortRow:
    period: int
    year: float
    payment: Decimal
    interest: Decimal
    principal: Decimal
    extra: Decimal
    balance: Decimal
    cumulative_interest: Decimal
    cumulative_principal: Decimal


@dataclass
class AmortizationResult:
    monthly_payment: Decimal
    principal: Decimal
    annual_rate: float
    term_months: int
    total_interest: Decimal
    total_paid: Decimal
    payoff_months: int
    rows: list[AmortRow] = field(default_factory=list)
    # Populated only when extra payments were supplied.
    months_saved: int = 0
    interest_saved: Decimal = ZERO

    @property
    def payoff_years(self) -> float:
        return round(self.payoff_months / 12, 2)

    def yearly(self) -> list[AmortRow]:
        """Year-end rows -- the natural resolution for a balance chart."""
        out = [r for r in self.rows if r.period % 12 == 0]
        if self.rows and (not out or out[-1].period != self.rows[-1].period):
            out.append(self.rows[-1])
        return out


def monthly_payment(
    principal: float | Decimal,
    annual_rate: float,
    term_months: int,
) -> Decimal:
    """Standard fixed-rate amortizing payment, rounded to cents.

    M = P * [ r(1+r)^n ] / [ (1+r)^n - 1 ],  r = annual_rate / 12
    """
    if term_months <= 0:
        raise ValueError("term_months must be positive")
    p = D(principal)
    if p < 0:
        raise ValueError("principal must be non-negative")

    r = D(annual_rate) / D(12)
    if r == 0:
        return cents(p / D(term_months))

    growth = (D(1) + r) ** term_months
    return cents(p * r * growth / (growth - D(1)))


def amortization_schedule(
    principal: float | Decimal,
    annual_rate: float,
    term_months: int,
    *,
    extra_monthly: float | Decimal = 0,
    extra_annual: float | Decimal = 0,
    _baseline: bool = False,
) -> AmortizationResult:
    """Build a full payment-by-payment schedule.

    Args:
        principal: Original loan amount.
        annual_rate: Nominal annual rate, e.g. 0.0675 for 6.75%.
        term_months: Scheduled term, e.g. 360 for a 30-year loan.
        extra_monthly: Additional principal paid every month.
        extra_annual: Additional principal paid once per year (month 12, 24...).
        _baseline: Internal. Suppresses the comparison run against no-extra.

    Returns:
        An :class:`AmortizationResult` whose final row leaves a zero balance.
    """
    p = D(principal)
    r = D(annual_rate) / D(12)
    scheduled = monthly_payment(p, annual_rate, term_months)
    extra_m = cents(extra_monthly)
    extra_a = cents(extra_annual)

    # A payment that never exceeds the interest charge means the loan grows
    # forever; that is a bad input, not a schedule worth returning.
    if r > 0 and scheduled <= cents(p * r) and extra_m == ZERO and extra_a == ZERO:
        raise ValueError(
            "payment does not cover monthly interest; loan would never amortize"
        )

    balance = p
    cum_interest = ZERO
    cum_principal = ZERO
    rows: list[AmortRow] = []
    period = 0

    # Cap iterations at the scheduled term: extra payments only shorten it.
    while balance > ZERO and period < term_months:
        period += 1
        interest = cents(balance * r)
        extra = extra_m + (extra_a if period % 12 == 0 else ZERO)
        principal_part = scheduled - interest + extra

        # Final payment: clear the loan exactly. This fires either because the
        # borrower is ahead of schedule, or because it is the last scheduled
        # month and cent-rounding on the level payment left a small residual --
        # a real lender trues up that last payment rather than carrying it.
        if principal_part >= balance or period == term_months:
            principal_part = balance
            scheduled_principal = scheduled - interest
            extra = max(ZERO, principal_part - scheduled_principal)

        payment = interest + principal_part
        balance -= principal_part
        cum_interest += interest
        cum_principal += principal_part

        rows.append(
            AmortRow(
                period=period,
                year=round(period / 12, 4),
                payment=cents(payment),
                interest=cents(interest),
                principal=cents(principal_part),
                extra=cents(extra),
                balance=cents(balance),
                cumulative_interest=cents(cum_interest),
                cumulative_principal=cents(cum_principal),
            )
        )

    result = AmortizationResult(
        monthly_payment=scheduled,
        principal=cents(p),
        annual_rate=annual_rate,
        term_months=term_months,
        total_interest=cents(cum_interest),
        total_paid=cents(cum_interest + cum_principal),
        payoff_months=period,
        rows=rows,
    )

    # Quantify what the extra payments bought.
    if not _baseline and (extra_m > ZERO or extra_a > ZERO):
        base = amortization_schedule(principal, annual_rate, term_months, _baseline=True)
        result.months_saved = base.payoff_months - result.payoff_months
        result.interest_saved = cents(base.total_interest - result.total_interest)

    return result
