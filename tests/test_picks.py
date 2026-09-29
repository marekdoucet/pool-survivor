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
    assert pk.load(tmp_path / "absent.json") == {"picks": [], "days": {}}


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
    assert store.load() == {"picks": [], "days": {}}
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
