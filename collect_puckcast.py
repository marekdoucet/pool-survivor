"""
Source supplémentaire : prédictions Puckcast (https://puckcast.ai).

On lit la page publique de la saison, https://puckcast.ai/2026-27/games,
autorisée par leur robots.txt (seule leur API /api/ est interdite, et on ne
l'utilise pas). Elle couvre TOUTE la saison, pas seulement les prochains jours.

Chaque match est un lien vers /matchup/<numéro de match LNH> qui indique
l'équipe favorite et sa probabilité de victoire :
    <a class="season-game" href="/matchup/2026020004"> … <span>EDM</span>
      <span class="stat-num">66.9%</span></a>
Le numéro LNH permet de retrouver visiteur/local dans le calendrier officiel.
"""

import re
import sqlite3
import datetime as dt

import requests

import collect_moneypuck as cm
import schedule as sch

URL = "https://puckcast.ai/2026-27/games"
SOURCE = "puckcast"
RE_GAME = re.compile(r'<a\b[^>]*\bhref="/matchup/(\d+)"[^>]*>(.*?)</a>', re.S)
RE_TEAM = re.compile(r">([A-Z]{2,3})</span>")
RE_PROB = re.compile(r'class="stat-num"[^>]*>\s*([\d.]+)\s*%')


class PuckcastError(Exception):
    pass


def parse_page(html):
    """→ {numéro de match LNH: (équipe favorite, probabilité de victoire)}"""
    games = {}
    for gid, body in RE_GAME.findall(html):
        teams, probs = RE_TEAM.findall(body), RE_PROB.findall(body)
        if len(teams) == 1 and len(probs) == 1:   # sinon : match joué ou format inconnu
            games[int(gid)] = (teams[0], round(float(probs[0]) / 100, 4))
    if not games:
        raise PuckcastError("aucune prédiction trouvée (la page a changé ?)")
    return games


def to_probs(games, conn, today):
    """Associe chaque prédiction au calendrier : {(game_date, away, home): (p_away, p_home)}.

    Seuls les matchs à venir (à partir d'aujourd'hui) sont gardés.
    """
    out, mismatched = {}, []
    rows = conn.execute("SELECT game_id, game_date, away, home FROM schedule "
                        "WHERE game_date >= ? AND away_score IS NULL", (today.isoformat(),))
    for gid, g, away, home in rows:
        if gid not in games:
            continue
        fav, p = games[gid]
        if fav not in (away, home):
            mismatched.append(gid)
            continue
        p_away = p if fav == away else 1 - p
        out[(g, away, home)] = (p_away, 1 - p_away)
    if mismatched:
        raise PuckcastError(f"favori absent du match pour {len(mismatched)} match(s), "
                            f"ex. {mismatched[0]}")
    return out


def collect_puckcast(db_path=cm.DB_PATH, today=None, session=None):
    """Remplace les prédictions Puckcast de l'instantané du jour."""
    today = today or dt.datetime.now(cm.TZ).date()
    collected_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    session = session or requests.Session()
    r = session.get(URL, headers=cm.HEADERS, timeout=60)
    r.raise_for_status()
    games = parse_page(r.text)

    conn = sqlite3.connect(db_path)
    cm.init_db(conn)
    sch.init_db(conn)
    probs = to_probs(games, conn, today)
    with conn:
        conn.execute("DELETE FROM probs WHERE snapshot=? AND source=?",
                     (today.isoformat(), SOURCE))
        conn.executemany(
            "INSERT INTO probs VALUES (?,?,?,?,?,?,?,?)",
            [(today.isoformat(), collected_at, g, a, h, round(pa, 4), round(ph, 4), SOURCE)
             for (g, a, h), (pa, ph) in probs.items()])
    conn.close()
    return probs


if __name__ == "__main__":
    probs = collect_puckcast()
    print(f"{len(probs)} matchs prédits par Puckcast")
