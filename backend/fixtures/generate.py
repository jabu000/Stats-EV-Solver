"""Generate the recorded payloads that back fixture mode.

**These are synthetic sample slates, not captured production responses.** The sandbox
this project was built in blocks outbound HTTPS to every sports data host, so real
payloads could not be recorded here. What they *are* is faithful to the real response
*shapes* -- the same key names, nesting, string-vs-number quirks and cross-referenced
id arrays -- so the normalisation, mapping, modelling and pricing code paths exercised
offline are exactly the ones that run against live data.

To replace these with genuine captures once you have network access:

    DATA_MODE=live python -m backend.fixtures.generate --record

which fetches each endpoint for real and writes the response through
`Provider.save_fixture`.

Player and team names below are real, because name resolution is a load-bearing part of
the pipeline and testing it against made-up names would prove nothing. The *statistics*
attached to them are generated, plausible-but-invented numbers -- they are not anyone's
actual performance record and must not be read as such.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent
SEASON = 2025
RNG = random.Random(20260826)

# --------------------------------------------------------------------- rosters
MLB_GAMES = [
    ("NYY", "BOS", 1), ("LAD", "SD", 2), ("ATL", "PHI", 3),
    ("HOU", "SEA", 4), ("CHC", "MIL", 5), ("COL", "ARI", 6),
]

MLB_PITCHERS = {
    "NYY": ("Gerrit Cole", "R"), "BOS": ("Garrett Crochet", "L"),
    "LAD": ("Yoshinobu Yamamoto", "R"), "SD": ("Dylan Cease", "R"),
    "ATL": ("Chris Sale", "L"), "PHI": ("Zack Wheeler", "R"),
    "HOU": ("Framber Valdez", "L"), "SEA": ("Logan Gilbert", "R"),
    "CHC": ("Shota Imanaga", "L"), "MIL": ("Freddy Peralta", "R"),
    "COL": ("Kyle Freeland", "L"), "ARI": ("Zac Gallen", "R"),
}

MLB_LINEUPS = {
    "NYY": ["Aaron Judge", "Juan Soto", "Anthony Volpe", "Jazz Chisholm Jr.", "Austin Wells",
            "Ben Rice", "Trent Grisham", "Oswaldo Cabrera", "Anthony Rizzo"],
    "BOS": ["Jarren Duran", "Rafael Devers", "Trevor Story", "Wilyer Abreu", "Triston Casas",
            "Ceddanne Rafaela", "Connor Wong", "Masataka Yoshida", "David Hamilton"],
    "LAD": ["Shohei Ohtani", "Mookie Betts", "Freddie Freeman", "Teoscar Hernandez", "Max Muncy",
            "Will Smith", "Tommy Edman", "Andy Pages", "Gavin Lux"],
    "SD": ["Fernando Tatis Jr.", "Luis Arraez", "Manny Machado", "Jackson Merrill", "Xander Bogaerts",
           "Jake Cronenworth", "Kyle Higashioka", "Jurickson Profar", "Bryce Johnson"],
    "ATL": ["Ronald Acuna Jr.", "Ozzie Albies", "Austin Riley", "Matt Olson", "Marcell Ozuna",
            "Michael Harris II", "Sean Murphy", "Orlando Arcia", "Jarred Kelenic"],
    "PHI": ["Kyle Schwarber", "Trea Turner", "Bryce Harper", "Alec Bohm", "Nick Castellanos",
            "J.T. Realmuto", "Brandon Marsh", "Bryson Stott", "Johan Rojas"],
    "HOU": ["Jose Altuve", "Yordan Alvarez", "Alex Bregman", "Kyle Tucker", "Jeremy Pena",
            "Yainer Diaz", "Chas McCormick", "Jake Meyers", "Mauricio Dubon"],
    "SEA": ["Julio Rodriguez", "Cal Raleigh", "Randy Arozarena", "J.P. Crawford", "Luke Raley",
            "Mitch Garver", "Dylan Moore", "Victor Robles", "Josh Rojas"],
    "CHC": ["Ian Happ", "Seiya Suzuki", "Dansby Swanson", "Nico Hoerner", "Michael Busch",
            "Pete Crow-Armstrong", "Miguel Amaya", "Isaac Paredes", "Nick Madrigal"],
    "MIL": ["Christian Yelich", "William Contreras", "Willy Adames", "Jackson Chourio", "Rhys Hoskins",
            "Sal Frelick", "Brice Turang", "Joey Ortiz", "Garrett Mitchell"],
    "COL": ["Brenton Doyle", "Ezequiel Tovar", "Ryan McMahon", "Kris Bryant", "Michael Toglia",
            "Nolan Jones", "Elias Diaz", "Jordan Beck", "Hunter Goodman"],
    "ARI": ["Ketel Marte", "Corbin Carroll", "Christian Walker", "Eugenio Suarez", "Lourdes Gurriel Jr.",
            "Gabriel Moreno", "Alek Thomas", "Geraldo Perdomo", "Jake McCarthy"],
}

BATS = {}  # filled deterministically below

NFL_GAMES = [("KC", "BUF"), ("PHI", "DAL"), ("SF", "SEA"), ("DET", "GB"),
             ("BAL", "CIN"), ("MIA", "NYJ"), ("HOU", "IND"), ("LAR", "TB")]

#: Depth added to every team below, so target and rush shares are computed against a
#: realistic denominator. With only four or five players the "team total" is far too
#: small and every starter's share is absurdly inflated.
NFL_DEPTH = [("Backup RB", "RB"), ("Slot WR", "WR"), ("Depth WR", "WR"), ("TE2", "TE")]

NFL_ROSTERS = {
    "KC": [("Patrick Mahomes", "QB"), ("Isiah Pacheco", "RB"), ("Kareem Hunt", "RB"),
           ("Rashee Rice", "WR"), ("Xavier Worthy", "WR"), ("JuJu Smith-Schuster", "WR"),
           ("Travis Kelce", "TE"), ("Noah Gray", "TE")],
    "BUF": [("Josh Allen", "QB"), ("James Cook", "RB"), ("Khalil Shakir", "WR"),
            ("Keon Coleman", "WR"), ("Dalton Kincaid", "TE")],
    "PHI": [("Jalen Hurts", "QB"), ("Saquon Barkley", "RB"), ("A.J. Brown", "WR"),
            ("DeVonta Smith", "WR"), ("Dallas Goedert", "TE")],
    "DAL": [("Dak Prescott", "QB"), ("Rico Dowdle", "RB"), ("CeeDee Lamb", "WR"),
            ("Jalen Tolbert", "WR"), ("Jake Ferguson", "TE")],
    "SF": [("Brock Purdy", "QB"), ("Christian McCaffrey", "RB"), ("Brandon Aiyuk", "WR"),
           ("Deebo Samuel", "WR"), ("George Kittle", "TE")],
    "SEA": [("Geno Smith", "QB"), ("Kenneth Walker III", "RB"), ("DK Metcalf", "WR"),
            ("Jaxon Smith-Njigba", "WR"), ("Noah Fant", "TE")],
    "DET": [("Jared Goff", "QB"), ("Jahmyr Gibbs", "RB"), ("Amon-Ra St. Brown", "WR"),
            ("Jameson Williams", "WR"), ("Sam LaPorta", "TE")],
    "GB": [("Jordan Love", "QB"), ("Josh Jacobs", "RB"), ("Jayden Reed", "WR"),
           ("Romeo Doubs", "WR"), ("Tucker Kraft", "TE")],
    "BAL": [("Lamar Jackson", "QB"), ("Derrick Henry", "RB"), ("Zay Flowers", "WR"),
            ("Rashod Bateman", "WR"), ("Mark Andrews", "TE")],
    "CIN": [("Joe Burrow", "QB"), ("Chase Brown", "RB"), ("Ja'Marr Chase", "WR"),
            ("Tee Higgins", "WR"), ("Mike Gesicki", "TE")],
    "MIA": [("Tua Tagovailoa", "QB"), ("De'Von Achane", "RB"), ("Tyreek Hill", "WR"),
            ("Jaylen Waddle", "WR"), ("Jonnu Smith", "TE")],
    "NYJ": [("Aaron Rodgers", "QB"), ("Breece Hall", "RB"), ("Garrett Wilson", "WR"),
            ("Allen Lazard", "WR"), ("Tyler Conklin", "TE")],
    "HOU": [("C.J. Stroud", "QB"), ("Joe Mixon", "RB"), ("Nico Collins", "WR"),
            ("Tank Dell", "WR"), ("Dalton Schultz", "TE")],
    "IND": [("Anthony Richardson", "QB"), ("Jonathan Taylor", "RB"), ("Michael Pittman Jr.", "WR"),
            ("Josh Downs", "WR"), ("Kylen Granson", "TE")],
    "LAR": [("Matthew Stafford", "QB"), ("Kyren Williams", "RB"), ("Puka Nacua", "WR"),
            ("Cooper Kupp", "WR"), ("Colby Parkinson", "TE")],
    "TB": [("Baker Mayfield", "QB"), ("Rachaad White", "RB"), ("Mike Evans", "WR"),
           ("Chris Godwin", "WR"), ("Cade Otton", "TE")],
}

CFB_GAMES = [("Georgia", "Alabama"), ("Ohio State", "Michigan"),
             ("Texas", "Oklahoma"), ("Oregon", "Washington")]

CFB_DEPTH = [("RB2", "RB"), ("WR3", "WR"), ("WR4", "WR"), ("TE1", "TE"), ("TE2", "TE")]

CFB_ROSTERS = {
    "Georgia": [("Carson Beck", "QB"), ("Trevor Etienne", "RB"), ("Arian Smith", "WR"), ("Dominic Lovett", "WR")],
    "Alabama": [("Jalen Milroe", "QB"), ("Justice Haynes", "RB"), ("Ryan Williams", "WR"), ("Germie Bernard", "WR")],
    "Ohio State": [("Will Howard", "QB"), ("TreVeyon Henderson", "RB"), ("Emeka Egbuka", "WR"), ("Jeremiah Smith", "WR")],
    "Michigan": [("Davis Warren", "QB"), ("Kalel Mullings", "RB"), ("Colston Loveland", "WR"), ("Tyler Morris", "WR")],
    "Texas": [("Quinn Ewers", "QB"), ("Jaydon Blue", "RB"), ("Isaiah Bond", "WR"), ("Matthew Golden", "WR")],
    "Oklahoma": [("Jackson Arnold", "QB"), ("Gavin Sawchuk", "RB"), ("Deion Burks", "WR"), ("Nic Anderson", "WR")],
    "Oregon": [("Dillon Gabriel", "QB"), ("Jordan James", "RB"), ("Tez Johnson", "WR"), ("Traeshon Holden", "WR")],
    "Washington": [("Will Rogers", "QB"), ("Jonah Coleman", "RB"), ("Denzel Boston", "WR"), ("Giles Jackson", "WR")],
}


def _with_depth(rosters: dict, depth: list) -> dict:
    """Append generically-named depth players to each roster.

    They never appear on a betting board -- Underdog does not post lines for them --
    but they are needed so that team target and carry totals are realistic and the
    starters' usage shares land where they would with a real roster.
    """
    return {
        team: roster + [(f"{team} {name}", position) for name, position in depth]
        for team, roster in rosters.items()
    }


def _write(provider: str, name: str, payload) -> None:
    path = FIXTURE_DIR / provider / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")


def _pid(prefix: str, index: int) -> str:
    return f"{prefix}{600000 + index}"


# ------------------------------------------------------------------ MLB fixtures
def build_mlb() -> dict[str, str]:
    """Schedule (with lineups + probables), bulk season stats, and platoon splits."""
    hitting, pitching = [], []
    hit_vl, hit_vr, pit_vl, pit_vr = [], [], [], []
    player_ids: dict[str, str] = {}
    dates_games = []

    index = 0
    for home, away, game_pk in MLB_GAMES:
        home_players, away_players = [], []
        for team, bucket in ((home, home_players), (away, away_players)):
            for slot, name in enumerate(MLB_LINEUPS[team], start=1):
                index += 1
                pid = _pid("", index)
                player_ids[name] = pid
                bats = ["L", "R", "R", "S", "R", "L", "R", "R", "L"][slot - 1]
                BATS[name] = bats
                bucket.append(
                    {"id": int(pid), "fullName": name, "batSide": {"code": bats},
                     "primaryPosition": {"abbreviation": "OF"}}
                )

                pa = RNG.randint(280, 620)
                # Plausible spread of true talent, then noisy counting stats around it.
                hit_rate = RNG.uniform(0.195, 0.290)
                k_rate = RNG.uniform(0.140, 0.320)
                hits, ks = int(pa * hit_rate), int(pa * k_rate)
                hitting.append({
                    "player": {"id": int(pid), "fullName": name},
                    "stat": {"plateAppearances": pa, "hits": hits, "strikeOuts": ks,
                             "obp": round(RNG.uniform(0.285, 0.400), 3)},
                })
                for split_rows, tilt in ((hit_vl, 1.08), (hit_vr, 0.96)):
                    split_pa = int(pa * RNG.uniform(0.26, 0.34))
                    split_rows.append({
                        "player": {"id": int(pid), "fullName": name},
                        "stat": {"plateAppearances": split_pa,
                                 "hits": int(split_pa * hit_rate * tilt),
                                 "strikeOuts": int(split_pa * k_rate * (2 - tilt))},
                    })

        probables = {}
        for team in (home, away):
            index += 1
            pid = _pid("", index)
            name, throws = MLB_PITCHERS[team]
            player_ids[name] = pid
            probables[team] = {"id": int(pid), "fullName": name, "pitchHand": {"code": throws}}

            starts = RNG.randint(14, 30)
            innings = round(starts * RNG.uniform(4.8, 6.4), 1)
            bf = int(innings * RNG.uniform(4.0, 4.5))
            k_rate = RNG.uniform(0.170, 0.330)
            pitching.append({
                "player": {"id": int(pid), "fullName": name},
                "stat": {"battersFaced": bf, "strikeOuts": int(bf * k_rate),
                         "hits": int(bf * RNG.uniform(0.185, 0.255)),
                         "gamesStarted": starts, "inningsPitched": str(innings),
                         "numberOfPitches": int(innings * RNG.uniform(15.5, 17.5))},
            })
            for split_rows, tilt in ((pit_vl, 0.93), (pit_vr, 1.08)):
                split_bf = int(bf * RNG.uniform(0.42, 0.58))
                split_rows.append({
                    "player": {"id": int(pid), "fullName": name},
                    "stat": {"battersFaced": split_bf,
                             "strikeOuts": int(split_bf * k_rate * tilt),
                             "hits": int(split_bf * RNG.uniform(0.18, 0.26))},
                })

        dates_games.append({
            "gamePk": game_pk,
            "gameDate": "2025-08-26T23:05:00Z",
            "venue": {"name": f"{home} Park"},
            "teams": {
                "home": {"team": {"abbreviation": home, "name": home},
                         "probablePitcher": probables[home]},
                "away": {"team": {"abbreviation": away, "name": away},
                         "probablePitcher": probables[away]},
            },
            "lineups": {"homePlayers": home_players, "awayPlayers": away_players},
            "weather": {"temp": str(RNG.randint(58, 92)),
                        "wind": f"{RNG.randint(3, 18)} mph, Out To CF"},
            "officials": [{"officialType": "Home Plate",
                           "official": {"fullName": "Angel Hernandez"}}],
        })

    _write("mlb_statsapi", "schedule_default",
           {"dates": [{"date": "2025-08-26", "games": dates_games}]})
    for name, rows in (("hitting_default", hitting), ("pitching_default", pitching),
                       ("hitting_default_vl", hit_vl), ("hitting_default_vr", hit_vr),
                       ("pitching_default_vl", pit_vl), ("pitching_default_vr", pit_vr)):
        _write("mlb_statsapi", name, {"stats": [{"splits": rows}]})

    return player_ids


# ------------------------------------------------------------- football fixtures
def build_nflverse() -> dict[str, str]:
    """Weekly player rows, the same shape nflverse's CSV parses into."""
    rows, ids = [], {}
    index = 0
    for team, roster in _with_depth(NFL_ROSTERS, NFL_DEPTH).items():
        for name, position in roster:
            index += 1
            pid = _pid("nfl", index)
            ids[name] = pid
            # Per-player talent, then week-to-week noise around it.
            tier = RNG.uniform(0.65, 1.35)
            for week in range(1, 10):
                row = {"player_id": pid, "player_display_name": name, "position": position,
                       "recent_team": team, "week": week, "season": SEASON}
                if position == "QB":
                    attempts = max(12, int(RNG.gauss(33 * tier, 5)))
                    row |= {"attempts": attempts,
                            "passing_yards": max(60, int(attempts * RNG.gauss(7.2, 1.3))),
                            "carries": RNG.randint(1, 8),
                            "rushing_yards": RNG.randint(-2, 55),
                            "rushing_tds": 1 if RNG.random() < 0.18 * tier else 0}
                elif position == "RB":
                    carries = max(2, int(RNG.gauss(15 * tier, 4)))
                    targets = max(0, int(RNG.gauss(4 * tier, 2)))
                    receptions = int(targets * RNG.uniform(0.6, 0.9))
                    row |= {"carries": carries,
                            "rushing_yards": max(-4, int(carries * RNG.gauss(4.4, 1.1))),
                            "rushing_tds": 1 if RNG.random() < 0.32 * tier else 0,
                            "targets": targets, "receptions": receptions,
                            "receiving_yards": int(receptions * RNG.uniform(5, 11)),
                            "receiving_air_yards": int(targets * RNG.uniform(1, 5)),
                            "receiving_tds": 1 if RNG.random() < 0.07 else 0}
                else:
                    targets = max(0, int(RNG.gauss(7 * tier, 2.5)))
                    receptions = int(targets * RNG.uniform(0.5, 0.85))
                    row |= {"targets": targets, "receptions": receptions,
                            "receiving_yards": max(0, int(receptions * RNG.gauss(12.5, 3))),
                            "receiving_air_yards": int(targets * RNG.uniform(6, 14)),
                            "receiving_tds": 1 if RNG.random() < 0.26 * tier else 0,
                            "carries": 1 if RNG.random() < 0.08 else 0,
                            "rushing_yards": RNG.randint(0, 14) if RNG.random() < 0.08 else 0}
                rows.append(row)
    _write("nflverse", "weekly_default", rows)
    return ids


def build_cfbd() -> dict[str, str]:
    """CFBD's long-format season stats, plus SP+ ratings and a games list."""
    stats, ids = [], {}
    index = 0
    for team, roster in _with_depth(CFB_ROSTERS, CFB_DEPTH).items():
        for name, position in roster:
            index += 1
            pid = _pid("cfb", index)
            ids[name] = pid
            tier = RNG.uniform(0.7, 1.4)

            def add(category: str, stat_type: str, value: float) -> None:
                stats.append({"playerId": pid, "player": name, "team": team,
                              "category": category, "statType": stat_type,
                              "stat": round(value, 1)})

            if position == "QB":
                attempts = int(RNG.gauss(255 * tier, 40))
                add("passing", "ATT", attempts)
                add("passing", "YDS", attempts * RNG.uniform(7.2, 9.4))
                add("passing", "TD", max(4, int(attempts * RNG.uniform(0.05, 0.09))))
                add("rushing", "CAR", int(RNG.gauss(60, 25)))
                add("rushing", "YDS", RNG.gauss(280, 150))
                add("rushing", "TD", RNG.randint(1, 11))
            elif position == "RB":
                carries = int(RNG.gauss(135 * tier, 30))
                add("rushing", "CAR", carries)
                add("rushing", "YDS", carries * RNG.uniform(4.2, 6.5))
                add("rushing", "TD", RNG.randint(4, 15))
                add("receiving", "REC", RNG.randint(8, 26))
                add("receiving", "YDS", RNG.randint(60, 260))
            else:
                receptions = int(RNG.gauss(44 * tier, 12))
                add("receiving", "REC", receptions)
                add("receiving", "YDS", receptions * RNG.uniform(11.5, 18.0))
                add("receiving", "TD", RNG.randint(2, 12))

    _write("cfbd", "player_season_default", stats)
    _write("cfbd", "ratings_default",
           [{"team": team, "rating": round(RNG.uniform(-4, 30), 1)} for team in CFB_ROSTERS])
    _write("cfbd", "games_default",
           [{"id": 4000 + i, "home_team": h, "away_team": a}
            for i, (h, a) in enumerate(CFB_GAMES)])
    return ids


# ------------------------------------------------------------- market & weather
def build_market() -> None:
    """ESPN scoreboard shape, carrying the spread/total the models key off."""
    def event(index: int, home: str, away: str, spread: float, total: float) -> dict:
        favourite = home if spread < 0 else away
        return {
            "id": str(index),
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "team": {"abbreviation": home}},
                    {"homeAway": "away", "team": {"abbreviation": away}},
                ],
                "odds": [{"overUnder": total, "spread": spread,
                          "details": f"{favourite} {-abs(spread):.1f}"}],
            }],
        }

    _write("market", "nfl_default", {"events": [
        event(100 + i, home, away,
              round(RNG.uniform(-9.5, 6.5) * 2) / 2, round(RNG.uniform(38.5, 52.5) * 2) / 2)
        for i, (home, away) in enumerate(NFL_GAMES)]})
    _write("market", "mlb_default", {"events": [
        event(200 + i, home, away,
              round(RNG.uniform(-2.0, 2.0) * 2) / 2, round(RNG.uniform(6.5, 11.5) * 2) / 2)
        for i, (home, away, _) in enumerate(MLB_GAMES)]})
    _write("market", "cfb_default", {"events": [
        event(300 + i, home, away,
              round(RNG.uniform(-24.5, 7.5) * 2) / 2, round(RNG.uniform(44.5, 68.5) * 2) / 2)
        for i, (home, away) in enumerate(CFB_GAMES)]})


def build_weather() -> None:
    """Open-Meteo hourly forecast shape."""
    hours = [f"2025-08-26T{h:02d}:00" for h in range(24)]
    _write("weather", "forecast_default", {
        "hourly": {
            "time": hours,
            "temperature_2m": [round(64 + 14 * (1 - abs(h - 15) / 15), 1) for h in range(24)],
            "relative_humidity_2m": [RNG.randint(38, 78) for _ in hours],
            "precipitation_probability": [RNG.choice([0, 0, 0, 5, 10, 25]) for _ in hours],
            "wind_speed_10m": [round(RNG.uniform(3, 17), 1) for _ in hours],
            "wind_direction_10m": [RNG.randint(0, 359) for _ in hours],
        }
    })


# ---------------------------------------------------------------- Underdog lines
def build_underdog(mlb_ids: dict, nfl_ids: dict, cfb_ids: dict) -> None:
    """The cross-referenced array shape the real over_under_lines endpoint returns."""
    players, appearances, games, teams, lines = [], [], [], [], []
    seen_teams: dict[str, str] = {}

    def team_id(abbr: str) -> str:
        if abbr not in seen_teams:
            seen_teams[abbr] = f"team-{len(seen_teams) + 1}"
            teams.append({"id": seen_teams[abbr], "abbr": abbr, "name": abbr})
        return seen_teams[abbr]

    def add_line(player_name, pid, sport, position, team, game_id, stat_key, value, multiplier=1.0):
        appearance_id = f"app-{len(appearances) + 1}"
        first, _, last = player_name.partition(" ")
        players.append({"id": pid, "first_name": first, "last_name": last,
                        "sport_id": sport, "position": position, "team_id": team_id(team)})
        appearances.append({"id": appearance_id, "player_id": pid,
                            "match_id": game_id, "match_type": "Game",
                            "team_id": team_id(team)})
        lines.append({
            "id": f"line-{len(lines) + 1}",
            "stat_value": str(value),
            "status": "active",
            "options": [
                {"id": f"opt-{len(lines) * 2 + 1}", "choice": "higher",
                 "payout_multiplier": str(multiplier), "choice_display": "Higher"},
                {"id": f"opt-{len(lines) * 2 + 2}", "choice": "lower",
                 "payout_multiplier": "1.0", "choice_display": "Lower"},
            ],
            "over_under": {
                "id": f"ou-{len(lines) + 1}",
                "title": player_name,
                "appearance_stat": {"appearance_id": appearance_id,
                                    "stat": stat_key, "display_stat": stat_key},
            },
        })

    def add_game(prefix, index, home, away, scheduled):
        game_id = f"{prefix}-game-{index}"
        games.append({"id": game_id, "home_team_id": team_id(home),
                      "away_team_id": team_id(away), "scheduled_at": scheduled,
                      "sport_id": prefix.upper(), "title": f"{away} @ {home}"})
        return game_id

    # --- MLB: pitcher strikeouts and batter 1+ hit -----------------------------
    for i, (home, away, _) in enumerate(MLB_GAMES):
        game_id = add_game("mlb", i, home, away, "2025-08-26T23:05:00Z")
        for team in (home, away):
            pitcher_name = MLB_PITCHERS[team][0]
            add_line(pitcher_name, mlb_ids[pitcher_name], "MLB", "SP", team, game_id,
                     "strikeouts_thrown", RNG.choice([4.5, 5.5, 5.5, 6.5, 6.5, 7.5]))
            for batter in MLB_LINEUPS[team][:6]:
                add_line(batter, mlb_ids[batter], "MLB", "OF", team, game_id, "hits", 0.5)

    # --- NFL --------------------------------------------------------------------
    market_by_position = {
        "QB": [("passing_yards", (215.5, 285.5)), ("rush_rec_tds", (0.5, 0.5))],
        "RB": [("rushing_yards", (44.5, 92.5)), ("receptions", (1.5, 4.5)),
               ("rush_rec_tds", (0.5, 0.5))],
        "WR": [("receiving_yards", (38.5, 88.5)), ("receptions", (2.5, 6.5)),
               ("rush_rec_tds", (0.5, 0.5))],
        "TE": [("receiving_yards", (28.5, 58.5)), ("receptions", (2.5, 5.5)),
               ("rush_rec_tds", (0.5, 0.5))],
    }
    for i, (home, away) in enumerate(NFL_GAMES):
        game_id = add_game("nfl", i, home, away, "2025-11-16T18:00:00Z")
        for team in (home, away):
            for name, position in NFL_ROSTERS[team]:
                for stat_key, (low, high) in market_by_position[position]:
                    value = 0.5 if low == high else round(RNG.uniform(low, high) - 0.5) + 0.5
                    # Occasionally boost a leg, as Underdog does, to exercise the
                    # multiplier path in pricing.
                    multiplier = 1.25 if RNG.random() < 0.06 else 1.0
                    add_line(name, nfl_ids[name], "NFL", position, team, game_id,
                             stat_key, value, multiplier)

    # --- CFB --------------------------------------------------------------------
    for i, (home, away) in enumerate(CFB_GAMES):
        game_id = add_game("cfb", i, home, away, "2025-11-15T20:00:00Z")
        for team in (home, away):
            for name, position in CFB_ROSTERS[team]:
                for stat_key, (low, high) in market_by_position[position]:
                    value = 0.5 if low == high else round(RNG.uniform(low, high) - 0.5) + 0.5
                    add_line(name, cfb_ids[name], "CFB", position, team, game_id,
                             stat_key, value)

    _write("underdog", "over_under_lines", {
        "over_under_lines": lines, "players": players, "appearances": appearances,
        "games": games, "solo_games": [], "teams": teams,
    })


def calibrate_lines() -> None:
    """Second pass: move each posted line next to what the model actually projects.

    A real sportsbook's line sits close to the true mean, and the edges worth betting
    are a couple of percentage points. Random lines produce a demo board where every
    pick shows a 70% edge, which is worse than useless -- it hides whether the pricing
    logic works and it teaches the wrong intuition about what a real edge looks like.

    So we generate placeholder lines, ask the pipeline what it projects, then re-post
    each line near that projection with a small random offset. The resulting board has
    realistic small edges in both directions, which is what the UI should be showing.
    """
    import sys

    sys.path.insert(0, str(FIXTURE_DIR.parent))
    from app.db import init_db, session_scope
    from app.domain import League, Market
    from app.ingest.pipeline import BoardBuilder
    from app.services.settings_store import UserSettings

    init_db()
    projections: dict[str, tuple[float, str]] = {}
    with session_scope() as session:
        for league in (League.MLB, League.NFL, League.CFB):
            board = BoardBuilder(session, UserSettings()).build(league)
            for bet in board.bets:
                if bet.underdog_line_id:
                    projections[bet.underdog_line_id] = (bet.projected_mean, bet.market.value)

    path = FIXTURE_DIR / "underdog" / "over_under_lines.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    threshold_markets = {Market.HITS_1_PLUS.value, Market.ANYTIME_TD.value}
    adjusted = 0
    dropped: set[str] = set()

    for line in payload["over_under_lines"]:
        entry = projections.get(line["id"])
        if entry is None:
            continue
        mean, market = entry
        if market == Market.ANYTIME_TD.value:
            # Underdog posts anytime-TD lines for plausible scorers, not for every
            # player on the field. Keeping the implausible ones would let "this backup
            # will not score" dominate the board with a fake edge.
            if mean < 0.30:
                dropped.add(line["id"])
            continue
        if market in threshold_markets:
            continue  # always a 0.5 line by definition

        # Offset by a few percent of the projection, so some lines are beatable and
        # some are not -- and round to the half-point Underdog actually posts. The
        # offset is clipped at two sigma: sorting a board by edge selects hard for the
        # tail, so unclipped draws would put a handful of 25%-edge fantasies on top and
        # teach a badly wrong intuition about what a real edge looks like.
        sigma = max(mean * 0.05, 0.25)
        offset = max(-2 * sigma, min(2 * sigma, RNG.gauss(0, sigma)))
        posted = max(0.5, round((mean + offset) - 0.5) + 0.5)
        line["stat_value"] = str(posted)
        adjusted += 1

    if dropped:
        payload["over_under_lines"] = [
            line for line in payload["over_under_lines"] if line["id"] not in dropped
        ]

    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(
        f"Calibrated {adjusted} lines to sit near their projections; "
        f"dropped {len(dropped)} implausible anytime-TD lines"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true",
                        help="Fetch real payloads instead (requires DATA_MODE=live).")
    args = parser.parse_args()
    if args.record:
        raise SystemExit(
            "Live recording requires network access to the sports data hosts. "
            "Run with DATA_MODE=live on a machine that can reach them."
        )

    mlb_ids = build_mlb()
    nfl_ids = build_nflverse()
    cfb_ids = build_cfbd()
    build_market()
    build_weather()
    build_underdog(mlb_ids, nfl_ids, cfb_ids)

    total = sum(1 for _ in FIXTURE_DIR.rglob("*.json"))
    print(f"Wrote {total} fixture files to {FIXTURE_DIR}")
    calibrate_lines()


if __name__ == "__main__":
    main()
