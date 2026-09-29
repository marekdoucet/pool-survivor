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
import sys

import requests

import collect_moneypuck as cm
import store

API = "https://api-web.nhle.com/v1/club-stats/{team}/now"
COLS = ["team", "player_id", "first_name", "last_name", "position",
        "games", "goals", "points", "headshot"]

# Les 32 tricodes, tels qu'ils apparaissent déjà dans le calendrier.
TEAMS = [
    "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL", "DET",
    "EDM", "FLA", "LAK", "MIN", "MTL", "NJD", "NSH", "NYI", "NYR", "OTT",
    "PHI", "PIT", "SEA", "SJS", "STL", "TBL", "TOR", "UTA", "VAN", "VGK",
    "WPG", "WSH",
]


class PlayersError(Exception):
    pass


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
        else:
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
