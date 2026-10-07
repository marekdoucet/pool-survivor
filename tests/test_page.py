import datetime as dt

import page


CTX = {
    "maj": {"fr": "mercredi 30 septembre", "en": "Wednesday September 30"},
    "semaine": {"fr": "28 septembre", "en": "September 28"},
    "numero": 1,
    "jour_match": {"fr": "samedi 3 octobre", "en": "Saturday October 3"},
    "meilleur": {"team": "COL", "opponent": "STL", "home": True, "p": 0.709,
                 "game_date": "2026-10-03"},
    "choix": [{"team": "COL", "opponent": "STL", "home": True, "p": 0.709,
               "e": 2.337,
               "jour": {"fr": "samedi 3 octobre", "en": "Saturday October 3"}},
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


def test_le_jeton_google_est_sur_les_deux_pages():
    """Google revalide periodiquement : si la balise disparait d'une des deux
    pages, la propriete est retiree."""
    for lang in ("fr", "en"):
        h = page.rendu(CTX, lang)
        assert f'content="{page.VERIF_GOOGLE}"' in h
        assert 'name="google-site-verification"' in h


# ── Politique de confidentialité ──────────────────────────────────────────
# Google l'exige pour publier l'écran de consentement OAuth en production.
# Sans elle, seuls les comptes inscrits comme « utilisateurs de test »
# peuvent se connecter — donc deux personnes, pas seize.

def test_la_page_existe_dans_les_deux_langues():
    for lang, mot in (("fr", "Politique de confidentialité"),
                      ("en", "Privacy policy")):
        h = page.confidentialite(lang)
        assert mot in h
        assert f'<html lang="{lang}"' in h


def test_elle_dit_ce_qui_est_recueilli():
    """Le contenu doit rester vrai : c'est un engagement public."""
    fr = page.confidentialite("fr")
    assert "courriel" in fr and "prénom" in fr
    assert "Neon" in fr
    assert "mot de passe" in fr          # on explique qu'on ne le voit jamais


def test_elle_dit_ce_qui_nest_pas_fait():
    fr, en = page.confidentialite("fr"), page.confidentialite("en")
    assert "Aucune publicité" in fr and "vendu" in fr
    assert "No advertising" in en and "sold" in en


def test_on_peut_demander_la_suppression():
    """Une politique qui ne dit pas comment effacer ne vaut rien."""
    for lang in ("fr", "en"):
        assert page.DEPOT in page.confidentialite(lang)


def test_elle_porte_la_balise_google_et_ses_alternatives():
    for lang in ("fr", "en"):
        h = page.confidentialite(lang)
        assert f'content="{page.VERIF_GOOGLE}"' in h
        assert 'hreflang="fr"' in h and 'hreflang="en"' in h
        assert f'rel="canonical" href="{page.CONFID[lang][1]}"' in h


def test_le_fond_sombre_ne_casse_pas():
    """La feuille de style utilise --lueur dans le fond du body. La page
    d'accueil la pose sur <html> aux couleurs de l'équipe ; ici il n'y a pas
    d'équipe. Sans valeur, le dégradé est invalide et la page vire au blanc."""
    for lang in ("fr", "en"):
        h = page.confidentialite(lang)
        assert "--lueur:" in h[:h.index("<head>")]
        assert '<main class="page">' in h


def test_le_sitemap_liste_les_quatre_pages():
    s = page.sitemap("2026-10-01")
    for _chemin, url in list(page.CHEMINS.values()) + list(page.CONFID.values()):
        assert f"<loc>{url}</loc>" in s
    assert s.count("<url>") == 4


def test_le_numero_de_semaine_est_dans_le_titre():
    """Pour sortir sur « pool survivor LNH semaine 2 »."""
    assert "<title>Pool survivor LNH, semaine 1 :" in page.rendu(CTX, "fr")
    assert "cheat sheet, week 1:" in page.rendu(CTX, "en")


def test_chaque_match_a_sa_phrase_de_pronostic():
    fr = page.rendu(CTX, "fr")
    assert ("<b>Avalanche du Colorado</b> contre Blues de St. Louis "
            "(samedi 3 octobre) — 70.9 % de probabilité de victoire.") in fr
    # sans date connue, la phrase reste correcte, sans parenthèses vides
    assert "<b>Oilers d&#x27;Edmonton</b> contre Kraken de Seattle — 67.8 %" in fr
    assert "() " not in fr
    assert "70.9% chance of winning." in page.rendu(CTX, "en")


def test_la_faq_est_dans_les_deux_langues():
    for lang in ("fr", "en"):
        h = page.rendu(CTX, lang)
        titre, questions = page.FAQ[lang]
        assert len(questions) >= 5
        for q, _r in questions:
            assert f"<h3>{page._html.escape(q)}</h3>" in h


def test_le_numero_part_du_premier_match_de_la_saison():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE schedule (game_date TEXT)")
    assert page.numero_semaine(conn, dt.date(2026, 10, 5)) == 1   # vide
    conn.execute("INSERT INTO schedule VALUES ('2026-09-29'), ('2026-10-10')")
    assert page.numero_semaine(conn, dt.date(2026, 9, 28)) == 1
    assert page.numero_semaine(conn, dt.date(2026, 10, 5)) == 2
    assert page.numero_semaine(conn, dt.date(2026, 12, 28)) == 14
