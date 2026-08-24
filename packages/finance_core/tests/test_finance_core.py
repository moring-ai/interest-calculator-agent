"""Regression tests for the math the agent is not allowed to do itself.

Expected values are hand-computed from published formulas and the 2026 IRS
tables, not captured from a previous run of this code.
"""

from decimal import Decimal

import pytest

from finance_core import (
    amortization_schedule, apr_from_apy, apy_from_apr, cagr,
    capital_gains_tax, monthly_payment, project_investment, project_savings,
)

D = Decimal


# --------------------------------------------------------------------------
# Compounding
# --------------------------------------------------------------------------

def test_lump_sum_matches_closed_form():
    # 10_000 * 1.05**10 = 16_288.946...
    assert project_savings(10_000, 0.05, 10, contributions_per_year=1).final_balance == D("16288.95")


@pytest.mark.parametrize("steps", [1, 2, 4, 12, 52, 365])
def test_result_is_independent_of_chart_granularity(steps):
    """The same account must not change value because the caller wanted a
    finer chart. This guards the full-precision accumulation convention."""
    assert project_savings(10_000, 0.05, 10, contributions_per_year=steps).final_balance == D("16288.95")


def test_zero_rate_returns_exactly_the_deposits():
    p = project_savings(1_000, 0.0, 5, contribution=100, contributions_per_year=12)
    assert p.final_balance == D("7000.00")
    assert p.total_interest == D("0.00")


def test_annuity_due_beats_ordinary_annuity():
    end = project_savings(0, 0.06, 10, contribution=500)
    begin = project_savings(0, 0.06, 10, contribution=500, contribution_timing="begin")
    assert begin.final_balance > end.final_balance


def test_deposits_plus_interest_equals_balance():
    p = project_savings(5_000, 0.045, 15, contribution=250)
    assert p.total_deposited + p.total_interest == p.final_balance


def test_apr_apy_round_trip():
    apy = apy_from_apr(0.05, 12)
    assert apy == pytest.approx(0.0511619, abs=1e-7)
    assert apr_from_apy(apy, 12) == pytest.approx(0.05, abs=1e-12)


def test_inflation_adjustment_reduces_value():
    p = project_savings(10_000, 0.05, 10, contributions_per_year=1, inflation_rate=0.03)
    # 10_000 * 1.05**10 / 1.03**10 = 16_288.946 / 1.343916 = 12_120.51
    assert p.real_final_balance == D("12120.51")
    assert p.real_final_balance < p.final_balance


def test_contribution_growth_increases_total():
    flat = project_savings(0, 0.05, 10, contribution=500)
    rising = project_savings(0, 0.05, 10, contribution=500, annual_contribution_growth=0.03)
    assert rising.total_contributions > flat.total_contributions


# --------------------------------------------------------------------------
# Mortgage
# --------------------------------------------------------------------------

def test_reference_mortgage_payment():
    # 300k @ 6.5% / 30y is a widely published $1,896.20.
    assert monthly_payment(300_000, 0.065, 360) == D("1896.20")


def test_zero_rate_loan_is_straight_division():
    assert monthly_payment(12_000, 0.0, 24) == D("500.00")


@pytest.mark.parametrize("principal,rate,term", [
    (300_000, 0.065, 360), (450_000, 0.0725, 360), (25_000, 0.0399, 60),
    (300_000, 0.065, 180), (12_000, 0.0, 24), (1_000_000, 0.0299, 360),
])
def test_every_loan_amortizes_to_exactly_zero(principal, rate, term):
    s = amortization_schedule(principal, rate, term)
    assert s.rows[-1].balance == D("0.00")
    assert s.payoff_months == term
    # Every dollar paid is either interest or principal, with no drift.
    assert s.total_paid - s.total_interest == s.principal


def test_first_payment_interest_is_rate_times_balance():
    s = amortization_schedule(300_000, 0.065, 360)
    assert s.rows[0].interest == D("1625.00")


def test_extra_payments_shorten_the_loan_and_save_interest():
    base = amortization_schedule(300_000, 0.065, 360)
    extra = amortization_schedule(300_000, 0.065, 360, extra_monthly=300)
    assert extra.payoff_months < base.payoff_months
    assert extra.months_saved == base.payoff_months - extra.payoff_months
    assert extra.interest_saved == base.total_interest - extra.total_interest
    assert extra.rows[-1].balance == D("0.00")


def test_payment_below_interest_is_rejected():
    with pytest.raises(ValueError, match="never amortize"):
        amortization_schedule(300_000, 0.99, 600)


# --------------------------------------------------------------------------
# Capital gains -- stacked on other income, per the 2026 tables
# --------------------------------------------------------------------------

def test_long_term_gain_entirely_above_the_zero_band():
    r = capital_gains_tax(80_000, 50_000, is_long_term=True, ordinary_taxable_income=50_000)
    assert r.federal_tax == D("4500.00")          # 30_000 * 15%


def test_long_term_gain_straddling_the_zero_band():
    """The case a single headline rate gets wrong: part of the gain fills the
    0% band before the rest is taxed at 15%."""
    r = capital_gains_tax(80_000, 50_000, is_long_term=True, ordinary_taxable_income=30_000)
    assert r.federal_tax == D("1582.50")          # 19_450 @ 0% + 10_550 @ 15%
    assert r.effective_rate == pytest.approx(0.05275)
    assert [s.rate for s in r.brackets] == [0.00, 0.15]


def test_large_gain_crosses_into_twenty_percent_and_niit():
    r = capital_gains_tax(1_000_000, 500_000, is_long_term=True, ordinary_taxable_income=250_000)
    assert r.federal_tax == D("85225.00")         # 295_500 @ 15% + 204_500 @ 20%
    assert r.niit == D("19000.00")                # 500_000 @ 3.8%
    assert r.total_tax == D("104225.00")
    assert r.marginal_rate == pytest.approx(0.238)


def test_short_term_gain_uses_ordinary_brackets():
    r = capital_gains_tax(80_000, 50_000, is_long_term=False, ordinary_taxable_income=30_000)
    assert r.federal_tax == D("4560.00")          # 20_400 @ 12% + 9_600 @ 22%


def test_short_term_costs_more_than_long_term():
    kw = dict(ordinary_taxable_income=30_000)
    short = capital_gains_tax(80_000, 50_000, is_long_term=False, **kw)
    long = capital_gains_tax(80_000, 50_000, is_long_term=True, **kw)
    assert short.total_tax > long.total_tax


def test_holding_period_boundary_is_more_than_one_year():
    assert not capital_gains_tax(2, 1, holding_period_days=365).is_long_term
    assert capital_gains_tax(2, 1, holding_period_days=366).is_long_term


def test_loss_owes_no_tax_and_explains_the_carryforward():
    r = capital_gains_tax(40_000, 50_000, is_long_term=True, ordinary_taxable_income=60_000)
    assert r.gain == D("-10000.00")
    assert r.total_tax == D("0")
    assert r.net_proceeds == D("40000.00")
    assert any("carries forward" in n for n in r.notes)


def test_state_tax_is_added_on_top():
    kw = dict(is_long_term=True, ordinary_taxable_income=50_000)
    plain = capital_gains_tax(80_000, 50_000, **kw)
    ca = capital_gains_tax(80_000, 50_000, state_rate=0.093, **kw)
    assert ca.state_tax == D("2790.00")           # 30_000 * 9.3%
    assert ca.total_tax == plain.total_tax + D("2790.00")


def test_unknown_filing_status_is_rejected():
    with pytest.raises(ValueError, match="unknown filing status"):
        capital_gains_tax(2, 1, is_long_term=True, filing_status="bogus")


def test_missing_holding_period_is_rejected():
    with pytest.raises(ValueError, match="holding_period_days or is_long_term"):
        capital_gains_tax(80_000, 50_000)


# --------------------------------------------------------------------------
# Investment
# --------------------------------------------------------------------------

def test_cagr_matches_closed_form():
    assert cagr(10_000, 26_533, 10) == pytest.approx(0.1025, abs=1e-4)


def test_cagr_rejects_nonpositive_start():
    with pytest.raises(ValueError):
        cagr(0, 100, 5)


def test_projection_gain_is_final_minus_invested():
    p = project_investment(10_000, 0.08, 20, contribution=500, realize_at_end=True,
                           ordinary_taxable_income=90_000)
    assert p.gain == p.nominal_final - p.total_invested
    assert p.sale is not None
    assert p.sale.net_proceeds == p.nominal_final - p.sale.total_tax
