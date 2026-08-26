"""Underdog entry payout structures, entry EV, and Kelly sizing.

The single most important thing this module encodes: **a Pick'em leg is not a coin
flip at even money.** A standard 3-pick pays 6x and needs all three legs to land, so
the break-even probability per leg is (1/6)^(1/3) = 55.0%, not 50%. A model that calls
a 53% leg "value" because it beats 50% is a losing model, and that mistake is the
default failure mode of prop tools.

Payout multipliers change -- Underdog runs promos, boosts and structure changes -- so
the tables here are defaults that the Settings tab overrides, never hard requirements.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.distributions import poisson_binomial_pmf
from app.schemas import CorrelationWarning, EntryLeg, EntryOutcome, EntryResponse

#: Standard ("power") play: every leg must win. legs -> multiplier.
DEFAULT_STANDARD_PAYOUTS: dict[int, float] = {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0}

#: Insured ("flex") play: a reduced payout for dropping one (or two) legs.
#: legs -> {number correct -> multiplier}.
DEFAULT_INSURED_PAYOUTS: dict[int, dict[int, float]] = {
    3: {3: 2.25, 2: 1.25},
    4: {4: 6.0, 3: 1.5},
    5: {5: 10.0, 4: 2.5, 3: 0.4},
}


@dataclass
class PayoutStructure:
    """User-configurable payout tables."""

    standard: dict[int, float] = field(
        default_factory=lambda: dict(DEFAULT_STANDARD_PAYOUTS)
    )
    insured: dict[int, dict[int, float]] = field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_INSURED_PAYOUTS.items()}
    )

    def table(self, entry_type: str, legs: int) -> dict[int, float]:
        """Payout multiplier by number of correct legs."""
        if entry_type == "insured":
            table = self.insured.get(legs)
            if table:
                return dict(table)
        multiplier = self.standard.get(legs)
        if multiplier is None:
            # Unlisted sizes fall back to the "double each extra leg" pattern Underdog
            # broadly follows, rather than refusing to price the entry.
            multiplier = 3.0 * (2.0 ** max(0, legs - 2))
        return {legs: multiplier}

    def supported_sizes(self, entry_type: str) -> list[int]:
        source = self.insured if entry_type == "insured" else self.standard
        return sorted(source.keys())


def break_even_probability(
    structure: PayoutStructure,
    entry_type: str = "standard",
    legs: int = 3,
    leg_multiplier: float = 1.0,
) -> float:
    """Per-leg probability needed to break even, assuming identical legs.

    For a standard entry this is the exact solution of `M * p^n = 1`. For an insured
    entry there is no closed form -- partial payouts make the equation a polynomial --
    so it is solved by bisection, which is fast and unconditionally stable here because
    entry EV is monotone in p.
    """
    table = structure.table(entry_type, legs)
    boosted = {k: v * max(leg_multiplier, 1e-9) for k, v in table.items()}

    if len(boosted) == 1 and legs in boosted:
        multiplier = boosted[legs]
        if multiplier <= 1.0:
            return 0.999999
        return float(multiplier ** (-1.0 / legs))

    low, high = 1e-6, 1.0 - 1e-6
    for _ in range(80):
        mid = (low + high) / 2.0
        pmf = poisson_binomial_pmf([mid] * legs)
        expected = sum(pmf[k] * boosted.get(k, 0.0) for k in range(legs + 1))
        if expected < 1.0:
            low = mid
        else:
            high = mid
    return float((low + high) / 2.0)


def kelly_fraction(probability: float, payout_multiplier: float) -> float:
    """Full-Kelly fraction of bankroll for a single all-or-nothing wager.

    `payout_multiplier` is gross (a 6x entry returns 6 for 1), so net odds are M - 1.
    Returns 0 for a bet with no edge -- Kelly never advises staking a negative-EV bet.
    """
    net_odds = payout_multiplier - 1.0
    if net_odds <= 0:
        return 0.0
    fraction = (probability * payout_multiplier - 1.0) / net_odds
    return max(0.0, min(fraction, 1.0))


def price_entry(
    legs: list[EntryLeg],
    *,
    structure: PayoutStructure | None = None,
    entry_type: str = "standard",
    stake: float = 10.0,
    bankroll: float = 1000.0,
    kelly_multiplier: float = 0.25,
) -> EntryResponse:
    """Full EV, payout distribution and Kelly stake for a slip."""
    structure = structure or PayoutStructure()
    notes: list[str] = []

    if not legs:
        return EntryResponse(
            legs=0, entry_type=entry_type, stake=stake, payout_table=[],
            expected_return=0.0, expected_profit=0.0, ev_percent=0.0,
            win_probability=0.0, kelly_stake=0.0, kelly_full=0.0,
            notes=["Add at least two legs to price an entry."],
        )

    count = len(legs)
    probabilities = [min(max(leg.probability, 1e-6), 1 - 1e-6) for leg in legs]

    # Boosted legs multiply the whole entry's payout.
    boost = 1.0
    for leg in legs:
        boost *= max(leg.payout_multiplier, 1e-9)
    if abs(boost - 1.0) > 1e-9:
        notes.append(f"Leg payout boosts multiply the entry payout by {boost:.2f}x.")

    table = structure.table(entry_type, count)
    if entry_type == "insured" and count not in structure.insured:
        notes.append(
            f"No insured payout table for a {count}-leg entry; priced as a standard entry."
        )

    # Legs are treated as independent here. Correlated legs make this optimistic for
    # positively-correlated slips, which is exactly why they are flagged below.
    pmf = poisson_binomial_pmf(probabilities)

    outcomes: list[EntryOutcome] = []
    expected_return = 0.0
    for correct in range(count + 1):
        multiplier = table.get(correct, 0.0) * boost
        probability = float(pmf[correct])
        contribution = probability * multiplier * stake
        expected_return += contribution
        if multiplier > 0 or correct == count:
            outcomes.append(
                EntryOutcome(
                    correct=correct,
                    probability=round(probability, 6),
                    multiplier=round(multiplier, 4),
                    contribution=round(contribution, 4),
                )
            )

    win_probability = float(
        sum(pmf[k] for k in range(count + 1) if table.get(k, 0.0) * boost >= 1.0)
    )
    top_multiplier = table.get(count, 0.0) * boost

    # Kelly on the all-or-nothing branch. For insured entries this understates the
    # optimal stake slightly, which is the safe direction to be wrong in.
    full_kelly = kelly_fraction(float(pmf[count]), top_multiplier)
    kelly_stake = round(full_kelly * kelly_multiplier * bankroll, 2)

    if expected_return <= stake:
        notes.append(
            "This entry is negative-EV at these probabilities -- Kelly recommends no stake."
        )

    return EntryResponse(
        legs=count,
        entry_type=entry_type,
        stake=stake,
        payout_table=sorted(outcomes, key=lambda o: -o.correct),
        expected_return=round(expected_return, 4),
        expected_profit=round(expected_return - stake, 4),
        ev_percent=round((expected_return / stake - 1.0) * 100.0, 3) if stake else 0.0,
        win_probability=round(win_probability, 5),
        kelly_stake=kelly_stake,
        kelly_full=round(full_kelly, 5),
        correlation_warnings=find_correlations(legs),
        notes=notes,
    )


def find_correlations(legs: list[EntryLeg]) -> list[CorrelationWarning]:
    """Flag legs whose outcomes are not independent.

    The independence assumption in `price_entry` is what makes the EV computable at
    all, but positively-correlated legs make a slip more volatile than the number
    suggests -- and Underdog restricts some of these combinations outright. Flagging is
    the honest middle ground between ignoring correlation and pretending to model a
    joint distribution we have no data to estimate.
    """
    warnings: list[CorrelationWarning] = []
    by_game: dict[str, list[EntryLeg]] = {}
    for leg in legs:
        if leg.game_id:
            by_game.setdefault(leg.game_id, []).append(leg)

    for game_id, group in by_game.items():
        if len(group) < 2:
            continue
        ids = [leg.bet_id for leg in group]
        teams = {leg.team for leg in group if leg.team}
        markets = {leg.market.value for leg in group}

        if {"passing_yards", "receiving_yards"} <= markets and len(teams) == 1:
            warnings.append(
                CorrelationWarning(
                    leg_ids=ids,
                    kind="stack",
                    detail=(
                        "QB passing yards and a receiver on the same team move together. "
                        "This slip wins bigger and loses more often than the EV implies."
                    ),
                    severity="warn",
                )
            )
        elif {"strikeouts", "hits_1_plus"} <= markets:
            warnings.append(
                CorrelationWarning(
                    leg_ids=ids,
                    kind="opposing",
                    detail=(
                        "A pitcher's strikeouts and an opposing hitter's hit chance are "
                        "negatively correlated -- these legs fight each other."
                    ),
                    severity="warn",
                )
            )
        else:
            warnings.append(
                CorrelationWarning(
                    leg_ids=ids,
                    kind="same_game",
                    detail=(
                        f"{len(group)} legs from the same game ({game_id}). Game script "
                        "links them, so treat the entry EV as approximate."
                    ),
                    severity="info",
                )
            )
    return warnings
