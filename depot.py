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

# Historique : chaque sauvegarde range l'état D'AVANT, avec le message de la
# modification qui l'a remplacé. Annuler = remettre la dernière ligne rangée et
# la retirer. Avant Neon, chaque sauvegarde était un commit GitHub, donc tout
# se récupérait ; depuis, le pool était réécrit en entier sans rien garder.
CREATION_HISTORIQUE = """
CREATE TABLE IF NOT EXISTS historique (
    id       BIGSERIAL   PRIMARY KEY,
    courriel TEXT        NOT NULL,
    etat     JSONB       NOT NULL,
    message  TEXT        NOT NULL,
    cree_le  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""
INDEX_HISTORIQUE = ("CREATE INDEX IF NOT EXISTS historique_courriel "
                    "ON historique (courriel, id)")
GARDER = 50     # versions conservées par personne
HISTORISER = ("INSERT INTO historique (courriel, etat, message) "
              "VALUES (%s, %s::jsonb, %s)")
# Supprime ce qui dépasse les GARDER plus récentes. Sous-requête vide (moins
# de GARDER lignes) : « id <= NULL » n'est jamais vrai, rien n'est supprimé.
ELAGUER = """
DELETE FROM historique WHERE courriel = %s AND id <= (
    SELECT id FROM historique WHERE courriel = %s
    ORDER BY id DESC LIMIT 1 OFFSET %s)
"""
DERNIERE = ("SELECT id, etat, message, cree_le FROM historique "
            "WHERE courriel = %s ORDER BY id DESC LIMIT 1")
OUBLIER = "DELETE FROM historique WHERE id = %s"


def init(conn):
    """Crée les tables si elles n'existent pas. Sans effet sinon."""
    with conn.cursor() as cur:
        cur.execute(CREATION)
        cur.execute(CREATION_HISTORIQUE)
        cur.execute(INDEX_HISTORIQUE)
    conn.commit()


def _decoder(etat):
    # psycopg3 décode le JSONB tout seul ; un autre pilote peut rendre du texte.
    return json.loads(etat) if isinstance(etat, str) else etat


def charger(conn, courriel):
    """L'état de cette personne, ou None si elle n'a pas encore de pool."""
    with conn.cursor() as cur:
        cur.execute(LECTURE, (courriel,))
        ligne = cur.fetchone()
    if not ligne or ligne[0] is None:
        return None
    return _decoder(ligne[0])


def enregistrer(conn, courriel, etat, message=None, historiser=True):
    """Remplace le pool de cette personne, en rangeant l'état d'avant.

    Une seule transaction : l'état d'avant rangé, le nouveau écrit, l'excédent
    élagué, puis un seul commit — jamais un historique à moitié écrit. Une
    sauvegarde qui ne change rien n'ajoute rien à l'historique.
    """
    if not courriel:
        raise ValueError("pas de courriel : impossible de savoir à qui écrire")
    avant = charger(conn, courriel) if historiser else None
    with conn.cursor() as cur:
        if avant is not None and avant != etat:
            cur.execute(HISTORISER, (courriel, json.dumps(avant),
                                     message or "Modification"))
            cur.execute(ELAGUER, (courriel, courriel, GARDER))
        cur.execute(ECRITURE, (courriel, json.dumps(etat)))
    conn.commit()


def derniere_modification(conn, courriel):
    """(message, quand) de la dernière modification annulable, ou None."""
    with conn.cursor() as cur:
        cur.execute(DERNIERE, (courriel,))
        ligne = cur.fetchone()
    return (ligne[2], ligne[3]) if ligne else None


def annuler(conn, courriel):
    """Remet l'état d'avant la dernière modification. → son message, ou None.

    La ligne remise est retirée de l'historique, sans en créer une nouvelle :
    annuler deux fois remonte deux modifications en arrière, au lieu de faire
    l'aller-retour entre les deux mêmes états.
    """
    with conn.cursor() as cur:
        cur.execute(DERNIERE, (courriel,))
        ligne = cur.fetchone()
        if not ligne:
            return None
        ident, etat, message, _quand = ligne
        cur.execute(ECRITURE, (courriel, json.dumps(_decoder(etat))))
        cur.execute(OUBLIER, (ident,))
    conn.commit()
    return message


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
    qu'avant, elle ne sait pas où ça va. Le message sert d'étiquette dans
    l'historique (« Pick retiré : COL… »), c'est ce qu'on lit avant d'annuler.
    En plus : annuler() et `derniere`, renseigné par load().

    La connexion est ouverte puis refermée à chaque opération. Streamlit relit
    le script à chaque interaction : garder une connexion ouverte entre deux
    exécutions donnerait une connexion morte plutôt qu'une économie.
    """

    def __init__(self, url, courriel, connecter=None, etat_vide=None):
        self.url = url
        self.courriel = courriel
        self._connecter = connecter or _connecter
        self._etat_vide = etat_vide or _etat_vide
        self.derniere = None

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
            # Lu dans la même connexion : afficher « annuler » ne coûte pas un
            # aller-retour de plus vers la base à chaque interaction.
            self.derniere = derniere_modification(conn, self.courriel)
        return self._etat_vide() if etat is None else etat

    def save(self, etat, message=None):
        with self._connecter(self.url) as conn:
            init(conn)
            enregistrer(conn, self.courriel, etat, message)

    def annuler(self):
        """Revient à l'état d'avant la dernière modification. → son message."""
        with self._connecter(self.url) as conn:
            init(conn)
            return annuler(conn, self.courriel)


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
