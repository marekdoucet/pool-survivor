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


def empty_state():
    return {"picks": [], "days": {}, "resets": []}


def _normalize(state):
    return {"picks": state.get("picks", []), "days": state.get("days", {}),
            "resets": state.get("resets", [])}


def _dumps(state):
    state = _normalize(state)
    state = {"picks": sorted(state["picks"], key=lambda p: p["week"]),
             "days": dict(sorted(state["days"].items())),
             "resets": sorted(set(state["resets"]))}
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


def reset(state, week):
    """Fait repartir le pool à partir du lundi `week`.

    N'efface rien : ajoute seulement une date de départ. Les picks antérieurs
    restent archivés dans le fichier et cessent simplement de bloquer des
    équipes. Annulable avec undo_reset.
    """
    state = _normalize(state)
    return {**state, "resets": sorted(set(state["resets"]) | {week.isoformat()})}


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
