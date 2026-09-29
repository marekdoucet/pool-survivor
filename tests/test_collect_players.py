import collect_players as cpl


def skater(pid, first, last, points, goals=0, pos="C", games=80):
    return {"playerId": pid, "firstName": {"default": first},
            "lastName": {"default": last}, "points": points, "goals": goals,
            "positionCode": pos, "gamesPlayed": games,
            "headshot": f"https://assets.nhle.com/mugs/{pid}.png"}


def test_garde_le_meneur_aux_points():
    payload = {"skaters": [skater(1, "Petit", "Pointeur", 40),
                           skater(2, "Nathan", "MacKinnon", 127, goals=53),
                           skater(3, "Moyen", "Joueur", 80)]}
    row = cpl.leader(payload, "COL")
    assert row[:5] == ["COL", 2, "Nathan", "MacKinnon", "C"]
    assert row[7] == 127


def test_egalite_departagee_par_les_buts_puis_le_nom():
    """Départage stable : sinon le fichier changerait sans raison chaque jour."""
    payload = {"skaters": [skater(1, "A", "Aaa", 80, goals=30),
                           skater(2, "B", "Bbb", 80, goals=40)]}
    assert cpl.leader(payload, "X")[1] == 2          # plus de buts

    payload = {"skaters": [skater(1, "A", "Aaa", 80, goals=30),
                           skater(2, "B", "Bbb", 80, goals=30)]}
    assert cpl.leader(payload, "X")[3] == "Bbb"      # puis le nom


def test_equipe_sans_statistiques_ne_donne_rien():
    assert cpl.leader({"skaters": []}, "SEA") is None
    assert cpl.leader({}, "SEA") is None


def test_joueur_sans_points_est_ignore():
    payload = {"skaters": [{"playerId": 9, "points": None},
                           skater(2, "Vrai", "Meneur", 12)]}
    assert cpl.leader(payload, "X")[1] == 2


def test_les_32_equipes_sont_listees():
    assert len(cpl.TEAMS) == 32
    assert len(set(cpl.TEAMS)) == 32
    assert "UTA" in cpl.TEAMS and all(len(t) == 3 for t in cpl.TEAMS)


# ── Couleur d'équipe ───────────────────────────────────────────────────────
# Extraits réels des logos officiels, pour que le test dise quelque chose.

def test_couleur_ignore_le_blanc_et_le_gris():
    """Le contour argenté du logo du Colorado est majoritaire sans rien dire."""
    svg = ('<path fill="#ffffff"/>' * 5 + '<path fill="#c1c6c8"/>' * 5
           + '<path fill="#236192"/>' * 3 + '<path fill="#010101"/>')
    assert cpl.team_color(svg) == "#236192"


def test_couleur_prend_la_plus_saturee_pas_la_plus_frequente():
    """Edmonton : marine cinq fois, orange une seule — l'orange gagne."""
    svg = '<path fill="#00205b"/>' * 5 + '<path fill="#cf4520"/>'
    assert cpl.team_color(svg) == "#cf4520"


def test_couleur_accepte_les_formes_courtes_et_le_style():
    assert cpl.team_color('<path style="fill:#fc0"/>') == "#ffcc00"


def test_logo_sans_couleur_exploitable_donne_le_neutre():
    assert cpl.team_color('<path fill="#ffffff"/><path fill="#010101"/>') == cpl.NEUTRE
    assert cpl.team_color("") == cpl.NEUTRE
