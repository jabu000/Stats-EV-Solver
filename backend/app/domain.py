"""Core domain vocabulary shared by providers, models, pricing and the API.

Underdog's own stat keys are messy and change over time, so everything is normalised
into the `Market` enum the moment it enters the system. Two of the markets the user
asked for are not really "over/under" markets at Underdog:

* **1+ Hit** is a 0.5 line on the `hits` stat.
* **Anytime TD** is a 0.5 line on a rush+rec touchdown stat.

Both normalise to "probability the count is at least 1", which is what
`Market.is_threshold_market` flags.
"""

from __future__ import annotations

from enum import Enum


class League(str, Enum):
    MLB = "MLB"
    NFL = "NFL"
    CFB = "CFB"

    @property
    def is_football(self) -> bool:
        return self in (League.NFL, League.CFB)


class Market(str, Enum):
    # MLB
    STRIKEOUTS = "strikeouts"
    HITS_1_PLUS = "hits_1_plus"
    # Football
    RECEIVING_YARDS = "receiving_yards"
    RUSHING_YARDS = "rushing_yards"
    PASSING_YARDS = "passing_yards"
    ANYTIME_TD = "anytime_td"
    RECEPTIONS = "receptions"

    @property
    def label(self) -> str:
        return _MARKET_LABELS[self]

    @property
    def is_threshold_market(self) -> bool:
        """True when the market is really "at least one", not a tunable line."""
        return self in (Market.HITS_1_PLUS, Market.ANYTIME_TD)

    @property
    def is_continuous(self) -> bool:
        """Yardage markets are continuous; the rest are counts."""
        return self in (
            Market.RECEIVING_YARDS,
            Market.RUSHING_YARDS,
            Market.PASSING_YARDS,
        )


_MARKET_LABELS: dict[Market, str] = {
    Market.STRIKEOUTS: "Strikeouts",
    Market.HITS_1_PLUS: "1+ Hit",
    Market.RECEIVING_YARDS: "Receiving Yards",
    Market.RUSHING_YARDS: "Rushing Yards",
    Market.PASSING_YARDS: "Passing Yards",
    Market.ANYTIME_TD: "Anytime TD",
    Market.RECEPTIONS: "Receptions",
}

MARKETS_BY_LEAGUE: dict[League, tuple[Market, ...]] = {
    League.MLB: (Market.STRIKEOUTS, Market.HITS_1_PLUS),
    League.NFL: (
        Market.RECEIVING_YARDS,
        Market.RUSHING_YARDS,
        Market.PASSING_YARDS,
        Market.ANYTIME_TD,
        Market.RECEPTIONS,
    ),
    League.CFB: (
        Market.RECEIVING_YARDS,
        Market.RUSHING_YARDS,
        Market.PASSING_YARDS,
        Market.ANYTIME_TD,
        Market.RECEPTIONS,
    ),
}


class Side(str, Enum):
    """Which way a pick is taken. Underdog calls these higher/lower."""

    HIGHER = "higher"
    LOWER = "lower"

    @property
    def label(self) -> str:
        return "Higher" if self is Side.HIGHER else "Lower"


class Handedness(str, Enum):
    LEFT = "L"
    RIGHT = "R"
    SWITCH = "S"


class RoofState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    DOME = "dome"
    RETRACTABLE = "retractable"

    @property
    def is_indoors(self) -> bool:
        return self in (RoofState.CLOSED, RoofState.DOME)
