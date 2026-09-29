import datetime as dt
import sqlite3
from pathlib import Path

import pytest

import collect_moneypuck as cm

FIX = Path(__file__).parent / "fixtures"


def read(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_upcoming_games_left_is_away():
    games, skipped = cm.parse_day(read("upcoming_3games.htm"))
    assert skipped == 0
    assert games == [
        ("PIT", "WSH", 0.41, 0.59),
        ("COL", "WPG", 0.638, 0.362),
        ("EDM", "ANA", 0.549, 0.451),
    ]


def test_preview_rows_without_label():
    # Seule la 1re ligne porte « Chance of Winning » ; les autres ont un lien Preview.
    games, skipped = cm.parse_day(read("preview_rows.htm"))
    assert skipped == 0 and len(games) == 5
    assert games[1] == ("MTL", "TOR", 0.507, 0.493)


def test_row_without_probs_or_score_is_an_error():
    html = read("upcoming_3games.htm").replace("41.0%", "").replace("59.0%", "")
    with pytest.raises(cm.ParseError, match="ni probabilités ni score"):
        cm.parse_day(html)


def test_finished_games_are_skipped():
    games, skipped = cm.parse_day(read("finished.htm"))
    assert games == []
    assert skipped == 6


def test_mixed_day_keeps_upcoming_games():
    # Le soir : certains matchs terminés, d'autres à venir sur la même page.
    finished_rows = cm.RE_ROW.findall(read("finished.htm"))[:2]
    upcoming = read("upcoming_3games.htm")
    html = upcoming.replace("</table>", "".join(finished_rows) + "</table>")
    games, skipped = cm.parse_day(html)
    assert len(games) == 3 and skipped == 2


def test_empty_day():
    assert cm.parse_day(read("empty.htm")) == ([], 0)


def test_non_table_page_is_an_error():
    with pytest.raises(cm.ParseError):
        cm.parse_day("<html>Checking your browser...</html>")


def test_unknown_team_is_an_error():
    html = read("upcoming_3games.htm").replace("logos/PIT.png", "logos/XYZ.png")
    with pytest.raises(cm.ParseError, match="inconnu"):
        cm.parse_day(html)


class FakeResponse:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status

    def raise_for_status(self):
        pass


class FakeSession:
    """Renvoie une page par date ; None simule une erreur réseau."""

    def __init__(self, pages):
        self.pages = pages

    def get(self, url, **kw):
        page = self.pages.get(url[-12:-4])
        if page is None:
            raise cm.requests.ConnectionError("réseau")
        return FakeResponse(page)


def test_collect_snapshots_and_rerun(tmp_path, monkeypatch):
    monkeypatch.setattr(cm.time, "sleep", lambda s: None)
    db = tmp_path / "t.db"
    d0 = dt.date(2026, 10, 7)
    pages = {"20261007": read("upcoming_3games.htm"), "20261008": read("empty.htm")}

    total, errors = cm.collect(2, db, today=d0, session=FakeSession(pages))
    assert (total, errors) == (3, [])

    # Relance le même jour avec un match retiré : l'instantané est remplacé.
    one_row = cm.RE_ROW.findall(pages["20261007"])[0]
    pages["20261007"] = f"<table>{one_row}</table>"
    cm.collect(2, db, today=d0, session=FakeSession(pages))

    # Instantané du lendemain : s'ajoute sans écraser celui de la veille.
    pages["20261008"] = read("upcoming_3games.htm")
    cm.collect(2, db, today=d0 + dt.timedelta(days=1),
               session=FakeSession({"20261008": pages["20261008"], "20261009": ""}))

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT snapshot, game_date, COUNT(*) FROM probs GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()
    assert rows == [("2026-10-07", "2026-10-07", 1), ("2026-10-08", "2026-10-08", 3)]


def test_network_error_keeps_previous_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(cm.time, "sleep", lambda s: None)
    db = tmp_path / "t.db"
    d0 = dt.date(2026, 10, 7)
    cm.collect(1, db, today=d0, session=FakeSession({"20261007": read("upcoming_3games.htm")}))

    total, errors = cm.collect(1, db, today=d0, session=FakeSession({}))
    assert errors == [d0]
    n = sqlite3.connect(db).execute("SELECT COUNT(*) FROM probs").fetchone()[0]
    assert n == 3
