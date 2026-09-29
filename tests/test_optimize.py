import datetime as dt
import itertools
import sqlite3

import numpy as np
import pytest

import collect_moneypuck as cm
import optimize as op
import schedule as sch


def brute_force(P):
    W, T = P.shape
    return max(op.expected_weeks([P[w, t] for w, t in enumerate(perm)])
               for perm in itertools.permutations(range(T), W))


def test_expected_weeks():
    assert op.expected_weeks([0.5, 0.5]) == pytest.approx(0.5 + 0.25)
    assert op.expected_weeks([1, 1, 1]) == 3


@pytest.mark.parametrize("seed", range(200))
def test_solver_matches_brute_force(seed):
    rng = np.random.default_rng(seed)
    W, T = 5, 7
    P = rng.uniform(0.35, 0.85, (W, T))
    P[rng.random((W, T)) < 0.15] = 0.0   # équipes qui ne jouent pas cette semaine
    assign, best = op.solve(P)
    assert len(set(assign.tolist())) == W   # jamais deux fois la même équipe
    assert best == pytest.approx(brute_force(P), abs=1e-9)


def test_solver_saves_strong_team_for_when_it_matters():
    # A est forte les deux semaines ; B n'est bonne qu'en semaine 1.
    P = np.array([[0.80, 0.78],
                  [0.80, 0.40]])
    assign, _ = op.solve(P)
    assert assign.tolist() == [1, 0]


def test_fit_strength_recovers_ordering():
    known = {("d", "VAN", "COL"): 0.25, ("d", "COL", "VAN"): 0.70,
             ("d", "VAN", "TOR"): 0.40, ("d", "TOR", "COL"): 0.40}
    s, h = op.fit_strength(known, ridge=0.01)
    assert s["COL"] > s["TOR"] > s["VAN"]
    assert h > 0   # avantage domicile


def test_week_start_is_monday():
    assert op.week_start(dt.date(2026, 10, 4)) == dt.date(2026, 9, 28)   # dimanche
    assert op.week_start(dt.date(2026, 10, 5)) == dt.date(2026, 10, 5)   # lundi


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    cm.init_db(conn)
    sch.init_db(conn)
    conn.executemany("INSERT INTO schedule (game_id, game_date, start_utc, away, home) "
                     "VALUES (?,?,?,?,?)", [
        (1, "2026-10-07", "x", "COL", "BOS"),   # mercredi : jamais permis
        (2, "2026-10-10", "x", "VAN", "COL"),   # samedi (2 matchs) : journée de pick
        (3, "2026-10-10", "x", "SEA", "NYR"),
        (4, "2026-10-11", "x", "COL", "CHI"),   # dimanche (1 match)
        (5, "2026-10-17", "x", "TOR", "VAN"),   # samedi (1 match)
        (6, "2026-10-18", "x", "COL", "TOR"),   # dimanche (2 matchs) : journée de pick
        (7, "2026-10-18", "x", "DAL", "STL"),
        (8, "2026-10-20", "x", "EDM", "CGY"),   # semaine sans match la fin de semaine
    ])
    conn.executemany("INSERT INTO probs VALUES (?,?,?,?,?,?,?,?)", [
        ("2026-10-04", "t", "2026-10-07", "COL", "BOS", 0.90, 0.10, "consensus"),
        ("2026-10-04", "t", "2026-10-10", "VAN", "COL", 0.30, 0.70, "consensus"),
        ("2026-10-04", "t", "2026-10-11", "COL", "CHI", 0.62, 0.38, "consensus"),
        ("2026-10-04", "t", "2026-10-10", "SEA", "NYR", 0.45, 0.55, "consensus"),
    ])
    return conn


W1, W2, W3 = dt.date(2026, 10, 5), dt.date(2026, 10, 12), dt.date(2026, 10, 19)


def test_pick_days(db):
    days = op.pick_days(db)
    assert days[W1] == op.PickDay(("2026-10-10",), 2, 1, "auto")
    assert days[W2] == op.PickDay(("2026-10-18",), 1, 2, "auto")   # dimanche l'emporte
    assert days[W3] == op.PickDay((), 0, 0, "aucun match")
    assert op.pick_days(db, {"2026-10-05": "2026-10-11"})[W1].how == "choisi"
    db.execute("INSERT INTO schedule (game_id, game_date, start_utc, away, home) "
               "VALUES (9, '2026-10-11', 'x', 'MTL', 'OTT')")
    assert op.pick_days(db)[W1] == op.PickDay(("2026-10-10", "2026-10-11"), 2, 2, "égalité")


def test_build_options_only_pick_day(db):
    weeks, snap, _s, _h = op.build_options(db, W1)
    assert snap == "2026-10-04"
    col = weeks[W1]["COL"]
    # Le match de mercredi (90 %) et celui de dimanche sont exclus : samedi seulement
    assert (col.game_date, col.opponent, col.home, col.p) == ("2026-10-10", "VAN", True, 0.70)
    assert "BOS" not in weeks[W1]
    # Semaine 2 : dimanche ; pas de consensus pour ce match → estimation du modèle
    assert weeks[W2]["TOR"].game_date == "2026-10-18"
    assert weeks[W2]["TOR"].source == "estimé"
    assert W3 not in weeks   # semaine sautée


def test_build_options_override_day(db):
    weeks, *_ = op.build_options(db, W1, overrides={"2026-10-05": "2026-10-11"})
    col = weeks[W1]["COL"]
    assert (col.game_date, col.opponent, col.p) == ("2026-10-11", "CHI", 0.62)


def test_build_options_after_pick_day_and_used(db):
    weeks, *_ = op.build_options(db, dt.date(2026, 10, 11), used={"TOR"})
    assert W1 not in weeks            # samedi passé : la semaine est terminée
    assert "TOR" not in weeks[W2]


def test_optimize_never_reuses_teams(db):
    plan, exp_weeks, _ = op.optimize(db, dt.date(2026, 10, 5), used={"CHI"})
    teams = [o.team for _m, o in plan if o]
    assert len(teams) == len(set(teams)) == 2
    assert "CHI" not in teams and exp_weeks > 0


def test_alternatives_best_matches_optimize(db):
    plan, exp_weeks, _ = op.optimize(db, dt.date(2026, 10, 5))
    alts = op.alternatives(db, dt.date(2026, 10, 5))
    assert alts[0][0].team == plan[0][1].team
    assert alts[0][1] == pytest.approx(exp_weeks)
    assert all(a[1] >= b[1] for a, b in zip(alts, alts[1:]))


def test_load_known_specific_snapshot(db):
    db.execute("INSERT INTO probs VALUES ('2026-10-03','t','2026-10-05','VAN','COL',0.4,0.6,'consensus')")
    known, snap = op.load_known(db, snapshot="2026-10-03")
    assert snap == "2026-10-03" and known == {("2026-10-05", "VAN", "COL"): 0.4}
