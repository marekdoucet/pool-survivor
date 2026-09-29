"""
Mes picks et mes choix de journée, gardés dans picks.json (petit fichier séparé
de survivor.db pour que la collecte automatique ne l'écrase jamais).

    {"picks":  [{"week": "2026-09-28", "team": "VGK", "game_date": "2026-10-03"}],
     "days":    {"2026-12-14": "2026-12-20"},
     "resets":  ["2027-01-11"]}

"days" : journée de pick imposée pour une semaine (lundi → date du samedi ou
du dimanche). Sans entrée, c'est la journée de fin de semaine qui a le plus
de matchs (voir optimize.pick_days).

"resets" : lundis où le pool est reparti à zéro. Quand il ne reste qu'une
personne en vie, le pool recommence à partir de la semaine en cours et toutes
les équipes redeviennent disponibles. Les picks des tours précédents restent
dans le fichier — ils ne contraignent plus le plan, mais l'historique n'est
pas perdu.
"""

import base64
import json
import datetime as dt
from pathlib import Path

import requests

import collect_moneypuck as cm
import optimize as op

PICKS_PATH = Path("picks.json")
GITHUB_API = "https://api.github.com/repos/{repo}/contents/{path}"


HORIZON = 8     # semaines planifiées par défaut


def empty_state():
    return {"picks": [], "days": {}, "resets": [], "out": None,
            "force": None, "pool": {}}


def _normalize(state):
    return {"picks": state.get("picks", []), "days": state.get("days", {}),
            "resets": state.get("resets", []), "out": state.get("out"),
            # mon propre « force » : même mécanique que pour les autres, sinon
            # « je suis encore en vie » ne pourrait rien contre une
            # élimination déduite des résultats.
            "force": state.get("force"), "pool": state.get("pool", {})}


def _dumps(state):
    state = _normalize(state)
    state = {"picks": sorted(state["picks"], key=lambda p: p["week"]),
             "days": dict(sorted(state["days"].items())),
             "resets": sorted(set(state["resets"])), "out": state["out"],
             "force": state["force"],
             "pool": {n: {"picks": dict(sorted(j.get("picks", {}).items())),
                          "out": j.get("out"),
                          "force": (j.get("force")
                                    or (DEHORS if j.get("out") else AUTO))}
                      for n, j in sorted(state["pool"].items())}}
    return json.dumps(state, indent=2, ensure_ascii=False) + "\n"


class GitHubPicks:
    """picks.json lu/écrit directement dans le dépôt GitHub.

    Nécessaire sur Streamlit Community Cloud, dont le disque est effacé à
    chaque redémarrage. Chaque enregistrement devient un commit.
    """

    def __init__(self, repo, token, branch="main", path="picks.json", session=None,
                 author=None):
        self.repo, self.branch, self.path = repo, branch, path
        # {"name", "email"} des commits ; sans ça, GitHub met le courriel du compte,
        # visible de tous dans un dépôt public.
        self.author = author
        self.session = session or requests.Session()
        self.headers = {"Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json"}
        self.sha = None   # version du fichier lue en dernier (évite d'écraser)

    def _url(self):
        return GITHUB_API.format(repo=self.repo, path=self.path)

    def load(self):
        r = self.session.get(self._url(), headers=self.headers,
                             params={"ref": self.branch}, timeout=20)
        if r.status_code == 404:
            self.sha = None
            return empty_state()
        r.raise_for_status()
        body = r.json()
        self.sha = body["sha"]
        return _normalize(json.loads(base64.b64decode(body["content"])))

    def save(self, state, message="Mise à jour des picks"):
        content = _dumps(state)
        payload = {"message": message, "branch": self.branch,
                   "content": base64.b64encode(content.encode("utf-8")).decode()}
        if self.sha:
            payload["sha"] = self.sha
        if self.author:
            payload["author"] = payload["committer"] = self.author
        r = self.session.put(self._url(), headers=self.headers, json=payload, timeout=20)
        if r.status_code in (409, 422):
            raise RuntimeError("picks.json a changé ailleurs entre-temps : recharge la page.")
        r.raise_for_status()
        self.sha = r.json()["content"]["sha"]


def load(path=PICKS_PATH):
    if not Path(path).exists():
        return empty_state()
    return _normalize(json.loads(Path(path).read_text(encoding="utf-8")))


def save(state, path=PICKS_PATH):
    Path(path).write_text(_dumps(state), encoding="utf-8")


# ── Tours ─────────────────────────────────────────────────────────────────

def last_reset(resets, today=None):
    """Lundi de départ du tour en cours, ou None si le pool n'a jamais reparti.

    Un reset daté du futur ne compte pas encore : `today` permet de préparer
    un tour à l'avance sans qu'il libère les équipes tout de suite.
    """
    passes = sorted(r for r in resets
                    if today is None or r <= today.isoformat())
    return dt.date.fromisoformat(passes[-1]) if passes else None


def this_round(picks, since):
    """Les picks du tour en cours. `since=None` : tous."""
    return [p for p in picks if since is None or p["week"] >= since.isoformat()]


# ── Les autres joueurs du pool ────────────────────────────────────────────
# {nom: {"picks": {lundi: équipe}, "out": date d'élimination ou None}}
# Mes picks à moi restent au premier niveau ("picks") : ce sont eux qui
# pilotent le plan, et les mêler aux autres compliquerait tout pour rien.

def pool(state):
    return _normalize(state)["pool"]


def set_pool(state, joueurs):
    """Remplace le registre complet (ce que renvoie un éditeur de tableau)."""
    propre = {}
    for nom, j in joueurs.items():
        nom = str(nom).strip()
        if nom:
            # Ne pas forcer AUTO quand une date « out » existe sans champ
            # « force » : ça effacerait une élimination déjà enregistrée.
            propre[nom] = {"picks": {k: v for k, v in j.get("picks", {}).items() if v},
                           "out": j.get("out"),
                           "force": (j.get("force")
                                     or (DEHORS if j.get("out") else AUTO))}
    return {**_normalize(state), "pool": propre}


# ── Élimination ───────────────────────────────────────────────────────────
# Par défaut elle se déduit des résultats : si le pick a perdu, c'est fini.
# Le manuel l'emporte toujours, pour les cas qu'aucun résultat ne dira — une
# cotisation impayée, une règle maison, une erreur de saisie.

AUTO, DEHORS, DEDANS = "auto", "out", "in"


def auto_out(picks_semaine, resultats, since=None):
    """Première semaine du tour où le pick a perdu, ou None.

    `resultats` : {(lundi ISO, équipe): a gagné}. Une absence veut dire match
    pas encore joué ou reporté — surtout pas une défaite.
    """
    for w in sorted(picks_semaine):
        if since and w < since.isoformat():
            continue
        gagne = resultats.get((w, picks_semaine[w]))
        if gagne is False:
            return w
    return None


def statut(joueur, resultats, since=None):
    """(éliminé ?, semaine, origine) pour un joueur du registre.

    origine : « manuel » si quelqu'un a forcé la valeur, « auto » sinon.
    """
    manuel = joueur.get("out")
    # Une date « out » sans champ « force » vient d'avant l'élimination
    # automatique : on la traite comme une élimination manuelle, sinon les
    # registres déjà remplis se remettraient tous en vie d'un coup.
    force = joueur.get("force") or (DEHORS if manuel else AUTO)
    if force == DEDANS:
        return False, None, "manuel"
    if force == DEHORS:
        # Élimination d'un tour précédent : le redépart l'efface.
        if manuel and since and manuel < since.isoformat():
            return False, None, "manuel"
        return True, manuel, "manuel"
    w = auto_out(joueur.get("picks", {}), resultats, since)
    return w is not None, w, "auto"


def merge_pool_week(joueurs, lignes, semaine):
    """Fusionne les lignes d'un éditeur de tableau dans le registre.

    L'éditeur ne montre qu'une semaine : les picks des semaines passées
    doivent survivre. Une ligne sans nom est ignorée (l'éditeur en crée de
    vides). Décocher « En vie » garde la date d'élimination si elle existait,
    pour ne pas la réécrire à chaque enregistrement.
    """
    nouveau = {}
    for ligne in lignes:
        nom = str(ligne.get("Joueur") or "").strip()
        if not nom:
            continue
        picks = dict(joueurs.get(nom, {}).get("picks", {}))
        team = ligne.get("Pick") or ""
        if team:
            picks[semaine] = team
        else:
            picks.pop(semaine, None)
        # « Statut » remplace l'ancienne case « En vie » : trois états, parce
        # que l'automatique doit pouvoir être contredit dans les deux sens.
        force = ligne.get("Statut") or AUTO
        ancien = joueurs.get(nom, {}).get("out")
        nouveau[nom] = {
            "picks": picks,
            "force": force,
            "out": (ancien or semaine) if force == DEHORS else None,
        }
    return nouveau


def pool_alive(state, since=None, resultats=None):
    """Noms encore en vie, résultats compris. Sans `resultats`, seul le manuel
    compte — c'est le comportement d'avant, gardé pour les appels qui n'ont
    pas la base sous la main."""
    res = resultats or {}
    vivants = []
    for nom, j in pool(state).items():
        elimine, _w, _o = statut(j, res, since)
        if not elimine:
            vivants.append(nom)
    return sorted(vivants)


def survivors(state, since=None, today=None, resultats=None):
    """Combien de personnes restent, moi comprise."""
    moi = 0 if eliminated(state, today) else 1
    return moi + len(pool_alive(state, since, resultats))


def pool_picks(state, week, since=None, resultats=None):
    """{nom: équipe} pour la semaine `week`, chez les joueurs encore en vie."""
    w = week.isoformat()
    vivants = set(pool_alive(state, since, resultats))
    return {n: j["picks"][w] for n, j in pool(state).items()
            if n in vivants and j.get("picks", {}).get(w)}


def popularity(state, week, since=None, resultats=None):
    """{équipe: combien d'adversaires la prennent} pour cette semaine.

    C'est la mesure qui compte dans un survivor : une équipe que tout le monde
    prend ne te démarque pas, même si elle est la plus probable.
    """
    compte = {}
    for team in pool_picks(state, week, since, resultats).values():
        compte[team] = compte.get(team, 0) + 1
    return compte


def alone_odds(mon_equipe, ma_proba, picks_rivaux, probas, n_vivants=None):
    """Probabilité de survivre ET de voir tomber TOUS les adversaires en vie.

    C'est la seule mesure qui dit ce que rapporte le fait de se démarquer :
    survivre en même temps que tout le monde ne fait pas avancer le pool.

    Deux pièges, tous deux gérés ici :
      - les gens sur la MÊME équipe tombent ensemble. On regroupe donc par
        équipe avant de multiplier, sinon le même événement compte plusieurs
        fois et le résultat s'effondre à tort vers zéro.
      - si quelqu'un prend la même équipe que moi, je ne peux jamais me
        retrouver seule : c'est 0, pas « presque 0 ».

    Retourne None si le calcul serait faux plutôt qu'approximatif :
      - un pick adverse sans probabilité connue ;
      - des adversaires en vie dont le pick n'est pas saisi (`n_vivants`).
        Les ignorer donnerait un chiffre bien trop optimiste : c'est la
        probabilité que TOUS tombent, et on en aurait oublié.
    """
    if n_vivants is not None and len(picks_rivaux) < n_vivants:
        return None
    equipes = set(picks_rivaux.values())
    if mon_equipe in equipes:
        return 0.0
    tous_morts = 1.0
    for eq in equipes:
        p = probas.get(eq)
        if p is None:
            return None
        tous_morts *= (1 - p)
    return ma_proba * tous_morts


def pool_used(state, nom, since=None):
    """Équipes déjà utilisées par `nom` dans le tour en cours."""
    j = pool(state).get(nom, {})
    return {t for w, t in j.get("picks", {}).items()
            if t and (since is None or w >= since.isoformat())}


def my_used(picks, since=None):
    """Mes équipes brûlées dans le tour, pick de la semaine en cours compris.

    Différent de ce que renvoie planning() : celui-ci libère le pick de la
    semaine tant qu'il est modifiable, parce qu'il faut pouvoir le replanifier.
    Pour comparer le potentiel restant de chacun, il faut au contraire la même
    règle pour tout le monde — sinon je paraîtrais avantagée d'une équipe.
    """
    return {p["team"] for p in this_round(picks, since)}


def round_week(since, today):
    """Numéro de la semaine en cours dans le tour (1 = celle du redépart)."""
    debut = since or op.week_start(today)
    return max(1, (op.week_start(today) - debut).days // 7 + 1)


def horizon(since, today, base=HORIZON):
    """Nombre de semaines à planifier.

    `base` semaines normalement : survivre plus longtemps serait étonnant, et
    optimiser sur toute la saison fait garder des équipes pour des semaines
    qu'on n'atteindra jamais. Si le tour dépasse quand même cette longueur,
    l'horizon suit — une semaine de plus par semaine — jusqu'au prochain
    redépart.
    """
    return max(base, round_week(since, today))


def eliminated(state, today=None):
    """Date de l'élimination si elle vaut encore pour le tour en cours.

    Un redépart efface l'élimination : on repart tous de zéro.
    """
    out = _normalize(state)["out"]
    if not out:
        return None
    depart = last_reset(_normalize(state)["resets"], today)
    if depart and out < depart.isoformat():
        return None            # élimination d'un tour précédent
    return dt.date.fromisoformat(out)


def mark_out(state, day):
    """Je suis éliminé : plus rien à optimiser jusqu'au prochain redépart."""
    return {**_normalize(state), "out": day.isoformat(), "force": DEHORS}


def back_in(state):
    """Je reste en vie malgré le résultat : règle maison, litige, erreur."""
    return {**_normalize(state), "out": None, "force": DEDANS}


def reset(state, week):
    """Fait repartir le pool à partir du lundi `week`.

    N'efface rien : ajoute seulement une date de départ. Les picks antérieurs
    restent archivés dans le fichier et cessent simplement de bloquer des
    équipes. Annulable avec undo_reset.
    """
    state = _normalize(state)
    # "out"/"force" remis à zéro : un redépart remet tout le monde en vie.
    return {**state, "out": None, "force": None,
            "resets": sorted(set(state["resets"]) | {week.isoformat()})}


def undo_reset(state, week):
    """Retire un départ de tour (erreur de manipulation)."""
    state = _normalize(state)
    return {**state, "resets": [r for r in state["resets"] if r != week.isoformat()]}


def add(picks, week, team, game_date=None, since=None):
    """Une seule équipe par semaine, jamais deux fois la même dans un tour."""
    if team not in cm.TEAMS:
        raise ValueError(f"équipe inconnue : {team}")
    others = [p for p in picks if p["week"] != week.isoformat()]
    if team in {p["team"] for p in this_round(others, since)}:
        raise ValueError(f"{team} a déjà été utilisée dans ce tour")
    return others + [{"week": week.isoformat(), "team": team, "game_date": game_date}]


def remove(picks, week):
    return [p for p in picks if p["week"] != week.isoformat()]


def set_day(days, week, day):
    """Impose la journée de pick d'une semaine ; day=None revient à l'automatique."""
    days = {k: v for k, v in days.items() if k != week.isoformat()}
    if day is not None:
        if op.week_start(day) != week or day.weekday() < 5:
            raise ValueError(f"{day} n'est pas le samedi ou le dimanche de la semaine du {week}")
        days[week.isoformat()] = day.isoformat()
    return days


def pick_deadline(today):
    """Dernier jour pour changer le pick de la semaine : le vendredi (jusqu'à minuit)."""
    return op.week_start(today) + dt.timedelta(days=4)


def planning(picks, today, since=None):
    """Point de départ du plan : (date de départ, équipes utilisées, pick provisoire).

    Le pick de la semaine en cours reste modifiable jusqu'au vendredi minuit
    (pick_deadline) : on planifie alors encore cette semaine, avec cette équipe
    de nouveau disponible. Ensuite il est verrouillé et le plan commence lundi
    prochain.

    `since` : lundi de départ du tour en cours. Seuls les picks de ce tour
    bloquent une équipe ; ceux des tours précédents sont de l'archive.
    """
    monday = op.week_start(today)
    picks = this_round(picks, since)
    current = next((p for p in picks if p["week"] == monday.isoformat()), None)
    used = {p["team"] for p in picks}
    if current is None:
        return today, used, None
    if today <= pick_deadline(today):
        return today, used - {current["team"]}, current
    return monday + dt.timedelta(days=7), used, None
