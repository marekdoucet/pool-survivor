"""
Mes picks, gardés dans picks.json (petit fichier séparé de survivor.db pour
que la collecte automatique ne l'écrase jamais).

    {"picks": [{"week": "2026-09-28", "team": "VGK", "game_date": "2026-10-04"}]}
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


class GitHubPicks:
    """picks.json lu/écrit directement dans le dépôt GitHub.

    Nécessaire sur Streamlit Community Cloud, dont le disque est effacé à
    chaque redémarrage. Chaque enregistrement devient un commit.
    """

    def __init__(self, repo, token, branch="main", path="picks.json", session=None):
        self.repo, self.branch, self.path = repo, branch, path
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
            return []
        r.raise_for_status()
        body = r.json()
        self.sha = body["sha"]
        return json.loads(base64.b64decode(body["content"]))["picks"]

    def save(self, picks, message="Mise à jour des picks"):
        picks = sorted(picks, key=lambda p: p["week"])
        content = json.dumps({"picks": picks}, indent=2, ensure_ascii=False) + "\n"
        payload = {"message": message, "branch": self.branch,
                   "content": base64.b64encode(content.encode("utf-8")).decode()}
        if self.sha:
            payload["sha"] = self.sha
        r = self.session.put(self._url(), headers=self.headers, json=payload, timeout=20)
        if r.status_code in (409, 422):
            raise RuntimeError("picks.json a changé ailleurs entre-temps : recharge la page.")
        r.raise_for_status()
        self.sha = r.json()["content"]["sha"]


def load(path=PICKS_PATH):
    if not Path(path).exists():
        return []
    return json.loads(Path(path).read_text(encoding="utf-8"))["picks"]


def save(picks, path=PICKS_PATH):
    picks = sorted(picks, key=lambda p: p["week"])
    Path(path).write_text(json.dumps({"picks": picks}, indent=2, ensure_ascii=False),
                          encoding="utf-8")


def add(picks, week, team, game_date=None):
    """Une seule équipe par semaine, jamais deux fois la même équipe."""
    if team not in cm.TEAMS:
        raise ValueError(f"équipe inconnue : {team}")
    others = [p for p in picks if p["week"] != week.isoformat()]
    if team in {p["team"] for p in others}:
        raise ValueError(f"{team} a déjà été utilisée")
    return others + [{"week": week.isoformat(), "team": team, "game_date": game_date}]


def remove(picks, week):
    return [p for p in picks if p["week"] != week.isoformat()]


def planning_start(picks, today):
    """Si la semaine en cours a déjà un pick, on planifie à partir de lundi prochain."""
    monday = op.week_start(today)
    if any(p["week"] == monday.isoformat() for p in picks):
        return monday + dt.timedelta(days=7)
    return today
