import json

import pytest

import depot


class Curseur:
    def __init__(self, journal, reponse=None):
        self.journal, self.reponse = journal, reponse

    def execute(self, sql, params=None):
        self.journal.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.reponse

    def fetchall(self):
        return self.reponse or []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Connexion:
    """Imite psycopg sans base : on vérifie le SQL envoyé, pas Postgres."""

    def __init__(self, reponse=None):
        self.journal, self.reponse, self.commits = [], reponse, 0

    def cursor(self):
        return Curseur(self.journal, self.reponse)

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


ETAT = {"picks": {"2026-10-05": "COL"}, "days": {}, "resets": [],
        "out": None, "force": None, "pool": {"Marie": "EDM"}}


def test_la_table_est_creee_sans_ecraser():
    conn = Connexion()
    depot.init(conn)
    sql = conn.journal[0][0]
    assert "CREATE TABLE IF NOT EXISTS pools" in sql
    assert conn.commits == 1


def test_ecrire_remplace_au_lieu_de_dupliquer():
    """ON CONFLICT plutôt que DELETE puis INSERT : deux onglets ouverts ne
    doivent pas pouvoir laisser la ligne absente entre les deux."""
    conn = Connexion()
    depot.enregistrer(conn, "moi@exemple.ca", ETAT)
    sql, params = conn.journal[-1]
    assert "ON CONFLICT (courriel) DO UPDATE" in sql
    assert params[0] == "moi@exemple.ca"
    assert json.loads(params[1]) == ETAT


def test_sans_courriel_on_refuse_decrire():
    """Sinon un visiteur anonyme écrirait dans une ligne fantôme."""
    for vide in (None, ""):
        with pytest.raises(ValueError):
            depot.enregistrer(Connexion(), vide, ETAT)


def test_une_personne_sans_pool_donne_none():
    assert depot.charger(Connexion(reponse=None), "neuf@exemple.ca") is None
    assert depot.charger(Connexion(reponse=(None,)), "neuf@exemple.ca") is None


def test_lecture_dun_etat_deja_decode():
    """psycopg3 décode le JSONB tout seul."""
    assert depot.charger(Connexion(reponse=(ETAT,)), "moi@exemple.ca") == ETAT


def test_lecture_dun_etat_encore_en_texte():
    """Un autre pilote peut rendre du texte brut."""
    conn = Connexion(reponse=(json.dumps(ETAT),))
    assert depot.charger(conn, "moi@exemple.ca") == ETAT


def test_chaque_courriel_a_son_pool():
    """Le cœur de la fonctionnalité : deux personnes, deux lignes."""
    conn = Connexion()
    depot.enregistrer(conn, "moi@exemple.ca", {"picks": {"a": "COL"}})
    depot.enregistrer(conn, "marie@exemple.ca", {"picks": {"a": "EDM"}})
    cles = [p[0] for _sql, p in conn.journal if p]
    assert "moi@exemple.ca" in cles and "marie@exemple.ca" in cles


# ── L'objet branché dans l'app ────────────────────────────────────────────

def test_le_depot_a_la_meme_interface_que_github():
    """app.py appelle remote.load() et remote.save(etat, message) sans savoir
    où ça va. Les deux dépôts doivent rester interchangeables."""
    import picks as pk
    for methode in ("load", "save"):
        assert hasattr(depot.PoolNeon, methode)
        assert hasattr(pk.GitHubPicks, methode)


def test_le_pool_lit_et_ecrit_pour_le_bon_courriel():
    conn = Connexion(reponse=(ETAT,))
    pool = depot.PoolNeon("postgresql://faux", "moi@exemple.ca",
                          connecter=lambda url: conn)
    assert pool.load() == ETAT
    pool.save({"picks": {}}, "message ignoré")
    assert any(p and p[0] == "moi@exemple.ca" for _sql, p in conn.journal)


# ── Le chemin de toute nouvelle personne ──────────────────────────────────
# Bogue du 1er octobre : PoolNeon.load() rendait None pour quelqu'un sans
# ligne, et app.py faisait aussitot state["picks"] :
#     TypeError: 'NoneType' object is not subscriptable
# C'est le chemin qu'emprunte CHAQUE nouvelle personne, donc celui qui doit le
# moins casser.

def test_une_personne_qui_arrive_recoit_un_etat_utilisable():
    pool = depot.PoolNeon("postgresql://faux", "nouveau@exemple.ca",
                          connecter=lambda url: Connexion(reponse=None))
    etat = pool.load()
    assert etat is not None
    assert etat["picks"] == []          # app.py fait state["picks"] sans detour
    assert "days" in etat and "pool" in etat


def test_un_etat_existant_nest_pas_remplace_par_du_vide():
    pool = depot.PoolNeon("postgresql://faux", "moi@exemple.ca",
                          connecter=lambda url: Connexion(reponse=(ETAT,)))
    assert pool.load()["picks"] == ETAT["picks"]


def test_les_deux_depots_repondent_pareil_a_un_pool_absent():
    """GitHubPicks rend empty_state() sur un 404 ; Neon doit faire de meme,
    sinon les deux ne sont plus interchangeables."""
    import picks as pk
    pool = depot.PoolNeon("postgresql://faux", "neuf@exemple.ca",
                          connecter=lambda url: Connexion(reponse=None))
    assert pool.load() == pk.empty_state()
