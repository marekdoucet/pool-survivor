import datetime as dt
import sqlite3

import pytest

import collect_moneypuck as cm
import collect_odds as co

NOW = dt.datetime(2026, 10, 7, 12, 0, tzinfo=dt.timezone.utc)


def event(away, home, start, books):
    """Construit un événement au format de The Odds API v4."""
    return {
        "id": f"{away}-{home}", "sport_key": "icehockey_nhl",
        "commence_time": start, "away_team": away, "home_team": home,
        "bookmakers": [
            {"key": key, "title": key, "last_update": start, "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": home, "price": ph}, {"name": away, "price": pa}]}]}
            for key, pa, ph in books
        ],
    }


EVENTS = [
    # 23 h 00 UTC = 19 h 00 HE le 7 octobre
    event("Pittsburgh Penguins", "Washington Capitals", "2026-10-07T23:00:00Z",
          [("pinnacle", 2.40, 1.62), ("draftkings", 2.35, 1.60)]),
    # 02 h 00 UTC le 8 = 22 h 00 HE le 7 : doit rester au 7 octobre
    event("Edmonton Oilers", "Anaheim Ducks", "2026-10-08T02:00:00Z",
          [("fanduel", 1.80, 2.05)]),
    # Déjà commencé : ignoré
    event("Montréal Canadiens", "Toronto Maple Leafs", "2026-10-07T11:00:00Z",
          [("fanduel", 2.10, 1.75)]),
]


def test_team_code_handles_accents_and_dots():
    assert co.team_code("Montréal Canadiens") == "MTL"
    assert co.team_code("St. Louis Blues") == "STL"
    assert co.team_code("Utah Hockey Club") == co.team_code("Utah Mammoth") == "UTA"
    assert co.team_code("Quebec Nordiques") is None


def test_all_32_teams_mapped():
    assert set(co.TEAM_CODES.values()) == cm.TEAMS


def test_devig_removes_margin():
    pa, ph = co.devig(1.91, 1.91)
    assert pa == pytest.approx(0.5) and pa + ph == pytest.approx(1)
    pa, ph = co.devig(2.40, 1.62)
    assert pa + ph == pytest.approx(1)
    assert pa < 1 / 2.40  # la marge est retirée proportionnellement


def test_parse_events():
    rows, unknown = co.parse_events(EVENTS, now=NOW)
    assert unknown == []
    assert {(r[0], r[1], r[2], r[3]) for r in rows} == {
        ("2026-10-07", "PIT", "WSH", "pinnacle"),
        ("2026-10-07", "PIT", "WSH", "draftkings"),
        ("2026-10-07", "EDM", "ANA", "fanduel"),  # date en heure de l'Est
    }


def test_unknown_team_reported_not_crashing():
    ev = event("Quebec Nordiques", "Boston Bruins", "2026-10-08T23:00:00Z",
               [("pinnacle", 3.0, 1.4)])
    rows, unknown = co.parse_events(EVENTS + [ev], now=NOW)
    assert unknown == ["Quebec Nordiques"] and len(rows) == 3


class FakeResponse:
    def __init__(self, payload, status=200, remaining="498"):
        self.payload, self.status_code = payload, status
        self.headers = {"x-requests-remaining": remaining}
        self.text = str(payload)

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, response):
        self.response, self.params = response, None

    def get(self, url, **kw):
        self.params = kw["params"]
        return self.response


def seed_moneypuck(db, snapshot):
    conn = sqlite3.connect(db)
    cm.init_db(conn)
    cm.save_day(conn, snapshot, "t", dt.date(2026, 10, 7), [
        ("PIT", "WSH", 0.41, 0.59),
        ("EDM", "ANA", 0.549, 0.451),
        ("COL", "WPG", 0.638, 0.362),   # pas encore coté par les casinos
    ])
    conn.close()


def test_collect_and_consensus(tmp_path):
    db = tmp_path / "t.db"
    today = dt.date(2026, 10, 7)
    seed_moneypuck(db, today.isoformat())

    session = FakeSession(FakeResponse(EVENTS))
    market = co.collect_odds("cle", db, today=today, session=session, now=NOW)
    assert session.params["regions"] == "us,eu" and session.params["markets"] == "h2h"

    pit = market[("2026-10-07", "PIT", "WSH")][0]
    expected = (co.devig(2.40, 1.62)[0] + co.devig(2.35, 1.60)[0]) / 2
    assert pit == pytest.approx(expected, abs=1e-4)

    conn = sqlite3.connect(db)
    weights = {"market": 0.7, "moneypuck": 0.3}
    assert co.build_consensus(conn, today.isoformat(), weights) == (2, 1)
    cons = dict(((a, h), pa) for a, h, pa in conn.execute(
        "SELECT away, home, p_away FROM probs WHERE source='consensus'"))
    assert cons[("PIT", "WSH")] == pytest.approx(0.7 * expected + 0.3 * 0.41, abs=1e-4)
    assert cons[("COL", "WPG")] == 0.638  # MoneyPuck seul

    # Relance le même jour : remplace au lieu de dupliquer. delai_mini=0 pour
    # neutraliser le garde-fou, qui sauterait sinon l'appel et ferait passer ce
    # test sans qu'il teste plus rien.
    co.collect_odds("cle", db, today=today, session=session, now=NOW,
                    delai_mini=dt.timedelta(0))
    co.build_consensus(conn, today.isoformat())
    n_odds, n_cons = conn.execute(
        "SELECT (SELECT COUNT(*) FROM odds), "
        "(SELECT COUNT(*) FROM probs WHERE source='consensus')").fetchone()
    assert (n_odds, n_cons) == (3, 3)


def test_bad_key_gives_clear_error(tmp_path):
    resp = FakeResponse({"message": "API key is not valid."}, status=401)
    with pytest.raises(co.OddsError, match="401.*not valid"):
        co.collect_odds("mauvaise", tmp_path / "t.db", session=FakeSession(resp))


# ── Le filet de rattrapage ────────────────────────────────────────────────
# Les crons vont par paires : une exécution principale, un rattrapage une heure
# plus tard si GitHub a sauté la première. Le rattrapage ne doit pas racheter
# les cotes, sinon il double la facture (248 crédits/mois au lieu de 124).

class SessionComptee(FakeSession):
    def __init__(self, response):
        super().__init__(response)
        self.appels = 0

    def get(self, url, **kw):
        self.appels += 1
        return super().get(url, **kw)


def collecte(db, session, heures_apres_minuit, delai=None):
    """Une collecte à une heure précise du 7 octobre, en UTC."""
    t = dt.datetime(2026, 10, 7, tzinfo=dt.timezone.utc) + dt.timedelta(hours=heures_apres_minuit)
    kw = {"delai_mini": delai} if delai is not None else {}
    return co.collect_odds("cle", db, today=dt.date(2026, 10, 7), session=session,
                           now=NOW, maintenant=t, **kw)


def test_le_rattrapage_une_heure_apres_evite_lappel_api(tmp_path):
    db = tmp_path / "t.db"
    session = SessionComptee(FakeResponse(EVENTS))

    assert collecte(db, session, 8) is not None       # principale : 8 h UTC
    assert collecte(db, session, 9) is None           # rattrapage : 9 h UTC
    assert session.appels == 1                        # un seul crédit dépensé

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM odds").fetchone()[0] == 3
    conn.close()


def test_le_rattrapage_collecte_si_la_principale_a_ete_sautee(tmp_path):
    """GitHub saute la collecte du soir : celle de 23 h doit prendre le relais."""
    db = tmp_path / "t.db"
    session = SessionComptee(FakeResponse(EVENTS))

    collecte(db, session, 8)                          # matin
    assert collecte(db, session, 23) is not None      # soir, 15 h plus tard
    assert session.appels == 2


def test_une_base_vide_collecte_toujours(tmp_path):
    session = SessionComptee(FakeResponse(EVENTS))
    assert collecte(tmp_path / "vide.db", session, 8) is not None
    assert session.appels == 1


def test_le_seuil_couvre_les_deux_collectes_quotidiennes():
    """13 h séparent les deux vraies collectes, 1 h sépare un rattrapage."""
    assert dt.timedelta(hours=1) < co.DELAI_MINI < dt.timedelta(hours=13)
