import sqlite3

import pytest

import form
import store


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    store.init_db(conn)
    games = [  # (id, date, away, home, score_a, score_h, période)
        (1, "2026-10-01", "VAN", "EDM", 1, 4, "REG"),
        (2, "2026-10-03", "EDM", "CGY", 3, 2, "OT"),    # victoire en prolongation
        (3, "2026-10-05", "SEA", "EDM", 2, 5, "REG"),
        (4, "2026-10-07", "EDM", "VAN", 2, 3, "SO"),    # défaite en tirs de barrage
        (5, "2026-10-09", "EDM", "COL", None, None, None),   # pas encore joué
    ]
    conn.executemany("INSERT INTO schedule VALUES (?,?,?,?,?,?,?,?)",
                     [(i, d, f"{d}T23:00:00Z", a, h, sa, sh, p) for i, d, a, h, sa, sh, p in games])
    return conn


def test_streaks_and_record(db):
    f = form.team_forms(db)
    edm = f["EDM"]
    assert (edm.wins, edm.losses) == (3, 1)
    assert edm.streak == -1 and edm.streak_label == "D1"
    assert f["VAN"].streak == 1 and f["VAN"].streak_label == "V1"
    assert f["TOR"].streak == 0 and f["TOR"].streak_label == "—"
    assert edm.last10 == "3-1"


def test_win_streak_counts_ot():
    t = form.TeamForm("X", [("d", "Y", True, w) for w in (False, True, True, True, True)])
    assert t.streak == 4 and t.streak_label == "V4"


def test_rating_centered():
    r = form.rating({"A": 1.0, "B": -1.0, "C": 0.0})
    assert r["C"] == pytest.approx(0.5)
    assert r["A"] > 0.5 > r["B"]
    assert r["A"] + r["B"] == pytest.approx(1)


def test_expected_vs_actual_uses_last_pregame_snapshot(db):
    rows = [
        ("2026-09-28", "2026-10-01", "VAN", "EDM", 0.30, "consensus"),   # projection initiale
        ("2026-10-01", "2026-10-01", "VAN", "EDM", 0.25, "consensus"),   # jour du match : retenue
        ("2026-10-02", "2026-10-03", "EDM", "CGY", 0.60, "moneypuck"),   # pas de consensus
        ("2026-10-06", "2026-10-05", "SEA", "EDM", 0.10, "consensus"),   # après le match : ignorée
    ]
    db.executemany("INSERT INTO probs VALUES (?,'t',?,?,?,?,1-?,?)",
                   [(s, g, a, h, p, p, src) for s, g, a, h, p, src in rows])
    ev = form.expected_vs_actual(db)
    assert [(g, round(p, 2), w) for g, _o, p, w in ev["EDM"]] == [
        ("2026-10-01", 0.75, True), ("2026-10-03", 0.60, True)]
    assert ev["VAN"] == [("2026-10-01", "EDM", 0.25, False)]


def test_source_accuracy(db):
    rows = [  # (collecte, date, visiteur, local, p_visiteur, source)
        ("2026-09-30", "2026-10-01", "VAN", "EDM", 0.20, "moneypuck"),   # EDM gagne : bon
        ("2026-09-30", "2026-10-01", "VAN", "EDM", 0.60, "puckcast"),    # mauvais favori
        ("2026-09-30", "2026-10-01", "VAN", "EDM", 0.30, "market"),
        ("2026-10-02", "2026-10-03", "EDM", "CGY", 0.70, "moneypuck"),   # EDM gagne : bon
    ]
    db.executemany("INSERT INTO probs VALUES (?,'t',?,?,?,?,1-?,?)",
                   [(s, g, a, h, p, p, src) for s, g, a, h, p, src in rows])
    acc = {r[0]: r[1:] for r in form.source_accuracy(db, ["moneypuck", "puckcast", "market", "dimers"])}
    n, brier, hits, brier_c, brier_m = acc["moneypuck"]
    assert n == 2 and hits == 1.0
    assert brier == pytest.approx((0.2 ** 2 + 0.3 ** 2) / 2)
    assert brier_c == pytest.approx(0.04) and brier_m == pytest.approx(0.09)
    assert acc["puckcast"][2] == 0.0          # 0 favori gagnant sur 1
    assert acc["dimers"] == (0, None, None, None, None)
