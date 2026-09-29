import datetime as dt
import sqlite3
from pathlib import Path

import pytest

import collect_dimers as cd
import collect_moneypuck as cm
import collect_odds as co

HTML = (Path(__file__).parent / "fixtures" / "dimers_schedule.htm").read_text(encoding="utf-8")
BEFORE = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.timezone.utc)


def test_parse_real_page():
    games = cd.parse_page(HTML, now=BEFORE)
    assert len(games) == 5
    pa, ph = games[("2026-09-29", "FLA", "CAR")]
    assert pa + ph == pytest.approx(1)
    assert ph == pytest.approx(0.5792, abs=1e-3)
    # 02 h 30 UTC le 30 = 22 h 30 HE le 29 : reste au 29 septembre
    assert {g for g, _a, _h in games} == {"2026-09-29"}


def test_started_games_skipped():
    after_first = dt.datetime(2026, 9, 29, 22, 0, tzinfo=dt.timezone.utc)
    assert len(cd.parse_page(HTML, now=after_first)) == 4


def test_page_without_state_is_an_error():
    with pytest.raises(cd.DimersError):
        cd.parse_page("<html>maintenance</html>")


def test_consensus_three_sources_renormalized(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.db")
    co.init_db(conn)
    rows = [
        ("A", "VAN", "COL", 0.30, "moneypuck"), ("A", "VAN", "COL", 0.20, "dimers"),
        ("A", "VAN", "COL", 0.25, "market"),
        ("B", "TOR", "MTL", 0.50, "moneypuck"), ("B", "TOR", "MTL", 0.60, "dimers"),
        ("C", "SEA", "CHI", 0.45, "moneypuck"),
    ]
    conn.executemany("INSERT INTO probs VALUES ('s','t',?,?,?,?,1-?,?)",
                     [(g, a, h, p, p, s) for g, a, h, p, s in rows])
    w = {"market": 0.6, "moneypuck": 0.2, "dimers": 0.2}
    assert co.build_consensus(conn, "s", w) == (1, 2)
    cons = dict(((g), pa) for g, pa in conn.execute(
        "SELECT game_date, p_away FROM probs WHERE source='consensus'"))
    assert cons["A"] == pytest.approx(0.6 * 0.25 + 0.2 * 0.30 + 0.2 * 0.20, abs=1e-4)
    assert cons["B"] == pytest.approx(0.55)   # poids 0,2 / 0,2 renormalisés
    assert cons["C"] == 0.45


@pytest.mark.parametrize("market, nick, code", [
    ("NY", "Rangers", "NYR"), ("NY", "Islanders", "NYI"), ("Montréal", "Canadiens", "MTL"),
    ("LA", "Kings", "LAK"), ("St. Louis", "Blues", "STL"), ("Utah", "Mammoth", "UTA"),
    ("TB", "Lightning", "TBL"), ("Vegas", "Golden Knights", "VGK"),
])
def test_team_code_short_markets(market, nick, code):
    assert cd.team_code({"Market": market, "Nickname": nick}) == code
