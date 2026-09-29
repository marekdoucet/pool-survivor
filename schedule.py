"""
Calendrier complet de la saison régulière (API publique de la LNH, gratuite, sans clé).
Sert à l'optimiseur pour les semaines au-delà des 49 jours couverts par MoneyPuck.

    python schedule.py
"""

import sqlite3
import datetime as dt

import requests

import collect_moneypuck as cm

API = "https://api-web.nhle.com/v1/schedule/{}"
SEASON_START = dt.date(2026, 9, 29)
SEASON_END = dt.date(2027, 4, 10)


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedule (
            game_id    INTEGER PRIMARY KEY,
            game_date  TEXT NOT NULL,   -- date du match (heure de l'Est)
            start_utc  TEXT NOT NULL,
            away       TEXT NOT NULL,
            home       TEXT NOT NULL
        )
    """)


def parse_week(payload):
    games = []
    for day in payload.get("gameWeek", []):
        for g in day.get("games", []):
            if g.get("gameType") != 2:   # 2 = saison régulière
                continue
            start = dt.datetime.fromisoformat(g["startTimeUTC"].replace("Z", "+00:00"))
            games.append((g["id"], start.astimezone(cm.TZ).date().isoformat(),
                          g["startTimeUTC"], g["awayTeam"]["abbrev"], g["homeTeam"]["abbrev"]))
    return games


def fetch_season(session=None, start=SEASON_START, end=SEASON_END):
    session = session or requests.Session()
    games, day = [], start
    while day <= end:
        r = session.get(API.format(day.isoformat()), timeout=20)
        r.raise_for_status()
        games += parse_week(r.json())
        day += dt.timedelta(days=7)   # chaque réponse couvre 7 jours
    return games


def update_schedule(db_path=cm.DB_PATH, session=None):
    games = fetch_season(session)
    bad = {t for g in games for t in g[3:5]} - cm.TEAMS
    if bad:
        raise ValueError(f"codes d'équipe inconnus dans le calendrier : {bad}")
    conn = sqlite3.connect(db_path)
    init_db(conn)
    with conn:   # remplace tout : gère les matchs reportés/déplacés
        conn.execute("DELETE FROM schedule")
        conn.executemany("INSERT OR REPLACE INTO schedule VALUES (?,?,?,?,?)", games)
    conn.close()
    return len(games)


if __name__ == "__main__":
    print(f"{update_schedule()} matchs de saison régulière enregistrés")
