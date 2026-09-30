"""
Joueur vedette de chaque équipe, pour la carte du pick.

API publique de la LNH : /v1/club-stats/<tricode>/now donne les statistiques
de la saison en cours — ou de la dernière saison complétée tant que la nouvelle
n'a pas commencé, ce qui donne quand même un meneur crédible en septembre.
On garde le meneur au chapitre des points.

    data/players.csv   team, player_id, first_name, last_name, position,
                       games, goals, points, headshot

Une ligne par équipe. Le fichier bouge rarement (un meneur ne change pas d'un
jour à l'autre) : _write_if_changed évite les commits vides.

    python collect_players.py
"""

import csv
import io
import re
import sys

import requests

import collect_moneypuck as cm
import store

API = "https://api-web.nhle.com/v1/club-stats/{team}/now"
API_SAISON = "https://api-web.nhle.com/v1/club-stats/{team}/{saison}/2"
# Matchs joués avant qu'un meneur de la saison en cours veuille dire quelque
# chose. En dessous, on prend celui de la saison précédente : au premier match,
# le « meneur » est le premier à marquer, et trente équipes n'ont rien du tout.
SAISON_MIN = 20
LOGO = "https://assets.nhle.com/logos/nhl/svg/{team}_dark.svg"
SHOT = "https://assets.nhle.com/mugs/actionshots/1296x729/{pid}.jpg"
COLS = ["team", "player_id", "first_name", "last_name", "position",
        "games", "goals", "points", "headshot", "action", "color", "season"]
NEUTRE = "#3a3a35"          # si le logo ne donne aucune couleur exploitable
RE_FILL = re.compile(r'fill[:=]"?\s*(#[0-9A-Fa-f]{3,6})')

# Les 32 tricodes, tels qu'ils apparaissent déjà dans le calendrier.
TEAMS = [
    "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL", "DET",
    "EDM", "FLA", "LAK", "MIN", "MTL", "NJD", "NSH", "NYI", "NYR", "OTT",
    "PHI", "PIT", "SEA", "SJS", "STL", "TBL", "TOR", "UTA", "VAN", "VGK",
    "WPG", "WSH",
]


class PlayersError(Exception):
    pass


def saison_precedente(saison):
    """20262027 → 20252026."""
    s = str(saison)
    return int(f"{int(s[:4]) - 1}{int(s[4:]) - 1}")


def assez_joue(payload, mini=SAISON_MIN):
    """Le meneur de cette saison veut-il déjà dire quelque chose ?"""
    joues = [s.get("gamesPlayed") or 0 for s in payload.get("skaters") or []]
    return max(joues, default=0) >= mini


def _hex6(c):
    c = c.lower().lstrip("#")
    return "#" + ("".join(x * 2 for x in c) if len(c) == 3 else c)


def team_color(svg):
    """Couleur d'équipe d'un logo SVG : la plus saturée qu'il contient.

    Prise dans le logo officiel, jamais inventée. La plus saturée plutôt que
    la plus fréquente parce que c'est elle qui porte l'identité : l'or des
    Bruins, l'orange des Oilers, le rouge du Canadien. Le gris et le blanc
    d'un contour sont souvent majoritaires sans rien vouloir dire.
    """
    seen = {}
    for i, raw in enumerate(RE_FILL.findall(svg)):
        c = _hex6(raw)
        r, g, b = (int(c[j:j + 2], 16) / 255 for j in (1, 3, 5))
        chroma = max(r, g, b) - min(r, g, b)
        if chroma >= 0.15:
            n, first = seen.get(c, (0, i))
            seen[c] = (n + 1, first)
    if not seen:
        return NEUTRE
    def cle(c):
        r, g, b = (int(c[j:j + 2], 16) / 255 for j in (1, 3, 5))
        n, first = seen[c]
        return (-(max(r, g, b) - min(r, g, b)), -n, first)   # départage stable
    return min(seen, key=cle)


def leader(payload, team):
    """Meneur de l'équipe aux points → ligne prête pour le CSV, ou None.

    Fonction pure : c'est elle que testent les tests, sans réseau.
    """
    skaters = payload.get("skaters") or []
    scored = [s for s in skaters if s.get("points") is not None]
    if not scored:
        return None
    # points, puis buts, puis nom : départage stable, donc pas de faux
    # changement de fichier quand deux joueurs sont à égalité.
    best = max(scored, key=lambda s: (s["points"], s.get("goals", 0),
                                      s.get("lastName", {}).get("default", "")))
    return [
        team,
        best.get("playerId"),
        best.get("firstName", {}).get("default", ""),
        best.get("lastName", {}).get("default", ""),
        best.get("positionCode", ""),
        best.get("gamesPlayed", 0),
        best.get("goals", 0),
        best["points"],
        best.get("headshot", ""),
        SHOT.format(pid=best.get("playerId")),
        "",                 # couleur : remplie par collect_players
        payload.get("season", ""),
    ]


def collect_players(teams=TEAMS, data_dir=store.DATA_DIR, session=None):
    """Écrit data/players.csv. Retourne (lignes, équipes en échec)."""
    session = session or requests.Session()
    rows, failed = [], []
    for team in teams:
        try:
            r = session.get(API.format(team=team), headers=cm.HEADERS, timeout=30)
            r.raise_for_status()
            payload = r.json()
            if not assez_joue(payload):
                # Début de saison : on garde le meneur de l'an dernier, sinon
                # la carte n'a plus ni joueur ni photo pendant des semaines.
                avant = saison_precedente(payload.get("season") or 0)
                r2 = session.get(API_SAISON.format(team=team, saison=avant),
                                 headers=cm.HEADERS, timeout=30)
                r2.raise_for_status()
                payload = r2.json()
            row = leader(payload, team)
        except (requests.RequestException, ValueError, KeyError):
            row = None
        if row is None:
            failed.append(team)
            continue
        try:
            lg = session.get(LOGO.format(team=team), headers=cm.HEADERS, timeout=30)
            lg.raise_for_status()
            # par nom, pas par position : une colonne ajoutee a la fin a
            # deja fait ecrire la couleur par-dessus la saison.
            row[COLS.index("color")] = team_color(lg.text)
        except requests.RequestException:
            row[COLS.index("color")] = NEUTRE
        rows.append(row)
    if not rows:
        raise PlayersError("aucune équipe n'a répondu (l'API a changé ?)")
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLS)
    w.writerows(sorted(rows))
    store._write_if_changed(data_dir / "players.csv", buf.getvalue().encode("utf-8"))
    return rows, failed


if __name__ == "__main__":
    rows, failed = collect_players()
    print(f"{len(rows)} meneurs enregistrés" + (f", {len(failed)} échec(s) : "
                                                f"{', '.join(failed)}" if failed else ""))
    sys.exit(1 if failed else 0)
