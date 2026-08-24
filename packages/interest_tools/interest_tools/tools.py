"""The tools the agent may call, and the calculators the API exposes.

Every function here is the *only* way a number reaches the user. The language
model picks which one to call and explains what comes back; it never computes.
That split is what makes the answers trustworthy: `finance_core` is unit-tested
against published formulas, whereas a model doing mental arithmetic is not
verifiable at all.

Rate arguments are named `..._percent` and take human-scale numbers (6.5 for
6.5%). The decimal-fraction convention used internally is a constant source of
factor-of-100 errors when a model has to guess which one an argument wants, so
the ambiguity is removed at the signature.
"""

from __future__ import annotations

from datetime import date, timedelta

from finance_core import (
    amortization_schedule, capital_gains_tax, charts, project_savings,
)
from finance_core.tax_tables import TAX_YEAR
from rate_feed import RateService

from .envelope import Assumption, Citation, ToolResult, money, pct

#: Assumed when a caller asks for inflation adjustment without giving a number.
DEFAULT_INFLATION = 0.025
#: High-yield savings tends to sit slightly below the policy rate.
SAVINGS_SPREAD_TO_FED_FUNDS = -0.001
#: Upper bound on points in a history chart, to keep tool payloads small.
MAX_CHART_POINTS = 180


def _citation(quote) -> Citation:
    return Citation(
        label=quote.label,
        source=quote.source,
        as_of=quote.as_of.isoformat(),
        url=quote.citation_url,
        freshness=quote.freshness.value,
    )


def _synthetic_note(quote) -> list[str]:
    if quote.freshness.value == "synthetic":
        return [
            f"The {quote.label} used here is SYNTHETIC placeholder data, not a "
            f"real market rate. Set FRED_API_KEY to get live rates."
        ]
    if quote.freshness.value == "stale":
        return [
            f"The {quote.label} is a cached value from {quote.as_of}; the live "
            f"source could not be reached."
        ]
    return []


class InterestTools:
    """Bound to a RateService so every tool can reach live rates."""

    def __init__(self, rates: RateService | None = None):
        self.rates = rates or RateService()

    # ------------------------------------------------------------------
    # Rate discovery
    # ------------------------------------------------------------------

    async def list_available_rates(self) -> dict:
        """List every rate Interest Calculator can look up, with its key and category."""
        from rate_feed import CATALOG
        return ToolResult(
            summary={"count": len(CATALOG)},
            detail={"rates": [
                {"key": d.key, "label": d.label, "category": d.category,
                 "description": d.description, "cadence": d.cadence}
                for d in CATALOG.values()
            ]},
        ).to_dict()

    async def get_current_rate(self, rate_key: str) -> dict:
        """Look up the latest published value for one rate.

        Args:
            rate_key: A key from `list_available_rates`, e.g. "mortgage_30y".
        """
        q = await self.rates.get_rate(rate_key)
        return ToolResult(
            summary={
                "label": q.label,
                "rate_percent": q.percent,
                "as_of": q.as_of.isoformat(),
                "source": q.source,
            },
            detail=q.to_dict(),
            citations=[_citation(q)],
            notes=_synthetic_note(q),
        ).to_dict()

    async def get_rate_board(self) -> dict:
        """Get the headline rates: mortgages, fed funds, treasuries, inflation."""
        quotes = await self.rates.get_board()
        notes = []
        for q in quotes:
            notes.extend(_synthetic_note(q))
        return ToolResult(
            summary={q.key: q.percent for q in quotes},
            detail={"rates": [q.to_dict() for q in quotes]},
            citations=[_citation(q) for q in quotes],
            notes=list(dict.fromkeys(notes)),   # de-duplicate
        ).to_dict()

    async def get_rate_history(self, rate_key: str, months: int = 24) -> dict:
        """Historical values for a rate, with a chart.

        Args:
            rate_key: A key from `list_available_rates`.
            months: How far back to look. Defaults to 24.
        """
        end = date.today()
        start = end - timedelta(days=int(months * 30.44))
        series = await self.rates.get_series(rate_key, start, end)

        values = [o.value for o in series.observations]
        # A daily series over two years is ~500 points, which bloats the MCP
        # payload and renders as a solid smear anyway. Thin it for the chart
        # while keeping the summary statistics over the full data.
        points = [(o.as_of.isoformat(), o.value) for o in series.observations]
        if len(points) > MAX_CHART_POINTS:
            step = len(points) // MAX_CHART_POINTS + 1
            thinned = points[::step]
            if thinned[-1] != points[-1]:
                thinned.append(points[-1])   # always keep the latest observation
            points = thinned

        chart = charts.rate_history_chart(
            series.label, points,
            chart_id=f"history-{rate_key}",
            source=series.source,
        )
        summary = {
            "label": series.label,
            "observations": len(values),
            "latest_percent": pct(values[-1]) if values else None,
            "min_percent": pct(min(values)) if values else None,
            "max_percent": pct(max(values)) if values else None,
        }
        return ToolResult(
            summary=summary,
            detail=series.to_dict(),
            charts=[chart.to_dict()],
            citations=[Citation(series.label, series.source,
                                series.observations[-1].as_of.isoformat() if values else "n/a",
                                series.citation_url, series.freshness.value)],
        ).to_dict()

    # ------------------------------------------------------------------
    # Mortgage
    # ------------------------------------------------------------------

    async def calculate_mortgage(
        self,
        loan_amount: float | None = None,
        annual_rate_percent: float | None = None,
        term_years: int = 30,
        home_price: float | None = None,
        down_payment: float | None = None,
        extra_monthly_payment: float = 0,
        extra_annual_payment: float = 0,
    ) -> dict:
        """Work out a mortgage payment and full amortization schedule.

        Supply either `loan_amount`, or `home_price` together with
        `down_payment`. If `annual_rate_percent` is omitted, the current
        national average for the matching term is fetched and used.

        Args:
            loan_amount: Amount borrowed.
            annual_rate_percent: Rate as a percent, e.g. 6.5. Omit to use live.
            term_years: Loan term, typically 30 or 15.
            home_price: Purchase price, if deriving the loan amount.
            down_payment: Cash down, if deriving the loan amount.
            extra_monthly_payment: Extra principal paid every month.
            extra_annual_payment: Extra principal paid once a year.
        """
        assumptions: list[Assumption] = []
        citations: list[Citation] = []
        notes: list[str] = []

        if loan_amount is None:
            if home_price is None:
                raise ValueError("supply either loan_amount, or home_price and down_payment")
            down = down_payment or 0.0
            loan_amount = float(home_price) - float(down)
            assumptions.append(Assumption(
                "loan_amount", money(loan_amount),
                f"Derived from a ${money(home_price):,.0f} price minus "
                f"${money(down):,.0f} down.", user_supplied=False))
            if down_payment is None:
                notes.append("No down payment given, so the full price is financed.")

        if loan_amount <= 0:
            raise ValueError("loan_amount must be positive")

        if annual_rate_percent is None:
            key = "mortgage_15y" if term_years <= 20 else "mortgage_30y"
            q = await self.rates.get_rate(key)
            rate = q.value
            citations.append(_citation(q))
            notes.extend(_synthetic_note(q))
            assumptions.append(Assumption(
                "annual_rate_percent", q.percent,
                f"Current {q.label} from {q.source}, as of {q.as_of}.",
                user_supplied=False))
        else:
            rate = float(annual_rate_percent) / 100.0
            assumptions.append(Assumption(
                "annual_rate_percent", float(annual_rate_percent),
                "Rate supplied by the user.", user_supplied=True))

        result = amortization_schedule(
            loan_amount, rate, int(term_years) * 12,
            extra_monthly=extra_monthly_payment,
            extra_annual=extra_annual_payment,
        )

        summary = {
            "monthly_payment": money(result.monthly_payment),
            "loan_amount": money(result.principal),
            "annual_rate_percent": pct(rate),
            "term_years": term_years,
            "total_interest": money(result.total_interest),
            "total_paid": money(result.total_paid),
            "payoff_years": result.payoff_years,
        }
        if result.months_saved:
            summary["months_saved_from_extra_payments"] = result.months_saved
            summary["interest_saved_from_extra_payments"] = money(result.interest_saved)
            notes.append(
                f"Paying extra clears the loan {result.months_saved} months "
                f"early and saves ${money(result.interest_saved):,.2f} in interest."
            )

        return ToolResult(
            summary=summary,
            detail={
                "schedule_yearly": [
                    {"year": int(r.year), "balance": money(r.balance),
                     "interest_paid": money(r.cumulative_interest),
                     "principal_paid": money(r.cumulative_principal)}
                    for r in result.yearly()
                ],
            },
            charts=[
                charts.mortgage_balance_chart(result).to_dict(),
                charts.mortgage_split_chart(result).to_dict(),
            ],
            assumptions=assumptions, citations=citations, notes=notes,
        ).to_dict()

    async def compare_mortgage_options(self, options: list[dict]) -> dict:
        """Compare several mortgage scenarios on one chart.

        Args:
            options: Each entry may set `label`, `loan_amount`,
                `annual_rate_percent`, `term_years`, and
                `extra_monthly_payment`. Missing rates are fetched live.
        """
        if not options or len(options) < 2:
            raise ValueError("supply at least two options to compare")
        if len(options) > 4:
            raise ValueError("compare at most four options at a time")

        runs, rows, citations, notes = [], [], [], []
        for i, opt in enumerate(options):
            term = int(opt.get("term_years", 30))
            amount = opt.get("loan_amount")
            if amount is None:
                raise ValueError(f"option {i} is missing loan_amount")

            if opt.get("annual_rate_percent") is None:
                key = "mortgage_15y" if term <= 20 else "mortgage_30y"
                q = await self.rates.get_rate(key)
                rate = q.value
                citations.append(_citation(q))
                notes.extend(_synthetic_note(q))
            else:
                rate = float(opt["annual_rate_percent"]) / 100.0

            res = amortization_schedule(
                amount, rate, term * 12,
                extra_monthly=opt.get("extra_monthly_payment", 0) or 0,
            )
            label = opt.get("label") or f"{term}-year at {pct(rate):.2f}%"
            runs.append((label, [(int(r.year), float(r.balance)) for r in res.yearly()]))
            rows.append({
                "label": label,
                "monthly_payment": money(res.monthly_payment),
                "total_interest": money(res.total_interest),
                "total_paid": money(res.total_paid),
                "payoff_years": res.payoff_years,
                "annual_rate_percent": pct(rate),
            })

        cheapest = min(rows, key=lambda r: r["total_interest"])
        lowest_payment = min(rows, key=lambda r: r["monthly_payment"])
        chart = charts.scenario_compare_chart(
            runs, chart_id="mortgage-compare",
            title="Remaining balance by scenario", x_label="Year",
            footnote=(
                f"{cheapest['label']} costs the least interest overall; "
                f"{lowest_payment['label']} has the smallest monthly payment."
            ),
        )
        return ToolResult(
            summary={
                "lowest_total_interest": cheapest["label"],
                "lowest_monthly_payment": lowest_payment["label"],
                "options": rows,
            },
            detail={"options": rows},
            charts=[chart.to_dict()],
            citations=citations,
            notes=list(dict.fromkeys(notes)),
        ).to_dict()

    # ------------------------------------------------------------------
    # Savings
    # ------------------------------------------------------------------

    async def calculate_savings(
        self,
        initial_deposit: float = 0,
        apy_percent: float | None = None,
        years: float = 10,
        monthly_contribution: float = 0,
        annual_contribution_growth_percent: float = 0,
        inflation_rate_percent: float | None = None,
        adjust_for_inflation: bool = True,
    ) -> dict:
        """Project a savings or CD balance, with contributions and compounding.

        If `apy_percent` is omitted, the current federal funds rate is used as a
        stand-in for a competitive high-yield savings account, which is flagged
        as an assumption because it is a proxy rather than a quoted bank rate.

        Args:
            initial_deposit: Starting balance.
            apy_percent: Annual percentage yield, e.g. 4.25. Omit to estimate.
            years: How long to project.
            monthly_contribution: Added every month.
            annual_contribution_growth_percent: Raise contributions yearly.
            inflation_rate_percent: Override the inflation assumption.
            adjust_for_inflation: Also report the value in today's dollars.
        """
        assumptions: list[Assumption] = []
        citations: list[Citation] = []
        notes: list[str] = []

        if apy_percent is None:
            q = await self.rates.get_rate("fed_funds")
            apy = max(0.0, q.value + SAVINGS_SPREAD_TO_FED_FUNDS)
            citations.append(_citation(q))
            notes.extend(_synthetic_note(q))
            notes.append(
                "No APY was given, so a competitive high-yield savings account "
                "is estimated from the federal funds rate. Actual bank APYs "
                "vary and a quoted rate should be used when known."
            )
            assumptions.append(Assumption(
                "apy_percent", pct(apy),
                f"Estimated from {q.label} ({q.percent}% as of {q.as_of}) "
                f"minus a {abs(SAVINGS_SPREAD_TO_FED_FUNDS)*100:.2f}pt spread.",
                user_supplied=False))
        else:
            apy = float(apy_percent) / 100.0
            assumptions.append(Assumption(
                "apy_percent", float(apy_percent), "APY supplied by the user.",
                user_supplied=True))

        inflation = None
        if adjust_for_inflation:
            if inflation_rate_percent is not None:
                inflation = float(inflation_rate_percent) / 100.0
                assumptions.append(Assumption(
                    "inflation_rate_percent", float(inflation_rate_percent),
                    "Inflation supplied by the user.", user_supplied=True))
            else:
                try:
                    q = await self.rates.get_rate("inflation_expectation_10y")
                    inflation = q.value
                    citations.append(_citation(q))
                    assumptions.append(Assumption(
                        "inflation_rate_percent", q.percent,
                        f"Market-implied expectation from {q.label}, "
                        f"as of {q.as_of}.", user_supplied=False))
                except Exception:
                    inflation = DEFAULT_INFLATION
                    assumptions.append(Assumption(
                        "inflation_rate_percent", pct(DEFAULT_INFLATION),
                        "Long-run default; live expectation unavailable.",
                        user_supplied=False))

        proj = project_savings(
            initial_deposit, apy, float(years),
            rate_kind="apy",
            contribution=monthly_contribution,
            contributions_per_year=12,
            annual_contribution_growth=float(annual_contribution_growth_percent) / 100.0,
            inflation_rate=inflation,
        )

        summary = {
            "final_balance": money(proj.final_balance),
            "total_deposited": money(proj.total_deposited),
            "total_interest": money(proj.total_interest),
            "apy_percent": pct(proj.effective_annual_rate),
            "years": proj.years,
        }
        if proj.real_final_balance is not None:
            summary["value_in_todays_dollars"] = money(proj.real_final_balance)

        chart_specs = [charts.savings_growth_chart(proj).to_dict()]
        real_chart = charts.real_vs_nominal_chart(proj)
        if real_chart:
            chart_specs.append(real_chart.to_dict())

        return ToolResult(
            summary=summary,
            detail={"yearly": [
                {"year": round(r.year, 2), "balance": money(r.closing_balance),
                 "interest_to_date": money(r.cumulative_interest),
                 "deposits_to_date": money(proj.principal + r.cumulative_contributions)}
                for r in proj.yearly()
            ]},
            charts=chart_specs,
            assumptions=assumptions, citations=citations, notes=notes,
        ).to_dict()

    # ------------------------------------------------------------------
    # Capital gains
    # ------------------------------------------------------------------

    async def calculate_capital_gains(
        self,
        sale_proceeds: float,
        cost_basis: float,
        holding_period_days: int | None = None,
        is_long_term: bool | None = None,
        other_taxable_income: float = 0,
        filing_status: str = "single",
        state_tax_rate_percent: float = 0.0,
    ) -> dict:
        """Estimate federal tax on a realized capital gain.

        The gain is stacked on top of `other_taxable_income`, so a gain that
        straddles a bracket threshold is split correctly instead of being taxed
        at one headline rate.

        Args:
            sale_proceeds: What the asset sold for.
            cost_basis: What was paid for it.
            holding_period_days: Days held; over 365 is long-term.
            is_long_term: Set directly instead of giving a holding period.
            other_taxable_income: The filer's other taxable income.
            filing_status: single, married_jointly, married_separately,
                or head_of_household.
            state_tax_rate_percent: Flat state rate on the gain, e.g. 9.3.
        """
        if holding_period_days is None and is_long_term is None:
            is_long_term = True   # Most questions are about long-term holdings.

        r = capital_gains_tax(
            sale_proceeds, cost_basis,
            holding_period_days=holding_period_days,
            is_long_term=is_long_term,
            ordinary_taxable_income=other_taxable_income,
            filing_status=filing_status,
            state_rate=float(state_tax_rate_percent) / 100.0,
        )

        assumptions = [Assumption(
            "tax_year", TAX_YEAR,
            f"US federal brackets for tax year {TAX_YEAR}.", user_supplied=False)]
        if holding_period_days is None:
            assumptions.append(Assumption(
                "is_long_term", bool(r.is_long_term),
                "Assumed a long-term holding because no period was given.",
                user_supplied=False))

        breakdown = [{"rate_percent": round(s.rate * 100, 2),
                      "amount_taxed": money(s.amount_taxed),
                      "tax": money(s.tax)} for s in r.brackets]

        return ToolResult(
            summary={
                "gain": money(r.gain),
                "federal_tax": money(r.federal_tax),
                "net_investment_income_tax": money(r.niit),
                "state_tax": money(r.state_tax),
                "total_tax": money(r.total_tax),
                "net_proceeds": money(r.net_proceeds),
                "effective_rate_percent": round(r.effective_rate * 100, 2),
                "marginal_rate_percent": round(r.marginal_rate * 100, 2),
                "treatment": "long-term" if r.is_long_term else "short-term",
            },
            detail={"bracket_breakdown": breakdown, "filing_status": r.filing_status,
                    "tax_year": r.tax_year, "source": r.source},
            assumptions=assumptions,
            notes=r.notes + [
                "Federal estimate only. It ignores state specifics beyond a flat "
                "rate, AMT, loss carryforwards, and anything else on a real "
                "return. Confirm with a tax professional before acting."
            ],
        ).to_dict()
