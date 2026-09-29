"""
Étape 3 du projet Pool Survivor NHL
Optimise le plan de picks sur les semaines restantes.

Règles : chaque semaine (lundi → dimanche), je choisis UN match d'une équipe ;
elle doit le gagner (prolongation et tirs de barrage comptent), sinon je suis
éliminé. Une équipe ne sert qu'une fois.

Objectif : maximiser l'espérance du nombre de semaines survécues
    E = p1 + p1·p2 + p1·p2·p3 + …
ce qui tient compte de l'élimination : une semaine proche pèse plus lourd.

Probabilités :
  - matchs dans l'horizon collecté (49 jours) : dernier consensus (étape 2)
  - au-delà : modèle de force des équipes ajusté sur ce consensus
    (logit p_domicile = avantage_domicile + force_domicile − force_visiteur)
    appliqué au calendrier officiel de la LNH (schedule.py)

Utilisation :
    python optimize.py
    python optimize.py --used MTL,TOR --from 2026-10-12
"""

import argparse
import math
import sqlite3
import datetime as dt
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

import collect_moneypuck as cm
import schedule as sch

RIDGE = 1.0   # régularisation des forces (évite les valeurs extrêmes)


@dataclass
class Option:
    """Le meilleur match d'une équipe pour une semaine donnée."""
    team: str
    game_date: str
    opponent: str
    home: bool
    p: float
    source: str   # « consensus » ou « estimé »


# ── Données ─────────────────────────────────────────────────────────────────

def load_known(conn, source="consensus", snapshot=None):
    """Instantané (le plus récent par défaut) : {(game_date, away, home): p_away}.

    Repli sur MoneyPuck si la source demandée est absente.
    """
    for src in (source, "moneypuck"):
        snap = snapshot or conn.execute(
            "SELECT MAX(snapshot) FROM probs WHERE source=?", (src,)).fetchone()[0]
        rows = conn.execute(
            "SELECT game_date, away, home, p_away FROM probs WHERE snapshot=? AND source=?",
            (snap, src)).fetchall()
        if rows:
            return {(g, a, h): p for g, a, h, p in rows}, snap
    return {}, None


def fit_strength(known, ridge=RIDGE):
    """Ajuste logit(p_home) = h + s_home − s_away par moindres carrés régularisés."""
    teams = sorted(cm.TEAMS)
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    X, y = [], []
    for (_g, away, home), p_away in known.items():
        p_home = min(max(1 - p_away, 0.01), 0.99)
        row = np.zeros(n + 1)
        row[idx[home]], row[idx[away]], row[n] = 1, -1, 1
        X.append(row)
        y.append(math.log(p_home / (1 - p_home)))
    X, y = np.array(X), np.array(y)
    reg = np.eye(n + 1) * ridge
    reg[n, n] = 0   # l'avantage domicile n'est pas régularisé
    coef = np.linalg.solve(X.T @ X + reg, X.T @ y)
    strength = dict(zip(teams, coef[:n]))
    return strength, coef[n]


def predict_p_away(strength, h, away, home):
    return 1 / (1 + math.exp(h + strength[home] - strength[away]))


def week_start(day):
    return day - dt.timedelta(days=day.weekday())   # lundi


def build_options(conn, from_date, used=(), snapshot=None):
    """{lundi: {équipe: Option}} pour toutes les semaines restantes.

    Pour la semaine en cours, seuls les matchs à partir de from_date comptent.
    """
    known, snapshot = load_known(conn, snapshot=snapshot)
    strength, h = fit_strength(known)
    games = conn.execute(
        "SELECT game_date, away, home FROM schedule WHERE game_date >= ? ORDER BY game_date",
        (from_date.isoformat(),)).fetchall()

    weeks = {}
    for g, away, home in games:
        if (g, away, home) in known:
            p_away, src = known[(g, away, home)], "consensus"
        else:
            p_away, src = predict_p_away(strength, h, away, home), "estimé"
        monday = week_start(dt.date.fromisoformat(g))
        for team, opp, is_home, p in ((away, home, False, p_away), (home, away, True, 1 - p_away)):
            if team in used:
                continue
            best = weeks.setdefault(monday, {}).get(team)
            if best is None or p > best.p:
                weeks[monday][team] = Option(team, g, opp, is_home, p, src)
    return dict(sorted(weeks.items())), snapshot, strength, h


# ── Optimisation ────────────────────────────────────────────────────────────

def expected_weeks(ps):
    total, alive = 0.0, 1.0
    for p in ps:
        alive *= p
        total += alive
    return total


def _hungarian(logP, weights):
    """Maximise Σ_w weights[w] · log P[w, équipe] (affectation exacte)."""
    assign = np.full(logP.shape[0], -1)
    rows, cols = linear_sum_assignment(-(weights[:, None] * logP))
    assign[rows] = cols
    return assign


def _linearized(P, logP, assign, max_iter=30):
    """Relinéarise E autour du plan courant et réaffecte, jusqu'à stabilité.

    ∂E/∂log p_k = Σ_{m≥k} S_m (S_m = survie jusqu'à la semaine m) : une semaine
    compte d'autant plus qu'il reste de la survie à protéger après elle.
    """
    for _ in range(max_iter):
        ps = np.array([P[w, t] if t >= 0 else 0.0 for w, t in enumerate(assign)])
        surv = np.cumprod(ps)
        weights = np.cumsum(surv[::-1])[::-1] + 1e-12
        new = _hungarian(logP, weights)
        if np.array_equal(new, assign):
            break
        assign = new
    return assign


def solve(P, restarts=20, seed=0):
    """P[w, t] = probabilité (0 si l'équipe t ne joue pas la semaine w).

    Retourne (assign[w] = indice d'équipe ou -1, espérance). Plusieurs départs
    (affectation hongroise sur Σ log p, pondérée ou non), chacun amélioré par
    relinéarisation puis recherche locale sur l'objectif réel E = Σ_k Π_{j≤k} p_j.
    """
    W = P.shape[0]
    logP = np.log(np.clip(P, 1e-9, 1))
    rng = np.random.default_rng(seed)
    starts = [np.ones(W), np.linspace(2, 1, W)]
    starts += [np.sort(rng.uniform(0.2, 2, W))[::-1] for _ in range(restarts)]

    best_assign, best = None, -1.0
    for weights in starts:
        assign = _linearized(P, logP, _hungarian(logP, weights))
        assign, score = _local_search(P, assign)
        if score > best + 1e-12:
            best_assign, best = assign, score
    return best_assign, best


def _local_search(P, assign):
    """Échanges entre semaines et remplacements par une équipe inutilisée."""
    W, T = P.shape
    assign = assign.copy()

    def score(a):
        return expected_weeks([P[w, t] if t >= 0 else 0.0 for w, t in enumerate(a)])

    best = score(assign)
    improved = True
    while improved:
        improved = False
        # Échange des équipes de deux semaines
        for i in range(W):
            for j in range(i + 1, W):
                assign[i], assign[j] = assign[j], assign[i]
                s = score(assign)
                if s > best + 1e-12:
                    best, improved = s, True
                else:
                    assign[i], assign[j] = assign[j], assign[i]
        # Remplacement par une équipe pas encore dans le plan
        for i in range(W):
            for t in sorted(set(range(T)) - set(assign.tolist())):
                old, assign[i] = assign[i], t
                s = score(assign)
                if s > best + 1e-12:
                    best, improved = s, True
                else:
                    assign[i] = old
    return assign, best


def _matrix(weeks, used):
    mondays = list(weeks)
    teams = sorted(cm.TEAMS - set(used))
    P = np.array([[weeks[m][t].p if t in weeks[m] else 0.0 for t in teams] for m in mondays])
    return mondays, teams, P


def optimize(conn, from_date, used=(), snapshot=None):
    weeks, snapshot, _strength, _h = build_options(conn, from_date, used, snapshot)
    mondays, teams, P = _matrix(weeks, used)
    assign, exp_weeks = solve(P)
    plan = [(m, weeks[m][teams[t]] if t >= 0 else None) for m, t in zip(mondays, assign)]
    return plan, exp_weeks, snapshot


def alternatives(conn, from_date, used=(), top=8, snapshot=None):
    """Chaque choix possible pour la première semaine, avec l'espérance du
    meilleur plan qui le suit : E = p + p · E(reste sans cette équipe).

    Retourne [(Option, espérance)] trié du meilleur au moins bon, limité aux
    `top` meilleures probabilités de la semaine.
    """
    weeks, *_ = build_options(conn, from_date, used, snapshot)
    if not weeks:
        return []
    mondays, teams, P = _matrix(weeks, used)
    first = weeks[mondays[0]]
    candidates = sorted(first.values(), key=lambda o: -o.p)[:top]
    result = []
    for opt in candidates:
        rest = np.delete(P[1:], teams.index(opt.team), axis=1)
        e_rest = solve(rest)[1] if rest.shape[0] else 0.0
        result.append((opt, opt.p * (1 + e_rest)))
    return sorted(result, key=lambda r: -r[1])


# ── Interface ligne de commande ─────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Plan optimal du pool survivor")
    ap.add_argument("--used", default="", help="équipes déjà utilisées, ex. MTL,TOR")
    ap.add_argument("--from", dest="from_date", default=None,
                    help="date de départ AAAA-MM-JJ (défaut : aujourd'hui)")
    args = ap.parse_args()

    used = {t.strip().upper() for t in args.used.split(",") if t.strip()}
    unknown = used - cm.TEAMS
    if unknown:
        ap.error(f"équipes inconnues : {', '.join(sorted(unknown))}")
    from_date = (dt.date.fromisoformat(args.from_date) if args.from_date
                 else dt.datetime.now(cm.TZ).date())

    conn = sqlite3.connect(cm.DB_PATH)
    plan, exp_weeks, snapshot = optimize(conn, from_date, used)
    conn.close()

    print(f"Données du {snapshot} — {len(plan)} semaines restantes, "
          f"{len(used)} équipe(s) déjà utilisée(s)\n")
    print(f"{'Semaine':<10} {'Équipe':<6} {'Match':<22} {'Proba':>6} {'Survie':>7}  Source")
    alive = 1.0
    for monday, opt in plan:
        if opt is None:
            print(f"{monday}  (aucune équipe disponible)")
            alive = 0.0
            continue
        alive *= opt.p
        match = f"{opt.game_date} {'vs' if opt.home else '@'} {opt.opponent}"
        print(f"{monday}  {opt.team:<6} {match:<22} {opt.p:6.1%} {alive:7.1%}  {opt.source}")
    print(f"\nEspérance : {exp_weeks:.2f} semaines survécues sur {len(plan)}")


if __name__ == "__main__":
    main()
