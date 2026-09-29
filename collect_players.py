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
LOGO = "https://assets.nhle.com/logos/nhl/svg/{team}_dark.svg"
SHOT = "https://assets.nhle.com/mugs/actionshots/1296x729/{pid}.jpg"
COLS = ["team", "player_id", "first_name", "last_name", "position",
        "games", "goals", "points", "headshot", "action", "color"]
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
    ]


def collect_players(teams=TEAMS, data_dir=store.DATA_DIR, session=None):
    """Écrit data/players.csv. Retourne (lignes, équipes en échec)."""
    session = session or requests.Session()
    rows, failed = [], []
    for team in teams:
        try:
            r = session.get(API.format(team=team), headers=cm.HEADERS, timeout=30)
            r.raise_for_status()
            row = leader(r.json(), team)
        except (requests.RequestException, ValueError, KeyError):
            row = None
        if row is None:
            failed.append(team)
            continue
        try:
            lg = session.get(LOGO.format(team=team), headers=cm.HEADERS, timeout=30)
            lg.raise_for_status()
            row[-1] = team_color(lg.text)
        except requests.RequestException:
            row[-1] = NEUTRE
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
