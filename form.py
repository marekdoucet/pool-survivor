"""
Forme des équipes : séries en cours, progression de la force, victoires
réelles vs attendues.

Une victoire en prolongation ou en tirs de barrage compte comme une victoire
(comme dans le pool) ; une défaite en prolongation compte comme une défaite.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field

import collect_moneypuck as cm
import optimize as op
import schedule as sch


@dataclass
class TeamForm:
    team: str
    results: list = field(default_factory=list)   # [(game_date, adversaire, domicile, gagné)]

    @property
    def wins(self):
        return sum(r[3] for r in self.results)

    @property
    def losses(self):
        return len(self.results) - self.wins

    @property
    def streak(self):
        """+n = n victoires d'affilée, −n = n défaites d'affilée, 0 = aucun match."""
        if not self.results:
            return 0
        last = self.results[-1][3]
        n = 0
        for r in reversed(self.results):
            if r[3] != last:
                break
            n += 1
        return n if last else -n

    @property
    def streak_label(self):
        s = self.streak
        return "—" if s == 0 else (f"V{s}" if s > 0 else f"D{-s}")

    @property
    def last10(self):
        w = sum(r[3] for r in self.results[-10:])
        return f"{w}-{len(self.results[-10:]) - w}"


def team_forms(conn):
    """{équipe: TeamForm} à partir des matchs terminés, en ordre chronologique."""
    sch.init_db(conn)   # ajoute les colonnes de résultats à une vieille base
    forms = {t: TeamForm(t) for t in cm.TEAMS}
    rows = conn.execute(
        "SELECT game_date, away, home, away_score, home_score FROM schedule "
        "WHERE away_score IS NOT NULL ORDER BY start_utc, game_id")
    for g, away, home, a_sc, h_sc in rows:
        forms[away].results.append((g, home, False, a_sc > h_sc))
        forms[home].results.append((g, away, True, h_sc > a_sc))
    return forms


def rating(strength):
    """Forces du modèle → chances de battre une équipe moyenne en terrain neutre."""
    mean = sum(strength.values()) / len(strength)
    return {t: 1 / (1 + math.exp(-(s - mean))) for t, s in strength.items()}


def strength_history(conn):
    """[(collecte, équipe, force)] pour chaque collecte du consensus.

    La première collecte est la projection initiale ; la dernière, où l'équipe
    en est rendue selon les données du moment.
    """
    snaps = [r[0] for r in conn.execute(
        "SELECT DISTINCT snapshot FROM probs WHERE source IN ('consensus', 'moneypuck') "
        "ORDER BY snapshot")]
    out = []
    for snap in snaps:
        known, _ = op.load_known(conn, snapshot=snap)
        if len(known) < 20:   # trop peu de matchs pour estimer 32 forces
            continue
        strength, _h = op.fit_strength(known)
        out += [(snap, t, r) for t, r in sorted(rating(strength).items())]
    return out


def pregame_probs(conn, source):
    """{(game_date, away, home): p_away} de la dernière collecte faite au plus
    tard le jour du match, pour une source donnée."""
    return {(g, a, h): pa for g, a, h, pa in conn.execute("""
        SELECT p.game_date, p.away, p.home, p.p_away FROM probs p
        JOIN (SELECT game_date, away, home, MAX(snapshot) AS snap FROM probs
              WHERE source = ? AND snapshot <= game_date
              GROUP BY game_date, away, home) last
          ON p.game_date = last.game_date AND p.away = last.away
         AND p.home = last.home AND p.snapshot = last.snap
        WHERE p.source = ?""", (source, source))}


def finished_games(conn):
    """[(game_date, away, home, le visiteur a gagné)] en ordre chronologique."""
    sch.init_db(conn)   # ajoute les colonnes de résultats à une vieille base
    return [(g, a, h, a_sc > h_sc) for g, a, h, a_sc, h_sc in conn.execute(
        "SELECT game_date, away, home, away_score, home_score FROM schedule "
        "WHERE away_score IS NOT NULL ORDER BY start_utc, game_id")]


def expected_vs_actual(conn):
    """{équipe: [(game_date, adversaire, proba avant-match, gagné)]}.

    Probabilité avant-match : consensus, sinon MoneyPuck. Les matchs sans
    probabilité collectée sont ignorés.
    """
    pre = {**pregame_probs(conn, "moneypuck"), **pregame_probs(conn, "consensus")}
    out = defaultdict(list)
    for g, a, h, away_won in finished_games(conn):
        if (g, a, h) in pre:
            pa = pre[(g, a, h)]
            out[a].append((g, h, pa, away_won))
            out[h].append((g, a, 1 - pa, not away_won))
    return dict(out)


def source_accuracy(conn, sources):
    """Précision de chaque source sur les matchs joués.

    Retourne [(source, nb matchs, score de Brier, % de favoris gagnants,
    Brier de la source sur les matchs cotés par les casinos, Brier des
    casinos sur ces mêmes matchs)].
    Brier = moyenne de (probabilité − résultat)² : 0,25 = pile ou face,
    plus bas = meilleur.
    """
    games = finished_games(conn)
    pre = {s: pregame_probs(conn, s) for s in sources}
    market = pre.get("market", {})
    out = []
    for s in sources:
        err, hits, err_c, err_m = [], [], [], []
        for g, a, h, away_won in games:
            if (g, a, h) not in pre[s]:
                continue
            pa = pre[s][(g, a, h)]
            err.append((pa - away_won) ** 2)
            if pa != 0.5:
                hits.append((pa > 0.5) == away_won)
            if (g, a, h) in market:
                err_c.append((pa - away_won) ** 2)
                err_m.append((market[(g, a, h)] - away_won) ** 2)
        mean = lambda xs: sum(xs) / len(xs) if xs else None
        out.append((s, len(err), mean(err), mean(hits), mean(err_c), mean(err_m)))
    return out
