"""Resolve an Underdog player name to a canonical player in our stats sources.

This is the least glamorous and most load-bearing part of the pipeline. Underdog,
MLB StatsAPI, nflverse and CFBD all spell names differently -- suffixes ("Jr.", "III"),
punctuation ("Ja'Marr" / "JaMarr"), accents ("Jose" / "Jose"), initials
("A.J. Brown" / "AJ Brown"), and nicknames ("Hollywood Brown" / "Marquise Brown").

A wrong match is worse than no match: it prices a bet with another player's numbers and
looks perfectly plausible on screen. So the matcher is deliberately conservative --
anything below `MIN_AUTO_SCORE` is recorded as *unmapped* and surfaced in Settings
instead of being guessed at.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from app.domain import League
from app.tables import PlayerAlias, UnmappedPlayer

#: Below this similarity we refuse to auto-match and flag the name instead.
MIN_AUTO_SCORE = 0.88
#: Above this we accept even without a team match.
STRONG_SCORE = 0.97

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_NICKNAMES = {
    "hollywood brown": "marquise brown",
    "dj moore": "d j moore",
    "aj brown": "a j brown",
    "cj stroud": "c j stroud",
    "kj osborn": "k j osborn",
    "tj hockenson": "t j hockenson",
    "jk dobbins": "j k dobbins",
}


def strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )


def normalize_name(name: str) -> str:
    """Aggressive normalisation used for comparison only, never for display."""
    text = strip_accents(name or "").lower()
    text = text.replace("&", " and ")
    # Delete intra-word punctuation rather than spacing it, so "Ja'Marr" and "JaMarr"
    # -- and "A.J." and "AJ" -- collapse to the same token and match exactly instead of
    # having to be rescued by the fuzzy scorer.
    text = re.sub(r"['\u2019.]", "", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = _NICKNAMES.get(text, text)
    parts = [p for p in text.split() if p not in _SUFFIXES]
    return " ".join(parts)


def name_key(name: str) -> str:
    """A compact key: first initial + last name. Cheap way to bucket candidates."""
    parts = normalize_name(name).split()
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0][0]}{parts[-1]}"


def similarity(a: str, b: str) -> float:
    """Blend of whole-string and surname similarity.

    Surnames carry most of the identifying signal, so a surname mismatch is heavily
    penalised even when the full strings look close ("Josh Allen" vs "Josh Allen" the
    linebacker is handled by team, but "Justin Jefferson" vs "Justin Jackson" must not
    slip through on first-name agreement alone).
    """
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    whole = SequenceMatcher(None, na, nb).ratio()
    last_a, last_b = na.split()[-1], nb.split()[-1]
    last = SequenceMatcher(None, last_a, last_b).ratio()
    first_a, first_b = na.split()[0], nb.split()[0]
    # Treat initials as compatible with the full first name they abbreviate.
    if len(first_a) == 1 or len(first_b) == 1:
        first = 1.0 if first_a[0] == first_b[0] else 0.0
    else:
        first = SequenceMatcher(None, first_a, first_b).ratio()

    return 0.30 * whole + 0.50 * last + 0.20 * first


@dataclass
class Candidate:
    """A possible target for a match, from one of the stats providers."""

    canonical_id: str
    name: str
    team: str | None = None
    position: str | None = None


@dataclass
class MatchResult:
    canonical_id: str | None
    canonical_name: str | None
    score: float
    resolved_by: str  # "exact" | "alias" | "fuzzy" | "unmapped"

    @property
    def matched(self) -> bool:
        return self.canonical_id is not None


class PlayerResolver:
    """Matches source names to candidates, caching decisions in the alias table."""

    def __init__(
        self,
        session: Session | None,
        league: League,
        candidates: list[Candidate],
        source: str = "underdog",
    ) -> None:
        self.session = session
        self.league = league
        self.source = source
        self.candidates = candidates
        # A slate has many lines per player, so resolutions are memoised: it saves the
        # fuzzy scan, and it stops the same alias being queued for insert repeatedly
        # within one transaction (which trips the unique constraint on flush).
        self._resolved: dict[str, MatchResult] = {}
        self._persisted: set[str] = set()
        self._by_exact: dict[str, list[Candidate]] = {}
        self._by_key: dict[str, list[Candidate]] = {}
        for candidate in candidates:
            self._by_exact.setdefault(normalize_name(candidate.name), []).append(candidate)
            self._by_key.setdefault(name_key(candidate.name), []).append(candidate)

    # ------------------------------------------------------------------ public
    def resolve(self, name: str, team: str | None = None) -> MatchResult:
        if not name:
            return MatchResult(None, None, 0.0, "unmapped")

        cache_key = f"{normalize_name(name)}|{(team or '').upper()}"
        cached = self._resolved.get(cache_key)
        if cached is not None:
            return cached

        result = self._resolve_uncached(name, team)
        self._resolved[cache_key] = result
        return result

    def _resolve_uncached(self, name: str, team: str | None) -> MatchResult:
        alias = self._lookup_alias(name)
        if alias is not None:
            return alias

        exact_matches = self._by_exact.get(normalize_name(name), [])
        exact = self._disambiguate(exact_matches, team)
        if exact is not None:
            return self._accept(name, exact, 1.0, "exact")
        if len(exact_matches) > 1:
            # Several players share this exact name and the team did not separate them.
            # Falling through to the fuzzy scorer would pick one arbitrarily and price
            # the bet with the wrong player's numbers, which looks entirely plausible
            # on screen. Refuse and surface it instead.
            self._record_unmapped(name, team, exact_matches[0], 1.0)
            return MatchResult(None, exact_matches[0].name, 1.0, "unmapped")

        best, score = self._best_fuzzy(name, team)
        if best is not None and score >= MIN_AUTO_SCORE:
            return self._accept(name, best, score, "fuzzy")

        self._record_unmapped(name, team, best, score)
        return MatchResult(None, best.name if best else None, score, "unmapped")

    # ----------------------------------------------------------------- internal
    def _lookup_alias(self, name: str) -> MatchResult | None:
        if self.session is None:
            return None
        row = (
            self.session.query(PlayerAlias)
            .filter_by(
                league=self.league.value,
                source=self.source,
                source_name=normalize_name(name),
            )
            .one_or_none()
        )
        if row is None:
            return None
        return MatchResult(row.canonical_id, row.canonical_name, row.confidence, "alias")

    def _disambiguate(
        self, matches: list[Candidate], team: str | None
    ) -> Candidate | None:
        """Pick among same-name candidates using team, else refuse if ambiguous."""
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        if team:
            same_team = [c for c in matches if c.team and c.team.upper() == team.upper()]
            if len(same_team) == 1:
                return same_team[0]
        return None

    def _best_fuzzy(self, name: str, team: str | None) -> tuple[Candidate | None, float]:
        # Bucket by first-initial + surname first; fall back to a full scan only if
        # that finds nothing, which keeps this linear in practice.
        pool = self._by_key.get(name_key(name)) or self.candidates
        best: Candidate | None = None
        best_score = 0.0
        for candidate in pool:
            score = similarity(name, candidate.name)
            if team and candidate.team:
                # Agreeing on team is strong evidence; disagreeing is strong evidence
                # against, since two players can share a name across teams.
                score += 0.04 if candidate.team.upper() == team.upper() else -0.12
            if score > best_score:
                best, best_score = candidate, score
        return best, min(best_score, 1.0)

    def _accept(
        self, source_name: str, candidate: Candidate, score: float, how: str
    ) -> MatchResult:
        normalized = normalize_name(source_name)
        if (
            self.session is not None
            and how != "alias"
            and normalized not in self._persisted
        ):
            # Sessions run with autoflush off, so a pending insert from another
            # resolver in the same transaction (MLB uses separate pitcher and batter
            # resolvers) would be invisible here and we would insert the row twice.
            self.session.flush()
            existing = (
                self.session.query(PlayerAlias)
                .filter_by(
                    league=self.league.value, source=self.source, source_name=normalized
                )
                .one_or_none()
            )
            if existing is None:
                self.session.add(
                    PlayerAlias(
                        league=self.league.value,
                        source=self.source,
                        source_name=normalized,
                        canonical_id=candidate.canonical_id,
                        canonical_name=candidate.name,
                        confidence=score,
                        resolved_by=how,
                    )
                )
            else:
                existing.canonical_id = candidate.canonical_id
                existing.canonical_name = candidate.name
                existing.confidence = score
                existing.resolved_by = how
            self._persisted.add(normalized)
        return MatchResult(candidate.canonical_id, candidate.name, score, how)

    def _record_unmapped(
        self, name: str, team: str | None, best: Candidate | None, score: float
    ) -> None:
        if self.session is None:
            return
        self.session.flush()
        existing = (
            self.session.query(UnmappedPlayer)
            .filter_by(league=self.league.value, source_name=name)
            .one_or_none()
        )
        if existing is not None:
            existing.times_seen += 1
            existing.best_guess = best.name if best else existing.best_guess
            existing.best_score = max(existing.best_score, score)
            return
        self.session.add(
            UnmappedPlayer(
                league=self.league.value,
                source_name=name,
                team=team,
                best_guess=best.name if best else None,
                best_score=score,
            )
        )
