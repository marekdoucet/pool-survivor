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


def test_supprimer_passe_par_une_confirmation(fichier):
    app = lancer()
    # Le ✕ n'est plus un bouton qui agit : c'est le « Supprimer » du menu.
    assert not [b for b in app.button if b.key == f"del-{PASSEE}"]
    app.button(key=f"del-ok-{PASSEE}").click().run()
    assert PASSEE not in equipes(fichier)


def test_un_pick_passe_supprime_peut_etre_remis(fichier):
    app = lancer()
    app.button(key=f"del-ok-{PASSEE}").click().run()
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
