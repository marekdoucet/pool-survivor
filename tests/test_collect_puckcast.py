import datetime as dt
import sqlite3
from pathlib import Path

import pytest

import collect_puckcast as cp
import store

HTML = (Path(__file__).parent / "fixtures" / "puckcast_games.htm").read_text(encoding="utf-8")
TODAY = dt.date(2026, 9, 29)


def test_parse_real_extract():
    assert cp.parse_page(HTML) == {
        2026020001: ("CAR", 0.639), 2026020002: ("MTL", 0.57),
        2026020004: ("EDM", 0.669), 2026020005: ("VGK", 0.634),
    }


def test_empty_page_is_an_error():
    with pytest.raises(cp.PuckcastError):
        cp.parse_page("<html>maintenance</html>")


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    store.init_db(conn)
    conn.executemany("INSERT INTO schedule VALUES (?,?,?,?,?,?,?,?)", [
        (2026020001, "2026-09-29", "x", "FLA", "CAR", None, None, None),
        (2026020002, "2026-09-29", "x", "MTL", "TOR", None, None, None),
        (2026020004, "2026-09-29", "x", "VAN", "EDM", 2, 5, "REG"),   # déjà joué : ignoré
        (2026020009, "2026-10-01", "x", "BOS", "NYR", None, None, None),  # pas chez Puckcast
    ])
    conn.commit()
    return conn


def test_to_probs_orients_on_schedule(db):
    probs = cp.to_probs(cp.parse_page(HTML), db, TODAY)
    assert probs == {
        ("2026-09-29", "FLA", "CAR"): (pytest.approx(0.361), pytest.approx(0.639)),
        ("2026-09-29", "MTL", "TOR"): (0.57, pytest.approx(0.43)),   # favori visiteur
    }


def test_favorite_not_in_game_is_an_error(db):
    with pytest.raises(cp.PuckcastError, match="favori"):
        cp.to_probs({2026020001: ("BOS", 0.6)}, db, TODAY)


class FakeSession:
    def get(self, url, **kw):
        return type("R", (), {"text": HTML, "raise_for_status": lambda self: None})()


def test_collect_replaces_snapshot(tmp_path, db):
    path = tmp_path / "t.db"
    cp.collect_puckcast(path, today=TODAY, session=FakeSession())
    cp.collect_puckcast(path, today=TODAY, session=FakeSession())   # relance : pas de doublon
    rows = sqlite3.connect(path).execute(
        "SELECT away, home, p_away FROM probs WHERE source='puckcast' ORDER BY away").fetchall()
    assert rows == [("FLA", "CAR", 0.361), ("MTL", "TOR", 0.57)]
