"""
Étape 2 du projet Pool Survivor NHL
Collecte les cotes « moneyline » de plusieurs casinos via The Odds API (plan
gratuit), retire la marge de chaque casino, puis calcule une probabilité
consensus pondérée entre le marché et MoneyPuck.

Tables écrites :
    odds   : cotes brutes et probabilités sans marge, par casino
    probs  : source='market'    moyenne des casinos pour chaque match
             source='consensus' moyenne pondérée (WEIGHTS) des sources disponibles
                                (marché, MoneyPuck, Dimers, Puckcast) pour chaque match

Coût : 1 requête = 1 crédit par région (REGIONS), donc 2 crédits par appel.
Le workflow déclenche quatre exécutions par jour, mais DELAI_MINI n'en laisse
passer que deux : ~124 des 500 crédits gratuits par mois. Les deux autres sont
des rattrapages, qui ne coûtent rien tant que l'exécution principale a réussi.

Utilisation :
    set ODDS_API_KEY=ta_cle        (ou setx pour la garder)
    python collect_odds.py
"""

import os
import sys
import sqlite3
import datetime as dt
import unicodedata
from collections import defaultdict

import requests

import collect_moneypuck as cm

API_URL = "https://api.the-odds-api.com/v4/sports/icehockey_nhl/odds"
REGIONS = "us,eu"   # « eu » inclut Pinnacle, le casino de référence du marché

# Les crons vont par paires : une exécution principale, puis un rattrapage une
# heure plus tard au cas où GitHub aurait sauté la première — ce qui arrive
# quand sa file est saturée. Le rattrapage refait tout le reste du travail,
# gratuit, mais ne doit pas racheter les cotes que la principale vient de
# collecter. Quatre heures séparent largement le rattrapage (1 h) des deux
# vraies collectes quotidiennes (13 h).
DELAI_MINI = dt.timedelta(hours=4)
# Poids de chaque source dans le consensus (renormalisés selon les sources
# disponibles pour un match). Le marché des casinos est en général le plus précis.
WEIGHTS = {"market": 0.55, "moneypuck": 0.2, "dimers": 0.15, "puckcast": 0.1}

TEAM_CODES = {
    "anaheim ducks": "ANA", "boston bruins": "BOS", "buffalo sabres": "BUF",
    "carolina hurricanes": "CAR", "columbus blue jackets": "CBJ",
    "calgary flames": "CGY", "chicago blackhawks": "CHI",
    "colorado avalanche": "COL", "dallas stars": "DAL",
    "detroit red wings": "DET", "edmonton oilers": "EDM",
    "florida panthers": "FLA", "los angeles kings": "LAK",
    "minnesota wild": "MIN", "montreal canadiens": "MTL",
    "new jersey devils": "NJD", "nashville predators": "NSH",
    "new york islanders": "NYI", "new york rangers": "NYR",
    "ottawa senators": "OTT", "philadelphia flyers": "PHI",
    "pittsburgh penguins": "PIT", "seattle kraken": "SEA",
    "san jose sharks": "SJS", "st louis blues": "STL",
    "tampa bay lightning": "TBL", "toronto maple leafs": "TOR",
    "utah mammoth": "UTA", "utah hockey club": "UTA",
    "vancouver canucks": "VAN", "vegas golden knights": "VGK",
    "winnipeg jets": "WPG", "washington capitals": "WSH",
}


class OddsError(Exception):
    pass


def team_code(name):
    """« Montréal Canadiens » → MTL, « St. Louis Blues » → STL, sinon None."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = " ".join(s.lower().replace(".", "").split())
    return TEAM_CODES.get(s)


def devig(price_away, price_home):
    """Cotes décimales → probabilités sans marge (normalisation proportionnelle)."""
    ia, ih = 1 / price_away, 1 / price_home
    return ia / (ia + ih), ih / (ia + ih)


def parse_events(events, now=None):
    """Réponse de l'API → lignes (game_date, away, home, book, cote_a, cote_h, p_a, p_h).

    Retourne aussi la liste des noms d'équipes inconnus (à ajouter à TEAM_CODES).
    Les matchs déjà commencés sont ignorés : leurs cotes « en direct »
    ne reflètent plus la probabilité d'avant-match.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    rows, unknown = [], set()
    for ev in events:
        start = dt.datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
        if start <= now:
            continue
        away, home = team_code(ev["away_team"]), team_code(ev["home_team"])
        if not away or not home:
            unknown |= {n for n, c in ((ev["away_team"], away), (ev["home_team"], home)) if not c}
            continue
        game_date = start.astimezone(cm.TZ).date().isoformat()
        for book in ev.get("bookmakers", []):
            market = next((m for m in book.get("markets", []) if m["key"] == "h2h"), None)
            if not market:
                continue
            prices = {o["name"]: o["price"] for o in market["outcomes"]}
            pa, ph = prices.get(ev["away_team"]), prices.get(ev["home_team"])
            if len(prices) != 2 or not pa or not ph or pa <= 1 or ph <= 1:
                continue
            fa, fh = devig(pa, ph)
            rows.append((game_date, away, home, book["key"], pa, ph, round(fa, 4), round(fh, 4)))
    return rows, sorted(unknown)


def fetch_events(api_key, session=None):
    session = session or requests.Session()
    r = session.get(API_URL, timeout=30, params={
        "apiKey": api_key, "regions": REGIONS, "markets": "h2h",
        "oddsFormat": "decimal", "dateFormat": "iso",
    })
    if r.status_code != 200:
        try:
            msg = r.json().get("message", r.text)
        except ValueError:
            msg = r.text
        raise OddsError(f"The Odds API a répondu {r.status_code} : {msg}")
    return r.json(), r.headers.get("x-requests-remaining")


def init_db(conn):
    cm.init_db(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS odds (
            snapshot     TEXT NOT NULL,
            collected_at TEXT NOT NULL,
            game_date    TEXT NOT NULL,
            away         TEXT NOT NULL,
            home         TEXT NOT NULL,
            book         TEXT NOT NULL,   -- ex. pinnacle, draftkings, fanduel
            price_away   REAL NOT NULL,   -- cote décimale brute
            price_home   REAL NOT NULL,
            p_away       REAL NOT NULL,   -- probabilité sans marge
            p_home       REAL NOT NULL,
            PRIMARY KEY (snapshot, game_date, away, home, book)
        )
    """)


def replace_source(conn, snapshot, source, rows, collected_at):
    """rows : {(game_date, away, home): (p_away, p_home)}"""
    conn.execute("DELETE FROM probs WHERE snapshot=? AND source=?", (snapshot, source))
    conn.executemany(
        "INSERT INTO probs VALUES (?,?,?,?,?,?,?,?)",
        [(snapshot, collected_at, g, a, h, round(pa, 4), round(ph, 4), source)
         for (g, a, h), (pa, ph) in rows.items()],
    )


def save_odds(conn, snapshot, collected_at, rows):
    """Remplace les cotes et la probabilité « market » de l'instantané."""
    by_game = defaultdict(list)
    for g, a, h, _book, _pa, _ph, fa, _fh in rows:
        by_game[(g, a, h)].append(fa)
    market = {k: (sum(v) / len(v), 1 - sum(v) / len(v)) for k, v in by_game.items()}
    with conn:
        conn.execute("DELETE FROM odds WHERE snapshot=?", (snapshot,))
        conn.executemany(
            "INSERT INTO odds VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(snapshot, collected_at, *r) for r in rows],
        )
        replace_source(conn, snapshot, "market", market, collected_at)
    return market


def build_consensus(conn, snapshot, weights=None):
    """Écrit source='consensus' pour chaque match de l'instantané.

    Moyenne pondérée des sources disponibles pour chaque match ; les poids
    sont renormalisés quand une source manque (ex. matchs lointains : MoneyPuck
    seul). Retourne (nb matchs avec marché, nb matchs sans marché).
    """
    weights = weights or WEIGHTS
    probs = defaultdict(dict)
    for g, a, h, pa, src in conn.execute(
            "SELECT game_date, away, home, p_away, source FROM probs "
            "WHERE snapshot=? AND source IN (%s)" % ",".join("?" * len(weights)),
            (snapshot, *weights)):
        probs[(g, a, h)][src] = pa

    consensus = {}
    for key, by_src in probs.items():
        total = sum(weights[s] for s in by_src)
        p = sum(weights[s] * p for s, p in by_src.items()) / total
        consensus[key] = (p, 1 - p)

    collected_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with conn:
        replace_source(conn, snapshot, "consensus", consensus, collected_at)
    n_market = sum("market" in v for v in probs.values())
    return n_market, len(consensus) - n_market


def derniere_collecte(conn):
    """Quand l'API a-t-elle été appelée pour la dernière fois ? None si jamais."""
    ligne = conn.execute("SELECT MAX(collected_at) FROM odds").fetchone()
    if not ligne or not ligne[0]:
        return None
    try:
        return dt.datetime.fromisoformat(ligne[0])
    except ValueError:      # horodatage illisible : on préfère collecter
        return None


def collect_odds(api_key, db_path=cm.DB_PATH, today=None, session=None, now=None,
                 delai_mini=DELAI_MINI, maintenant=None):
    snapshot = (today or dt.datetime.now(cm.TZ).date()).isoformat()
    maintenant = maintenant or dt.datetime.now(dt.timezone.utc)
    collected_at = maintenant.isoformat(timespec="seconds")

    conn = sqlite3.connect(db_path)
    init_db(conn)
    precedente = derniere_collecte(conn)
    if precedente is not None and maintenant - precedente < delai_mini:
        conn.close()
        heures = (maintenant - precedente).total_seconds() / 3600
        print(f"Cotes déjà collectées il y a {heures:.1f} h "
              f"(seuil {delai_mini.total_seconds() / 3600:.0f} h) : appel API évité.")
        return None

    try:
        events, remaining = fetch_events(api_key, session)
        rows, unknown = parse_events(events, now)
        if unknown:
            print(f"⚠ équipes inconnues (ajoute-les à TEAM_CODES) : "
                  f"{', '.join(unknown)}")
        if events and not rows:
            raise OddsError("aucune cote exploitable dans la réponse")
        market = save_odds(conn, snapshot, collected_at, rows)
    finally:
        conn.close()            # réseau coupé ou réponse inexploitable : on ferme

    books = sorted({r[3] for r in rows})
    print(f"{len(market)} matchs cotés par {len(books)} casinos ({', '.join(books)})")
    print(f"Crédits The Odds API restants ce mois-ci : {remaining}")
    return market


def main():
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        sys.exit("ODDS_API_KEY n'est pas définie (voir l'en-tête du fichier).")
    try:
        collect_odds(api_key)
    except (requests.RequestException, OddsError) as e:
        sys.exit(f"⚠ {e}")
    conn = sqlite3.connect(cm.DB_PATH)
    n_mk, n_mp = build_consensus(conn, dt.datetime.now(cm.TZ).date().isoformat())
    conn.close()
    print(f"Consensus : {n_mk} matchs avec cotes des casinos, {n_mp} sans")


if __name__ == "__main__":
    main()
