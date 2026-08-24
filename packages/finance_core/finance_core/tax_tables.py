"""US federal tax tables, kept as data so they can be updated without touching
any calculation logic.

Source: IRS inflation adjustments for tax year 2026 (Rev. Proc. 2025-32), as
tabulated by the Tax Foundation. Married-filing-separately ordinary brackets
follow the statutory construction of one half of the married-filing-jointly
bracket widths.

These are *federal* figures only and deliberately ignore state tax, AMT, the
net operating loss rules, and every other wrinkle of a real return. Anything
this package produces is an estimate for comparison, not tax advice.
"""

from __future__ import annotations

from typing import Literal

FilingStatus = Literal[
    "single", "married_jointly", "married_separately", "head_of_household"
]

TAX_YEAR = 2026
SOURCE = "IRS Rev. Proc. 2025-32 (tax year 2026)"

# Each entry is (upper_bound_of_bracket, marginal_rate). math.inf caps the top.
INF = float("inf")

ORDINARY_BRACKETS: dict[str, list[tuple[float, float]]] = {
    "single": [
        (12_400, 0.10), (50_400, 0.12), (105_700, 0.22), (201_775, 0.24),
        (256_225, 0.32), (640_600, 0.35), (INF, 0.37),
    ],
    "married_jointly": [
        (24_800, 0.10), (100_800, 0.12), (211_400, 0.22), (403_550, 0.24),
        (512_450, 0.32), (768_700, 0.35), (INF, 0.37),
    ],
    "married_separately": [
        (12_400, 0.10), (50_400, 0.12), (105_700, 0.22), (201_775, 0.24),
        (256_225, 0.32), (384_350, 0.35), (INF, 0.37),
    ],
    "head_of_household": [
        (17_700, 0.10), (67_450, 0.12), (105_700, 0.22), (201_775, 0.24),
        (256_200, 0.32), (640_600, 0.35), (INF, 0.37),
    ],
}

# Long-term capital gains / qualified dividends.
LTCG_BRACKETS: dict[str, list[tuple[float, float]]] = {
    "single": [(49_450, 0.00), (545_500, 0.15), (INF, 0.20)],
    "married_jointly": [(98_900, 0.00), (613_700, 0.15), (INF, 0.20)],
    "married_separately": [(49_450, 0.00), (306_850, 0.15), (INF, 0.20)],
    "head_of_household": [(66_200, 0.00), (579_600, 0.15), (INF, 0.20)],
}

STANDARD_DEDUCTION: dict[str, float] = {
    "single": 16_100,
    "married_jointly": 32_200,
    "married_separately": 16_100,
    "head_of_household": 24_150,
}

# Net Investment Income Tax: 3.8% on the lesser of net investment income or
# the amount by which MAGI exceeds the threshold. Not inflation-indexed.
NIIT_RATE = 0.038
NIIT_THRESHOLD: dict[str, float] = {
    "single": 200_000,
    "married_jointly": 250_000,
    "married_separately": 125_000,
    "head_of_household": 200_000,
}

# A gain is long-term only if the asset was held MORE than one year.
LONG_TERM_HOLDING_DAYS = 365


def validate_status(status: str) -> str:
    if status not in ORDINARY_BRACKETS:
        raise ValueError(
            f"unknown filing status {status!r}; expected one of "
            f"{sorted(ORDINARY_BRACKETS)}"
        )
    return status
