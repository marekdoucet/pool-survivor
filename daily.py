"""
Collecte quotidienne complète :
MoneyPuck → cotes des casinos → Dimers → Kalshi → calendrier → Puckcast → consensus.

    python daily.py

La base survivor.db est d'abord reconstruite à partir de data/ (source de
vérité versionnée dans Git), puis la collecte du jour y est exportée.

Sans ODDS_API_KEY, le consensus se fait sans les casinos (rien ne plante).
Code de sortie 1 si une des collectes a échoué.
"""

import os
import sys
import sqlite3
import datetime as dt

import requests

import collect_moneypuck as cm
import collect_odds as co
import collect_dimers as cd
import collect_kalshi as kl
import collect_puckcast as cp
import collect_players as cpl
import page
import schedule as sch
import store


def main():
    today = dt.datetime.now(cm.TZ).date()
    failed = False

    if any(store.DATA_DIR.glob("probs/*.csv.gz")):
        print(f"── Base reconstruite depuis {store.DATA_DIR}/ : {store.build_db()} lignes\n")

    print("── MoneyPuck")
    n_days = 49
    _total, errors = cm.collect(n_days, today=today)
    failed |= len(errors) > n_days // 2

    print("\n── Casinos (The Odds API)")
    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("ODDS_API_KEY absente : consensus sans les casinos")
    else:
        # FORCER_CASINOS : contourne DELAI_MINI pour une vérification manuelle
        # ponctuelle (ex. après un changement de REGIONS), jamais utilisé par
        # les exécutions planifiées — sinon la paire de rattrapage perdrait
        # sa gratuité.
        #
        # GitHub Actions transmet un booléen d'entrée comme la CHAÎNE "true"
        # ou "false" — et "false" est vrai en Python, car non vide. D'où le
        # test explicite contre "true" plutôt qu'un simple if sur la valeur.
        force = os.environ.get("FORCER_CASINOS", "").lower() == "true"
        kw = {"delai_mini": dt.timedelta(0)} if force else {}
        try:
            co.collect_odds(api_key, today=today, **kw)
        except (requests.RequestException, co.OddsError) as e:
            print(f"⚠ {e}")
            failed = True

    print("\n── Dimers")
    try:
        print(f"{len(cd.collect_dimers(today=today))} matchs prédits")
    except (requests.RequestException, cd.DimersError, ValueError) as e:
        print(f"⚠ {e}")
        failed = True

    print("\n── Kalshi")
    try:
        print(f"{len(kl.collect_kalshi(today=today))} matchs avec prix des deux côtés")
    except (requests.RequestException, kl.KalshiError) as e:
        print(f"⚠ {e}")
        failed = True

    print("\n── Calendrier LNH")
    try:
        print(f"{sch.update_schedule()} matchs de saison régulière")
    except (requests.RequestException, ValueError) as e:
        print(f"⚠ {e} (on garde le calendrier précédent)")
        failed = True

    print("\n── Puckcast")   # après le calendrier : il sert à orienter visiteur/local
    try:
        print(f"{len(cp.collect_puckcast(today=today))} matchs prédits")
    except (requests.RequestException, cp.PuckcastError) as e:
        print(f"⚠ {e}")
        failed = True

    print("\n── Joueurs vedettes")
    try:
        rows, ko = cpl.collect_players()
        print(f"{len(rows)} meneurs" + (f", {len(ko)} sans statistiques : "
                                        f"{', '.join(ko)}" if ko else ""))
    except (requests.RequestException, cpl.PlayersError) as e:
        print(f"⚠ {e} (on garde les joueurs précédents)")
        failed = True

    print("\n── Consensus")
    conn = sqlite3.connect(cm.DB_PATH)
    co.init_db(conn)
    n_mk, n_other = co.build_consensus(conn, today.isoformat())
    changed = store.export_snapshot(conn, today.isoformat()) + store.export_schedule(conn)
    conn.close()
    poids = ", ".join(f"{s} {w:.0%}" for s, w in co.WEIGHTS.items())
    print(f"{n_mk} matchs avec cotes des casinos, {n_other} sans (poids : {poids})")
    print(f"\n── Export : {len(changed)} fichier(s) modifié(s) dans {store.DATA_DIR}/")

    print("\n── Page publique")
    try:
        modifies = page.ecrire()
        print(f"{len(modifies)} fichier(s) dans docs/"
              + (" (rien n'a change)" if not modifies else ""))
    except Exception as e:
        # La vitrine ne doit JAMAIS faire echouer la collecte : les
        # donnees comptent, la page est secondaire.
        print(f"⚠ page non generee : {e}")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
