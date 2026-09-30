import datetime as dt

import page


CTX = {
    "maj": {"fr": "mercredi 30 septembre", "en": "Wednesday September 30"},
    "semaine": {"fr": "28 septembre", "en": "September 28"},
    "jour_match": {"fr": "samedi 3 octobre", "en": "Saturday October 3"},
    "meilleur": {"team": "COL", "opponent": "STL", "home": True, "p": 0.709,
                 "game_date": "2026-10-03"},
    "choix": [{"team": "COL", "opponent": "STL", "home": True, "p": 0.709,
               "e": 2.337},
              {"team": "EDM", "opponent": "SEA", "home": True, "p": 0.678,
               "e": 2.300}],
    "disettes": [{"semaine": {"fr": "19 octobre", "en": "October 19"},
                  "p": 0.624}],
    "joueurs": {"COL": {"first_name": "Nathan", "last_name": "MacKinnon",
                        "position": "C", "points": "127", "color": "#236192",
                        "headshot": "https://exemple/portrait.png",
                        "action": "https://exemple/action.jpg"}},
}


def test_le_contenu_est_dans_le_html_pas_en_javascript():
    """Le point de tout l'exercice : l'app Streamlit renvoie 5 Ko de script
    sans un mot de contenu, donc rien à indexer."""
    h = page.rendu(CTX, "fr")
    assert "Avalanche du Colorado" in h
    assert "70.9" in h
    assert "Blues de St. Louis" in h
    assert len(h) > 3000


def test_les_expressions_visees_sont_dans_le_titre():
    fr = page.rendu(CTX, "fr")
    en = page.rendu(CTX, "en")
    assert "<title>Pool survivor LNH" in fr
    assert "NHL survivor pool cheat sheet" in en


def test_chaque_langue_utilise_ses_noms_dequipes():
    fr, en = page.rendu(CTX, "fr"), page.rendu(CTX, "en")
    assert "Avalanche du Colorado" in fr and "Colorado Avalanche" not in fr
    assert "Colorado Avalanche" in en and "Avalanche du Colorado" not in en


def test_les_deux_pages_se_declarent_alternatives():
    """Sans hreflang, Google traite les deux pages comme du contenu dupliqué
    et en déclasse une."""
    for lang in ("fr", "en"):
        h = page.rendu(CTX, lang)
        assert 'hreflang="fr"' in h and 'hreflang="en"' in h
        assert 'hreflang="x-default"' in h
        assert f'<html lang="{lang}"' in h


def test_chaque_page_declare_sa_propre_adresse_canonique():
    assert f'rel="canonical" href="{page.BASE}/"' in page.rendu(CTX, "fr")
    assert f'rel="canonical" href="{page.BASE}/en/"' in page.rendu(CTX, "en")


def test_la_description_resume_le_pick():
    h = page.rendu(CTX, "fr")
    debut = h[h.index('name="description"'):]
    assert "Avalanche du Colorado" in debut[:200]


def test_les_semaines_creuses_apparaissent():
    assert "19 octobre" in page.rendu(CTX, "fr")
    assert "October 19" in page.rendu(CTX, "en")


def test_sans_semaine_creuse_la_section_disparait():
    ctx = {**CTX, "disettes": []}
    assert "à éviter" not in page.rendu(ctx, "fr")
    assert "Weeks to avoid" not in page.rendu(ctx, "en")


def test_le_html_est_echappe():
    """Les noms viennent de nos tables, mais rien ne doit passer brut."""
    ctx = {**CTX, "semaine": {"fr": "<script>x</script>", "en": "x"}}
    assert "<script>x</script>" not in page.rendu(ctx, "fr")


def test_le_sitemap_liste_les_deux_pages():
    s = page.sitemap("2026-09-30")
    assert f"{page.BASE}/</loc>" in s and f"{page.BASE}/en/</loc>" in s
    assert "2026-09-30" in s


def test_robots_pointe_vers_le_sitemap():
    assert f"{page.BASE}/sitemap.xml" in page.robots()


def test_les_32_equipes_sont_traduites():
    assert len(page.TEAMS["fr"]) == 32
    assert len(page.TEAMS["en"]) == 32
    assert set(page.TEAMS["fr"]) == set(page.TEAMS["en"])


def test_les_dates_suivent_lusage_de_chaque_langue():
    d = dt.date(2026, 10, 3)
    assert page.fr_date(d, "fr") == "3 octobre"
    assert page.fr_date(d, "en") == "October 3"


def test_le_pourcentage_suit_lusage_de_chaque_langue():
    assert page.pourcent(0.709, "fr") == "70.9 %"    # espace insécable d'usage
    assert page.pourcent(0.709, "en") == "70.9%"


# ── L'apparence de l'app, reprise sur la vitrine ──────────────────────────

def test_la_carte_de_hockey_est_sur_la_page():
    h = page.rendu(CTX, "fr")
    assert 'class="carte-fond"' in h and "https://exemple/action.jpg" in h
    assert 'class="carte-vis"' in h and "https://exemple/portrait.png" in h
    assert "Nathan MacKinnon" in h and "127 pts" in h


def test_le_poste_est_traduit():
    assert "Centre" in page.rendu(CTX, "fr")
    assert "Center" in page.rendu(CTX, "en")


def test_la_lueur_prend_la_couleur_de_lequipe():
    h = page.rendu(CTX, "fr")
    assert "--lueur:rgba(35,97,146" in h          # #236192
    assert "--photo:url('https://exemple/action.jpg')" in h


def test_lencre_des_jetons_est_calculee():
    assert page.encre("#ffb81c") == "#11110f"    # or des Bruins
    assert page.encre("#00205b") == "#fff"       # marine des Leafs


def test_sans_joueur_la_carte_se_reduit_au_logo():
    # On cherche l'attribut, pas le mot : le nom des classes apparaît aussi
    # dans la feuille de style, qui est toujours présente.
    ctx = {**CTX, "joueurs": {}}
    h = page.rendu(ctx, "fr")
    assert 'class="carte-logo"' in h
    assert 'class="carte-fond"' not in h


def test_les_logos_sont_dans_le_tableau():
    h = page.rendu(CTX, "fr")
    assert "logos/nhl/svg/COL_dark.svg" in h
    assert "logos/nhl/svg/EDM_dark.svg" in h


def test_les_images_sont_en_chargement_differe():
    """Le texte doit s'afficher avant les images : c'est lui qu'on indexe."""
    h = page.rendu(CTX, "fr")
    assert h.count('loading="lazy"') >= 4


def test_le_bouton_mene_a_lapp_et_previent_du_delai():
    """Sans l'avertissement, un visiteur qui attend deux minutes croit a une
    panne — c'est exactement ce qui nous est arrive."""
    for lang, mot in (("fr", "Ouvrir l'application"), ("en", "Open the app")):
        h = page.rendu(CTX, lang)
        assert page.APP in h
        assert mot in h
        assert ("panne" if lang == "fr" else "not broken") in h
