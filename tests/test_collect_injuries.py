import sqlite3
from pathlib import Path

import pytest

import collect_injuries as ci

# Trois vrais blocs de la page : Colorado (dont un gardien, marqué par un
# second <span>, et une apostrophe échappée), le New Jersey, et Columbus, qui
# n'a aucun blessé donc aucun tableau.
HTML = (Path(__file__).parent / "fixtures" / "puckcast_injuries.htm").read_text(
    encoding="utf-8")


def lire(**kw):
    return ci.parse_page(HTML, minimum_equipes=1, **kw)


def test_les_blesses_de_colorado_sont_lus():
    col = [l for l in lire() if l[0] == "COL"]
    assert ("COL", "Trent Miner", "G", "IR", "Undisclosed") in col
    assert len(col) == 4


def test_le_marqueur_gardien_ne_se_colle_pas_au_nom():
    """Le HTML met un second <span>G</span> DANS la cellule du nom : sans soin,
    le nom devient « Trent MinerG »."""
    noms = {l[1] for l in lire()}
    assert "Trent Miner" in noms
    assert "Trent MinerG" not in noms


def test_les_apostrophes_echappees_sont_decodees():
    noms = {l[1] for l in lire()}
    assert "Logan O'Connor" in noms
    assert not any("&#x27;" in n for n in noms)


def test_une_equipe_sans_blesse_ne_donne_aucune_ligne():
    """Columbus n'a pas de tableau. Légitime : pas une erreur."""
    assert [l for l in lire() if l[0] == "CBJ"] == []


def test_les_statuts_sont_gardes_tels_quels():
    statuts = {l[3] for l in lire() if l[0] == "NJD"}
    assert {"Out", "IR"} <= statuts


def test_les_lignes_sont_triees_pour_un_fichier_stable():
    """Même contenu, mêmes octets : sinon chaque collecte commiterait un faux
    changement."""
    assert lire() == sorted(lire())


def test_une_page_qui_a_change_est_une_erreur_pas_une_equipe_en_sante():
    """Zéro bloc d'équipe ne veut PAS dire que tout le monde est en santé."""
    with pytest.raises(ci.InjuriesError):
        ci.parse_page("<html>maintenance</html>")


def test_un_nom_dequipe_inconnu_est_signale_pas_avale():
    inconnues = []
    page = HTML.replace("Colorado Avalanche", "Quebec Nordiques")
    ci.parse_page(page, inconnues, minimum_equipes=1)
    assert inconnues == ["Quebec Nordiques"]


# ── Base ─────────────────────────────────────────────────────────────────

class FakeSession:
    def __init__(self, page):
        self.page = page

    def get(self, url, **kw):
        return type("R", (), {"text": self.page,
                              "raise_for_status": lambda self: None})()


def test_la_collecte_remplace_la_table(tmp_path):
    db = tmp_path / "t.db"
    ci.collect_injuries(db, FakeSession(HTML), minimum_equipes=1)
    ci.collect_injuries(db, FakeSession(HTML), minimum_equipes=1)   # relance : pas de doublon
    n = sqlite3.connect(db).execute("SELECT COUNT(*) FROM injuries").fetchone()[0]
    assert n == len(lire())


def test_une_page_cassee_laisse_les_blesses_precedents(tmp_path):
    """Lue et analysée AVANT de toucher à la base : une panne ne doit pas
    effacer les blessés connus."""
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    ci.init_db(conn)
    conn.execute("INSERT INTO injuries VALUES ('COL','Ancien','C','IR','Genou')")
    conn.commit()
    conn.close()

    with pytest.raises(ci.InjuriesError):
        ci.collect_injuries(db, FakeSession("<html>maintenance</html>"))

    restant = sqlite3.connect(db).execute("SELECT player FROM injuries").fetchall()
    assert restant == [("Ancien",)]
