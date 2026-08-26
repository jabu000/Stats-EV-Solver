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
make api            # http://127.0.0.1:8000
```

Or, to have it run itself — API always up, slates recorded and graded on a schedule:

```bash
make setup && make build
make install-service
```

For frontend work, `make dev` runs the Vite dev server on :5173 with the API proxied.

It starts in **fixture mode** against recorded sample slates, so it works with no
network at all. To use real data, set `DATA_MODE=live` in `.env`, restart, and click
**Test connections** in the Settings tab.

`make seed` fills the Track Record tab with a demo graded history. It is useful for
seeing what the tab looks like, but seeded picks are deliberately **excluded** from the
hit rate, the ROI and the calibrator, so the numbers stay yours — pass
`?include_demo=true` to see them counted. Skip it if an empty tab won't confuse you.

Note that `make clean` removes `frontend/dist` as well as the database, so `make build`
has to run again before `make api` serves anything but a 503.

---

## Daily workflow

1. Open the league tab and find the bets you like.
2. Press **Record slate**. *This is the step that publishes the board for grading.*
3. Grade it — either let the scheduled job do it overnight, or press **Grade now** on
   the Track Record tab once the games have finished.

**Looking at a board records nothing.** Reads are read-only: you can refresh, switch
leagues and re-sort as often as you like without polluting the track record. Only
**Record slate** (or `make snapshot`) writes, and it is idempotent — recording the same
slate five times leaves one row per pick, refreshing the stored closing line each time
without touching the projection that was originally published.

That is what makes closing-line value meaningful: the projection is frozen at the
moment you recorded it, and the line keeps moving.

---

## Running it in the background

```bash
make install-service     # launchd on macOS, systemd --user on Linux
make service-status
make uninstall-service
```

This installs two things: the API, kept alive and restarted if it dies, and a job
schedule:

| When | What |
|---|---|
| 09:00, 13:00, 16:00 | `snapshot` — record each league's slate |
| 18:30 | `snapshot` + `grade` — late slate, then settle whatever finished |

Snapshotting several times a day is deliberate: the earlier board is the one with an
edge, and keeping it means the closing line has something to be compared against.
Grading skips anything already settled, so nothing double-counts.

Logs land in `data/logs/jobs.log` and `data/logs/api.log`, truncated at 5 MB so an
always-on service can't fill a disk.

On Linux the units are installed for your user, which means they stop when you log out.
`sudo loginctl enable-linger $USER` keeps them running.

The scripts are plain files in `ops/` — `install-service.sh` writes the unit and
`run-jobs.sh` is what actually runs, so you can read them before trusting them, or call
`./ops/run-jobs.sh both` by hand.

### Or somewhere that isn't your laptop

A laptop that's closed records no slates, and a slate that isn't recorded is never
graded. `Dockerfile` and `render.yaml` deploy the whole thing — web service, Postgres,
and the two scheduled jobs — to Render. **[DEPLOY.md](DEPLOY.md)** is the step-by-step,
including what it costs and what breaks if you go cheap.

Two things that only matter once it has a public URL, and both are off by default:

- `ACCESS_PASSWORD` puts HTTP Basic in front of everything but the health check. Set it.
  Without it, anyone with the link reads your betting history and can write to Settings.
- `DATABASE_URL` accepts a managed `postgres://…` URL verbatim. The track record is the
  one thing here that can't be regenerated, so it should not live on an ephemeral disk.

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

### Grading

Grading is automatic. **Grade now** in the UI, or `make grade`, fetches actual results
and settles every pick that has one:

| League | Result source | Notes |
|---|---|---|
| MLB | StatsAPI `stats?stats=byDateRange` | two requests per day, hitters and pitchers |
| NFL | nflverse weekly player stats | anytime TD sums rushing **and** receiving |
| CFB | CollegeFootballData `/games/players` | free key required |

Football results are indexed by week, not date, so the grader **infers the week from
the date** and reports the week it used in the response. Pass an explicit `week` to
override it.

```bash
make snapshot   # record the current slate for every league
make grade      # settle yesterday's picks
make status     # pending count, hit rate, expected hit rate, ROI, Brier
make check      # is every upstream provider answering?
```

`GET /api/track-record/pending` lists what is recorded but unsettled — that count is
the badge on the Track Record tab. `POST /api/track-record/grade/auto` with no date
grades **every** date that has pending picks, not just yesterday, so a service that was
offline for a week catches up in one call. Posting actuals by hand still works via
`POST /api/track-record/grade` when a result feed is down.

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
  grading/     grader.py (settlement, track record), results.py (actual results)
  cli.py       snapshot / grade / status / check / serve, for the scheduler
  scheduler.py in-process job schedule, for deployments with no cron service
  security.py  optional password gate
frontend/src/  App.tsx (5-tab shell), components/, pages/
ops/           install-service.sh (launchd/systemd), run-jobs.sh
Dockerfile     node builds the frontend, python serves it
render.yaml    web service + Postgres + the two cron jobs
```

Nothing in `models/` imports a provider and nothing in `providers/` imports a model, so
a data source can be swapped or a model retuned without touching the other side.

The guiding rule throughout the pipeline is **degrade, never blank**. A slate with no
posted lineups, a dead weather API and half the names unresolved should still produce a
board — with lower confidence, visible warnings, and the unresolved names surfaced.

---

## Testing

```bash
make test     # 224 tests
```

Coverage worth knowing about: the Poisson-binomial is checked against brute-force
enumeration to machine precision; the negative binomial round-trips mean and variance;
break-even probabilities are verified to actually break even by round-tripping through
the entry pricer; the per-leg EV identity is checked against simulated entries; and
every model is tested for directional correctness (platoon advantage, game script, wind,
park, lineup slot, talent gap) and monotonicity in the line.

The recording and grading path is tested against the behaviours that make a track
record trustworthy rather than merely present: that reading a board writes nothing;
that recording the same slate twice leaves one row per pick; that a re-record refreshes
the closing line *without* rewriting the published projection; that the same slate
prices identically in a fresh process (a per-process Monte-Carlo seed used to flip
coin-flip bets and duplicate them); that anytime-TD grading counts rushing and receiving
scores; that football week inference is reported rather than silent; and that demo seed
data stays out of both the calibrator and the headline numbers.

The deployment surface is tested too, since its failure modes only appear once it is
public: that a managed `postgres://` URL is rewritten into something SQLAlchemy 2 will
open, that the database password never reaches a log line, that the password gate
refuses malformed credentials without crashing while leaving the health check open, and
that the in-process schedule fires each hour exactly once and records before it grades.
The suite itself passes against both SQLite and a real Postgres.

---

## Honest limitations

Read this part.

1. **The fixtures are synthetic.** This was built in a sandbox whose egress policy blocks
   every sports data host, so the recorded slates in `backend/fixtures/` are *generated*
   samples shaped exactly like the real payloads — same keys, nesting and quirks — not
   captured production responses. Player and team names are real (name resolution can't
   be tested against invented names); the statistics attached to them are invented and
   are nobody's actual performance record.
2. **Live mode is unverified by me.** The code paths — the line and stats providers and
   the three result fetchers that grading depends on — are written against the documented
   response shapes and exercised end to end offline, but no live request was ever made
   from the build environment. Run **Test connections** in Settings (or `make check`) on
   first use: it reports each provider individually, so a failure is immediately obvious
   rather than showing up as a mysteriously thin board or a stuck pending count.
3. **The demo board's edges are larger than real ones.** The fixture's "market" is a
   noisy line-setter placed near each projection, not a real book defending its
   position. Real edges are a few percentage points. The 1+ Hit market shows large edges
   because it's pinned to a 0.5 line — that part is realistic.
4. **Probabilities are uncalibrated until you have history.** The UI says so explicitly.
   Treat early numbers as rankings, not as probabilities.
5. **CFB is weaker than NFL.** CFBD carries no target counts (receptions stand in) and no
   red-zone splits, and college samples are small with enormous talent gaps. Games played
   is now derived per team from the schedule, so byes no longer inflate per-game rates —
   but if the schedule feed is unavailable it falls back to a season-wide estimate.
6. **Red-zone share is inferred from touchdown history**, not snap-level goal-line data.
   It's the noisiest input in the anytime-TD model and is flagged as such in the drawer.
7. **Correlation is flagged, not modelled.** Entry EV assumes independent legs, which is
   optimistic for positively-correlated slips.
8. **Football grading infers the week from the date.** A pick recorded on an unusual
   date — an international game, a rescheduled game, a Week 18 Saturday — can be looked
   up against the wrong week and come back ungraded. The response names the week it
   used, and an explicit `week` overrides it.
9. **The Render deployment is written but not built.** The sandbox blocks Docker Hub, so
   the image was never assembled. Everything the image packages was verified directly —
   the whole suite passes against a real Postgres, the password gate and the `$PORT`
   binding were exercised against a running server — but the `docker build` itself has
   not run. See [DEPLOY.md](DEPLOY.md).
10. **The model may systematically favour "lower".** Yardage markets are right-skewed, so
   if a book sets its line at the median rather than the mean, this model will lean under
   more than it should. The track record is how you find out — watch hit rate against
   expected hit rate per market before trusting it with money.

None of this is a guarantee of profit. It's a transparent pricing tool that shows its
work and grades itself honestly. Bet accordingly, or don't.
