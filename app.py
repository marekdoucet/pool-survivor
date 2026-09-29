"""
Site Streamlit du pool survivor NHL.

    streamlit run app.py

Données : reconstruites depuis data/ (mis à jour par GitHub Actions).
Picks : picks.json local, ou directement dans le dépôt GitHub si la section
[github] est présente dans .streamlit/secrets.toml (voir DEPLOIEMENT.md).
"""

import os
import sqlite3
import datetime as dt
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import collect_moneypuck as cm
import optimize as op
import picks as pk
import store

PICKS_PATH = Path(os.environ.get("SURVIVOR_PICKS", pk.PICKS_PATH))
CACHE_DB = Path(".cache") / "survivor.db"

SOURCES = {"consensus": "Consensus", "market": "Casinos", "moneypuck": "MoneyPuck",
           "dimers": "Dimers"}
# Palette catégorielle de référence, dans un ordre fixe : la couleur suit la source.
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]

st.set_page_config(page_title="Pool survivor NHL", page_icon="🏒", layout="wide")

PCT = st.column_config.NumberColumn(format="%.1f %%")   # valeurs déjà × 100


# ── Données ────────────────────────────────────────────────────────────────
# La base est reconstruite depuis data/ quand un fichier y change. `version`
# (liste des fichiers) fait partie de la clé de chaque cache ci-dessous.

def data_version():
    files = sorted(store.DATA_DIR.rglob("*.csv*"))
    return tuple((str(f), f.stat().st_size, f.stat().st_mtime) for f in files)


@st.cache_data
def build_db(version):
    CACHE_DB.parent.mkdir(exist_ok=True)
    store.build_db(CACHE_DB)
    return str(CACHE_DB)


if "SURVIVOR_DB" in os.environ:      # base imposée (tests, démo)
    DB_PATH = os.environ["SURVIVOR_DB"]
    VERSION = Path(DB_PATH).stat().st_mtime if Path(DB_PATH).exists() else 0
else:
    VERSION = data_version()
    DB_PATH = build_db(VERSION) if VERSION else cm.DB_PATH


def connect():
    return sqlite3.connect(DB_PATH)


@st.cache_data
def snapshots(version):
    with connect() as c:
        return [r[0] for r in c.execute(
            "SELECT DISTINCT snapshot FROM probs WHERE source='consensus' "
            "ORDER BY snapshot DESC")]


@st.cache_data
def plan_for(version, from_date, used, snapshot=None):
    with connect() as c:
        return op.optimize(c, from_date, set(used), snapshot)


@st.cache_data
def alternatives_for(version, from_date, used):
    with connect() as c:
        return op.alternatives(c, from_date, set(used))


@st.cache_data
def history(version):
    with connect() as c:
        return pd.read_sql("SELECT snapshot, game_date, away, home, p_away, source FROM probs", c)


def match_label(opt):
    return f"{'vs' if opt.home else '@'} {opt.opponent} — {opt.game_date}"


def win_prob(df, team):
    """Probabilité que `team` gagne, depuis des lignes (away, home, p_away)."""
    return df["p_away"].where(df["away"] == team, 1 - df["p_away"])


# ── Barre latérale : mes picks ─────────────────────────────────────────────

def picks_backend():
    try:
        gh = st.secrets.get("github")
    except FileNotFoundError:
        gh = None
    if gh:
        return pk.GitHubPicks(gh["repo"], gh["token"], gh.get("branch", "main"))
    return None


def save_picks(new_picks, message):
    try:
        if remote:
            remote.save(new_picks, message)
        else:
            pk.save(new_picks, PICKS_PATH)
    except Exception as e:   # conflit, jeton expiré, réseau…
        st.sidebar.error(f"Pick non enregistré : {e}")
        return
    st.rerun()


today = dt.datetime.now(cm.TZ).date()
remote = picks_backend()
picks = remote.load() if remote else pk.load(PICKS_PATH)
used = tuple(sorted(p["team"] for p in picks))
from_date = pk.planning_start(picks, today)

with st.sidebar:
    st.header("Mes picks")
    if not picks:
        st.caption("Aucun pick enregistré.")
    if remote:
        st.caption(f"Sauvegardés dans GitHub ({remote.repo})")
    for p in picks:
        col1, col2 = st.columns([4, 1])
        col1.markdown(f"**{p['team']}** — semaine du {p['week']}")
        if col2.button("✕", key=f"del-{p['week']}", help="Retirer ce pick"):
            save_picks(pk.remove(picks, dt.date.fromisoformat(p["week"])),
                       f"Pick retiré : {p['team']} (semaine du {p['week']})")

    st.subheader("Enregistrer un pick")
    mondays = []
    m = op.week_start(dt.date(2026, 9, 29))
    while m <= op.week_start(today):
        mondays.append(m)
        m += dt.timedelta(days=7)
    with st.form("pick"):
        week = st.selectbox("Semaine du", mondays[::-1], format_func=str)
        free = sorted(cm.TEAMS - {p["team"] for p in picks if p["week"] != week.isoformat()})
        team = st.selectbox("Équipe", free)
        if st.form_submit_button("Enregistrer"):
            save_picks(pk.add(picks, week, team), f"Pick : {team} (semaine du {week})")


# ── En-tête ───────────────────────────────────────────────────────────────

st.title("🏒 Pool survivor NHL 2026-27")

v = VERSION
snaps = snapshots(v) if v else []
if not snaps:
    st.error("Aucune donnée. Lance d'abord `python daily.py`.")
    st.stop()

plan, exp_weeks, snapshot = plan_for(v, from_date, used)
hist = history(v)
latest = hist[hist.snapshot == snapshot]
counts = latest.groupby("source").size()
st.caption(
    f"Données du {snapshot} · "
    + " · ".join(f"{SOURCES[s]} : {counts.get(s, 0)} matchs" for s in SOURCES if s != "consensus")
    + f" · {len(used)} équipe(s) utilisée(s)"
)

tab_pick, tab_plan, tab_evol, tab_diff = st.tabs(
    ["Pick de la semaine", "Plan complet", "Évolution des probabilités", "Depuis hier"])


# ── Pick de la semaine ────────────────────────────────────────────────────

with tab_pick:
    current = next((p for p in picks if p["week"] == op.week_start(today).isoformat()), None)
    if current:
        st.success(f"Pick de cette semaine déjà enregistré : **{current['team']}**. "
                   f"Le plan commence la semaine du {from_date}.")

    if not plan or plan[0][1] is None:
        st.warning("Aucun match disponible pour la prochaine semaine.")
    else:
        monday, best = plan[0]
        st.subheader(f"Semaine du {monday}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Pick recommandé", best.team)
        c1.caption(match_label(best))
        c2.metric("Probabilité de victoire", f"{best.p:.1%}")
        c3.metric("Semaines survécues (espérance)", f"{exp_weeks:.2f}")

        game = latest[(latest.game_date == best.game_date)
                      & ((latest.away == best.team) | (latest.home == best.team))]
        by_src = dict(zip(game.source, win_prob(game, best.team)))
        st.markdown("**Détail par source** : " + " · ".join(
            f"{SOURCES[s]} {by_src[s]:.1%}" for s in SOURCES if s in by_src))

        st.markdown("#### Autres choix possibles cette semaine")
        st.caption("Espérance du meilleur plan si je prends cette équipe maintenant. "
                   "« Coût » = semaines d'espérance perdues par rapport au meilleur choix.")
        alts = alternatives_for(v, from_date, used)
        top_e = alts[0][1]
        st.dataframe(pd.DataFrame([{
            "Équipe": o.team, "Match": match_label(o), "Probabilité": 100 * o.p,
            "Espérance": e, "Coût": top_e - e, "Source": o.source,
        } for o, e in alts]), hide_index=True, width="stretch", column_config={
            "Probabilité": PCT,
            "Espérance": st.column_config.NumberColumn(format="%.2f"),
            "Coût": st.column_config.NumberColumn(format="%.2f"),
        })


# ── Plan complet ──────────────────────────────────────────────────────────

with tab_plan:
    rows, alive = [], 1.0
    for monday, o in plan:
        alive *= o.p if o else 0.0
        rows.append({
            "Semaine du": str(monday), "Équipe": o.team if o else "—",
            "Match": match_label(o) if o else "aucune équipe disponible",
            "Probabilité": 100 * o.p if o else 0.0, "Survie cumulée": 100 * alive,
            "Source": o.source if o else "",
        })
    st.caption(f"Espérance : **{exp_weeks:.2f}** semaines survécues sur {len(plan)}. "
               "« estimé » = au-delà des 49 jours collectés, probabilité tirée du modèle "
               "de force des équipes. Le plan est recalculé chaque jour.")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 height=min(38 + 35 * len(rows), 1000), column_config={
                     "Probabilité": PCT,
                     "Survie cumulée": st.column_config.ProgressColumn(
                         format="%.1f %%", min_value=0.0, max_value=100.0),
                 })


# ── Évolution des probabilités ────────────────────────────────────────────

with tab_evol:
    default_team = plan[0][1].team if plan and plan[0][1] else sorted(cm.TEAMS)[0]
    teams_sorted = sorted(cm.TEAMS)
    c1, c2 = st.columns(2)
    team = c1.selectbox("Équipe", teams_sorted, index=teams_sorted.index(default_team))
    team_games = (latest[((latest.away == team) | (latest.home == team))
                         & (latest.source == "consensus")]
                  .sort_values("game_date"))
    if team_games.empty:
        st.info("Aucun match à venir pour cette équipe dans les données collectées.")
    else:
        labels = {
            f"{r.game_date} — " + (f"@ {r.home}" if r.away == team else f"vs {r.away}"):
            (r.game_date, r.away, r.home) for r in team_games.itertuples()
        }
        default_game = next((i for i, (g, *_x) in enumerate(labels.values())
                             if plan and plan[0][1] and g == plan[0][1].game_date
                             and team == plan[0][1].team), 0)
        choice = c2.selectbox("Match", list(labels), index=default_game)
        g, a, h = labels[choice]
        series = hist[(hist.game_date == g) & (hist.away == a) & (hist.home == h)
                      & hist.source.isin(SOURCES)].copy()
        series["Probabilité"] = win_prob(series, team)
        series["Source"] = series.source.map(SOURCES)
        series["Collecte"] = pd.to_datetime(series.snapshot)
        present = [s for s in SOURCES if s in set(series.source)]

        base = alt.Chart(series).encode(
            x=alt.X("Collecte:T", title="Date de collecte",
                    axis=alt.Axis(format="%d/%m", tickCount={"interval": "day", "step": 1})),
            y=alt.Y("Probabilité:Q", title=f"Probabilité que {team} gagne",
                    axis=alt.Axis(format="%"), scale=alt.Scale(zero=False)),
            color=alt.Color("Source:N", title="Source",
                            scale=alt.Scale(domain=[SOURCES[s] for s in present],
                                            range=[COLORS[list(SOURCES).index(s)]
                                                   for s in present])),
            tooltip=[alt.Tooltip("Source:N"), alt.Tooltip("Collecte:T", format="%Y-%m-%d"),
                     alt.Tooltip("Probabilité:Q", format=".1%")],
        )
        chart = (base.mark_line(strokeWidth=2) + base.mark_point(size=64, filled=True)
                 ).properties(height=360)
        st.altair_chart(chart, width="stretch")
        if series.snapshot.nunique() < 2:
            st.caption("Une seule collecte pour l'instant : les courbes apparaîtront "
                       "au fil des collectes quotidiennes.")
        with st.expander("Voir les données"):
            st.dataframe(series[["snapshot", "Source", "Probabilité"]]
                         .assign(Probabilité=lambda d: 100 * d.Probabilité)
                         .rename(columns={"snapshot": "Collecte"}),
                         hide_index=True, column_config={"Probabilité": PCT})


# ── Depuis hier ───────────────────────────────────────────────────────────

with tab_diff:
    if len(snaps) < 2:
        st.info("Il faut au moins deux collectes pour comparer. Reviens après la "
                "prochaine collecte quotidienne.")
    else:
        prev = snaps[1]
        prev_plan, prev_exp, _ = plan_for(v, from_date, used, prev)
        st.caption(f"Comparaison entre la collecte du {prev} et celle du {snapshot}.")

        c1, c2 = st.columns(2)
        now_pick = plan[0][1].team if plan and plan[0][1] else "—"
        old_pick = prev_plan[0][1].team if prev_plan and prev_plan[0][1] else "—"
        c1.metric("Pick de la semaine", now_pick)
        c1.caption("Inchangé" if now_pick == old_pick else f"Avant : {old_pick}")
        c2.metric("Espérance", f"{exp_weeks:.2f}", f"{exp_weeks - prev_exp:+.2f}")

        old = {m: o.team for m, o in prev_plan if o}
        changes = [{"Semaine du": str(m), "Avant": old.get(m, "—"), "Maintenant": o.team}
                   for m, o in plan if o and old.get(m) != o.team]
        st.markdown("#### Changements dans le plan")
        if changes:
            st.dataframe(pd.DataFrame(changes), hide_index=True)
        else:
            st.caption("Aucun changement.")

        st.markdown("#### Plus gros mouvements de probabilité (consensus)")
        cons = hist[hist.source == "consensus"]
        a = cons[cons.snapshot == snapshot].set_index(["game_date", "away", "home"]).p_away
        b = cons[cons.snapshot == prev].set_index(["game_date", "away", "home"]).p_away
        moves = (a - b).dropna()
        moves = moves.reindex(moves.abs().sort_values(ascending=False).index).head(10)
        if moves.empty:
            st.caption("Aucun match commun entre les deux collectes.")
        else:
            rows = []
            for (g, away, home), d in moves.items():
                team, sign = (away, 1) if d > 0 else (home, -1)
                before = b[(g, away, home)] if sign > 0 else 1 - b[(g, away, home)]
                rows.append({"Match": f"{away} @ {home}", "Date": g, "En hausse": team,
                             "Avant": 100 * before, "Maintenant": 100 * (before + abs(d)),
                             "Hausse (points)": 100 * abs(d)})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
                "Avant": PCT, "Maintenant": PCT,
                "Hausse (points)": st.column_config.NumberColumn(format="+%.1f"),
            })
