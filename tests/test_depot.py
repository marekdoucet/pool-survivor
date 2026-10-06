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
    base = Memoire()
    base.pools["moi@exemple.ca"] = ETAT
    pool = depot.PoolNeon("postgresql://faux", "moi@exemple.ca",
                          connecter=lambda url: base.connexion())
    assert pool.load() == ETAT
    pool.save({"picks": {}}, "un message")
    assert base.pools["moi@exemple.ca"] == {"picks": {}}


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
    base = Memoire()
    base.pools["moi@exemple.ca"] = ETAT
    pool = depot.PoolNeon("postgresql://faux", "moi@exemple.ca",
                          connecter=lambda url: base.connexion())
    assert pool.load()["picks"] == ETAT["picks"]


def test_les_deux_depots_repondent_pareil_a_un_pool_absent():
    """GitHubPicks rend empty_state() sur un 404 ; Neon doit faire de meme,
    sinon les deux ne sont plus interchangeables."""
    import picks as pk
    pool = depot.PoolNeon("postgresql://faux", "neuf@exemple.ca",
                          connecter=lambda url: Connexion(reponse=None))
    assert pool.load() == pk.empty_state()


# ── Historique et annulation ──────────────────────────────────────────────
# Avant Neon, chaque sauvegarde était un commit GitHub, donc récupérable.
# Depuis, le pool était réécrit en entier sans rien garder : un pick supprimé
# par erreur était perdu.
#
# Un faux qui garde un VRAI état (pools + historique) plutôt que de rendre la
# même réponse à toutes les requêtes : la logique d'annulation enchaîne
# plusieurs requêtes sur deux tables, l'ancien faux ne pouvait pas la suivre.
# Il reconnaît exactement les requêtes de depot.py ; le SQL lui-même n'est
# vérifié que contre un vrai Postgres (Neon).

class Memoire:
    def __init__(self):
        self.pools, self.historique, self.suivant = {}, [], 1
        self.commits = 0

    def connexion(self):
        return MemoireConnexion(self)


class MemoireCurseur:
    def __init__(self, base):
        self.b, self.res = base, None

    def execute(self, sql, params=()):
        b = self.b
        if sql in (depot.CREATION, depot.CREATION_HISTORIQUE, depot.INDEX_HISTORIQUE):
            return
        if sql == depot.LECTURE:
            etat = b.pools.get(params[0])
            self.res = None if etat is None else (etat,)
        elif sql == depot.ECRITURE:
            b.pools[params[0]] = json.loads(params[1])
        elif sql == depot.HISTORISER:
            b.historique.append([b.suivant, params[0], json.loads(params[1]),
                                 params[2], f"t{b.suivant}"])
            b.suivant += 1
        elif sql == depot.ELAGUER:
            courriel, _c, garder = params
            ids = sorted((h[0] for h in b.historique if h[1] == courriel), reverse=True)
            if len(ids) > garder:
                seuil = ids[garder]
                b.historique = [h for h in b.historique
                                if not (h[1] == courriel and h[0] <= seuil)]
        elif sql == depot.DERNIERE:
            lignes = [h for h in b.historique if h[1] == params[0]]
            h = max(lignes, key=lambda h: h[0]) if lignes else None
            self.res = None if h is None else (h[0], h[2], h[3], h[4])
        elif sql == depot.OUBLIER:
            b.historique = [h for h in b.historique if h[0] != params[0]]
        else:
            raise AssertionError(f"requête inattendue : {sql[:60]}")

    def fetchone(self):
        return self.res

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class MemoireConnexion:
    def __init__(self, base):
        self.b = base

    def cursor(self):
        return MemoireCurseur(self.b)

    def commit(self):
        self.b.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def pool_neon(base, courriel="moi@exemple.ca"):
    return depot.PoolNeon("postgresql://faux", courriel,
                          connecter=lambda url: base.connexion())


def test_une_sauvegarde_range_letat_davant():
    base = Memoire()
    pool = pool_neon(base)
    pool.save({"picks": [{"week": "2026-09-28", "team": "COL"}]}, "Pick enregistré : COL")
    assert base.historique == []                    # premier état : rien d'avant
    pool.save({"picks": []}, "Pick retiré : COL")
    assert len(base.historique) == 1
    assert base.historique[0][2] == {"picks": [{"week": "2026-09-28", "team": "COL"}]}
    assert base.historique[0][3] == "Pick retiré : COL"


def test_annuler_remet_le_pick_supprime():
    """Le cas exact de la question de Marek : supprimer COL, puis le ravoir."""
    base = Memoire()
    pool = pool_neon(base)
    avec_col = {"picks": [{"week": "2026-09-28", "team": "COL"}]}
    pool.save(avec_col, "Pick enregistré : COL")
    pool.save({"picks": []}, "Pick retiré : COL")
    assert pool.annuler() == "Pick retiré : COL"
    assert base.pools["moi@exemple.ca"] == avec_col


def test_annuler_deux_fois_remonte_deux_modifications():
    """Sans retirer la ligne remise, la 2e annulation ferait juste
    l'aller-retour entre les deux mêmes états."""
    base = Memoire()
    pool = pool_neon(base)
    for i in range(3):
        pool.save({"version": i}, f"modif {i}")
    pool.annuler()
    pool.annuler()
    assert base.pools["moi@exemple.ca"] == {"version": 0}
    assert pool.annuler() is None                   # plus rien à annuler


def test_une_annulation_nest_pas_elle_meme_historisee():
    base = Memoire()
    pool = pool_neon(base)
    pool.save({"version": 0}, "a")
    pool.save({"version": 1}, "b")
    pool.annuler()
    assert base.historique == []


def test_une_sauvegarde_identique_najoute_rien():
    base = Memoire()
    pool = pool_neon(base)
    pool.save({"version": 0}, "a")
    pool.save({"version": 0}, "rien n'a changé")
    assert base.historique == []


def test_lhistorique_garde_les_50_plus_recentes():
    base = Memoire()
    pool = pool_neon(base)
    for i in range(60):
        pool.save({"version": i}, f"modif {i}")
    lignes = [h for h in base.historique if h[1] == "moi@exemple.ca"]
    assert len(lignes) == depot.GARDER
    assert lignes[-1][2] == {"version": 58}         # la plus récente gardée


def test_chacun_annule_seulement_ses_propres_modifications():
    base = Memoire()
    moi, marie = pool_neon(base), pool_neon(base, "marie@exemple.ca")
    moi.save({"v": 0}, "moi a"); moi.save({"v": 1}, "moi b")
    marie.save({"v": 0}, "marie a"); marie.save({"v": 1}, "marie b")
    moi.annuler()
    assert base.pools["moi@exemple.ca"] == {"v": 0}
    assert base.pools["marie@exemple.ca"] == {"v": 1}


def test_load_donne_la_derniere_modification_sans_requete_de_plus():
    base = Memoire()
    pool = pool_neon(base)
    pool.save({"v": 0}, "a")
    pool.save({"v": 1}, "Pick retiré : COL")
    pool.load()
    assert pool.derniere[0] == "Pick retiré : COL"


def test_lannulation_se_fait_en_un_seul_commit():
    """Jamais un état remis sans que la ligne d'historique soit retirée."""
    base = Memoire()
    pool = pool_neon(base)
    pool.save({"v": 0}, "a"); pool.save({"v": 1}, "b")
    avant = base.commits
    pool.annuler()
    assert base.commits - avant == 2                # init() + l'annulation elle-même
