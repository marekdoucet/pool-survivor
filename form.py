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


def expected_vs_actual(conn):
    """{équipe: [(game_date, adversaire, proba avant-match, gagné)]}.

    La probabilité avant-match est celle de la dernière collecte faite au plus
    tard le jour du match (consensus, sinon MoneyPuck). Les matchs sans
    probabilité collectée sont ignorés.
    """
    sch.init_db(conn)
    pre = {}
    for src in ("moneypuck", "consensus"):   # le consensus écrase MoneyPuck
        for g, a, h, pa in conn.execute(f"""
                SELECT p.game_date, p.away, p.home, p.p_away FROM probs p
                JOIN (SELECT game_date, away, home, MAX(snapshot) AS snap FROM probs
                      WHERE source = '{src}' AND snapshot <= game_date
                      GROUP BY game_date, away, home) last
                  ON p.game_date = last.game_date AND p.away = last.away
                 AND p.home = last.home AND p.snapshot = last.snap
                WHERE p.source = '{src}'"""):
            pre[(g, a, h)] = pa

    out = defaultdict(list)
    for g, a, h, a_sc, h_sc in conn.execute(
            "SELECT game_date, away, home, away_score, home_score FROM schedule "
            "WHERE away_score IS NOT NULL ORDER BY start_utc, game_id"):
        if (g, a, h) not in pre:
            continue
        pa = pre[(g, a, h)]
        out[a].append((g, h, pa, a_sc > h_sc))
        out[h].append((g, a, 1 - pa, h_sc > a_sc))
    return dict(out)
