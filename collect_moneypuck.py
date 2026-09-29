"""
Étape 1 du projet Pool Survivor NHL
Collecte les probabilités de victoire MoneyPuck pour les N prochains jours
et les enregistre dans une base SQLite avec la date de collecte.
Chaque exécution ajoute un « instantané », ce qui permettra plus tard
de tracer l'évolution des probabilités dans le temps.

Utilisation :
    pip install -r requirements.txt
    python collect_moneypuck.py            # 49 jours à partir d'aujourd'hui
    python collect_moneypuck.py 10         # 10 jours seulement
"""

import re
import sys
import sqlite3
import datetime as dt
import time
from zoneinfo import ZoneInfo

import requests

DB_PATH = "survivor.db"
URL = "https://moneypuck.com/moneypuck/dates/{:%Y%m%d}.htm"
HEADERS = {"User-Agent": "Mozilla/5.0 (pool-survivor perso)"}
SOURCE = "moneypuck"

# Les pages MoneyPuck sont en heure de l'Est. On s'y aligne pour que
# « aujourd'hui » soit le même en local et sur GitHub Actions (UTC).
TZ = ZoneInfo("America/Toronto")

TEAMS = {
    "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL", "DET", "EDM",
    "FLA", "LAK", "MIN", "MTL", "NJD", "NSH", "NYI", "NYR", "OTT", "PHI", "PIT",
    "SEA", "SJS", "STL", "TBL", "TOR", "UTA", "VAN", "VGK", "WPG", "WSH",
}

# Chaque match est une ligne <tr> à 5 cellules :
#   [<h2>23.1%</h2>] [logo VAN] [heure] [logo NJD] [<h2>76.9%</h2>]
# L'équipe de gauche est l'équipe visiteuse, celle de droite joue à domicile.
# Le libellé « Chance of Winning » n'apparaît pas sur toutes les lignes
# (absent des matchs avec lien « Preview ») : on se fie seulement au <h2>.
# Un match terminé (ou en cours) n'a pas de probabilités, seulement le score.
RE_ROW = re.compile(r"<tr\b.*?</tr>", re.S | re.I)
RE_PROB = re.compile(r"<h2>\s*([\d.]+)\s*%\s*</h2>", re.I)
RE_SCORE = re.compile(r"\d+\s*-\s*\d+")
RE_LOGO = re.compile(r"/logos/([A-Za-z]{2,3})\.png")


class ParseError(Exception):
    pass


def parse_day(html):
    """Retourne (matchs à venir [(away, home, p_away, p_home)], nb de matchs ignorés)."""
    if "<table" not in html.lower():
        raise ParseError("pas de <table> dans la page (blocage ou page d'erreur ?)")

    games, skipped = [], 0
    for row in RE_ROW.findall(html):
        teams = [t.upper() for t in RE_LOGO.findall(row)]
        probs = [round(float(p) / 100, 4) for p in RE_PROB.findall(row)]
        if len(teams) != 2:
            raise ParseError(f"ligne avec {len(teams)} logos")
        if not probs:
            if not RE_SCORE.search(re.sub(r"<[^>]*>", " ", row)):
                raise ParseError(f"{teams}: ni probabilités ni score")
            skipped += 1  # match terminé ou en cours
            continue
        if len(probs) != 2:
            raise ParseError(f"{teams}: {len(probs)} probabilités")
        away, home = teams
        p_away, p_home = probs
        unknown = {away, home} - TEAMS
        if unknown:
            raise ParseError(f"code d'équipe inconnu : {unknown}")
        if abs(p_away + p_home - 1) > 0.01:
            raise ParseError(f"{away}@{home}: {p_away:.3f} + {p_home:.3f} ≠ 1")
        games.append((away, home, p_away, p_home))
    return games, skipped


def fetch_html(session, day, retries=3):
    for attempt in range(1, retries + 1):
        try:
            r = session.get(URL.format(day), headers=HEADERS, timeout=20)
            if r.status_code == 404:
                return ""
            r.raise_for_status()
            return r.text
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(2 * attempt)


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS probs (
            snapshot     TEXT NOT NULL,   -- date de la collecte (heure de l'Est)
            collected_at TEXT NOT NULL,   -- horodatage UTC exact de la collecte
            game_date    TEXT NOT NULL,   -- date du match
            away         TEXT NOT NULL,
            home         TEXT NOT NULL,
            p_away       REAL NOT NULL,
            p_home       REAL NOT NULL,
            source       TEXT NOT NULL,
            PRIMARY KEY (snapshot, game_date, away, home, source)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_probs_game ON probs (game_date, away, home)")


def save_day(conn, snapshot, collected_at, day, games, source=SOURCE):
    """Remplace l'instantané du jour pour cette date de match.

    Si on relance la collecte le même jour, un match reporté/retiré
    ne reste pas dans l'instantané.
    """
    with conn:
        conn.execute(
            "DELETE FROM probs WHERE snapshot=? AND game_date=? AND source=?",
            (snapshot, day.isoformat(), source),
        )
        conn.executemany(
            "INSERT INTO probs VALUES (?,?,?,?,?,?,?,?)",
            [(snapshot, collected_at, day.isoformat(), a, h, pa, ph, source)
             for a, h, pa, ph in games],
        )


def collect(n_days=49, db_path=DB_PATH, today=None, session=None, pause=0.5):
    """Collecte n_days jours. Retourne (nb matchs, liste des jours en erreur)."""
    today = today or dt.datetime.now(TZ).date()
    collected_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    session = session or requests.Session()

    conn = sqlite3.connect(db_path)
    init_db(conn)

    total, errors = 0, []
    for k in range(n_days):
        day = today + dt.timedelta(days=k)
        try:
            games, skipped = parse_day(fetch_html(session, day) or "<table></table>")
        except (requests.RequestException, ParseError) as e:
            # On ne touche pas à l'instantané de ce jour : mieux vaut un trou
            # qu'une journée effacée par erreur.
            print(f"{day}  ⚠ {e}")
            errors.append(day)
            continue
        save_day(conn, today.isoformat(), collected_at, day, games)
        total += len(games)
        extra = f"  ({skipped} déjà commencés/terminés)" if skipped else ""
        print(f"{day}  {len(games):2d} matchs{extra}")
        time.sleep(pause)  # on reste poli avec le serveur

    conn.close()
    return total, errors


def main():
    n_days = int(sys.argv[1]) if len(sys.argv) > 1 else 49
    total, errors = collect(n_days)
    print(f"\n{total} matchs enregistrés dans {DB_PATH}")
    if errors:
        print(f"{len(errors)} jour(s) en erreur : {', '.join(map(str, errors))}")
    # Code de sortie non nul si plus de la moitié des jours échouent :
    # GitHub Actions signalera alors l'échec (étape 5).
    sys.exit(1 if len(errors) > n_days // 2 else 0)


if __name__ == "__main__":
    main()
