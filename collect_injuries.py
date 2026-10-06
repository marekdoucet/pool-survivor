"""
Rapport de blessures : https://puckcast.ai/nhl-injury-report

Une seule page, les 32 équipes, en HTML ordinaire — aucune API (leur /api/ est
interdit par le robots.txt, et on ne l'utilise pas). Chaque équipe est un bloc :

    <h3><a href="/teams/col">Colorado Avalanche</a></h3> … 4 injured …
    <table> <tr> <td>Trent Miner<span>G</span></td> <td>G</td>
                 <td>IR</td> <td>Undisclosed</td> </tr> … </table>

Affichage seulement : ça n'entre PAS dans les probabilités. MoneyPuck et
Puckcast tiennent déjà compte des blessures dans leurs modèles ; les ajouter au
consensus les compterait deux fois.

On ne garde que la situation actuelle (data/injuries.csv, réécrit à chaque
collecte, sans historique) : un blessé d'hier ne dit rien sur le match de ce
soir.
"""

import html
import re
import sqlite3

import requests

import collect_moneypuck as cm
import collect_odds as co

URL = "https://puckcast.ai/nhl-injury-report"
COLS = ["team", "player", "pos", "status", "injury"]

RE_EQUIPE = re.compile(r'<h3[^>]*><a[^>]*href="/teams/[a-z]+"[^>]*>([^<]+)</a></h3>')
RE_TABLE = re.compile(r"<table.*?</table>", re.S)
RE_LIGNE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
RE_CELLULE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
RE_PREMIER_SPAN = re.compile(r"<span[^>]*>([^<]*)")


class InjuriesError(Exception):
    pass


def _texte(fragment):
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def parse_page(page_html, inconnues=None, minimum_equipes=20):
    """→ [(équipe, joueur, poste, statut, blessure)], triées.

    Le bloc d'une équipe sans blessé n'a pas de tableau : elle ne produit
    aucune ligne, ce qui est un résultat légitime. Le garde-fou porte donc sur
    le nombre de BLOCS D'ÉQUIPE trouvés, pas sur le nombre de blessés : une
    page qui change de format en trouve zéro, et on ne veut pas prendre ça pour
    « tout le monde est en santé ».

    `inconnues` : liste à remplir avec les noms d'équipe non reconnus, pour que
    l'oubli soit dit plutôt qu'avalé.
    """
    blocs = list(RE_EQUIPE.finditer(page_html))
    if len(blocs) < minimum_equipes:
        raise InjuriesError(
            f"{len(blocs)} bloc(s) d'équipe trouvé(s), au moins {minimum_equipes} "
            f"attendus (la page a changé ?)")

    lignes = []
    for bloc, suivant in zip(blocs, blocs[1:] + [None]):
        nom = html.unescape(bloc.group(1)).strip()
        code = co.team_code(nom)
        if not code:
            if inconnues is not None:
                inconnues.append(nom)
            continue
        section = page_html[bloc.end(): suivant.start() if suivant else len(page_html)]
        tableau = RE_TABLE.search(section)
        if not tableau:
            continue
        for tr in RE_LIGNE.findall(tableau.group(0)):
            cellules = RE_CELLULE.findall(tr)
            if len(cellules) != 4:
                continue
            # La première cellule porte un second <span> (le « G » des
            # gardiens) : le nom est le texte du premier span seulement.
            m = RE_PREMIER_SPAN.search(cellules[0])
            joueur = html.unescape(m.group(1)).strip() if m else _texte(cellules[0])
            lignes.append((code, joueur, _texte(cellules[1]),
                           _texte(cellules[2]), _texte(cellules[3])))
    return sorted(lignes)


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS injuries (
            team   TEXT NOT NULL,
            player TEXT NOT NULL,
            pos    TEXT NOT NULL,
            status TEXT NOT NULL,
            injury TEXT NOT NULL,
            PRIMARY KEY (team, player)
        )
    """)


def collect_injuries(db_path=cm.DB_PATH, session=None, minimum_equipes=20):
    """Remplace tout le contenu de la table par la situation actuelle.

    La page est lue et analysée AVANT de toucher à la base : une erreur réseau
    ou un format inattendu laisse les blessures précédentes en place.
    """
    session = session or requests.Session()
    r = session.get(URL, headers=cm.HEADERS, timeout=60)
    r.raise_for_status()
    inconnues = []
    lignes = parse_page(r.text, inconnues, minimum_equipes)
    if inconnues:
        print(f"⚠ équipe(s) non reconnue(s) dans le rapport de blessures, "
              f"ignorée(s) : {', '.join(inconnues)}")

    conn = sqlite3.connect(db_path)
    init_db(conn)
    with conn:
        conn.execute("DELETE FROM injuries")
        conn.executemany("INSERT OR REPLACE INTO injuries VALUES (?,?,?,?,?)", lignes)
    conn.close()
    return lignes


if __name__ == "__main__":
    for ligne in collect_injuries():
        print(" · ".join(ligne))
