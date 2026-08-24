"""Investment growth, realized returns, and capital gains tax estimates.

Capital gains are computed by *stacking*: a gain sits on top of the taxpayer's
other taxable income and is taxed bracket by bracket from there. Quoting a
single headline rate ("you're in the 15% bracket") is the usual shortcut and it
is wrong whenever a gain straddles a threshold -- which is exactly the case a
user asking about a large sale cares about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .compounding import project_savings, SavingsProjection, real_rate
from .money import D, ZERO, cents
from .tax_tables import (
    LONG_TERM_HOLDING_DAYS, LTCG_BRACKETS, NIIT_RATE, NIIT_THRESHOLD,
    ORDINARY_BRACKETS, SOURCE, TAX_YEAR, validate_status,
)


# --------------------------------------------------------------------------
# Return metrics
# --------------------------------------------------------------------------

def cagr(begin_value: float | Decimal, end_value: float | Decimal, years: float) -> float:
    """Compound annual growth rate."""
    if years <= 0:
        raise ValueError("years must be positive")
    b, e = float(D(begin_value)), float(D(end_value))
    if b <= 0:
        raise ValueError("begin_value must be positive")
    if e < 0:
        raise ValueError("end_value must be non-negative")
    return (e / b) ** (1.0 / years) - 1.0


def total_return(begin_value: float | Decimal, end_value: float | Decimal) -> float:
    """Simple cumulative return over the whole period."""
    b = float(D(begin_value))
    if b <= 0:
        raise ValueError("begin_value must be positive")
    return float(D(end_value)) / b - 1.0


# --------------------------------------------------------------------------
# Capital gains
# --------------------------------------------------------------------------

@dataclass
class BracketSlice:
    rate: float
    amount_taxed: Decimal
    tax: Decimal


@dataclass
class CapitalGainsResult:
    proceeds: Decimal
    cost_basis: Decimal
    gain: Decimal
    is_long_term: bool
    federal_tax: Decimal
    niit: Decimal
    state_tax: Decimal
    total_tax: Decimal
    net_proceeds: Decimal
    effective_rate: float
    marginal_rate: float
    filing_status: str
    tax_year: int = TAX_YEAR
    source: str = SOURCE
    brackets: list[BracketSlice] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _stacked_tax(
    amount: Decimal, brackets: list[tuple[float, float]], income_floor: Decimal
) -> tuple[Decimal, list[BracketSlice]]:
    """Tax `amount` as if it sits on top of `income_floor` of other income."""
    remaining = D(amount)
    position = D(income_floor)
    total = ZERO
    slices: list[BracketSlice] = []

    for upper, rate in brackets:
        if remaining <= ZERO:
            break
        ceiling = D(upper)  # Decimal handles the infinite top bracket natively.
        if position >= ceiling:
            continue
        room = ceiling - position
        taxed = remaining if remaining < room else room
        tax = taxed * D(rate)
        if taxed > ZERO:
            slices.append(BracketSlice(rate=rate, amount_taxed=cents(taxed), tax=cents(tax)))
        total += tax
        remaining -= taxed
        position += taxed

    return cents(total), slices


def capital_gains_tax(
    proceeds: float | Decimal,
    cost_basis: float | Decimal,
    *,
    holding_period_days: int | None = None,
    is_long_term: bool | None = None,
    ordinary_taxable_income: float | Decimal = 0,
    filing_status: str = "single",
    state_rate: float = 0.0,
    include_niit: bool = True,
) -> CapitalGainsResult:
    """Estimate federal tax on a realized capital gain.

    Args:
        proceeds: Sale price.
        cost_basis: What was originally paid, adjusted for basis changes.
        holding_period_days: Days held. More than 365 makes the gain long-term.
        is_long_term: Overrides `holding_period_days` when supplied.
        ordinary_taxable_income: Other taxable income the gain stacks on top of.
        filing_status: One of the statuses in `tax_tables`.
        state_rate: Flat state rate applied to the whole gain, e.g. 0.093.
        include_niit: Apply the 3.8% net investment income tax where owed.
    """
    validate_status(filing_status)

    gross = D(proceeds)
    basis = D(cost_basis)
    gain = gross - basis
    notes: list[str] = []

    if is_long_term is None:
        if holding_period_days is None:
            raise ValueError("supply either holding_period_days or is_long_term")
        is_long_term = holding_period_days > LONG_TERM_HOLDING_DAYS

    # A loss owes no tax. Federal law lets only $3,000 of net capital loss
    # offset ordinary income per year, with the rest carried forward -- worth
    # surfacing, but modelling a carryforward needs a multi-year return.
    if gain <= ZERO:
        notes.append(
            "This is a capital loss. Losses offset capital gains first; up to "
            "$3,000 of any remaining net loss can offset ordinary income per "
            "year, and the rest carries forward."
        )
        return CapitalGainsResult(
            proceeds=cents(gross), cost_basis=cents(basis), gain=cents(gain),
            is_long_term=is_long_term, federal_tax=ZERO, niit=ZERO, state_tax=ZERO,
            total_tax=ZERO, net_proceeds=cents(gross), effective_rate=0.0,
            marginal_rate=0.0, filing_status=filing_status, notes=notes,
        )

    brackets = LTCG_BRACKETS[filing_status] if is_long_term else ORDINARY_BRACKETS[filing_status]
    other_income = D(ordinary_taxable_income)
    federal_tax, slices = _stacked_tax(gain, brackets, other_income)

    niit = ZERO
    if include_niit:
        magi = other_income + gain
        excess = magi - D(NIIT_THRESHOLD[filing_status])
        if excess > ZERO:
            base = gain if gain < excess else excess
            niit = cents(base * D(NIIT_RATE))
            notes.append(
                f"Net investment income tax of {NIIT_RATE:.1%} applies to "
                f"${base:,.2f} of this gain."
            )

    state_tax = cents(gain * D(state_rate))
    total_tax = cents(federal_tax + niit + state_tax)

    if not is_long_term:
        notes.append(
            "Short-term gains are taxed as ordinary income. Holding more than "
            "one year would move this into long-term rates."
        )

    marginal = slices[-1].rate if slices else 0.0
    if include_niit and niit > ZERO:
        marginal += NIIT_RATE

    return CapitalGainsResult(
        proceeds=cents(gross),
        cost_basis=cents(basis),
        gain=cents(gain),
        is_long_term=is_long_term,
        federal_tax=federal_tax,
        niit=niit,
        state_tax=state_tax,
        total_tax=total_tax,
        net_proceeds=cents(gross - total_tax),
        effective_rate=float(total_tax / gain) if gain > ZERO else 0.0,
        marginal_rate=marginal + state_rate,
        filing_status=filing_status,
        brackets=slices,
        notes=notes,
    )


# --------------------------------------------------------------------------
# Growth + realization
# --------------------------------------------------------------------------

@dataclass
class InvestmentProjection:
    growth: SavingsProjection
    nominal_final: Decimal
    total_invested: Decimal
    gain: Decimal
    cagr: float
    real_final: Decimal | None = None
    real_annual_return: float | None = None
    sale: CapitalGainsResult | None = None


def project_investment(
    principal: float | Decimal,
    annual_return: float,
    years: float,
    *,
    contribution: float | Decimal = 0,
    contributions_per_year: int = 12,
    inflation_rate: float | None = None,
    realize_at_end: bool = False,
    ordinary_taxable_income: float | Decimal = 0,
    filing_status: str = "single",
    state_rate: float = 0.0,
) -> InvestmentProjection:
    """Grow an investment, then optionally sell it and pay the tax.

    `annual_return` is treated as an effective annual rate, matching how fund
    performance is quoted.
    """
    growth = project_savings(
        principal, annual_return, years,
        rate_kind="apy",
        contribution=contribution,
        contributions_per_year=contributions_per_year,
        inflation_rate=inflation_rate,
    )

    invested = growth.total_deposited
    final = growth.final_balance
    gain = cents(final - invested)

    sale = None
    if realize_at_end:
        sale = capital_gains_tax(
            final, invested,
            is_long_term=years > 1,
            ordinary_taxable_income=ordinary_taxable_income,
            filing_status=filing_status,
            state_rate=state_rate,
        )

    real_return = real_rate(annual_return, inflation_rate) if inflation_rate is not None else None

    return InvestmentProjection(
        growth=growth,
        nominal_final=final,
        total_invested=invested,
        gain=gain,
        cagr=cagr(invested, final, years) if invested > ZERO and years > 0 else 0.0,
        real_final=growth.real_final_balance,
        real_annual_return=real_return,
        sale=sale,
    )
