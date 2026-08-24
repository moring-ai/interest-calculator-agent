"""Money primitives.

Currency is `Decimal` everywhere in this package; rates are plain floats.
Every value that represents dollars is quantized to cents at the point it is
produced, so schedules amortize the way a real lender's ledger does rather
than drifting by fractions of a cent over 360 periods.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, getcontext
from typing import Union

# Enough precision that intermediate compounding never loses cents.
getcontext().prec = 28

Numeric = Union[int, float, str, Decimal]

CENT = Decimal("0.01")
ZERO = Decimal("0")


def D(value: Numeric) -> Decimal:
    """Coerce to Decimal without inheriting binary-float noise.

    Floats are routed through `repr` so 0.1 becomes Decimal("0.1") rather than
    Decimal("0.1000000000000000055511151231257827021181583404541015625").
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(value)


def cents(value: Numeric) -> Decimal:
    """Round to the nearest cent, half away from zero (how lenders round)."""
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def as_float(value: Numeric) -> float:
    """Boundary helper: Decimal -> float for JSON serialization."""
    return float(D(value))
