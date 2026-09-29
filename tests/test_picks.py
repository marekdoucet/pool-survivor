import datetime as dt

import pytest

import picks as pk

W1, W2 = dt.date(2026, 9, 28), dt.date(2026, 10, 5)


def test_add_replace_and_no_reuse(tmp_path):
    picks = pk.add([], W1, "VGK", "2026-10-04")
    picks = pk.add(picks, W1, "EDM")            # remplace le pick de la semaine
    assert [p["team"] for p in picks] == ["EDM"]
    with pytest.raises(ValueError, match="déjà"):
        pk.add(picks, W2, "EDM")
    with pytest.raises(ValueError, match="inconnue"):
        pk.add(picks, W2, "XYZ")

    path = tmp_path / "p.json"
    pk.save({"picks": pk.add(picks, W2, "COL"), "days": {}}, path)
    loaded = pk.load(path)
    assert [p["team"] for p in loaded["picks"]] == ["EDM", "COL"]
    assert pk.remove(loaded["picks"], W1)[0]["team"] == "COL"


def test_old_file_without_days(tmp_path):
    path = tmp_path / "p.json"
    path.write_text('{"picks": [{"week": "2026-09-28", "team": "VGK", "game_date": null}]}')
    assert pk.load(path)["days"] == {}


def test_set_day():
    days = pk.set_day({}, W1, dt.date(2026, 10, 4))        # dimanche de la semaine 1
    assert days == {"2026-09-28": "2026-10-04"}
    assert pk.set_day(days, W1, None) == {}                  # retour à l'automatique
    with pytest.raises(ValueError):
        pk.set_day({}, W1, dt.date(2026, 10, 2))           # vendredi
    with pytest.raises(ValueError):
        pk.set_day({}, W1, dt.date(2026, 10, 10))          # samedi d'une autre semaine


def test_load_missing_file(tmp_path):
    assert pk.load(tmp_path / "absent.json") == {"picks": [], "days": {}, "resets": [], "out": None, "pool": {}}


def test_planning():
    wed, fri, sat = dt.date(2026, 9, 30), dt.date(2026, 10, 2), dt.date(2026, 10, 3)
    picks = pk.add(pk.add([], dt.date(2026, 9, 21), "EDM"), W1, "VGK")
    assert pk.pick_deadline(wed) == fri
    assert pk.planning([], wed) == (wed, set(), None)
    # Jusqu'au vendredi minuit : modifiable, VGK redevient disponible
    start, used, prov = pk.planning(picks, wed)
    assert (start, used, prov["team"]) == (wed, {"EDM"}, "VGK")
    assert pk.planning(picks, fri)[2]["team"] == "VGK"      # le vendredi même
    # Dès samedi : verrouillé, on passe à lundi prochain
    assert pk.planning(picks, sat) == (W2, {"EDM", "VGK"}, None)


class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code, self.body = status, body or {}

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise pk.requests.HTTPError(str(self.status_code))


class FakeGitHub:
    """Simule l'API « contents » de GitHub pour un seul fichier."""

    def __init__(self):
        self.content, self.sha, self.puts = None, None, []

    def get(self, url, **kw):
        if self.content is None:
            return FakeResponse(404)
        return FakeResponse(200, {"sha": self.sha, "content": self.content})

    def put(self, url, json=None, **kw):
        self.puts.append(json)
        if json.get("sha") != self.sha:
            return FakeResponse(409)
        self.content, self.sha = json["content"], f"sha{len(self.puts)}"
        return FakeResponse(200, {"content": {"sha": self.sha}})


def test_github_picks_roundtrip_and_conflict():
    gh = FakeGitHub()
    me = {"name": "moi", "email": "1+moi@users.noreply.github.com"}
    store = pk.GitHubPicks("moi/pool", "jeton", session=gh, author=me)
    assert store.load() == {"picks": [], "days": {}, "resets": [], "out": None, "pool": {}}
    store.save({"picks": pk.add([], W1, "VGK"), "days": {}}, "Pick VGK")
    assert gh.puts[0]["message"] == "Pick VGK" and gh.puts[0]["branch"] == "main"
    assert gh.puts[0]["author"] == gh.puts[0]["committer"] == me   # pas le vrai courriel

    other = pk.GitHubPicks("moi/pool", "jeton", session=gh)
    state = other.load()
    assert [p["team"] for p in state["picks"]] == ["VGK"]
    other.save({"picks": pk.add(state["picks"], W2, "CAR"),
                "days": pk.set_day(state["days"], W2, dt.date(2026, 10, 11))})

    # `store` a une version périmée : on refuse d'écraser le pick de `other`
    with pytest.raises(RuntimeError, match="recharge"):
        store.save({"picks": pk.add([], W2, "COL"), "days": {}})
    final = pk.GitHubPicks("moi/pool", "j", session=gh).load()
    assert [p["team"] for p in final["picks"]] == ["VGK", "CAR"]
    assert final["days"] == {"2026-10-05": "2026-10-11"}


# ── Tours : le pool repart quand il ne reste qu'une personne en vie ────────

R = dt.date(2027, 1, 11)          # lundi du redépart


def test_sans_reset_rien_ne_change():
    assert pk.last_reset([]) is None
    assert pk.this_round([{"week": "2026-09-28"}], None) == [{"week": "2026-09-28"}]


def test_reset_libere_les_equipes_deja_prises():
    picks = pk.add(pk.add([], W1, "VGK"), W2, "COL")
    # sans redépart, VGK reste bloquée
    with pytest.raises(ValueError, match="déjà"):
        pk.add(picks, R, "VGK")
    # après le redépart, elle redevient disponible
    assert [p["team"] for p in pk.add(picks, R, "VGK", since=R)] == ["VGK", "COL", "VGK"]


def test_reset_narchive_pas_les_anciens_picks():
    """Le fichier garde tout : un redépart ne doit rien effacer."""
    etat = {"picks": pk.add([], W1, "VGK"), "days": {}, "resets": []}
    apres = pk.reset(etat, R)
    assert apres["resets"] == ["2027-01-11"]
    assert [p["team"] for p in apres["picks"]] == ["VGK"]      # toujours là
    assert pk.undo_reset(apres, R)["resets"] == []             # annulable


def test_reset_deux_fois_ne_duplique_pas():
    etat = pk.reset(pk.reset(pk.empty_state(), R), R)
    assert etat["resets"] == ["2027-01-11"]


def test_last_reset_prend_le_plus_recent_et_ignore_le_futur():
    resets = ["2026-11-02", "2027-01-11"]
    assert pk.last_reset(resets) == R
    # un redépart préparé pour plus tard ne libère pas encore les équipes
    assert pk.last_reset(resets, today=dt.date(2026, 12, 1)) == dt.date(2026, 11, 2)
    assert pk.last_reset(resets, today=dt.date(2026, 10, 1)) is None


def test_planning_ignore_les_picks_dun_tour_precedent():
    picks = pk.add(pk.add([], W1, "VGK"), W2, "COL")
    apres = dt.date(2027, 1, 13)      # mercredi de la semaine du redépart
    _, used_avant, _ = pk.planning(picks, apres)
    _, used_apres, _ = pk.planning(picks, apres, since=R)
    assert used_avant == {"VGK", "COL"}
    assert used_apres == set()        # tout le monde redevient disponible


# ── Horizon et élimination ────────────────────────────────────────────────

def test_horizon_de_base_puis_qui_suit_le_tour():
    """8 semaines tant qu'on n'a pas dépassé 8 ; ensuite une de plus par semaine."""
    assert pk.round_week(R, dt.date(2027, 1, 13)) == 1
    assert pk.horizon(R, dt.date(2027, 1, 13)) == 8        # semaine 1
    assert pk.horizon(R, R + dt.timedelta(weeks=7)) == 8   # semaine 8
    assert pk.horizon(R, R + dt.timedelta(weeks=8)) == 9   # semaine 9
    assert pk.horizon(R, R + dt.timedelta(weeks=12)) == 13


def test_horizon_sans_redepart_part_de_la_semaine_courante():
    today = dt.date(2026, 10, 7)
    assert pk.round_week(None, today) == 1
    assert pk.horizon(None, today) == 8


def test_elimination_et_retour():
    etat = pk.mark_out(pk.empty_state(), dt.date(2026, 11, 9))
    assert pk.eliminated(etat) == dt.date(2026, 11, 9)
    assert pk.eliminated(pk.back_in(etat)) is None


def test_un_redepart_remet_en_vie():
    """Le pool repart : l'élimination du tour précédent ne compte plus."""
    etat = pk.mark_out(pk.empty_state(), dt.date(2026, 11, 9))
    assert pk.reset(etat, R)["out"] is None
    assert pk.eliminated(pk.reset(etat, R)) is None


def test_elimination_dun_tour_precedent_est_ignoree():
    """Cas tordu : un « out » resté dans le fichier, plus vieux que le tour."""
    etat = {"picks": [], "days": {}, "resets": [R.isoformat()],
            "out": "2026-11-09"}
    assert pk.eliminated(etat, today=dt.date(2027, 2, 1)) is None


# ── Le registre du pool ───────────────────────────────────────────────────

def pool_etat(**joueurs):
    return pk.set_pool(pk.empty_state(), joueurs)


def test_registre_garde_les_joueurs_et_leurs_picks():
    etat = pool_etat(
        Alex={"picks": {"2026-09-28": "COL"}, "out": None},
        Marie={"picks": {"2026-09-28": "TOR"}, "out": None})
    assert sorted(pk.pool(etat)) == ["Alex", "Marie"]
    assert pk.pool_picks(etat, W1) == {"Alex": "COL", "Marie": "TOR"}


def test_noms_vides_ignores_et_picks_vides_nettoyes():
    """Un éditeur de tableau renvoie des lignes vides : elles ne doivent pas
    créer de joueur fantôme."""
    etat = pk.set_pool(pk.empty_state(), {
        "  ": {"picks": {}, "out": None},
        "Alex": {"picks": {"2026-09-28": "COL", "2026-10-05": ""}, "out": None}})
    assert list(pk.pool(etat)) == ["Alex"]
    assert pk.pool(etat)["Alex"]["picks"] == {"2026-09-28": "COL"}


def test_un_elimine_ne_compte_plus_ni_dans_les_vivants_ni_dans_la_popularite():
    etat = pool_etat(
        Alex={"picks": {"2026-09-28": "COL"}, "out": None},
        Bob={"picks": {"2026-09-28": "COL"}, "out": "2026-09-21"},
        Marie={"picks": {"2026-09-28": "TOR"}, "out": None})
    assert pk.pool_alive(etat) == ["Alex", "Marie"]
    assert pk.popularity(etat, W1) == {"COL": 1, "TOR": 1}   # Bob exclu


def test_survivants_incluent_moi():
    etat = pool_etat(Alex={"picks": {}, "out": None},
                     Bob={"picks": {}, "out": None})
    assert pk.survivors(etat) == 3                       # eux deux + moi
    assert pk.survivors(pk.mark_out(etat, W1)) == 2      # moi éliminée


def test_une_elimination_dun_tour_precedent_ne_compte_plus():
    """Après un redépart, tout le monde repart en vie."""
    etat = pool_etat(Bob={"picks": {}, "out": "2026-11-09"})
    assert pk.pool_alive(etat) == []
    assert pk.pool_alive(etat, since=R) == ["Bob"]        # R est postérieur


def test_equipes_deja_prises_par_un_joueur():
    etat = pool_etat(Alex={"picks": {"2026-09-28": "COL", "2027-01-18": "TOR"},
                           "out": None})
    assert pk.pool_used(etat, "Alex") == {"COL", "TOR"}
    assert pk.pool_used(etat, "Alex", since=R) == {"TOR"}   # tour en cours
    assert pk.pool_used(etat, "Inconnu") == set()


# ── Fusion des lignes de l'éditeur ────────────────────────────────────────
# L'éditeur ne montre qu'une semaine : le risque est d'effacer les autres.

SEM = "2026-10-05"


def test_fusion_preserve_les_semaines_passees():
    joueurs = {"Alex": {"picks": {"2026-09-28": "COL"}, "out": None}}
    lignes = [{"Joueur": "Alex", "Pick": "TOR", "Statut": pk.AUTO}]
    fusion = pk.merge_pool_week(joueurs, lignes, SEM)
    assert fusion["Alex"]["picks"] == {"2026-09-28": "COL", SEM: "TOR"}


def test_fusion_efface_le_pick_de_la_semaine_si_on_le_vide():
    joueurs = {"Alex": {"picks": {"2026-09-28": "COL", SEM: "TOR"}, "out": None}}
    lignes = [{"Joueur": "Alex", "Pick": "", "Statut": pk.AUTO}]
    fusion = pk.merge_pool_week(joueurs, lignes, SEM)
    assert fusion["Alex"]["picks"] == {"2026-09-28": "COL"}


def test_fusion_ignore_les_lignes_sans_nom():
    lignes = [{"Joueur": "", "Pick": "COL", "Statut": pk.AUTO},
              {"Joueur": None, "Pick": "TOR", "Statut": pk.AUTO},
              {"Joueur": "  Bob  ", "Pick": "MTL", "Statut": pk.AUTO}]
    fusion = pk.merge_pool_week({}, lignes, SEM)
    assert list(fusion) == ["Bob"]
    assert fusion["Bob"]["picks"] == {SEM: "MTL"}


def test_fusion_garde_la_date_delimination_dorigine():
    """Sinon la date serait réécrite à chaque enregistrement du registre."""
    joueurs = {"Bob": {"picks": {}, "out": "2026-09-28"}}
    lignes = [{"Joueur": "Bob", "Pick": "", "Statut": pk.DEHORS}]
    assert pk.merge_pool_week(joueurs, lignes, SEM)["Bob"]["out"] == "2026-09-28"
    # nouvellement éliminé : la semaine courante fait foi
    assert pk.merge_pool_week({}, lignes, SEM)["Bob"]["out"] == SEM
    # remis en vie
    revie = [{"Joueur": "Bob", "Pick": "", "Statut": pk.AUTO}]
    assert pk.merge_pool_week(joueurs, revie, SEM)["Bob"]["out"] is None


def test_fusion_retire_un_joueur_absent_des_lignes():
    """Supprimer une ligne dans l'éditeur retire bien la personne."""
    joueurs = {"Alex": {"picks": {}, "out": None}, "Bob": {"picks": {}, "out": None}}
    fusion = pk.merge_pool_week(joueurs, [{"Joueur": "Alex", "Pick": "",
                                           "Statut": pk.AUTO}], SEM)
    assert list(fusion) == ["Alex"]


def test_mes_equipes_brulees_incluent_la_semaine_en_cours():
    """planning() libère le pick modifiable ; pour comparer les joueurs entre
    eux il faut la même règle pour tous, donc on le compte."""
    picks = pk.add(pk.add([], W1, "VGK"), W2, "COL")
    assert pk.my_used(picks) == {"VGK", "COL"}
    # après un redépart, seules les équipes du tour comptent
    assert pk.my_used(picks, since=W2) == {"COL"}


# ── Élimination automatique ───────────────────────────────────────────────

SEM1, SEM2 = "2026-09-28", "2026-10-05"


def test_le_pick_perdu_elimine():
    j = {"picks": {SEM1: "COL"}}
    assert pk.statut(j, {(SEM1, "COL"): False}) == (True, SEM1, "auto")
    assert pk.statut(j, {(SEM1, "COL"): True}) == (False, None, "auto")


def test_match_pas_encore_joue_ne_signifie_pas_defaite():
    """Un match reporté ou à venir laisse le résultat absent. Conclure
    « éliminé » sur une absence serait le pire des bogues possibles ici."""
    j = {"picks": {SEM1: "COL"}}
    assert pk.statut(j, {}) == (False, None, "auto")
    assert pk.statut(j, {(SEM1, "TOR"): False}) == (False, None, "auto")


def test_elimination_a_la_premiere_defaite_pas_la_derniere():
    j = {"picks": {SEM1: "COL", SEM2: "TOR"}}
    res = {(SEM1, "COL"): False, (SEM2, "TOR"): False}
    assert pk.statut(j, res)[1] == SEM1


def test_le_manuel_lemporte_sur_les_resultats():
    """Cotisation impayée, règle maison : on doit pouvoir sortir quelqu'un qui
    a gagné, et garder quelqu'un qui a perdu."""
    gagnant = {"picks": {SEM1: "COL"}, "force": pk.DEHORS, "out": SEM1}
    assert pk.statut(gagnant, {(SEM1, "COL"): True}) == (True, SEM1, "manuel")

    perdant = {"picks": {SEM1: "COL"}, "force": pk.DEDANS}
    assert pk.statut(perdant, {(SEM1, "COL"): False}) == (False, None, "manuel")


def test_ancien_registre_sans_champ_force_reste_elimine():
    """Compatibilité : les registres écrits avant l'automatique n'ont que la
    date. Ils ne doivent pas se remettre en vie tout seuls."""
    vieux = {"picks": {}, "out": "2026-09-21"}
    assert pk.statut(vieux, {})[0] is True


def test_les_resultats_dun_tour_precedent_ne_comptent_plus():
    j = {"picks": {SEM1: "COL", "2027-01-11": "TOR"}}
    res = {(SEM1, "COL"): False}
    assert pk.statut(j, res, since=R)[0] is False    # défaite d'avant le redépart


def test_les_vivants_tiennent_compte_des_resultats():
    etat = pool_etat(Alex={"picks": {SEM1: "COL"}, "out": None},
                     Bob={"picks": {SEM1: "TOR"}, "out": None})
    res = {(SEM1, "COL"): True, (SEM1, "TOR"): False}
    assert pk.pool_alive(etat, resultats=res) == ["Alex"]
    assert pk.survivors(etat, resultats=res) == 2          # Alex + moi
    assert pk.popularity(etat, W1, resultats=res) == {"COL": 1}
