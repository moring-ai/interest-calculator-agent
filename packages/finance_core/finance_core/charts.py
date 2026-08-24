"""Chart specifications: the contract between the math and the UI.

A spec is plain JSON describing *what* to plot, never how to style it. The
frontend owns colors, fonts, and interaction; this module owns the numbers and
their labels. Keeping the builders here rather than in the API layer means the
agent's tools and the REST endpoints emit byte-identical charts for the same
inputs, so a chart rendered from a chat answer matches one rendered from the
calculator panel.

The `data` array is a list of row objects sharing one x key, which is what
Recharts and most charting libraries consume directly with no reshaping.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any, Literal

from .compounding import SavingsProjection
from .money import as_float
from .mortgage import AmortizationResult

ChartType = Literal["line", "area", "bar", "stacked-bar", "stacked-area"]
ValueFormat = Literal["currency", "percent", "number"]


@dataclass
class SeriesSpec:
    key: str
    label: str
    #: Semantic role, so the frontend can assign palette slots consistently
    #: across charts ("principal" is always the same color everywhere).
    role: str = "default"


@dataclass
class ChartSpec:
    id: str
    type: ChartType
    title: str
    x_key: str
    x_label: str
    y_label: str
    series: list[SeriesSpec]
    data: list[dict[str, Any]]
    y_format: ValueFormat = "currency"
    x_format: ValueFormat = "number"
    subtitle: str | None = None
    footnote: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _f(value: Decimal | float | int) -> float:
    return as_float(value) if isinstance(value, Decimal) else float(value)


# --------------------------------------------------------------------------
# Mortgage
# --------------------------------------------------------------------------

def mortgage_balance_chart(result: AmortizationResult, chart_id: str = "mortgage-balance") -> ChartSpec:
    """Remaining balance and cumulative interest, year by year."""
    rows = [{
        "year": r.year,
        "balance": _f(r.balance),
        "interest_paid": _f(r.cumulative_interest),
        "principal_paid": _f(r.cumulative_principal),
    } for r in result.yearly()]

    return ChartSpec(
        id=chart_id,
        type="line",
        title="Loan balance over time",
        subtitle=f"${_f(result.principal):,.0f} at {result.annual_rate:.3%} "
                 f"over {result.term_months // 12} years",
        x_key="year", x_label="Year", y_label="Dollars",
        series=[
            SeriesSpec("balance", "Remaining balance", role="balance"),
            SeriesSpec("interest_paid", "Interest paid to date", role="interest"),
            SeriesSpec("principal_paid", "Principal paid to date", role="principal"),
        ],
        data=rows,
        footnote=f"Total interest over the life of the loan: "
                 f"${_f(result.total_interest):,.2f}",
    )


def mortgage_split_chart(result: AmortizationResult, chart_id: str = "mortgage-split") -> ChartSpec:
    """How each year's payments divide between interest and principal.

    This is the chart that actually explains amortization: early years are
    almost entirely interest, and the crossover point is visible.
    """
    by_year: dict[int, dict[str, float]] = {}
    for r in result.rows:
        year = (r.period - 1) // 12 + 1
        bucket = by_year.setdefault(year, {"year": year, "interest": 0.0, "principal": 0.0})
        bucket["interest"] += _f(r.interest)
        bucket["principal"] += _f(r.principal)

    rows = [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in b.items()}
            for b in by_year.values()]

    return ChartSpec(
        id=chart_id,
        type="stacked-bar",
        title="Where each year's payments go",
        x_key="year", x_label="Year", y_label="Paid that year",
        series=[
            SeriesSpec("interest", "Interest", role="interest"),
            SeriesSpec("principal", "Principal", role="principal"),
        ],
        data=rows,
        footnote="Early payments are mostly interest; the crossover is where "
                 "more of each payment starts reducing the balance.",
    )


# --------------------------------------------------------------------------
# Savings / investment
# --------------------------------------------------------------------------

def savings_growth_chart(
    projection: SavingsProjection,
    chart_id: str = "savings-growth",
    title: str = "Balance growth",
) -> ChartSpec:
    """Deposits versus earned interest, stacked to show compounding."""
    principal = _f(projection.principal)
    rows = [{
        "year": round(r.year, 2),
        "deposits": round(principal + _f(r.cumulative_contributions), 2),
        "interest": _f(r.cumulative_interest),
        "balance": _f(r.closing_balance),
    } for r in projection.yearly()]

    return ChartSpec(
        id=chart_id,
        type="stacked-area",
        title=title,
        subtitle=f"{projection.effective_annual_rate:.3%} effective annual rate "
                 f"over {projection.years:g} years",
        x_key="year", x_label="Year", y_label="Balance",
        series=[
            SeriesSpec("deposits", "Money you put in", role="principal"),
            SeriesSpec("interest", "Interest earned", role="interest"),
        ],
        data=rows,
        footnote=f"Of the ${_f(projection.final_balance):,.2f} final balance, "
                 f"${_f(projection.total_interest):,.2f} is interest.",
    )


def real_vs_nominal_chart(
    projection: SavingsProjection, chart_id: str = "real-vs-nominal"
) -> ChartSpec | None:
    """Nominal balance against its inflation-adjusted purchasing power."""
    if projection.inflation_rate is None:
        return None

    infl = projection.inflation_rate
    rows = [{
        "year": round(r.year, 2),
        "nominal": _f(r.closing_balance),
        "real": round(_f(r.closing_balance) / ((1 + infl) ** r.year), 2),
    } for r in projection.yearly()]

    return ChartSpec(
        id=chart_id,
        type="line",
        title="Nominal balance vs. purchasing power",
        subtitle=f"Adjusted for {infl:.2%} annual inflation",
        x_key="year", x_label="Year", y_label="Dollars",
        series=[
            SeriesSpec("nominal", "Nominal balance", role="balance"),
            SeriesSpec("real", "In today's dollars", role="real"),
        ],
        data=rows,
        footnote="The gap is what inflation takes out of the headline number.",
    )


# --------------------------------------------------------------------------
# Comparison -- the chart that makes the product worth using
# --------------------------------------------------------------------------

def scenario_compare_chart(
    scenarios: list[tuple[str, list[tuple[float, float]]]],
    *,
    chart_id: str = "scenario-compare",
    title: str = "Scenario comparison",
    x_label: str = "Year",
    y_label: str = "Dollars",
    y_format: ValueFormat = "currency",
    footnote: str | None = None,
) -> ChartSpec:
    """Overlay several named (year, value) runs on shared axes.

    Args:
        scenarios: (label, [(x, y), ...]) pairs. Series keys are derived from
            the position so labels can contain any characters.
    """
    series = [SeriesSpec(f"s{i}", label, role=f"scenario{i}")
              for i, (label, _) in enumerate(scenarios)]

    merged: dict[float, dict[str, Any]] = {}
    for i, (_, points) in enumerate(scenarios):
        for x, y in points:
            merged.setdefault(x, {"x": x})[f"s{i}"] = round(float(y), 2)

    return ChartSpec(
        id=chart_id, type="line", title=title,
        x_key="x", x_label=x_label, y_label=y_label, y_format=y_format,
        series=series,
        data=[merged[k] for k in sorted(merged)],
        footnote=footnote,
    )


def rate_history_chart(
    label: str,
    observations: list[tuple[str, float]],
    *,
    chart_id: str = "rate-history",
    source: str | None = None,
) -> ChartSpec:
    """A single rate's history. `observations` are (iso_date, decimal_rate)."""
    return ChartSpec(
        id=chart_id, type="area", title=f"{label} over time",
        x_key="date", x_label="Date", y_label="Rate", y_format="percent",
        x_format="number",
        series=[SeriesSpec("rate", label, role="rate")],
        data=[{"date": d, "rate": round(v * 100, 4)} for d, v in observations],
        footnote=f"Source: {source}" if source else None,
    )
