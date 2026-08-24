"""Deterministic financial math. No LLM, no network, no I/O.

Every number the agent reports comes from here. The language model chooses
which function to call and explains the result; it never does the arithmetic.
"""

from .money import D, cents, as_float
from .compounding import (
    PeriodRow, SavingsProjection, apr_from_apy, apy_from_apr,
    effective_annual_rate, project_savings, real_rate,
)
from .mortgage import (
    AmortizationResult, AmortRow, amortization_schedule, monthly_payment,
)
from .investment import (
    BracketSlice, CapitalGainsResult, InvestmentProjection, cagr,
    capital_gains_tax, project_investment, total_return,
)
from .tax_tables import SOURCE as TAX_SOURCE, TAX_YEAR

__version__ = "0.1.0"

__all__ = [
    "D", "cents", "as_float",
    "PeriodRow", "SavingsProjection", "apr_from_apy", "apy_from_apr",
    "effective_annual_rate", "project_savings", "real_rate",
    "AmortizationResult", "AmortRow", "amortization_schedule", "monthly_payment",
    "BracketSlice", "CapitalGainsResult", "InvestmentProjection", "cagr",
    "capital_gains_tax", "project_investment", "total_return",
    "TAX_YEAR", "TAX_SOURCE",
]
