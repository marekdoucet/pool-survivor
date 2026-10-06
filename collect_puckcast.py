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

Pour les matchs des HORIZON_JOURS prochains jours, et pour la journée de pick
jusqu'à HORIZON_PICK_JOURS, on relit en plus la page /matchup/<id> elle-même :
la page de saison est générée une fois et reste figée, donc elle ignore les
gardiens confirmés. Voir matchs_proches et affiner_horizon_proche.
"""

import re
import sqlite3
import datetime as dt

import requests

import collect_moneypuck as cm
import collect_odds as co
import optimize as op
import schedule as sch

URL = "https://puckcast.ai/2026-27/games"
SOURCE = "puckcast"
RE_GAME = re.compile(r'<a\b[^>]*\bhref="/matchup/(\d+)"[^>]*>(.*?)</a>', re.S)
RE_TEAM = re.compile(r">([A-Z]{2,3})</span>")
RE_PROB = re.compile(r'class="stat-num"[^>]*>\s*([\d.]+)\s*%')

# Affinage par match : la page de saison est générée une fois et ne bouge
# plus, donc elle ignore les gardiens confirmés — trouvé en comparant son
# 64,3 % à la page de match individuelle, qui affichait 58,7 % le même jour,
# avec « with goalies, edges and the total ». La page de match n'a ce widget
# que pour les matchs pas encore joués ; les gardiens se confirment en
# général dans les 24-48 h, d'où un horizon court plutôt que les ~1300
# matchs de la saison (ce qui serait abusif envers leur serveur).
URL_MATCHUP = "https://puckcast.ai/matchup/{id}"
HORIZON_JOURS = 2
# La page de Puckcast le dit elle-même : la prédiction complète — gardiens
# confirmés, repos, forme — « publishes inside the seven days before puck
# drop and replaces these numbers ». Sept jours, donc, mais seulement pour la
# journée de pick : relire tous les matchs de la semaine serait abusif.
HORIZON_PICK_JOURS = 7
RE_WIN_PROB = re.compile(
    r'aria-label="Win probability: ([^,"]+?) ([\d.]+)%, ([^,"]+?) ([\d.]+)%"')


def nom_vers_code(nom):
    """« Avalanche » → COL. Les noms de la page de match sont des surnoms
    seuls, jamais « Ville Surnom » : même repli que collect_dimers.team_code,
    sur le suffixe de TEAM_CODES."""
    nick = co.team_code(nom) or " " + nom.lower()
    matches = {c for full, c in co.TEAM_CODES.items() if full.endswith(nick)}
    return matches.pop() if len(matches) == 1 else None


def parse_matchup(html):
    """→ {code_équipe: probabilité_de_victoire}, ou {} si le widget est
    absent (match déjà joué, ou format de page différent)."""
    m = RE_WIN_PROB.search(html)
    if not m:
        return {}
    nom_a, p_a, nom_b, p_b = m.groups()
    code_a, code_b = nom_vers_code(nom_a), nom_vers_code(nom_b)
    if not code_a or not code_b:
        return {}
    return {code_a: round(float(p_a) / 100, 4), code_b: round(float(p_b) / 100, 4)}


def matchs_proches(conn, today):
    """Les (game_id, game_date, away, home) à relire individuellement :

    - tous ceux des HORIZON_JOURS prochains jours (gardiens confirmés) ;
    - ceux de la JOURNÉE DE PICK de chaque semaine, jusqu'à
      HORIZON_PICK_JOURS : c'est le seul match qui compte pour un pick, et
      Puckcast publie sa vraie prédiction dans les sept jours avant la mise au
      jeu. Sans ça, un match de samedi regardé le lundi restait sur le chiffre
      figé de la page de saison alors que Puckcast avait déjà mieux.

    La journée de pick est celle par défaut (la plus chargée du week-end, les
    deux en cas d'égalité) : le collecteur est le même pour tout le monde, il ne
    connaît pas le choix manuel d'une personne en particulier.
    """
    fin_proche = (today + dt.timedelta(days=HORIZON_JOURS)).isoformat()
    fin_pick = (today + dt.timedelta(days=HORIZON_PICK_JOURS)).isoformat()
    jours_pick = {d for pd in op.pick_days(conn).values() for d in pd.days}
    lignes = conn.execute(
        "SELECT game_id, game_date, away, home FROM schedule "
        "WHERE game_date >= ? AND game_date < ? AND away_score IS NULL",
        (today.isoformat(), fin_pick)).fetchall()
    return [l for l in lignes if l[1] < fin_proche or l[1] in jours_pick]


def affiner_horizon_proche(probs, conn, today, session):
    """Remplace, pour les matchs proches, la probabilité de la page de
    saison par celle — mise à jour, gardiens confirmés — de la page de match.

    `probs` est le dictionnaire final {(game_date, away, home): (p_away,
    p_home)} rendu par to_probs ; modifié en place et retourné.

    Ne touche que les matchs où la page de match répond ET donne les deux
    équipes : une erreur réseau ou un format inattendu sur un match laisse
    simplement le chiffre de la page de saison, moins précis mais pas absent.
    """
    for gid, g, away, home in matchs_proches(conn, today):
        if (g, away, home) not in probs:
            continue
        try:
            r = session.get(URL_MATCHUP.format(id=gid), headers=cm.HEADERS, timeout=30)
            r.raise_for_status()
        except requests.RequestException:
            continue
        cote = parse_matchup(r.text)
        if away in cote and home in cote:
            probs[(g, away, home)] = (cote[away], cote[home])
    return probs


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
    probs = affiner_horizon_proche(probs, conn, today, session)
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
