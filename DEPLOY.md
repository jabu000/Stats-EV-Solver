# Deploying to Render

Everything here is already in the repo — `Dockerfile`, `render.yaml`, the CLI the cron
jobs call. This document is what *you* have to do, in order, and what to check at each
step so a failure is obvious rather than silent.

Budget about 30 minutes, most of it waiting for the first build.

---

## Before you start: is Render the right call?

Be honest about what you're buying. Running on Render costs roughly **$14–20/month**
(web service + Postgres + cron jobs). Running `make install-service` on your own laptop
costs nothing and gives you the same platform.

Render is worth it if you want the board reachable from your phone, or you want slates
recorded on days your laptop is closed. A snapshot that never runs is a pick that never
gets graded, and the track record is the entire point of this thing.

If neither of those matters to you, close this file and run `make install-service`.

---

## Step 0 — Prerequisites

1. **The code is on GitHub.** Render deploys from a repo. This branch is already pushed;
   merge it to `main` or point Render at the branch directly.
2. **A Render account** — <https://render.com>, sign up with GitHub so it can see the
   repo. Free to create; you only pay for what you provision.
3. **A payment method on file.** Cron jobs and always-on services need a paid plan. You
   can start free (see Step 6) and add the card later.
4. **A CollegeFootballData API key** if you want the CFB tab to work in live mode. Free,
   takes a minute: <https://collegefootballdata.com/key>. Skip it if you only care about
   MLB and NFL — CFB will just report the provider as unavailable.

You do **not** need an Underdog token to start. The endpoint is usually readable without
one, and there's a Settings field for it if that changes.

---

## Step 1 — Create the Blueprint

In the Render dashboard: **New → Blueprint**, pick this repository, choose the branch,
and Render reads `render.yaml`.

It will show you what it's about to create:

| Resource | What it is |
|---|---|
| `stats-ev-solver` | the web service — the board and API |
| `stats-ev-solver-db` | Postgres — the track record |
| `stats-ev-solver-snapshot` | cron, 3×/day — records slates |
| `stats-ev-solver-grade` | cron, 1×/day — settles picks |

Render will prompt for the three values marked `sync: false` in the blueprint, because
they're secrets and don't belong in a repo:

- **`ACCESS_PASSWORD`** — *set this*. See Step 2.
- **`CFBD_API_KEY`** — your CollegeFootballData key, or leave blank.
- **`UNDERDOG_TOKEN`** — leave blank for now.

You'll be asked for `CFBD_API_KEY` and `UNDERDOG_TOKEN` **three times**, once per
service. Give the same values each time — the cron jobs fetch data too, and a cron job
without the CFB key will snapshot MLB and NFL and quietly skip college.

Press **Apply**. The first build takes 5–10 minutes: it installs numpy, scipy and pandas,
then builds the frontend.

---

## Step 2 — Set a password. Really.

`ACCESS_PASSWORD` puts HTTP Basic in front of everything except `/api/health`. Without
it, `stats-ev-solver.onrender.com` is a public URL that anyone can:

- read your entire board and betting history from, and
- **write to** — the Settings tab is an API, and it accepts a bearer token, payout
  multipliers and bankroll.

Existing secrets are never returned to the browser (the Settings endpoint reports
presence flags, not values), so nobody reads your token back out. They can still replace
it, and they can read every bet you've recorded.

Use a long random password. Any username works at the prompt; only the password is
checked.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

This is a lock on a door, not a security system — one shared password, no accounts. That
is proportionate for a single-user tool at a URL nobody's been given. It is not
proportionate if you plan to share the link around.

---

## Step 3 — Watch the first deploy

Open the `stats-ev-solver` service → **Logs**. You want to see:

```
INFO:     Uvicorn running on http://0.0.0.0:10000
INFO:     Application startup complete.
```

Render then polls `/api/health` and marks the service **Live**.

Visit the URL. Your browser will ask for the password. You should land on the MLB tab
with a board of bets.

**If you get a plain-text page saying the frontend hasn't been built**, the Docker build
skipped the frontend stage — check the build log for an `npm ci` failure.

**If the build fails on `npm ci`**, `frontend/package-lock.json` is out of sync with
`package.json`. Run `cd frontend && npm install` locally, commit the lockfile, push.

---

## Step 4 — Switch to live data and verify each provider

The blueprint already sets `DATA_MODE=live`. Confirm the providers actually answer:

Open the **Settings** tab and press **Test connections**. You get one row per provider.
What you're looking for:

| Provider | If it fails |
|---|---|
| Underdog | The endpoint is undocumented and can start requiring auth. Get a bearer token from your browser's dev tools on underdogfantasy.com (Network tab → any API request → `Authorization` header) and paste it into Settings. Failing that, use the CSV paste box. |
| MLB StatsAPI | Almost never fails. If it does, it's an outage — wait. |
| Open-Meteo | Weather degrades gracefully. A failure lowers confidence, it doesn't blank the board. |
| nflverse | Serves from GitHub releases. A failure usually means GitHub is having a moment. |
| CollegeFootballData | Almost always a missing or wrong `CFBD_API_KEY`. |
| ESPN scoreboard | Spreads and totals. Without it, game-script adjustment falls back to neutral — noticeably worse football projections. |

A red row here is the single most useful diagnostic in the whole app. A board that looks
thin or oddly-priced is nearly always a provider that quietly isn't answering.

> **This is the part I could not test.** The build sandbox blocks every sports data host,
> so no live request was ever made from it. The live code paths are written against the
> documented response shapes and exercised end to end against recorded fixtures — but the
> first real call to each provider happens on your deployment, not mine. Run **Test
> connections** before you trust a single number.

---

## Step 5 — Confirm the schedule works

Don't wait a day to find out. Trigger the cron jobs by hand:

`stats-ev-solver-snapshot` → **Trigger Run**. In its logs:

```
snapshot MLB: 84 new, 0 line updates (source=live)
snapshot NFL: 188 new, 0 line updates (source=live)
snapshot CFB: 80 new, 0 line updates (source=live)
```

`source=live` is the bit that matters. `source=fixture` means `DATA_MODE` didn't take —
check the cron job's own env vars, which are separate from the web service's.

Then `stats-ev-solver-grade` → **Trigger Run**. Right after a snapshot it'll settle
nothing, because the games haven't finished. That's correct. You want it to run without
erroring.

Reload the app: the **Track Record** tab should show a pending count matching what the
snapshot recorded.

### What the schedule actually does

| Job | UTC | US Eastern | |
|---|---|---|---|
| snapshot | 16:00, 20:00, 23:00 | 12pm, 4pm, 7pm | records each league's slate |
| grade | 11:00 | 7am | settles everything outstanding |

Recording several times a day is deliberate. The board you'd act on is the *early* one;
keeping it means the closing line has something to be measured against, which is what
CLV means. Re-recording the same slate doesn't duplicate anything — it refreshes the
stored closing line and leaves the published projection alone.

Grading catches up. It settles **every** date with outstanding picks, not just yesterday,
so a job that fails or a feed that's late fixes itself on the next run.

**Cron schedules in `render.yaml` are UTC and do not shift for daylight saving.** Your
19:00 job is 7pm Eastern in winter and 6pm Eastern in summer. If that matters, edit the
`schedule:` lines twice a year, or just accept the hour of drift.

---

## Step 6 — Choosing plans (and what breaks if you go cheap)

Approximate monthly cost — **check <https://render.com/pricing>, these change**:

| Resource | Plan in the blueprint | Roughly |
|---|---|---|
| Web service | `starter` | ~$7 |
| Postgres | `basic-256mb` | ~$6 |
| Cron jobs (×2) | `starter` | billed per run-minute; pennies |

**Free web service** (`plan: free`) spins down after 15 minutes of inactivity, and the
next visit takes ~50 seconds to wake up. Tolerable for the UI. **Not** tolerable if you
set `ENABLE_SCHEDULER=true` and expect the in-process schedule to fire — a sleeping
service runs nothing. Free tier also has monthly instance hours you can exhaust.

**Free Postgres** works but **Render expires free databases** after a limited period, and
when it goes, your entire track record goes with it. The track record is the only thing
here that can't be regenerated. Do not put it on a free database.

**Cron jobs need a paid plan.** If you'd rather not pay for them, delete both `cron`
blocks from `render.yaml` and set `ENABLE_SCHEDULER=true` on the web service instead:
the same jobs then run on a background task inside the API process, at the hours in
`SCHEDULER_HOURS_UTC`. This only works on a plan that stays awake. Don't run both —
you'd just do the same work twice.

**Don't use a persistent disk with SQLite instead of Postgres.** It technically works,
but a disk pins the service to a single instance, doesn't play well with cron jobs
running as separate containers, and gets you nothing Postgres doesn't.

---

## Step 7 — After the first slate grades

Come back the next morning and look at the Track Record tab.

- **Hit rate vs expected hit rate** is the number to watch. If expected is 62% and actual
  is 51%, the model is overconfident, and you'd rather learn that from this table than
  from your bankroll.
- **Brier score** converges much faster than ROI. A bad model shows up here weeks before
  the money says so.
- Probabilities are labelled uncalibrated until a market has **120+ graded picks**. Until
  then treat the board as a *ranking*, not as probabilities. This takes a few weeks of
  daily snapshots. There is no shortcut.

---

## Troubleshooting

**502 / "Application failed to respond"** — the service didn't bind the port Render
assigned. It reads `$PORT`, which Render sets automatically; if you overrode `PORT` in
the dashboard, remove it.

**Board is empty, no error** — almost always a provider. Settings → **Test connections**.
On a live slate it can also just be a quiet day; the board tells you which.

**Everything shows "unmapped"** — name resolution failed wholesale, which means the
stats provider answered but the *lines* provider returned something unexpected, or vice
versa. Settings → Unmapped players shows what it couldn't match.

**Pending count climbs and never falls** — grading isn't settling. Check the grade cron's
logs. Two common causes: football week inference picked the wrong week (the log line
says which week it used), or the result provider is failing (`make check` equivalent:
Settings → Test connections).

**Track record reset to zero** — your database was recreated. If you were on free
Postgres, that's what happened, and it's not recoverable. Move to a paid database.

**Deploy succeeded but the page is stale** — hard-refresh. The SPA is served with normal
caching; Render doesn't invalidate your browser cache.

**Postgres connection errors after an idle period** — shouldn't happen; the engine is
configured with `pool_pre_ping` and connection recycling for exactly this. If it does,
the database is genuinely down — check its status page in the dashboard.

---

## Running the same image locally

The container is the deployment. If you want to check a change before pushing:

```bash
docker build -t stats-ev-solver .
docker run --rm -p 8000:8000 \
  -e DATA_MODE=fixture \
  -e ACCESS_PASSWORD=letmein \
  stats-ev-solver
```

Then <http://127.0.0.1:8000>, username anything, password `letmein`.

> The Docker build itself is untested — the build sandbox blocks Docker Hub, so the image
> was never assembled. Everything it packages (the Postgres path, the password gate, the
> scheduler, the CLI the cron jobs call) was verified directly against a real Postgres
> instance and a running server. If the image fails to build, it will be something
> mundane in the Dockerfile, and the build log will say what.

---

## Updating a deployment

`autoDeploy: true` is set, so pushing to the deployed branch rebuilds and redeploys.
The database is separate and survives.

If you change `render.yaml` itself, go to the Blueprint in the dashboard and re-sync it —
Render doesn't apply blueprint changes on a plain code push.
