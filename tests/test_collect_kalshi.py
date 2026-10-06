import datetime as dt
import sqlite3

import collect_kalshi as k
import collect_moneypuck as cm


def marche(ticker, bid=None, ask=None):
    """Un marche tel que l'API le rend vraiment : prix en texte, ou absents
    du JSON (pas a 0) sans liquidite."""
    m = {"ticker": ticker}
    if bid is not None:
        m["yes_bid_dollars"] = f"{bid:.4f}"
    if ask is not None:
        m["yes_ask_dollars"] = f"{ask:.4f}"
    return m


# ── Lecture du ticker ──────────────────────────────────────────────────────

def test_le_ticker_donne_la_date_et_les_deux_equipes():
    assert k.parse_ticker("KXNHLGAME-26OCT03STLCOL-COL") == (
        "2026-10-03", "STL", "COL", "COL")


def test_une_autre_serie_est_ignoree():
    """KXNHLSPREAD par exemple : meme prefixe de date, autre marche."""
    assert k.parse_ticker("KXNHLSPREAD-26OCT03STLCOL-COL") is None


def test_un_ticker_malforme_ne_fait_pas_planter():
    assert k.parse_ticker("nimporte quoi") is None
    assert k.parse_ticker("KXNHLGAME-26XXX03STLCOL-COL") is None   # mois invalide


# ── Prix ────────────────────────────────────────────────────────────────

def test_les_prix_sont_en_texte_pas_en_nombre():
    """Kalshi rend « 0.7000 », pas 0.7 : de l'argent reste en chaine le plus
    longtemps possible, pour ne pas heriter des arrondis du binaire."""
    assert k.prix_milieu(marche("x", bid=0.70, ask=0.72)) == 0.71


def test_sans_aucune_liquidite_on_rend_none():
    """bid/ask sont ABSENTS du JSON sans acheteur ni vendeur, jamais a 0 —
    sinon un marche tout neuf se lirait comme une victoire certaine."""
    assert k.prix_milieu(marche("x")) is None


def test_avec_un_seul_cote_on_prend_ce_qui_existe():
    assert k.prix_milieu(marche("x", bid=0.60)) == 0.60
    assert k.prix_milieu(marche("x", ask=0.65)) == 0.65


# ── Assemblage des deux marches d'un match ────────────────────────────────

def test_les_deux_marches_dun_match_se_combinent():
    payload = {"markets": [
        marche("KXNHLGAME-26OCT03STLCOL-COL", bid=0.70, ask=0.72),
        marche("KXNHLGAME-26OCT03STLCOL-STL", bid=0.27, ask=0.30),
    ]}
    games = k.parse_markets(payload, aujourdhui=dt.date(2026, 10, 1))
    pa, ph = games[("2026-10-03", "STL", "COL")]
    assert ph > pa                      # Colorado (domicile) favori
    assert pa + ph == 1                 # normalise, meme si Kalshi donne ~1.006


def test_sans_les_deux_cotes_le_match_est_ignore():
    """Un seul marche sans liquidite suffit a rendre le match inutilisable :
    impossible de savoir quelle part de 100% il represente."""
    payload = {"markets": [
        marche("KXNHLGAME-26OCT03STLCOL-COL", bid=0.70, ask=0.72),
        marche("KXNHLGAME-26OCT03STLCOL-STL"),      # pas encore de liquidite
    ]}
    assert k.parse_markets(payload, aujourdhui=dt.date(2026, 10, 1)) == {}


def test_les_matchs_passes_sont_ignores():
    payload = {"markets": [
        marche("KXNHLGAME-26OCT01STLCOL-COL", bid=0.70, ask=0.72),
        marche("KXNHLGAME-26OCT01STLCOL-STL", bid=0.27, ask=0.30),
    ]}
    assert k.parse_markets(payload, aujourdhui=dt.date(2026, 10, 2)) == {}


def test_un_marche_hors_serie_nhl_est_ignore_silencieusement():
    """Pas d'erreur : la liste peut un jour contenir un autre type de
    contrat sans que la collecte du jour casse pour autant."""
    payload = {"markets": [marche("KXSOMETHINGELSE-ABC", bid=0.5, ask=0.6)]}
    assert k.parse_markets(payload, aujourdhui=dt.date(2026, 10, 1)) == {}


# ── Collecte de bout en bout, avec un faux réseau ─────────────────────────

class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.text = payload, status, str(payload)

    def json(self):
        return self.payload


class FakeSession:
    """Une seule page : pas de curseur de pagination."""

    def __init__(self, markets):
        self.markets = markets

    def get(self, url, params=None, timeout=None):
        return FakeResponse({"markets": self.markets, "cursor": None})


def test_collecte_ecrit_dans_probs_avec_la_source_kalshi(tmp_path):
    db = tmp_path / "t.db"
    session = FakeSession([
        marche("KXNHLGAME-26OCT03STLCOL-COL", bid=0.70, ask=0.72),
        marche("KXNHLGAME-26OCT03STLCOL-STL", bid=0.27, ask=0.30),
    ])
    games = k.collect_kalshi(db, today=dt.date(2026, 10, 1), session=session)
    assert ("2026-10-03", "STL", "COL") in games

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT source, away, home FROM probs WHERE source='kalshi'").fetchone()
    assert row == ("kalshi", "STL", "COL")


def test_une_erreur_http_est_signalee_clairement(tmp_path):
    session = FakeSession([])
    session.get = lambda *a, **kw: FakeResponse({}, status=500)
    try:
        k.collect_kalshi(tmp_path / "t.db", session=session)
        assert False, "aurait du lever KalshiError"
    except k.KalshiError as e:
        assert "500" in str(e)


def test_la_pagination_est_suivie():
    class SessionPaginee:
        def __init__(self):
            self.appels = []

        def get(self, url, params=None, timeout=None):
            self.appels.append(params.get("cursor"))
            if params.get("cursor") is None:
                return FakeResponse({"markets": [marche("KXNHLGAME-26OCT03STLCOL-COL",
                                                        bid=0.7, ask=0.72)],
                                     "cursor": "page2"})
            return FakeResponse({"markets": [marche("KXNHLGAME-26OCT03STLCOL-STL",
                                                     bid=0.27, ask=0.30)],
                                 "cursor": None})

    session = SessionPaginee()
    payload = k.fetch_markets(session)
    assert len(payload["markets"]) == 2
    assert session.appels == [None, "page2"]


# ── Les quatre équipes dont Kalshi n'utilise pas le tricode de la LNH ─────
# Bogue du 6 octobre : l'ancien motif exigeait trois lettres par équipe, donc
# NJ, LA, SJ et TB étaient écartées sans un mot — 18 tickers sur 92, dont tous
# les matchs des Devils, choisis par Marek cette semaine-là. Les tickers
# ci-dessous sont réels, lus sur l'API.

def test_les_devils_sont_lus_malgre_le_code_NJ():
    assert k.parse_ticker("KXNHLGAME-26OCT10VANNJ-NJ") == (
        "2026-10-10", "VAN", "NJD", "NJD")
    assert k.parse_ticker("KXNHLGAME-26OCT10VANNJ-VAN") == (
        "2026-10-10", "VAN", "NJD", "VAN")


def test_les_quatre_codes_differents_sont_traduits():
    assert k.parse_ticker("KXNHLGAME-26OCT10LAVGK-LA")[1] == "LAK"
    assert k.parse_ticker("KXNHLGAME-26OCT10TBNYI-TB")[1] == "TBL"
    assert k.parse_ticker("KXNHLGAME-26OCT10EDMSJ-SJ")[2] == "SJS"


def test_une_paire_de_longueur_variable_se_coupe_sans_ambiguite():
    """« VANNJ » n'a pas de séparateur et 5 lettres : le suffixe du ticker
    nomme l'une des équipes, on la retire, il reste l'autre."""
    assert k.parse_ticker("KXNHLGAME-26OCT12OTTNJ-OTT")[1:3] == ("OTT", "NJD")
    assert k.parse_ticker("KXNHLGAME-26OCT12OTTNJ-NJ")[1:3] == ("OTT", "NJD")


def test_une_equipe_inconnue_est_rejetee_pas_inventee():
    assert k.parse_ticker("KXNHLGAME-26OCT10ZZZNJ-ZZZ") is None


def test_les_tickers_illisibles_sont_signales_pas_avales():
    payload = {"markets": [
        marche("KXNHLGAME-26OCT10ZZZNJ-ZZZ", bid=0.5, ask=0.6),
        marche("KXNHLGAME-26OCT10VANNJ-NJ", bid=0.65, ask=0.67),
        marche("KXNHLGAME-26OCT10VANNJ-VAN", bid=0.33, ask=0.35),
    ]}
    illisibles = []
    games = k.parse_markets(payload, dt.date(2026, 10, 6), illisibles)
    assert illisibles == ["KXNHLGAME-26OCT10ZZZNJ-ZZZ"]
    assert ("2026-10-10", "VAN", "NJD") in games


def test_tous_les_codes_de_kalshi_sont_connus():
    """Chaque cible de la traduction est un vrai tricode de la LNH."""
    import collect_moneypuck as cm
    assert set(k.CODES_KALSHI.values()) <= set(cm.TEAMS)
