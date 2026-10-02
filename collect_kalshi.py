"""
Source supplémentaire : marchés de prédiction Kalshi (série KXNHLGAME).

Kalshi est une bourse d'événements réglementée (CFTC), pas un casino : le prix
d'un contrat « Équipe X gagne » se lit directement comme la probabilité que le
marché lui attribue, sans marge de casino à retirer. L'API de lecture est
publique, sans clé.

    base : https://api.elections.kalshi.com/trade-api/v2
    liste : GET /markets?series_ticker=KXNHLGAME&status=open
    ticker d'un marché : KXNHLGAME-26OCT03STLCOL-COL
                                   └┬─┘└┬┘└┬┘ └┬┘
                                 année mois+jour visiteur gagnant

Limite assumée : un match loin dans le calendrier peut n'avoir encore aucun
acheteur ni vendeur — yes_bid et yes_ask valent alors 0 (pas None : l'API les
omet du JSON). On ignore ces marchés plutôt que d'enregistrer un prix inventé.
"""

import re
import json
import sqlite3
import datetime as dt

import requests

import collect_moneypuck as cm
import collect_odds as co

URL = "https://api.elections.kalshi.com/trade-api/v2/markets"
SERIE = "KXNHLGAME"
SOURCE = "kalshi"

# KXNHLGAME-26OCT03STLCOL-COL : date (2 chiffres d'année, mois en lettres,
# jour), puis les deux équipes collées (visiteur d'abord), puis l'équipe que
# CE marché concerne (celle dont le OUI vaut « elle gagne »).
RE_TICKER = re.compile(r"^KXNHLGAME-(\d{2})([A-Z]{3})(\d{2})([A-Z]{6})-([A-Z]{3})$")
MOIS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


class KalshiError(Exception):
    pass


def parse_ticker(ticker):
    """→ (date_iso, visiteur, domicile, equipe_du_marche) ou None si le motif
    ne correspond pas (un autre type de marché NHL, pas un match classique)."""
    m = RE_TICKER.match(ticker)
    if not m:
        return None
    an, mois_txt, jour, paire, equipe = m.groups()
    mois = MOIS.get(mois_txt)
    if not mois:
        return None
    away, home = paire[:3], paire[3:]
    if equipe not in (away, home):
        return None
    date = dt.date(2000 + int(an), mois, int(jour))
    return date.isoformat(), away, home, equipe


def prix_milieu(marche):
    """Le prix « oui » en dollars (= probabilité), ou None sans liquidité.

    yes_bid/yes_ask sont absents du JSON — pas à 0 — quand personne n'a encore
    posté d'ordre. Le milieu du spread lisse le bruit d'un marché fin ; avec
    un seul côté, on prend ce qui existe.

    Kalshi les rend en texte ("0.7000"), pas en nombre : de l'argent reste en
    chaîne tant qu'on peut, pour ne pas hériter des arrondis du binaire.
    """
    bid, ask = marche.get("yes_bid_dollars"), marche.get("yes_ask_dollars")
    bid = float(bid) if bid is not None else None
    ask = float(ask) if ask is not None else None
    if bid is None and ask is None:
        return None
    if bid is None or ask is None:
        return bid if bid is not None else ask
    return (bid + ask) / 2


def parse_markets(payload, aujourdhui=None):
    """→ {(game_date, away, home): (p_away, p_home)} pour les matchs à venir.

    Les deux marchés d'un même match (un par équipe) se combinent : chacun
    donne la probabilité de SA propre équipe, donc pas besoin de les
    normaliser l'un par rapport à l'autre comme pour des cotes de casino.
    """
    aujourdhui = aujourdhui or dt.datetime.now(cm.TZ).date()
    par_match = {}
    for marche in payload.get("markets", []):
        info = parse_ticker(marche["ticker"])
        if not info:
            continue
        date_iso, away, home, equipe = info
        if dt.date.fromisoformat(date_iso) < aujourdhui:
            continue
        p = prix_milieu(marche)
        if p is None:
            continue
        cle = (date_iso, away, home)
        cote = par_match.setdefault(cle, {})
        cote[equipe] = p

    games = {}
    for (date_iso, away, home), cote in par_match.items():
        if away in cote and home in cote:
            s = cote[away] + cote[home]   # vaut ~1, mais rarement exactement
            if s > 0:
                games[(date_iso, away, home)] = (cote[away] / s, cote[home] / s)
    return games


def fetch_markets(session=None):
    session = session or requests.Session()
    marches, curseur = [], None
    while True:
        params = {"series_ticker": SERIE, "status": "open", "limit": 200}
        if curseur:
            params["cursor"] = curseur
        r = session.get(URL, params=params, timeout=30)
        if r.status_code != 200:
            raise KalshiError(f"Kalshi a répondu {r.status_code} : {r.text[:200]}")
        payload = r.json()
        marches += payload.get("markets", [])
        curseur = payload.get("cursor")
        if not curseur:
            break
    return {"markets": marches}


def collect_kalshi(db_path=cm.DB_PATH, today=None, session=None):
    snapshot = (today or dt.datetime.now(cm.TZ).date()).isoformat()
    collected_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    payload = fetch_markets(session)
    games = parse_markets(payload, today)

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
    for (g, a, h), (pa, ph) in sorted(collect_kalshi().items()):
        print(f"{g}  {a} @ {h}  {pa:.1%} / {ph:.1%}")
