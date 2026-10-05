import ui


def contraste(a, b):
    """Rapport de contraste WCAG entre deux couleurs, pour vérifier ink()."""
    def lum(c):
        h = c.lstrip("#")
        ch = []
        for i in (0, 2, 4):
            v = int(h[i:i + 2], 16) / 255
            ch.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    l1, l2 = sorted((lum(a), lum(b)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


# ── Texte barré (personnes éliminées dans un tableau) ──────────────────────

def test_barre_insere_un_combinant_apres_chaque_lettre():
    """st.dataframe ne permet pas de styler une cellule au cas par cas : le
    texte lui-même porte la marque, lettre par lettre, dernière comprise."""
    b = ui.barre("abc")
    assert b == "a̶b̶c̶"
    assert len(b) == 6


def test_barre_dune_chaine_vide_reste_vide():
    assert ui.barre("") == ""


def test_encre_noire_sur_les_couleurs_claires():
    assert ui.ink("#ffb81c") == "#11110f"      # or des Bruins
    assert ui.ink("#fedd00") == "#11110f"      # jaune des Blackhawks


def test_encre_blanche_sur_les_couleurs_foncees():
    assert ui.ink("#00205b") == "#ffffff"      # marine des Maple Leafs
    assert ui.ink("#a6192e") == "#ffffff"      # rouge du Canadien


def test_encre_choisit_toujours_le_meilleur_des_deux():
    """Propriété qui compte vraiment : sur n'importe quelle couleur d'équipe,
    ink() ne doit jamais retourner la moins lisible des deux encres."""
    for bg in ["#ffb81c", "#00205b", "#a6192e", "#fedd00", "#236192",
               "#cf4520", "#00843d", "#b9975b", "#c8102e", "#3a3a35"]:
        choisi = ui.ink(bg)
        autre = "#ffffff" if choisi == "#11110f" else "#11110f"
        assert contraste(bg, choisi) >= contraste(bg, autre), bg


def test_forme_courte_acceptee():
    assert ui.ink("#fff") == "#11110f"
    assert ui.ink("#000") == "#ffffff"


def test_carte_sans_joueur_retombe_sur_le_logo():
    html = ui._carte("COL", None)
    assert "ps-card-logo" in html and "ps-card-shot" not in html
    assert ui._carte("COL", {"action": ""})  == html   # photo manquante : idem


def test_carte_avec_joueur_montre_la_photo_et_la_plaque():
    html = ui._carte("COL", {"first_name": "Nathan", "last_name": "MacKinnon",
                             "position": "C", "points": 127, "color": "#236192",
                             "action": "https://exemple/photo.jpg"})
    assert "ps-card-shot" in html and "https://exemple/photo.jpg" in html
    assert "Nathan MacKinnon" in html
    assert "Centre" in html and "127 pts" in html
    assert "#236192" in html


def test_les_noms_francais_couvrent_les_32_equipes():
    assert len(ui.TEAMS) == 32
    assert ui.name("MTL") == "Canadiens de Montréal"
    assert ui.name("XXX") == "XXX"          # tricode inconnu : on le laisse tel quel


def test_carte_superpose_le_portrait_sur_la_photo_daction():
    """Le portrait détouré garantit qu'on voit LE joueur.

    Les photos d'action de la LNH ne sont pas cadrées sur lui : sur celle de
    McDavid, c'est le gardien adverse qui occupe le centre, et un recadrage
    en portrait le montrait à sa place.
    """
    joueur = {"first_name": "Connor", "last_name": "McDavid", "position": "C",
              "points": 138, "color": "#cf4520",
              "action": "https://exemple/action.jpg",
              "headshot": "https://exemple/portrait.png"}
    html = ui._carte("EDM", joueur)
    assert "ps-card-face" in html and "https://exemple/portrait.png" in html
    assert "ps-card-shot" in html and "https://exemple/action.jpg" in html
    # le portrait porte le nom pour les lecteurs d'écran, le décor non
    assert 'alt="Connor McDavid"' in html
    assert html.index("ps-card-shot") < html.index("ps-card-face")   # décor dessous


def test_carte_sans_portrait_garde_la_photo_daction():
    """Tous les joueurs en ont un aujourd'hui, mais rien ne le garantit."""
    html = ui._carte("EDM", {"first_name": "A", "last_name": "B", "position": "C",
                             "points": 1, "color": "#cf4520",
                             "action": "https://exemple/action.jpg"})
    assert "ps-card-face" not in html
    assert "ps-card-shot" in html
