"""MCP server exposing the deterministic interest-calculator tools.

This replaces the AgentCore Gateway that previously fronted these tools. It is a
plain MCP server, so it can be run by ToolHive and consumed by anything that
speaks MCP -- currently the agent on AgentCore Runtime and the interface
backend, which uses it for the no-LLM calculator endpoints.

The tool docstrings and parameter descriptions are the contract the language
model reads, so they are written for that reader: they say what a tool is for,
what units it expects, and -- for the calculators -- that the model must not do
the arithmetic itself. FastMCP derives the JSON Schema from the type hints and
`Field` descriptions below, which is why there is no hand-maintained schema file
any more.
"""

from __future__ import annotations

import logging
import os
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from interest_tools import InterestTools
from rate_feed import FredProvider, MockProvider, RateService

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

# Stateless HTTP keeps every request independent, which is what we want behind a
# reverse proxy: no session affinity to preserve, and both clients (the agent
# and the backend) can call concurrently without sharing state.
mcp = FastMCP(
    "interest-calculator-tools",
    instructions=(
        "Deterministic mortgage, savings, and capital-gains calculators backed "
        "by live interest rate data. Every number these tools return is "
        "computed, never estimated."
    ),
    # Bind all interfaces inside the container. The override is deliberately
    # NOT called MCP_HOST: ToolHive injects MCP_HOST=127.0.0.1 into every
    # workload it runs, which would pin uvicorn to the container's loopback
    # where ToolHive's own ingress proxy cannot reach it -- the server comes up
    # healthy and every request still fails with 502.
    host=os.environ.get("MCP_BIND_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_BIND_PORT", os.environ.get("MCP_PORT", "8000"))),
    stateless_http=True,
)

_service = RateService(
    primary=FredProvider(),                       # reads FRED_API_KEY
    fallback=MockProvider(),
    allow_synthetic=os.environ.get("ALLOW_SYNTHETIC_RATES", "true").lower() == "true",
)
_tools = InterestTools(_service)
log.info("rate service ready: %s", _service.status())


RateKey = Annotated[str, Field(
    description=(
        "Rate key from list_available_rates, e.g. mortgage_30y, mortgage_15y, "
        "fed_funds, prime_rate, sofr, treasury_2y, treasury_10y, "
        "inflation_expectation_10y"
    ),
)]


class MortgageOption(BaseModel):
    """One scenario in a mortgage comparison."""

    label: str | None = Field(None, description="Short name shown in the chart legend.")
    loan_amount: float = Field(..., gt=0, description="Amount borrowed, in dollars.")
    annual_rate_percent: float | None = Field(
        None, description="Rate as a percent. Omit to use the current market average.")
    term_years: int = Field(30, ge=1, le=50, description="Term in years.")
    extra_monthly_payment: float = Field(
        0, ge=0, description="Extra principal paid every month.")


# --------------------------------------------------------------------------
# Rate discovery
# --------------------------------------------------------------------------

@mcp.tool()
async def list_available_rates() -> dict:
    """List every interest rate this server can look up, with key and category.

    Call this first when you are unsure which rate key to use.
    """
    return await _tools.list_available_rates()


@mcp.tool()
async def get_current_rate(rate_key: RateKey) -> dict:
    """Get the latest published value for a single interest rate.

    Returns the rate with its source and observation date. Use this whenever the
    user asks what a rate is right now.
    """
    return await _tools.get_current_rate(rate_key)


@mcp.tool()
async def get_rate_board() -> dict:
    """Get all the headline rates at once.

    Covers 30- and 15-year mortgages, the federal funds rate, 2- and 10-year
    treasuries, and expected inflation. Use this for a general "what are rates
    doing" question.
    """
    return await _tools.get_rate_board()


@mcp.tool()
async def get_rate_history(
    rate_key: RateKey,
    months: Annotated[int, Field(24, ge=1, le=600,
        description="How many months of history to return.")] = 24,
) -> dict:
    """Get the recent history of one rate, with a ready-to-render chart.

    Use this when the user asks whether a rate has gone up or down, or wants to
    see a trend.
    """
    return await _tools.get_rate_history(rate_key, months)


# --------------------------------------------------------------------------
# Mortgage
# --------------------------------------------------------------------------

@mcp.tool()
async def calculate_mortgage(
    loan_amount: Annotated[float | None, Field(None, description="Amount borrowed, in dollars.")] = None,
    annual_rate_percent: Annotated[float | None, Field(None, description=
        "Interest rate as a percent, e.g. 6.5. Omit to use the current market average.")] = None,
    term_years: Annotated[int, Field(30, ge=1, le=50, description=
        "Loan term in years, typically 30 or 15.")] = 30,
    home_price: Annotated[float | None, Field(None, description=
        "Purchase price, if the loan amount should be derived.")] = None,
    down_payment: Annotated[float | None, Field(None, description=
        "Cash paid down, if the loan amount should be derived.")] = None,
    extra_monthly_payment: Annotated[float, Field(0, ge=0, description=
        "Extra principal paid every month.")] = 0,
    extra_annual_payment: Annotated[float, Field(0, ge=0, description=
        "Extra principal paid once per year.")] = 0,
) -> dict:
    """Calculate a mortgage payment and full amortization schedule.

    Returns charts of the balance over time and of how each year's payments
    split between interest and principal. Supply either `loan_amount`, or
    `home_price` together with `down_payment`. Omit `annual_rate_percent` to use
    the current national average for that term.

    NEVER compute a mortgage payment yourself; always call this.
    """
    return await _tools.calculate_mortgage(
        loan_amount=loan_amount,
        annual_rate_percent=annual_rate_percent,
        term_years=term_years,
        home_price=home_price,
        down_payment=down_payment,
        extra_monthly_payment=extra_monthly_payment,
        extra_annual_payment=extra_annual_payment,
    )


@mcp.tool()
async def compare_mortgage_options(
    options: Annotated[list[MortgageOption], Field(
        description="Between two and four scenarios to compare.",
        min_length=2, max_length=4)],
) -> dict:
    """Compare two to four mortgage scenarios side by side on a single chart.

    Reports which option has the lowest monthly payment and which costs the
    least interest overall. Use this for "30-year vs 15-year" or "should I pay
    extra" questions rather than making several separate calls.
    """
    return await _tools.compare_mortgage_options([o.model_dump() for o in options])


# --------------------------------------------------------------------------
# Savings
# --------------------------------------------------------------------------

@mcp.tool()
async def calculate_savings(
    initial_deposit: Annotated[float, Field(0, ge=0, description="Starting balance in dollars.")] = 0,
    apy_percent: Annotated[float | None, Field(None, description=
        "Annual percentage yield as a percent, e.g. 4.25. Omit to estimate from current rates.")] = None,
    years: Annotated[float, Field(10, gt=0, le=100, description="How many years to project.")] = 10,
    monthly_contribution: Annotated[float, Field(0, ge=0, description="Amount added every month.")] = 0,
    annual_contribution_growth_percent: Annotated[float, Field(0, ge=0, le=100, description=
        "Percent to increase the monthly contribution each year.")] = 0,
    inflation_rate_percent: Annotated[float | None, Field(None, description=
        "Override the inflation assumption, as a percent.")] = None,
    adjust_for_inflation: Annotated[bool, Field(True, description=
        "Also report the balance in today's dollars.")] = True,
) -> dict:
    """Project how a savings balance, CD, or investment grows over time.

    Handles optional monthly contributions and returns charts of the balance and
    of its inflation-adjusted purchasing power. Omit `apy_percent` to estimate a
    competitive high-yield savings rate from the current federal funds rate.

    NEVER compute compound growth yourself; always call this.
    """
    return await _tools.calculate_savings(
        initial_deposit=initial_deposit,
        apy_percent=apy_percent,
        years=years,
        monthly_contribution=monthly_contribution,
        annual_contribution_growth_percent=annual_contribution_growth_percent,
        inflation_rate_percent=inflation_rate_percent,
        adjust_for_inflation=adjust_for_inflation,
    )


# --------------------------------------------------------------------------
# Capital gains
# --------------------------------------------------------------------------

@mcp.tool()
async def calculate_capital_gains(
    sale_proceeds: Annotated[float, Field(ge=0, description="What the asset sold for, in dollars.")],
    cost_basis: Annotated[float, Field(ge=0, description="What was originally paid for it, in dollars.")],
    holding_period_days: Annotated[int | None, Field(None, ge=0, description=
        "Days the asset was held. More than 365 qualifies as long-term.")] = None,
    is_long_term: Annotated[bool | None, Field(None, description=
        "Set directly instead of supplying a holding period.")] = None,
    other_taxable_income: Annotated[float, Field(0, ge=0, description=
        "The filer's other taxable income for the year. Strongly affects the result.")] = 0,
    filing_status: Annotated[Literal[
        "single", "married_jointly", "married_separately", "head_of_household"
    ], Field("single", description="Federal filing status.")] = "single",
    state_tax_rate_percent: Annotated[float, Field(0, ge=0, le=20, description=
        "Flat state tax rate on the gain as a percent, e.g. 9.3 for California.")] = 0,
) -> dict:
    """Estimate federal tax on a realized capital gain.

    The gain is stacked on top of the filer's other income, so a gain that
    straddles a bracket threshold is split correctly instead of being taxed at
    one headline rate. Reports the bracket-by-bracket breakdown, net investment
    income tax, and effective and marginal rates.

    NEVER estimate capital gains tax yourself; always call this.
    """
    return await _tools.calculate_capital_gains(
        sale_proceeds=sale_proceeds,
        cost_basis=cost_basis,
        holding_period_days=holding_period_days,
        is_long_term=is_long_term,
        other_taxable_income=other_taxable_income,
        filing_status=filing_status,
        state_tax_rate_percent=state_tax_rate_percent,
    )


if __name__ == "__main__":
    log.info("starting MCP server on %s:%s", mcp.settings.host, mcp.settings.port)
    mcp.run(transport="streamable-http")
