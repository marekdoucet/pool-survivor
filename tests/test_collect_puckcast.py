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


# ── Affinage par la page de match (gardiens confirmés) ──────────────────────
# Trouvé le 2 octobre : la page de saison restait figée à 64,3 % pour COL,
# alors que la page de match individuelle affichait 58,7 %, gardiens
# confirmés. La page de saison est générée une fois et ne bouge plus ; la
# page de match se met à jour à l'approche de la mise au jeu.

ARIA_COL_STL = (
    '<div aria-label="Win probability: Blues 41.3%, Avalanche 58.7%">x</div>')
ARIA_SANS_WIDGET = "<html><body>match déjà joué, pas de pronostic</body></html>"


def test_nom_vers_code_reconnait_le_surnom_seul():
    assert cp.nom_vers_code("Avalanche") == "COL"
    assert cp.nom_vers_code("Maple Leafs") == "TOR"   # surnom à deux mots


def test_parse_matchup_lit_les_deux_equipes():
    assert cp.parse_matchup(ARIA_COL_STL) == {"STL": 0.413, "COL": 0.587}


def test_parse_matchup_sans_widget_rend_vide():
    """Les matchs déjà joués perdent le widget : pas d'erreur, juste rien à
    affiner — la page de saison reste la seule source pour eux."""
    assert cp.parse_matchup(ARIA_SANS_WIDGET) == {}


class FakeSessionMatchup:
    """Sert la page de saison à l'URL de saison, et un contenu par match aux
    URL de match — contrairement au FakeSession plus haut, qui sert la même
    page partout et ne peut donc pas tester l'affinage."""

    def __init__(self, par_id):
        self.par_id = par_id
        self.demandes = []

    def get(self, url, **kw):
        self.demandes.append(url)
        if url == cp.URL:
            html = HTML
        else:
            gid = int(url.rsplit("/", 1)[-1])
            html = self.par_id.get(gid, ARIA_SANS_WIDGET)
        return type("R", (), {"text": html, "raise_for_status": lambda self: None})()


def test_affiner_horizon_proche_remplace_le_chiffre_de_la_saison(db):
    """Le cœur du correctif : le chiffre affiné doit l'emporter sur celui de
    la page de saison pour un match dans l'horizon."""
    probs = {("2026-09-29", "FLA", "CAR"): (0.361, 0.639)}
    session = FakeSessionMatchup({
        2026020001: '<div aria-label="Win probability: Panthers 20.0%, Hurricanes 80.0%">x</div>',
    })
    affine = cp.affiner_horizon_proche(dict(probs), db, TODAY, session)
    assert affine[("2026-09-29", "FLA", "CAR")] == (0.2, 0.8)


def test_affiner_horizon_proche_ignore_les_matchs_hors_du_dictionnaire():
    """matchs_proches peut lister un match que to_probs n'a pas retenu (pas
    couvert par Puckcast, par ex.) : pas de KeyError."""
    conn = sqlite3.connect(":memory:")
    store.init_db(conn)
    conn.execute("INSERT INTO schedule VALUES (?,?,?,?,?,?,?,?)",
                (2026020009, "2026-10-01", "x", "BOS", "NYR", None, None, None))
    conn.commit()
    affine = cp.affiner_horizon_proche({}, conn, dt.date(2026, 9, 30),
                                       FakeSessionMatchup({}))
    assert affine == {}


def test_une_erreur_reseau_sur_un_match_ne_fait_rien_perdre(db):
    """Le chiffre de la page de saison, moins précis mais présent, vaut mieux
    qu'une exception qui ferait échouer toute la collecte."""
    class SessionCassee:
        def get(self, url, **kw):
            raise __import__("requests").ConnectionError("réseau coupé")

    probs = {("2026-09-29", "FLA", "CAR"): (0.361, 0.639)}
    affine = cp.affiner_horizon_proche(dict(probs), db, TODAY, SessionCassee())
    assert affine == probs   # inchangé, pas d'exception propagée


def test_collecte_de_bout_en_bout_integre_laffinage(tmp_path):
    """Vérifie que collect_puckcast() appelle bien l'affinage, pas seulement
    que les fonctions marchent isolément."""
    path = tmp_path / "t.db"
    conn = sqlite3.connect(path)
    store.init_db(conn)
    conn.executemany("INSERT INTO schedule VALUES (?,?,?,?,?,?,?,?)", [
        (2026020001, "2026-09-29", "x", "FLA", "CAR", None, None, None),
    ])
    conn.commit()
    conn.close()

    session = FakeSessionMatchup({
        2026020001: '<div aria-label="Win probability: Panthers 10.0%, Hurricanes 90.0%">x</div>',
    })
    cp.collect_puckcast(path, today=TODAY, session=session)
    row = sqlite3.connect(path).execute(
        "SELECT p_away, p_home FROM probs WHERE source='puckcast' "
        "AND away='FLA' AND home='CAR'").fetchone()
    assert row == (0.1, 0.9)   # le chiffre affiné, pas le 36,1/63,9 de la saison


# ── Horizon : 2 jours pour tout, 7 jours pour la journée de pick ───────────
# La page de Puckcast le dit : la prédiction complète (gardiens confirmés,
# repos, forme) est publiée dans les sept jours avant la mise au jeu. Avec
# seulement 2 jours, un match de samedi regardé le lundi restait sur le
# chiffre figé de la page de saison.

def calendrier(lignes):
    conn = sqlite3.connect(":memory:")
    store.init_db(conn)
    conn.executemany("INSERT INTO schedule VALUES (?,?,?,?,?,?,?,?)",
                     [(gid, d, "x", a, h, None, None, None) for gid, d, a, h in lignes])
    conn.commit()
    return conn


LUNDI = dt.date(2026, 10, 5)


def test_la_journee_de_pick_est_relue_a_cinq_jours():
    conn = calendrier([
        (1, "2026-10-10", "VAN", "NJD"),   # samedi : 2 matchs → journée de pick
        (2, "2026-10-10", "TOR", "COL"),
        (3, "2026-10-11", "BOS", "MTL"),   # dimanche : 1 match → pas de pick
    ])
    ids = {r[0] for r in cp.matchs_proches(conn, LUNDI)}
    assert {1, 2} <= ids
    assert 3 not in ids


def test_tout_match_des_deux_prochains_jours_est_relu():
    """Les gardiens se confirment la veille : même hors journée de pick."""
    conn = calendrier([(4, "2026-10-06", "EDM", "SEA"),
                       (1, "2026-10-10", "VAN", "NJD")])
    assert 4 in {r[0] for r in cp.matchs_proches(conn, LUNDI)}


def test_au_dela_de_sept_jours_rien_nest_relu():
    conn = calendrier([(5, "2026-10-17", "VAN", "NJD"),     # samedi suivant
                       (6, "2026-10-17", "TOR", "COL")])
    assert cp.matchs_proches(conn, LUNDI) == []


def test_egalite_du_week_end_les_deux_jours_sont_relus():
    """Égalité samedi/dimanche : la journée de pick n'est pas tranchée, on
    relit les deux plutôt que de deviner."""
    conn = calendrier([(1, "2026-10-10", "VAN", "NJD"),
                       (3, "2026-10-11", "BOS", "MTL")])
    assert {1, 3} <= {r[0] for r in cp.matchs_proches(conn, LUNDI)}
