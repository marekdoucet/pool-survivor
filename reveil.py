"""
Garde l'application Streamlit éveillée en la visitant avec un vrai navigateur.

Streamlit Community Cloud endort une application restée environ 12 h sans
visiteur. Un `curl` ne compte pas comme visiteur : il reçoit l'enveloppe HTML
mais n'ouvre jamais la session (websocket) qui fait tourner l'app. Pire, quand
l'app dort, l'adresse répond 200 avec la page « Zzzz… » : l'ancien réveil par
curl se croyait réussi et l'app s'endormait quand même.

Ce script fait donc ce que ferait une personne :
  1. ouvrir l'app dans Chromium (sans écran) ;
  2. cliquer « Yes, get this app back up! » si l'app dort ;
  3. attendre que la vraie page s'affiche, dans le cadre /~/+/ où Streamlit
     Cloud sert l'app.

    python reveil.py      # code de sortie 0 si l'app a répondu, 1 sinon
"""

import re
import sys
import time

from playwright.sync_api import sync_playwright

import page

# Texte de la navigation, présent sur toutes les pages de l'app.
TEMOIN = "Pick de la semaine"
BOUTON_REVEIL = re.compile(r"get this app back up", re.I)
DELAI = 300          # secondes ; le réveil complet a été mesuré à 182 s


def cadre_app(pg):
    return next((f for f in pg.frames if "/~/+/" in f.url), None)


def texte(cadre):
    try:
        return cadre.inner_text("body", timeout=2000)
    except Exception:
        return ""


def reveiller(url=page.APP, delai=DELAI):
    """Retourne (succès, secondes, a_dû_cliquer)."""
    debut = time.monotonic()
    clique = False
    with sync_playwright() as p:
        navigateur = p.chromium.launch()
        try:
            pg = navigateur.new_page()
            pg.goto(url, wait_until="domcontentloaded", timeout=120_000)
            while time.monotonic() - debut < delai:
                # Le bouton de réveil peut être sur la page ou dans un cadre.
                for cadre in pg.frames:
                    bouton = cadre.get_by_role("button", name=BOUTON_REVEIL)
                    try:
                        if bouton.count():
                            bouton.first.click()
                            clique = True
                            print("App endormie : bouton de réveil cliqué.")
                    except Exception:
                        pass
                app = cadre_app(pg)
                if app and TEMOIN in texte(app):
                    # Laisser la session ouverte un moment : c'est elle que
                    # Streamlit compte comme visite.
                    time.sleep(10)
                    return True, time.monotonic() - debut, clique
                time.sleep(5)
            pg.screenshot(path="reveil-echec.png")
            return False, time.monotonic() - debut, clique
        finally:
            navigateur.close()


if __name__ == "__main__":
    ok, secondes, clique = reveiller()
    if ok:
        print(f"Application éveillée en {secondes:.0f} s"
              + (" (elle dormait)." if clique else "."))
        sys.exit(0)
    print(f"::warning::Réveil échoué après {secondes:.0f} s : la page de "
          f"l'app n'est pas apparue. Capture : reveil-echec.png")
    sys.exit(1)
