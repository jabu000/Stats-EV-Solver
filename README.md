# Stats EV Solver

Prices Underdog Pick'em player props for **MLB**, **NFL** and **college football**, and
ranks them two ways: by **expected value** and by **probability of hitting**. Those are
different questions, and the platform refuses to pretend otherwise.

Markets tracked:

| League | Bet types |
|---|---|
| MLB | Strikeouts (pitcher), 1+ Hit (batter) |
| NFL / CFB | Receiving Yards, Rushing Yards, Passing Yards, Anytime TD, Receptions |

---

## Quick start

```bash
make setup          # venv + backend deps + npm install
make build          # build the frontend
make seed           # optional: demo history so the Track Record tab has data
make api            # http://127.0.0.1:8000
```

For frontend work, `make dev` runs the Vite dev server on :5173 with the API proxied.

It starts in **fixture mode** against recorded sample slates, so it works with no
network at all. To use real data, set `DATA_MODE=live` in `.env`, restart, and click
**Test connections** in the Settings tab.

---

## The one number that matters

**A Pick'em leg is not a coin flip at even money.** A standard 3-pick pays 6x and needs
all three legs to land, so the break-even probability per leg is

```
(1/6)^(1/3) = 55.0%
```

A model that calls a 53% leg "value" because it beats 50% is a losing model, and that is
the default failure mode of prop tools. Every edge on this board is measured against the
break-even implied by **your** configured entry structure (Settings → *How EV is
measured*), not against 50%:

| Entry | Payout | Break-even per leg |
|---|---|---|
| 2-pick standard | 3x | 57.7% |
| 3-pick standard | 6x | 55.0% |
| 4-pick standard | 10x | 56.2% |
| 5-pick standard | 20x | 54.9% |

Payout multipliers are editable, because Underdog changes them.

---

## How the models work

Shared machinery lives in `backend/app/models/distributions.py`. Choice of distribution
is where most prop models go wrong, and it matters most exactly where the money is —
in the tail near the line.

- **Counts** (strikeouts, receptions) are overdispersed relative to Poisson, because the
  underlying rate itself varies game to game. Modelled as a negative binomial; a Poisson
  model is systematically overconfident on both tails.
- **"At least one"** markets (1+ Hit, Anytime TD) are sums of non-identical Bernoullis,
  computed as an **exact Poisson-binomial** rather than a normal approximation.
- **Yardage** is a **compound** distribution: an overdispersed count of catches or
  carries, each producing a right-skewed gain. A receiver's most likely single game is
  well *below* his average, with a long right tail from one broken play — pricing that
  off a symmetric distribution overstates "higher" every time. Validated against the
  empirical benchmark: P(a player exceeds his own average) lands near 0.44 for a
  high-volume receiver and 0.36 for a low-volume one.

### MLB — strikeouts

Works per batter faced, not per game. For each hitter the starter is projected to face:
the pitcher's rate against that side of the plate, **log5**-combined with that hitter's
own strikeout rate against this hand, then adjusted for park, weather, umpire and
catcher framing, and summed across the projected batters faced by cycling the order.

Two things this gets right that season-average models don't:

- **Volume and rate are separate.** An elite arm on a short leash will not reach a 6.5
  line. `expected_batters_faced` is a first-class term driven by innings per start,
  opponent OBP and game script.
- **Handedness is matchup-specific.** A lefty facing a left-heavy lineup is a different
  pitcher from the same lefty facing a right-heavy one.

Platoon splits are applied as **regressed ratios** to the well-measured overall rate,
never as absolute rates — that distinction alone moved projections by 20-30%.

### MLB — 1+ hit

Not "project hits and compare to 0.5". For each plate appearance the hitter is likely to
get, the chance of a hit; then the chance at least one lands. Plate appearances are
split between the starter and the bullpen by batting-order position, and the fractional
PA count is handled by evaluating both integer cases and weighting them. Lineup slot is
a real edge the market underprices.

Inputs: contact rate vs the pitcher's hand, opposing starter and bullpen, park BABIP
factor, weather (temperature and wind move batted-ball carry), and team defence.

### Football — all five markets

Volume comes from the market: projected plays, split by a **game-script-adjusted pass
rate**, times the player's shrunk usage share. Underdogs throw and favourites run, and
that effect is worth more than most efficiency adjustments while being fully knowable
before kickoff. Then efficiency, opponent adjustment, and weather — where wind is
strongly non-linear (negligible below 12 mph, collapsing above 20) and cold is routinely
over-weighted by public models.

Anytime TD is driven off the market's implied team total rather than the player's own
scoring rate, because touchdown rates are the noisiest thing in football.

**College football** uses the same skeleton with much harder shrinkage, SP+-style
opponent adjustment, and a blowout-script correction for large talent gaps. CFB
projections are meaningfully weaker than NFL ones and are labelled with lower confidence.

### Every projection explains itself

Click any row for the **"Why" drawer**: the factor breakdown that produced the number,
the projected distribution with the posted line marked on it, and any warnings. If a
factor can't be explained on screen, it shouldn't be moving the number.

---

## Ranking

- **Best Value** ranks by `Score` — the edge over break-even, shrunk toward zero by
  confidence (how much of the projection came from the player's own data rather than a
  prior). A thin-sample outlier cannot top the board. The Score column is displayed, so
  the sort order is always explainable.
- **Most Likely** ranks by probability, but still pushes negative-EV bets to the bottom.

## Entry builder

Prices the whole slip, not the legs in isolation: payout branches for standard and
insured entries, fractional Kelly staking, and **correlation flags** for QB→WR stacks,
same-game legs, and opposing pitcher/hitter combinations. Correlation is flagged rather
than modelled — the honest middle ground between ignoring it and pretending to a joint
distribution there's no data to estimate.

## Track record & calibration

Every published board is snapshotted. Once picks are graded, the Track Record tab shows
hit rate against **expected** hit rate, ROI, Brier score, a calibration curve and CLV.

Brier and calibration converge far faster than ROI, so a bad model is visible in them
weeks before the bankroll shows it. Reporting expected vs actual side by side makes
overconfidence impossible to hide.

Once a market has 120+ graded picks, a **centered isotonic regression** correction is
fitted and the *calibrated* probability is what gets displayed and priced. Until then
the UI explicitly labels probabilities as uncalibrated. The calibrator:

- shrinks each pooled block toward the model's own prediction by that block's sample
  size, so a handful of lucky picks in a sparse band can't invert the model;
- falls back to the identity outside its fitted domain rather than extrapolating;
- interpolates between block centroids instead of stepping, preserving the model's
  ordering — which is the part most likely to be right.

Grade picks by posting actuals:

```bash
curl -X POST localhost:8000/api/track-record/grade \
  -H 'Content-Type: application/json' \
  -d '{"results":[{"player_key":"12345","market":"strikeouts","actual":7}]}'
```

---

## Data sources

| Need | Source | Key |
|---|---|---|
| Lines, payout multipliers, boosts | `api.underdogfantasy.com/beta/v6/over_under_lines` | none, token override |
| MLB schedule, lineups, probables, platoon splits | MLB StatsAPI | none |
| Weather | Open-Meteo | none |
| NFL usage and efficiency | nflverse GitHub releases | none |
| CFB stats and SP+ ratings | CollegeFootballData | free key |
| Spreads and totals | ESPN scoreboard | none |

MLB uses the **bulk** stats endpoints, so a full slate — schedule, lineups, probables,
season rates and platoon splits for every hitter and pitcher — costs about seven
requests rather than the ~400 a naive per-player implementation would make.

Park factors, stadium coordinates and roof state ship as editable JSON in
`backend/app/static_data/`.

### If Underdog's endpoint breaks

It's undocumented and can change or start requiring auth without notice. Three ways in,
tried in order: unauthenticated → bearer token from Settings → **manual CSV/JSON paste**,
so a broken scrape never leaves you with nothing:

```csv
player,market,line,team,opponent
Ja'Marr Chase,receiving yards,72.5,CIN,BAL
Aaron Judge,1+ hit,0.5,NYY,BOS
```

### Name resolution

The quietest failure point. Underdog, MLB StatsAPI, nflverse and CFBD all spell names
differently — suffixes, apostrophes, accents, initials, nicknames. A *wrong* match is
worse than no match: it prices a bet with another player's numbers and looks entirely
plausible on screen. So the matcher is deliberately conservative — anything below 88%
similarity, and any genuinely ambiguous name (two players called Josh Allen with no team
to separate them), is recorded as **unmapped** and surfaced in Settings rather than
guessed at.

---

## Architecture

```
backend/app/
  providers/   one interface, two backends (live HTTP / recorded fixtures)
  ingest/      pipeline.py (orchestration), mapping.py (name resolution)
  features/    context.py is the provider↔model contract; mlb.py, football.py
  models/      distributions.py + one module per market + calibration.py
  pricing/     edge.py, entry.py, correlation.py
  grading/     grader.py
frontend/src/  App.tsx (5-tab shell), components/, pages/
```

Nothing in `models/` imports a provider and nothing in `providers/` imports a model, so
a data source can be swapped or a model retuned without touching the other side.

The guiding rule throughout the pipeline is **degrade, never blank**. A slate with no
posted lineups, a dead weather API and half the names unresolved should still produce a
board — with lower confidence, visible warnings, and the unresolved names surfaced.

---

## Testing

```bash
make test     # 157 tests
```

Coverage worth knowing about: the Poisson-binomial is checked against brute-force
enumeration to machine precision; the negative binomial round-trips mean and variance;
break-even probabilities are verified to actually break even by round-tripping through
the entry pricer; the per-leg EV identity is checked against simulated entries; and
every model is tested for directional correctness (platoon advantage, game script, wind,
park, lineup slot, talent gap) and monotonicity in the line.

---

## Honest limitations

Read this part.

1. **The fixtures are synthetic.** This was built in a sandbox whose egress policy blocks
   every sports data host, so the recorded slates in `backend/fixtures/` are *generated*
   samples shaped exactly like the real payloads — same keys, nesting and quirks — not
   captured production responses. Player and team names are real (name resolution can't
   be tested against invented names); the statistics attached to them are invented and
   are nobody's actual performance record.
2. **Live mode is unverified by me.** The code paths are written against the documented
   response shapes and exercised end to end offline, but no live request was ever made
   from the build environment. Run **Test connections** in Settings on first use — it
   reports each provider individually so a failure is immediately obvious rather than
   showing up as a mysteriously thin board.
3. **The demo board's edges are larger than real ones.** The fixture's "market" is a
   noisy line-setter placed near each projection, not a real book defending its
   position. Real edges are a few percentage points. The 1+ Hit market shows large edges
   because it's pinned to a 0.5 line — that part is realistic.
4. **Probabilities are uncalibrated until you have history.** The UI says so explicitly.
   Treat early numbers as rankings, not as probabilities.
5. **CFB is weaker than NFL.** CFBD carries no target counts (receptions stand in) and no
   red-zone splits, and college samples are small with enormous talent gaps.
6. **Red-zone share is inferred from touchdown history**, not snap-level goal-line data.
   It's the noisiest input in the anytime-TD model and is flagged as such in the drawer.
7. **Correlation is flagged, not modelled.** Entry EV assumes independent legs, which is
   optimistic for positively-correlated slips.
8. **The model may systematically favour "lower".** Yardage markets are right-skewed, so
   if a book sets its line at the median rather than the mean, this model will lean under
   more than it should. The track record is how you find out — watch hit rate against
   expected hit rate per market before trusting it with money.

None of this is a guarantee of profit. It's a transparent pricing tool that shows its
work and grades itself honestly. Bet accordingly, or don't.
