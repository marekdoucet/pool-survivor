"""
Les menus de la barre latérale, cliqués pour de vrai avec AppTest.

L'app tourne en mode fichier (pas de secrets en local) et SURVIVOR_PICKS la
redirige vers une copie temporaire : on ne touche jamais au picks.json du
dépôt.
"""

import datetime as dt
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import optimize as op

APP = Path(__file__).resolve().parent.parent / "app.py"
LUNDI = op.week_start(dt.date.today())
PASSEE = (LUNDI - dt.timedelta(weeks=1)).isoformat()
AVANT = (LUNDI - dt.timedelta(weeks=2)).isoformat()


@pytest.fixture
def fichier(tmp_path, monkeypatch):
    chemin = tmp_path / "picks.json"
    chemin.write_text(json.dumps({"picks": [
        {"week": AVANT, "team": "EDM", "game_date": None},
        {"week": PASSEE, "team": "COL", "game_date": None},
        {"week": LUNDI.isoformat(), "team": "NJD", "game_date": None},
    ]}), encoding="utf-8")
    monkeypatch.setenv("SURVIVOR_PICKS", str(chemin))
    return chemin


def lancer():
    app = AppTest.from_file(str(APP), default_timeout=300)
    app.run()
    assert not app.exception, "\n".join(str(e.value) for e in app.exception)
    return app


def equipes(chemin):
    return {p["week"]: p["team"] for p in json.loads(chemin.read_text())["picks"]}


def supprimer(app, semaine):
    app.button(key=f"del-{semaine}").click().run()
    app.button(key=f"del-ok-{semaine}").click().run()


def test_le_x_demande_avant_de_supprimer(fichier):
    app = lancer()
    app.button(key=f"del-{PASSEE}").click().run()
    assert equipes(fichier)[PASSEE] == "COL"          # rien de supprimé encore
    assert any("Supprimer COL" in c.value for c in app.caption)
    app.button(key=f"del-ok-{PASSEE}").click().run()
    assert PASSEE not in equipes(fichier)


def test_garder_annule_la_demande(fichier):
    app = lancer()
    app.button(key=f"del-{PASSEE}").click().run()
    app.button(key=f"del-non-{PASSEE}").click().run()
    assert equipes(fichier)[PASSEE] == "COL"
    assert not any("Supprimer COL" in c.value for c in app.caption)


def lignes_mes_picks(app):
    return [m.value for m in app.sidebar.markdown if "semaine du" in m.value]


def test_la_liste_va_du_plus_ancien_au_pick_de_cette_semaine(tmp_path, monkeypatch):
    """Neon écrit l'état tel quel, sans le trier (contrairement au fichier et
    à GitHub, qui passent par _dumps) : un pick corrigé restait à la fin des
    données, sous celui de cette semaine. On reproduit ces données dans le
    désordre et on vérifie que l'AFFICHAGE, lui, est dans l'ordre."""
    chemin = tmp_path / "picks.json"
    chemin.write_text(json.dumps({"picks": [
        {"week": LUNDI.isoformat(), "team": "NJD", "game_date": None},
        {"week": AVANT, "team": "EDM", "game_date": None},
        {"week": PASSEE, "team": "COL", "game_date": None},
    ]}), encoding="utf-8")
    monkeypatch.setenv("SURVIVOR_PICKS", str(chemin))
    app = lancer()
    semaines = [l.split("semaine du ")[1] for l in lignes_mes_picks(app)]
    assert semaines == [AVANT, PASSEE, LUNDI.isoformat()]


def test_un_pick_passe_supprime_peut_etre_remis(fichier):
    app = lancer()
    supprimer(app, PASSEE)
    assert PASSEE not in equipes(fichier)

    app.selectbox(key="corr-sem").set_value(dt.date.fromisoformat(PASSEE)).run()
    app.selectbox(key=f"corr-eq-{PASSEE}").set_value("COL").run()
    app.button(key="corr-ok").click().run()
    assert equipes(fichier)[PASSEE] == "COL"


def test_corriger_un_pick_passe_remplace_lequipe(fichier):
    app = lancer()
    app.selectbox(key="corr-sem").set_value(dt.date.fromisoformat(PASSEE)).run()
    app.selectbox(key=f"corr-eq-{PASSEE}").set_value("TOR").run()
    app.button(key="corr-ok").click().run()
    assert equipes(fichier)[PASSEE] == "TOR"
    assert equipes(fichier)[AVANT] == "EDM"          # les autres semaines intactes


def test_une_equipe_deja_utilisee_est_refusee(fichier):
    """EDM est déjà prise deux semaines avant : même règle que partout."""
    app = lancer()
    app.selectbox(key="corr-sem").set_value(dt.date.fromisoformat(PASSEE)).run()
    app.selectbox(key=f"corr-eq-{PASSEE}").set_value("EDM").run()
    app.button(key="corr-ok").click().run()
    assert equipes(fichier)[PASSEE] == "COL"          # rien n'a changé
    assert any("déjà" in e.value for e in app.error)
