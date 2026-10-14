"""
L'app en anglais : chaque page s'ouvre, et aucun texte français n'y reste.

AppTest ne sait pas naviguer entre des pages-fonctions de st.navigation : on
fait donc tourner une copie du script où la page voulue est celle par défaut.
Comme pour test_app_menus, SURVIVOR_PICKS pointe vers un fichier temporaire.
"""

import datetime as dt
import json
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import optimize as op

APP = Path(__file__).resolve().parent.parent / "app.py"
PAGES = ["pick", "plan", "pool", "carte", "evolution", "forme", "precision",
         "depuis-hier"]
LUNDI = op.week_start(dt.date.today())
PASSEE = (LUNDI - dt.timedelta(weeks=1)).isoformat()
AVANT = (LUNDI - dt.timedelta(weeks=2)).isoformat()

# Un mot ou un accent typiquement français. L'aide du bouton « FR » est la
# seule phrase française voulue en mode anglais.
FRANCAIS = re.compile(r"[éèêàâçùûôîœ]|\b(le|la|les|des|du|une|pour|avec|sans|"
                      r"semaine|équipe|matchs?|aucune?|dans|cette|mon|ton|tes|"
                      r"est|sont|pas|jour)\b", re.I)
VOULU = {"Passer en français"}


@pytest.fixture
def fichier(tmp_path, monkeypatch):
    """Mon pool, un registre de deux adversaires, et moi maintenu en vie :
    sinon une défaite réelle m'éliminerait et cacherait presque tout."""
    chemin = tmp_path / "picks.json"
    chemin.write_text(json.dumps({
        "picks": [{"week": AVANT, "team": "EDM", "game_date": None},
                  {"week": PASSEE, "team": "COL", "game_date": None}],
        "force": "in",
        "pool": {"Alice": {"picks": {AVANT: "TOR", PASSEE: "DAL",
                                     LUNDI.isoformat(): "NJD"}},
                 "Bob": {"picks": {AVANT: "BOS", PASSEE: "CAR"}}},
    }), encoding="utf-8")
    monkeypatch.setenv("SURVIVOR_PICKS", str(chemin))
    return chemin


def lancer(page="pick", lang=None):
    source = APP.read_text(encoding="utf-8")
    source = source.replace('url_path="pick", default=True', 'url_path="pick"')
    source = source.replace(f'url_path="{page}"', f'url_path="{page}", default=True', 1)
    app = AppTest.from_string(source, default_timeout=300)
    if lang:
        app.query_params["lang"] = lang
    app.run()
    assert not app.exception, "\n".join(str(e.value) for e in app.exception)
    return app


def textes(app):
    """Tout le texte visible : libellés, contenus, options, en-têtes."""
    vus = set()

    def visite(n):
        for attr in ("value", "label", "body", "help", "placeholder"):
            try:
                v = getattr(n, attr)
            except Exception:
                continue
            if isinstance(v, str) and v.strip():
                vus.add(v)
        try:
            opts = n.options
        except Exception:
            opts = None
        if isinstance(opts, (list, tuple)):
            vus.update(str(o) for o in opts)
        # En-têtes de tableau : ce que le navigateur affiche, c'est le libellé
        # de column_config quand il y en a un, sinon le nom de la colonne.
        proto = getattr(n, "proto", None)
        colonnes = getattr(proto, "columns", None) if proto is not None else None
        if isinstance(colonnes, str) and colonnes:
            libelles = {k: c.get("label") for k, c in json.loads(colonnes).items()}
            try:
                noms = list(n.value.columns)
            except Exception:
                noms = []
            vus.update(libelles.get(c) or c for c in noms)
        enfants = getattr(n, "children", None)
        for e in (enfants.values() if isinstance(enfants, dict) else ()):
            visite(e)

    visite(app._tree)
    return {t for t in vus if not t.startswith("<style>")}


@pytest.mark.parametrize("page", PAGES)
def test_chaque_page_est_entierement_en_anglais(fichier, page):
    restes = sorted(t for t in textes(lancer(page, "en"))
                    if FRANCAIS.search(t) and t not in VOULU)
    assert not restes, "Texte français en mode anglais :\n" + "\n".join(restes)


def test_le_detecteur_voit_vraiment_le_francais(fichier):
    """Sans langue demandée, l'app est en français : le détecteur doit le voir,
    sinon le test au-dessus réussirait pour rien."""
    assert any(FRANCAIS.search(t) for t in textes(lancer("pick")))


def test_le_bouton_bascule_dans_les_deux_sens(fichier):
    app = lancer()
    assert app.title[0].value.endswith("Pool survivor NHL 2026-27")
    app.button(key="langue").click().run()
    assert app.title[0].value.endswith("NHL Survivor Pool 2026-27")
    assert app.query_params.get("lang") in ("en", ["en"])
    app.button(key="langue").click().run()
    assert app.title[0].value.endswith("Pool survivor NHL 2026-27")
    assert "lang" not in app.query_params


def test_un_refus_de_pick_est_traduit(fichier):
    """EDM est déjà prise deux semaines avant : le message de picks.py, en
    français à la source, doit arriver en anglais."""
    app = lancer(lang="en")
    app.selectbox(key="corr-sem").set_value(dt.date.fromisoformat(PASSEE)).run()
    app.selectbox(key=f"corr-eq-{PASSEE}").set_value("EDM").run()
    app.button(key="corr-ok").click().run()
    assert any("has already been used" in e.value for e in app.error)


def test_eliminated_nest_jamais_enregistre_comme_une_equipe(fichier):
    """Bob a pris COL la semaine passée, et COL a perdu : il est éliminé, et
    sa case de cette semaine affiche « Eliminated ». Enregistrer le registre
    en anglais ne doit pas écrire ce mot comme si c'était une équipe."""
    bob = json.loads(fichier.read_text(encoding="utf-8"))
    bob["pool"]["Bob"]["picks"][PASSEE] = "COL"
    fichier.write_text(json.dumps(bob), encoding="utf-8")
    app = lancer("pool", "en")
    registre = next(d for d in app.dataframe
                    if "Statut" in d.value.columns).value
    assert registre.set_index("Joueur").loc["Bob", "Pick"] == "Eliminated"
    app.button(key=next(b.key for b in app.button
                        if b.label == "Save the registry")).click().run()
    sauve = json.loads(fichier.read_text(encoding="utf-8"))["pool"]
    equipes = {e for j in sauve.values() for e in j["picks"].values()}
    assert "Eliminated" not in equipes and "Éliminé" not in equipes
