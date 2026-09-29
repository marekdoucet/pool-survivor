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
import collect_odds as co
import form
import optimize as op
import picks as pk
import store
import ui

PICKS_PATH = Path(os.environ.get("SURVIVOR_PICKS", pk.PICKS_PATH))
CACHE_DB = Path(".cache") / "survivor.db"

SOURCES = {"consensus": "Consensus", "market": "Casinos", "moneypuck": "MoneyPuck",
           "dimers": "Dimers", "puckcast": "Puckcast"}
# Palette catégorielle de référence (8 couleurs), dans un ordre fixe :
# la couleur suit la source, ou l'équipe (au plus 8 équipes comparées à la fois).
# Pas calibrés pour fond sombre — mêmes teintes et même ordre que la version
# fond clair, mais re-choisis pour la surface sombre, pas éclaircis au hasard.
TEAM_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500",
               "#d55181", "#008300", "#9085e9", "#e66767"]
COLORS = TEAM_COLORS[:len(SOURCES)]

st.set_page_config(page_title="Pool survivor NHL", page_icon="🏒", layout="wide")
ui.inject_css()

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
def plan_for(version, from_date, used, overrides, snapshot=None):
    with connect() as c:
        return op.optimize(c, from_date, set(used), snapshot, dict(overrides))


@st.cache_data
def alternatives_for(version, from_date, used, overrides):
    with connect() as c:
        return op.alternatives(c, from_date, set(used), overrides=dict(overrides))


@st.cache_data
def options_for(version, from_date, used, overrides):
    """{lundi: {équipe: meilleur match de la journée de pick}} pour les semaines restantes."""
    with connect() as c:
        return op.build_options(c, from_date, set(used), overrides=dict(overrides))[0]


@st.cache_data
def pick_days_for(version, overrides):
    with connect() as c:
        return op.pick_days(c, dict(overrides))


@st.cache_data
def history(version):
    with connect() as c:
        return pd.read_sql("SELECT snapshot, game_date, away, home, p_away, source FROM probs", c)


@st.cache_data
def forms(version):
    with connect() as c:
        return form.team_forms(c)


@st.cache_data
def strength_hist(version):
    with connect() as c:
        return pd.DataFrame(form.strength_history(c), columns=["snapshot", "team", "rating"])


@st.cache_data
def accuracy(version):
    with connect() as c:
        return form.source_accuracy(c, list(SOURCES))


@st.cache_data
def expected(version):
    with connect() as c:
        return form.expected_vs_actual(c)


MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.",
        "oct.", "nov.", "déc."]


JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def fr_date(d):
    return f"{d.day} {MOIS[d.month - 1]}"


def fr_weekend(monday):
    """Lundi → '19-20 déc.' (ou '31 oct.-1 nov.')."""
    sat, sun = monday + dt.timedelta(days=5), monday + dt.timedelta(days=6)
    if sat.month == sun.month:
        return f"{sat.day}-{sun.day} {MOIS[sun.month - 1]}"
    return f"{fr_date(sat)}-{fr_date(sun)}"


def fr_day(iso):
    """'2026-10-03' → 'samedi 3 oct.'"""
    d = dt.date.fromisoformat(iso)
    return f"{JOURS[d.weekday()]} {fr_date(d)}"


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
        author = {"name": gh.get("author_name", "marekdoucet"),
                  "email": gh.get("author_email",
                                  "183759644+marekdoucet@users.noreply.github.com")}
        return pk.GitHubPicks(gh["repo"], gh["token"], gh.get("branch", "main"),
                              author=author)
    return None


def save_state(new_state, message):
    try:
        if remote:
            remote.save(new_state, message)
        else:
            pk.save(new_state, PICKS_PATH)
    except Exception as e:   # conflit, jeton expiré, réseau…
        st.sidebar.error(f"Non enregistré : {e}")
        return
    st.rerun()


def save_picks(new_picks, message):
    save_state({**state, "picks": new_picks}, message)


today = dt.datetime.now(cm.TZ).date()
remote = picks_backend()
state = remote.load() if remote else pk.load(PICKS_PATH)
picks, day_overrides = state["picks"], state["days"]
overrides = tuple(sorted(day_overrides.items()))   # clé de cache
all_days = pick_days_for(VERSION, overrides) if VERSION else {}
this_monday = op.week_start(today)
from_date, used_set, provisional = pk.planning(picks, today)
used = tuple(sorted(used_set))

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

    st.subheader("Journée de pick")
    ties = [m for m, d in all_days.items() if d.how == "égalité" and m >= op.week_start(today)]
    st.caption("Par défaut : la journée de fin de semaine qui a le plus de matchs. "
               "Change-la ici si l'organisateur en décide autrement.")
    if ties:
        st.warning("Égalité samedi/dimanche à confirmer : "
                   + ", ".join(f"fin de semaine du {fr_weekend(m)}" for m in ties)
                   + ". En attendant, les deux jours sont permis.")
    future = [m for m in all_days if m >= op.week_start(today)]
    if future:
        with st.form("jour"):
            wk = st.selectbox(
                "Semaine du", future,
                format_func=lambda m: f"{fr_date(m)} — " + (
                    " ou ".join(fr_day(d) for d in all_days[m].days) or "aucun match")
                    + (" (choisi)" if all_days[m].how == "choisi" else ""))
            pd_ = all_days[wk]
            sat = (wk + dt.timedelta(days=5)).isoformat()
            sun = (wk + dt.timedelta(days=6)).isoformat()
            choices = {"Automatique (le plus de matchs)": None,
                       f"{fr_day(sat)} ({pd_.sat_games} matchs)": sat,
                       f"{fr_day(sun)} ({pd_.sun_games} matchs)": sun}
            choice = st.radio("Journée", list(choices))
            if st.form_submit_button("Enregistrer la journée"):
                day = choices[choice]
                new_days = pk.set_day(day_overrides, wk,
                                      dt.date.fromisoformat(day) if day else None)
                save_state({**state, "days": new_days},
                           f"Journée de pick, semaine du {wk} : "
                           + (fr_day(day) if day else "automatique"))
        chosen = {m: d for m, d in all_days.items() if d.how == "choisi" and m in future}
        if chosen:
            st.caption("Journées choisies : " + ", ".join(
                f"sem. du {fr_date(m)} → {fr_day(d.days[0])}" for m, d in chosen.items()))


# ── En-tête ───────────────────────────────────────────────────────────────

st.title("🏒 Pool survivor NHL 2026-27")

v = VERSION
snaps = snapshots(v) if v else []
if not snaps:
    st.error("Aucune donnée. Lance d'abord `python daily.py`.")
    st.stop()

plan, exp_weeks, snapshot = plan_for(v, from_date, used, overrides)
hist = history(v)
latest = hist[hist.snapshot == snapshot]
counts = latest.groupby("source").size()
st.caption(
    f"Données du {snapshot} · "
    + " · ".join(f"{SOURCES[s]} : {counts.get(s, 0)} matchs" for s in SOURCES if s != "consensus")
    + f" · {len(used)} équipe(s) utilisée(s)"
)

tab_pick, tab_plan, tab_map, tab_evol, tab_form, tab_acc, tab_diff = st.tabs(
    ["Pick de la semaine", "Plan complet", "Carte des matchups",
     "Évolution des probabilités", "Équipes en forme", "Précision des sources",
     "Depuis hier"])
options = options_for(v, from_date, used, overrides)
planned = {o.team: m for m, o in plan if o}   # équipe → semaine où le plan l'utilise


def best_later(team, after):
    """Meilleur match de l'équipe après la semaine `after` : (lundi, Option) ou None."""
    later = [(m, w[team]) for m, w in options.items() if m > after and team in w]
    return max(later, key=lambda x: x[1].p, default=None)
team_form = forms(v)


# ── Pick de la semaine ────────────────────────────────────────────────────

with tab_pick:
    current = next((p for p in picks if p["week"] == this_monday.isoformat()), None)
    if current and not provisional:
        st.success(f"Pick de cette semaine verrouillé : **{current['team']}**. "
                   f"Le plan commence la semaine du {from_date}.")
    if provisional and this_monday in options:
        week_opts = sorted(options[this_monday].values(), key=lambda o: -o.p)
        mine = options[this_monday].get(provisional["team"])
        st.success(f"Ton pick de la semaine : **{provisional['team']}**"
                   + (f" ({match_label(mine)}, {mine.p:.1%})" if mine else "")
                   + f". Tu peux le changer jusqu'à {fr_day(pk.pick_deadline(today).isoformat())} minuit")
        with st.form("changer"):
            change_labels = [f"{o.team} {match_label(o)} — {o.p:.1%}" for o in week_opts]
            teams_ = [o.team for o in week_opts]
            idx = teams_.index(provisional["team"]) if provisional["team"] in teams_ else 0
            new = st.selectbox("Changer mon pick pour", range(len(week_opts)),
                               index=idx, format_func=lambda i, lb=change_labels: lb[i])
            if st.form_submit_button("Remplacer mon pick"):
                o = week_opts[new]
                save_picks(pk.add(picks, this_monday, o.team, o.game_date),
                           f"Pick modifié : {o.team} (semaine du {this_monday})")

    if not plan or plan[0][1] is None:
        st.warning("Aucun match disponible pour la prochaine semaine.")
    else:
        monday, best = plan[0]
        game = latest[(latest.game_date == best.game_date)
                      & ((latest.away == best.team) | (latest.home == best.team))]
        by_src = dict(zip(game.source, win_prob(game, best.team)))
        ui.hero(best.team,
                f"Pick recommandé · fin de semaine du {fr_weekend(monday)}",
                f"contre {ui.name(best.opponent)} · "
                + ("à domicile" if best.home else "à l'étranger")
                + f" · {fr_day(best.game_date)}",
                best.p)
        # La couleur suit la source, jamais son rang : on indexe dans SOURCES,
        # pas dans la liste filtrée, sinon une source absente repeint les autres.
        shown = [s for s in SOURCES if s in by_src]
        ui.chips([(SOURCES[s], by_src[s]) for s in shown],
                 [COLORS[list(SOURCES).index(s)] for s in shown])
        pd_week = all_days.get(monday)
        if pd_week:
            txt = " ou ".join(fr_day(d) for d in pd_week.days)
            is_sat = pd_week.days[0] == (monday + dt.timedelta(days=5)).isoformat()
            n_games = pd_week.sat_games if is_sat else pd_week.sun_games
            if pd_week.how == "égalité":
                st.warning(f"Journée de pick : **{txt}** — égalité "
                           f"({pd_week.sat_games} matchs chacun), à confirmer dans la "
                           f"barre latérale.")
            else:
                st.info(f"Journée de pick : **{txt}** ({n_games} matchs)"
                        + (" — choisie par toi" if pd_week.how == "choisi" else ""))
        st.caption(f"Espérance sur tout le plan : **{exp_weeks:.2f}** semaines "
                   f"survécues sur {len(plan)}.")

        st.markdown("#### Pourquoi pas une autre équipe ?")
        st.caption("Le plan est optimisé sur toute la saison d'un coup : une équipe "
                   "forte cette semaine peut être gardée pour une semaine où son "
                   "match est encore meilleur, ou où aucune autre équipe ne fait mieux.")
        alts = alternatives_for(v, from_date, used, overrides)
        top_e = alts[0][1]
        reasons = []
        for o, _e in alts:
            if o.team == best.team:
                continue
            pm = planned.get(o.team)
            if pm and pm != monday:
                po = options[pm][o.team]
                later_match = (f"{po.p:.0%} {'vs' if po.home else '@'} {po.opponent} "
                               f"la semaine du {fr_date(pm)}")
                if po.p >= o.p:
                    reasons.append(f"**{o.team}** : {o.p:.0%} cette semaine, mais "
                                   f"{later_match} → le plan la garde pour ce match.")
                else:
                    reasons.append(f"**{o.team}** : {o.p:.0%} cette semaine ; le plan la "
                                   f"garde pour {later_match}, une semaine où peu "
                                   f"d'équipes ont un bon match.")
            elif not pm:
                reasons.append(f"**{o.team}** : {o.p:.0%} cette semaine, mais une autre "
                               f"équipe fait mieux chaque semaine où elle joue → hors plan.")
            if len(reasons) == 4:
                break
        st.markdown("\n".join(f"- {x}" for x in reasons))

        st.markdown("#### Tous les choix possibles cette semaine")
        st.caption("« Espérance » = semaines survécues attendues si je prends cette équipe "
                   "maintenant (et le meilleur plan ensuite). « Coût » = espérance perdue "
                   "par rapport au meilleur choix.")
        rows = []
        for o, e in alts:
            later = best_later(o.team, monday)
            pm = planned.get(o.team)
            rows.append({
                "Équipe": o.team, "Match cette semaine": match_label(o),
                "Probabilité": 100 * o.p,
                "Meilleur match plus tard": (
                    f"{later[1].p:.1%} {'vs' if later[1].home else '@'} "
                    f"{later[1].opponent} ({fr_date(later[0])})" if later else "—"),
                "Au plan": "cette semaine" if pm == monday else (
                    f"sem. du {fr_date(pm)}" if pm else "hors plan"),
                "Espérance": e, "Coût": top_e - e,
                "Série": team_form[o.team].streak_label,
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
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
            "Semaine du": str(monday),
            "Journée": fr_day(o.game_date) if o else "—", "Équipe": o.team if o else "—",
            "Match": match_label(o) if o else "aucune équipe disponible",
            "Probabilité": 100 * o.p if o else 0.0, "Survie cumulée": 100 * alive,
            "Source": o.source if o else "",
        })
    skipped = [m for m, d in all_days.items() if not d.days and m >= op.week_start(from_date)]
    if skipped:
        st.info("Semaine(s) sautée(s), aucun match la fin de semaine : "
                + ", ".join(f"fin de semaine du {fr_weekend(m)}" for m in skipped))
    st.caption(f"Espérance : **{exp_weeks:.2f}** semaines survécues sur {len(plan)}. "
               "« estimé » = au-delà des 49 jours collectés, probabilité tirée du modèle "
               "de force des équipes. Le plan est recalculé chaque jour.")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 height=min(38 + 35 * len(rows), 1000), column_config={
                     "Probabilité": PCT,
                     "Survie cumulée": st.column_config.ProgressColumn(
                         format="%.1f %%", min_value=0.0, max_value=100.0),
                 })


# ── Carte des matchups ────────────────────────────────────────────────────

# Rampe séquentielle bleue de la palette de référence (clair → foncé)
BLUES = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]

with tab_map:
    st.caption("Chaque case : la probabilité de victoire du **meilleur match** de "
               "l'équipe à la journée de pick de la semaine (vide = elle ne joue pas "
               "ce jour-là). Les cases "
               "encadrées sont les picks du plan. Une équipe se lit de gauche à "
               "droite : on voit quand elle vaut le plus.")
    mondays_all = list(options)
    n_weeks = st.slider("Semaines affichées", 4, len(mondays_all), min(12, len(mondays_all)))
    shown = mondays_all[:n_weeks]
    labels = [fr_date(m) for m in shown]
    order = sorted(cm.TEAMS - set(used),
                   key=lambda t: (planned.get(t) or dt.date.max, t))
    cells = pd.DataFrame([{
        "Équipe": t, "Semaine": fr_date(m), "Probabilité": o.p,
        "Match": f"{'vs' if o.home else '@'} {o.opponent} — {o.game_date}",
        "Au plan": planned.get(t) == m, "Valeur": f"{100 * o.p:.0f}",
    } for m in shown for t, o in options[m].items()])

    x = alt.X("Semaine:O", sort=labels, title="Semaine du",
              axis=alt.Axis(orient="top", labelAngle=0))
    y = alt.Y("Équipe:N", sort=order, title=None)
    heat = alt.Chart(cells).mark_rect(cornerRadius=3, stroke="white", strokeWidth=2).encode(
        x=x, y=y,
        color=alt.Color("Probabilité:Q", title="Probabilité",
                        scale=alt.Scale(domain=[0.45, 0.85], range=BLUES, clamp=True),
                        legend=alt.Legend(format="%", orient="bottom")),
        tooltip=["Équipe:N", "Semaine:O", "Match:N",
                 alt.Tooltip("Probabilité:Q", format=".1%"), "Au plan:N"],
    )
    ring = alt.Chart(cells[cells["Au plan"]]).mark_rect(
        fill=None, stroke="#1a1a19", strokeWidth=2.5, cornerRadius=3).encode(x=x, y=y)
    text = alt.Chart(cells[cells["Au plan"]]).mark_text(fontSize=11, fontWeight="bold").encode(
        x=x, y=y, text="Valeur:N",
        color=alt.condition("datum.Probabilité > 0.7", alt.value("white"),
                            alt.value("#1a1a19")))
    st.altair_chart((heat + ring + text).properties(height=24 * len(order) + 40),
                    width="stretch")
    st.caption("Équipes triées par semaine prévue au plan ; celles hors plan à la fin. "
               "Survole une case pour voir le match.")


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


# ── Équipes en forme ──────────────────────────────────────────────────────

with tab_form:
    played = any(f.results for f in team_form.values())
    hist_r = strength_hist(v)
    snaps_r = sorted(hist_r.snapshot.unique())
    first = hist_r[hist_r.snapshot == snaps_r[0]].set_index("team").rating
    last = hist_r[hist_r.snapshot == snaps_r[-1]].set_index("team").rating
    st.caption("« Force » = chances de battre une équipe moyenne en terrain neutre, "
               "selon le consensus de chaque collecte. Une série de victoires est déjà "
               "en bonne partie intégrée dans les cotes : cet onglet aide à comprendre "
               "le plan, pas à le contredire.")

    # Séries en cours
    st.subheader("Séries de victoires")
    if not played:
        st.info("Aucun match joué pour l'instant : les séries apparaîtront après les "
                "premiers matchs (saison dès le 29 septembre).")
    else:
        n_min = st.slider("Victoires d'affilée, au moins", 2, 10, 4)
        hot = sorted((f for f in team_form.values() if f.streak >= n_min),
                     key=lambda f: -f.streak)
        if not hot:
            best = max(team_form.values(), key=lambda f: f.streak)
            st.caption(f"Aucune équipe sur une série de {n_min} victoires ou plus "
                       f"(meilleure série actuelle : {best.team}, {best.streak_label}).")
        else:
            st.dataframe(pd.DataFrame([{
                "Équipe": f.team, "Série": f.streak_label, "Fiche": f"{f.wins}-{f.losses}",
                "10 derniers": f.last10, "Force actuelle": 100 * last.get(f.team, 0.5),
                "Disponible": "Déjà utilisée" if f.team in used else "Oui",
            } for f in hot]), hide_index=True, width="stretch",
                column_config={"Force actuelle": PCT})

    # Progression de la force
    st.subheader("Progression : projection initiale vs maintenant")
    rise = (last - first).sort_values(ascending=False)
    default = [t for t in rise.index if t not in used and rise[t] >= 0.01][:5]
    if not default:   # pas encore de vraie progression : les plus fortes
        default = [t for t in last.sort_values(ascending=False).index if t not in used][:5]
    chosen = st.multiselect("Équipes (8 au maximum)", sorted(cm.TEAMS), default=default,
                            max_selections=8,
                            help="Par défaut : les équipes pas encore utilisées qui ont "
                                 "le plus progressé depuis la première collecte.")
    if chosen:
        data = hist_r[hist_r.team.isin(chosen)].assign(
            Collecte=lambda d: pd.to_datetime(d.snapshot), Force=lambda d: d.rating,
            Équipe=lambda d: d.team)
        color = alt.Color("Équipe:N", scale=alt.Scale(domain=chosen,
                                                      range=TEAM_COLORS[:len(chosen)]),
                          legend=alt.Legend(orient="bottom", title=None))
        base = alt.Chart(data).encode(
            x=alt.X("Collecte:T", title="Date de collecte",
                    axis=alt.Axis(format="%d/%m", tickCount={"interval": "day", "step": 1})),
            y=alt.Y("Force:Q", title="Force", axis=alt.Axis(format="%"),
                    scale=alt.Scale(zero=False)),
            color=color,
            tooltip=[alt.Tooltip("Équipe:N"), alt.Tooltip("Collecte:T", format="%Y-%m-%d"),
                     alt.Tooltip("Force:Q", format=".1%")],
        )
        labels = alt.Chart(data[data.snapshot == snaps_r[-1]]).mark_text(
            align="left", dx=8, fontSize=12).encode(
            x="Collecte:T", y="Force:Q", text="Équipe:N", color=color)
        chart = base.mark_line(strokeWidth=2) + base.mark_point(size=64, filled=True)
        if len(chosen) <= 4:   # au-delà, les étiquettes se chevauchent : légende seule
            chart += labels
        st.altair_chart(chart.properties(height=380), width="stretch")
        if len(snaps_r) < 2:
            st.caption("Une seule collecte pour l'instant : la progression se dessinera "
                       "au fil des jours.")
        st.dataframe(pd.DataFrame([{
            "Équipe": t, "Projection initiale": 100 * first[t], "Maintenant": 100 * last[t],
            "Écart (points)": 100 * (last[t] - first[t]),
            "Fiche": f"{team_form[t].wins}-{team_form[t].losses}",
            "Série": team_form[t].streak_label,
            "Disponible": "Déjà utilisée" if t in used else "Oui",
        } for t in chosen]), hide_index=True, width="stretch", column_config={
            "Projection initiale": PCT, "Maintenant": PCT,
            "Écart (points)": st.column_config.NumberColumn(format="%+.1f")})

    # Victoires réelles vs attendues
    st.subheader("Victoires réelles vs attendues")
    ev = expected(v)
    if not ev:
        st.info("Disponible après les premiers matchs joués.")
    else:
        table = pd.DataFrame([{
            "Équipe": t, "Matchs": len(g), "Victoires": sum(w for *_x, w in g),
            "Attendues": sum(p for _d, _o, p, _w in g),
        } for t, g in ev.items()]).assign(Écart=lambda d: d.Victoires - d.Attendues)
        table = table.sort_values("Écart", ascending=False)
        st.caption("« Attendues » = somme des probabilités d'avant-match. Un écart positif : "
                   "l'équipe gagne plus que prévu (forme réelle ou chance).")
        c1, c2 = st.columns([2, 3])
        c1.dataframe(table, hide_index=True, width="stretch", column_config={
            "Attendues": st.column_config.NumberColumn(format="%.1f"),
            "Écart": st.column_config.NumberColumn(format="%+.1f")})
        pick_t = c2.selectbox("Détail pour", list(table.Équipe))
        games = ev[pick_t]
        cum = pd.DataFrame({"Match": range(1, len(games) + 1),
                            "Réelles": pd.Series([w for *_x, w in games]).cumsum(),
                            "Attendues": pd.Series([p for _d, _o, p, _w in games]).cumsum()})
        long = cum.melt("Match", var_name="Série", value_name="Victoires")
        c2.altair_chart(alt.Chart(long).mark_line(strokeWidth=2, point=True).encode(
            x=alt.X("Match:Q", title="Matchs joués", axis=alt.Axis(tickMinStep=1)),
            y=alt.Y("Victoires:Q", title=f"Victoires cumulées de {pick_t}"),
            color=alt.Color("Série:N", scale=alt.Scale(domain=["Réelles", "Attendues"],
                                                       range=COLORS[:2]),
                            legend=alt.Legend(orient="bottom", title=None)),
            tooltip=["Match:Q", "Série:N", alt.Tooltip("Victoires:Q", format=".1f")],
        ).properties(height=320), width="stretch")


# ── Précision des sources ─────────────────────────────────────────────────

with tab_acc:
    st.caption("Poids actuels dans le consensus : " + " · ".join(
        f"{SOURCES[s]} {w:.0%}" for s, w in co.WEIGHTS.items())
        + ". Quand une source n'a pas de probabilité pour un match, les poids des "
          "autres sont répartis entre elles.")
    acc = [row for row in accuracy(v) if row[1]]
    if not acc:
        st.info("Disponible après les premiers matchs joués : chaque source sera "
                "jugée sur ses probabilités d'avant-match.")
    else:
        st.dataframe(pd.DataFrame([{
            "Source": SOURCES[s], "Matchs jugés": n, "Score de Brier": brier,
            "Favoris gagnants": None if hits is None else 100 * hits,
            "Brier (matchs cotés)": brier_c, "Casinos, mêmes matchs": brier_m,
        } for s, n, brier, hits, brier_c, brier_m in sorted(acc, key=lambda r: r[2])]),
            hide_index=True, width="stretch", column_config={
                "Score de Brier": st.column_config.NumberColumn(format="%.4f"),
                "Favoris gagnants": PCT,
                "Brier (matchs cotés)": st.column_config.NumberColumn(format="%.4f"),
                "Casinos, mêmes matchs": st.column_config.NumberColumn(format="%.4f"),
            })
        st.caption(
            "**Score de Brier** : moyenne de (probabilité − résultat)². 0,25 = pile ou "
            "face ; plus c'est bas, mieux c'est. Les sources ne couvrent pas les mêmes "
            "matchs (Dimers : la veille seulement ; casinos : quelques jours avant) : "
            "pour une comparaison juste, regarde les deux dernières colonnes, calculées "
            "sur les seuls matchs cotés par les casinos. Il faut environ 100 matchs "
            "avant de tirer des conclusions ; ensuite, on pourra ajuster les poids.")


# ── Depuis hier ───────────────────────────────────────────────────────────

with tab_diff:
    if len(snaps) < 2:
        st.info("Il faut au moins deux collectes pour comparer. Reviens après la "
                "prochaine collecte quotidienne.")
    else:
        prev = snaps[1]
        prev_plan, prev_exp, _ = plan_for(v, from_date, used, overrides, prev)
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
