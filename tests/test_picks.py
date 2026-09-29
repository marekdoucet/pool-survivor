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
    pk.save(pk.add(picks, W2, "COL"), path)
    assert [p["team"] for p in pk.load(path)] == ["EDM", "COL"]
    assert pk.remove(pk.load(path), W1)[0]["team"] == "COL"


def test_load_missing_file(tmp_path):
    assert pk.load(tmp_path / "absent.json") == []


def test_planning_start():
    wed = dt.date(2026, 9, 30)
    assert pk.planning_start([], wed) == wed
    assert pk.planning_start(pk.add([], W1, "VGK"), wed) == W2


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
    store = pk.GitHubPicks("moi/pool", "jeton", session=gh)
    assert store.load() == []
    store.save(pk.add([], W1, "VGK"), "Pick VGK")
    assert gh.puts[0]["message"] == "Pick VGK" and gh.puts[0]["branch"] == "main"

    other = pk.GitHubPicks("moi/pool", "jeton", session=gh)
    assert [p["team"] for p in other.load()] == ["VGK"]
    other.save(pk.add(other.load(), W2, "CAR"))

    # `store` a une version périmée : on refuse d'écraser le pick de `other`
    with pytest.raises(RuntimeError, match="recharge"):
        store.save(pk.add([], W2, "COL"))
    assert [p["team"] for p in pk.GitHubPicks("moi/pool", "j", session=gh).load()] == ["VGK", "CAR"]
