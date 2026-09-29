"""
Source supplémentaire : prédictions Dimers (probabilité de victoire, prolongation incluse).

On lit uniquement la page publique https://www.dimers.com/bet-hub/nhl/schedule,
autorisée par leur robots.txt. Elle couvre la prochaine journée de matchs ;
avec une collecte quotidienne, chaque match reçoit sa prédiction Dimers avant
d'être joué. On n'utilise pas l'API interne du site.

Les données sont dans le JSON « ng-state » embarqué dans la page :
    MatchData : Date (UTC), AwayTeam/HomeTeam {Market, Nickname}
    PreData   : PythagAway / PythagHome = probabilité de victoire
"""

import re
import json
import sqlite3
import datetime as dt

import requests

import collect_moneypuck as cm
import collect_odds as co

URL = "https://www.dimers.com/bet-hub/nhl/schedule"
SOURCE = "dimers"
RE_STATE = re.compile(r'<script id="ng-state" type="application/json">(.*?)</script>', re.S)


class DimersError(Exception):
    pass


def team_code(team):
    """{Market: 'NY', Nickname: 'Rangers'} → NYR. Le surnom est unique dans la LNH."""
    code = co.team_code(f"{team['Market']} {team['Nickname']}")
    if code:
        return code
    nick = co.team_code(team["Nickname"]) or " " + team["Nickname"].lower()
    matches = {c for name, c in co.TEAM_CODES.items() if name.endswith(nick)}
    return matches.pop() if len(matches) == 1 else None


def parse_page(html, now=None):
    """→ {(game_date, away, home): (p_away, p_home)} pour les matchs pas encore commencés."""
    m = RE_STATE.search(html)
    if not m:
        raise DimersError("données « ng-state » absentes (la page a changé ?)")
    state = json.loads(m.group(1))
    keys = [k for k in state if "round/matches" in k]
    if not keys:
        raise DimersError("aucune liste de matchs dans la page")

    now = now or dt.datetime.now(dt.timezone.utc)
    games = {}
    for match in state[keys[0]].get("body", []):
        md, pre = match.get("MatchData", {}), match.get("PreData") or {}
        pa, ph = pre.get("PythagAway"), pre.get("PythagHome")
        if pa is None or ph is None:
            continue
        start = dt.datetime.fromisoformat(md["Date"].replace("Z", "+00:00"))
        if start <= now:
            continue
        away, home = team_code(md["AwayTeam"]), team_code(md["HomeTeam"])
        if not away or not home:
            raise DimersError(f"équipe inconnue : {md['AwayTeam']['DisplayName']} / "
                              f"{md['HomeTeam']['DisplayName']}")
        s = pa + ph   # la somme vaut 0,9997… : on normalise
        games[(start.astimezone(cm.TZ).date().isoformat(), away, home)] = (pa / s, ph / s)
    return games


def collect_dimers(db_path=cm.DB_PATH, today=None, session=None, now=None):
    """Ajoute/remplace les prédictions Dimers de l'instantané du jour.

    Contrairement aux casinos, on ne remplace que les matchs présents dans la
    page : ceux collectés plus tôt dans la journée restent.
    """
    snapshot = (today or dt.datetime.now(cm.TZ).date()).isoformat()
    collected_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    session = session or requests.Session()
    r = session.get(URL, headers=cm.HEADERS, timeout=30)
    r.raise_for_status()
    games = parse_page(r.text, now)

    conn = sqlite3.connect(db_path)
    cm.init_db(conn)
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO probs VALUES (?,?,?,?,?,?,?,?)",
            [(snapshot, collected_at, g, a, h, round(pa, 4), round(ph, 4), SOURCE)
             for (g, a, h), (pa, ph) in games.items()],
        )
    conn.close()
    return games


if __name__ == "__main__":
    for (g, a, h), (pa, ph) in sorted(collect_dimers().items()):
        print(f"{g}  {a} @ {h}  {pa:.1%} / {ph:.1%}")
