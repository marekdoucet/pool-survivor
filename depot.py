"""
Le pool de chaque personne, rangé dans Postgres (Neon).

Une seule table, une ligne par personne. L'état stocké est exactement le
dictionnaire que picks.py manipule déjà — picks, jours, redéparts, éliminations,
registre — sérialisé en JSON. Aucun calcul ne vit ici : ce module range et
ressort, rien d'autre. C'est ce qui permet de brancher une vraie base sans
toucher à une seule ligne de la logique du pool.

Sans section [neon] dans les secrets, l'app retombe sur picks.json. C'est le
mode développement local, et c'est aussi le filet si la base devient
inaccessible.
"""

import json

CREATION = """
CREATE TABLE IF NOT EXISTS pools (
    courriel   TEXT PRIMARY KEY,
    etat       JSONB       NOT NULL,
    modifie_le TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# ON CONFLICT plutôt que DELETE puis INSERT : deux onglets ouverts en même
# temps ne doivent pas pouvoir laisser la ligne absente entre les deux.
ECRITURE = """
INSERT INTO pools (courriel, etat, modifie_le)
VALUES (%s, %s::jsonb, now())
ON CONFLICT (courriel) DO UPDATE
SET etat = EXCLUDED.etat, modifie_le = now()
"""

LECTURE = "SELECT etat FROM pools WHERE courriel = %s"


def init(conn):
    """Crée la table si elle n'existe pas. Sans effet si elle est déjà là."""
    with conn.cursor() as cur:
        cur.execute(CREATION)
    conn.commit()


def charger(conn, courriel):
    """L'état de cette personne, ou None si elle n'a pas encore de pool."""
    with conn.cursor() as cur:
        cur.execute(LECTURE, (courriel,))
        ligne = cur.fetchone()
    if not ligne or ligne[0] is None:
        return None
    etat = ligne[0]
    # psycopg3 décode le JSONB tout seul ; un autre pilote peut rendre du texte.
    return json.loads(etat) if isinstance(etat, str) else etat


def enregistrer(conn, courriel, etat):
    """Remplace le pool de cette personne."""
    if not courriel:
        raise ValueError("pas de courriel : impossible de savoir à qui écrire")
    with conn.cursor() as cur:
        cur.execute(ECRITURE, (courriel, json.dumps(etat)))
    conn.commit()


def courriels(conn):
    """Qui a un pool. Sert au diagnostic, pas à l'affichage."""
    with conn.cursor() as cur:
        cur.execute("SELECT courriel FROM pools ORDER BY courriel")
        return [l[0] for l in cur.fetchall()]


def _etat_vide():
    # Import tardif : depot.py doit rester importable sans picks.py.
    import picks
    return picks.empty_state()


def _connecter(url):
    # Import tardif : le module doit rester importable (et testable) sur une
    # machine sans psycopg, par exemple pour la collecte quotidienne.
    import psycopg
    return psycopg.connect(url)


class PoolNeon:
    """Le pool d'une personne, avec la même interface que pk.GitHubPicks.

    load() et save(etat, message) : l'app appelle exactement les mêmes méthodes
    qu'avant, elle ne sait pas où ça va. Le message de commit n'a plus de sens
    ici, il est ignoré — on le garde dans la signature pour que les deux dépôts
    restent interchangeables.

    La connexion est ouverte puis refermée à chaque opération. Streamlit relit
    le script à chaque interaction : garder une connexion ouverte entre deux
    exécutions donnerait une connexion morte plutôt qu'une économie.
    """

    def __init__(self, url, courriel, connecter=None, etat_vide=None):
        self.url = url
        self.courriel = courriel
        self._connecter = connecter or _connecter
        self._etat_vide = etat_vide or _etat_vide

    def load(self):
        """L'état de cette personne, ou un état vide si elle arrive.

        charger() rend None quand la ligne n'existe pas, mais l'app attend
        toujours un dictionnaire : elle fait state["picks"] sans détour. C'est
        le chemin de TOUTE nouvelle personne, donc celui qui doit le moins
        casser — GitHubPicks.load() fait déjà pareil sur un 404.
        """
        with self._connecter(self.url) as conn:
            init(conn)
            etat = charger(conn, self.courriel)
        return self._etat_vide() if etat is None else etat

    def save(self, etat, message=None):
        with self._connecter(self.url) as conn:
            init(conn)
            enregistrer(conn, self.courriel, etat)


class PoolAnonyme:
    """Personne n'est connecté : il n'y a aucun pool à montrer.

    Avant Neon, l'app affichait un pool unique et partagé — celui de Marek. Une
    fois les pools séparés, montrer ce pool-là à un visiteur de passage serait
    exposer les données de quelqu'un. On rend donc un état vide : les pages
    d'analyse continuent de fonctionner, les pages personnelles invitent à se
    connecter.
    """

    def __init__(self, etat_vide):
        self._etat_vide = etat_vide

    def load(self):
        return self._etat_vide()

    def save(self, etat, message=None):
        raise PermissionError("Connecte-toi pour enregistrer ton pool.")
