"""The shape every tool and every calculator endpoint returns.

One envelope for all of them, because the frontend renders an agent answer and
a direct calculator call with the same component. The agent gets the same
structure as JSON, which is what lets it describe results without ever having
to restate a number it might get wrong.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Citation:
    """Where a number came from. Attached to anything sourced externally."""

    label: str
    source: str
    as_of: str
    url: str | None = None
    freshness: str = "live"


@dataclass
class Assumption:
    """An input the caller did not supply that the tool chose on their behalf.

    Surfacing these is what keeps a projection honest: a user who never
    mentioned inflation still needs to see that 2.5% was assumed.
    """

    key: str
    value: Any
    description: str
    #: True when the value came from the caller rather than a default.
    user_supplied: bool = False


@dataclass
class ToolResult:
    #: Short headline figures, already formatted for display.
    summary: dict[str, Any]
    #: Full structured result for anything that wants the detail.
    detail: dict[str, Any] = field(default_factory=dict)
    #: Chart specs, ready to render.
    charts: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    #: Caveats the agent should repeat to the user.
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "detail": self.detail,
            "charts": self.charts,
            "assumptions": [asdict(a) for a in self.assumptions],
            "citations": [asdict(c) for c in self.citations],
            "notes": self.notes,
        }


def money(value) -> float:
    """Round a Decimal or float to cents for JSON."""
    return round(float(value), 2)


def pct(value: float) -> float:
    """Decimal fraction -> percentage number, for display."""
    return round(value * 100, 4)
