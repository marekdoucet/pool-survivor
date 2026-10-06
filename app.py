"""
Site Streamlit du pool survivor NHL.

    streamlit run app.py

Données : reconstruites depuis data/ (mis à jour par GitHub Actions).
Picks : picks.json local, ou directement dans le dépôt GitHub si la section
[github] est présente dans .streamlit/secrets.toml (voir DEPLOIEMENT.md).
"""

import hashlib
import importlib
import os
import sqlite3
import sys
import datetime as dt
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import auth
import collect_moneypuck as cm
import collect_odds as co
import depot
import form
import optimize as op
import picks as pk
import store
import ui


# ── Modules à jour ─────────────────────────────────────────────────────────
# Streamlit relit app.py à chaque affichage mais ne recharge JAMAIS les
# modules importés. Sur Streamlit Cloud, un déploiement échange les fichiers
# sans relancer le processus : on se retrouve avec le nouvel app.py et
# l'ancien picks.py, et la page plante sur une fonction qui n'existe pas
# encore (« module 'picks' has no attribute 'pool' »). C'est arrivé trois
# fois ; le bouton « Reboot app » corrige, mais il ne faut pas en dépendre.
#
# On compare donc la date du fichier à celle retenue au dernier chargement.
# Le repère est posé SUR le module, qui survit d'un affichage à l'autre — une
# variable de app.py serait réinitialisée à chaque fois. Sans changement :
# huit appels à stat(), soit quelques microsecondes.
_MODULES = ("auth", "depot", "collect_moneypuck", "schedule", "collect_odds", "form",
            "optimize", "store", "picks", "ui")


def _recharge_les_modules_modifies():
    for nom in _MODULES:
        mod = sys.modules.get(nom)
        fichier = getattr(mod, "__file__", None)
        if not fichier:
            continue
        try:
            date = os.stat(fichier).st_mtime
        except OSError:
            continue
        if getattr(mod, "_date_chargement", None) != date:
            try:
                importlib.reload(mod)
            except Exception:      # un module cassé ne doit pas tuer la page
                continue
            sys.modules[nom]._date_chargement = date


_recharge_les_modules_modifies()

PICKS_PATH = Path(os.environ.get("SURVIVOR_PICKS", pk.PICKS_PATH))
CACHE_DB = Path(".cache") / "survivor.db"

SOURCES = {"consensus": "Consensus", "market": "Casinos", "kalshi": "Kalshi",
           "moneypuck": "MoneyPuck", "dimers": "Dimers", "puckcast": "Puckcast"}
# Palette catégorielle de référence (8 couleurs), dans un ordre fixe :
# la couleur suit la source, ou l'équipe (au plus 8 équipes comparées à la fois).
# Pas calibrés pour fond sombre — mêmes teintes et même ordre que la version
# fond clair, mais re-choisis pour la surface sombre, pas éclaircis au hasard.
TEAM_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500",
               "#d55181", "#008300", "#9085e9", "#e66767"]
COLORS = TEAM_COLORS[:len(SOURCES)]

st.set_page_config(page_title="Pool survivor NHL", page_icon="🏒", layout="wide")
ui.inject_css()

# Le titre est dessiné ici, avant toute préparation de données. Sans ça,
# l'écran reste vide pendant que la base se reconstruit, que picks.json est
# lu sur GitHub et que le plan s'optimise — sur Streamlit Cloud, un réveil
# après inactivité prend des minutes, et un écran blanc ne se distingue pas
# d'une panne. Mieux vaut montrer quelque chose qui vit.
st.title("🏒 Pool survivor NHL 2026-27")
STATUT = st.empty()
STATUT.caption("Chargement des données…")

PCT = st.column_config.NumberColumn(format="%.1f %%")   # valeurs déjà × 100
# L'espérance n'est PAS un pourcentage : c'est un nombre de semaines survécues.
# Sans unité, à côté de colonnes en %, 2.34 se lit de travers.
SEM = "%.2f sem."
SEM_SIGNE = "%+.2f sem."


# ── Données ────────────────────────────────────────────────────────────────
# La base est reconstruite depuis data/ quand un fichier y change. `version`
# (un condensé des fichiers) fait partie de la clé de chaque cache ci-dessous.

# ttl=10 : data/ ne bouge qu'aux deux collectes de la journée, alors que le
# script est rejoué à chaque clic. Sans ce cache, chaque affichage relit tout
# le dossier — 66 ms en fin de saison.
@st.cache_data(ttl=10)
def data_version():
    """Condensé de l'état de data/ : change dès qu'un fichier change.

    Un condensé court plutôt que la liste des fichiers : cette valeur sert de
    clé à une dizaine de caches, que Streamlit rehache à chaque affichage. La
    liste atteint 380 entrées en fin de saison (deux fichiers par jour), ce
    qui mesurait 134 ms de hachage par affichage ; le condensé les ramène à
    quelques microsecondes.
    """
    h = hashlib.sha256()
    vide = True
    for f in sorted(store.DATA_DIR.rglob("*.csv*")):
        info = f.stat()
        h.update(f"{f}|{info.st_size}|{info.st_mtime}".encode())
        vide = False
    return "" if vide else h.hexdigest()


@st.cache_data
def build_db(version):
    CACHE_DB.parent.mkdir(exist_ok=True)
    store.build_db(CACHE_DB)
    return str(CACHE_DB)


if "SURVIVOR_DB" in os.environ:      # base imposée (tests, démo)
    DB_PATH = os.environ["SURVIVOR_DB"]
    VERSION = Path(DB_PATH).stat().st_mtime if Path(DB_PATH).exists() else 0
else:
    with st.spinner("Reconstruction de la base depuis data/…"):
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
def plan_for(version, from_date, used, overrides, horizon, snapshot=None):
    with connect() as c:
        return op.optimize(c, from_date, set(used), snapshot, dict(overrides), horizon)


@st.cache_data
def alternatives_for(version, from_date, used, overrides, horizon, include=()):
    with connect() as c:
        return op.alternatives(c, from_date, set(used), overrides=dict(overrides),
                               horizon=horizon, include=include)


@st.cache_data
def options_for(version, from_date, used, overrides, horizon):
    """{lundi: {équipe: meilleur match de la journée de pick}} sur l'horizon."""
    with connect() as c:
        return op.build_options(c, from_date, set(used), overrides=dict(overrides),
                                horizon=horizon)[0]


@st.cache_data
def pick_days_for(version, overrides):
    with connect() as c:
        return op.pick_days(c, dict(overrides))


@st.cache_data
def resultats(version, overrides):
    """{(lundi ISO, équipe): a gagné} pour les matchs de journée de pick joués.

    Un match absent du dictionnaire n'est pas une défaite : il n'est pas
    encore joué, ou reporté. Seuls les matchs de la journée de pick comptent —
    une équipe peut jouer d'autres soirs dans la semaine.
    """
    jours = pick_days_for(version, overrides) if version else {}
    autorises = {d: m for m, pd in jours.items() for d in pd.days}
    out = {}
    with connect() as c:
        for g, away, home, away_gagne in form.finished_games(c):
            m = autorises.get(g)
            if m is None:
                continue
            out[(m.isoformat(), away)] = away_gagne
            out[(m.isoformat(), home)] = not away_gagne
    return out


@st.cache_data
def players(version):
    """tricode → meneur de l'équipe, depuis data/players.csv (facultatif)."""
    path = store.DATA_DIR / "players.csv"
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    return {r["team"]: r for r in df.to_dict("records")}


@st.cache_data
def history(version):
    with connect() as c:
        return pd.read_sql("SELECT snapshot, game_date, away, home, p_away, source FROM probs", c)


@st.cache_data
def blesses(version):
    """{équipe: [(joueur, poste, statut, blessure)]}, gardiens d'abord.

    Affichage seulement : jamais dans les probabilités, les modèles tiennent
    déjà compte des blessures. Une base sans la table (démo, ancienne
    version) donne simplement un dictionnaire vide.
    """
    try:
        with connect() as c:
            rows = c.execute("SELECT team, player, pos, status, injury "
                             "FROM injuries").fetchall()
    except sqlite3.OperationalError:
        return {}
    par_equipe = {}
    for team, player, pos, status, injury in rows:
        par_equipe.setdefault(team, []).append((player, pos, status, injury))
    for lst in par_equipe.values():
        lst.sort(key=lambda r: (r[1] != "G", r[0]))   # gardiens en tête
    return par_equipe


@st.cache_data
def gardiens(version):
    """{(game_date, away, home): {équipe: (gardien, statut, mis à jour)}}.

    Lus sur la page de chaque match proche chez Puckcast, qui cite RotoWire.
    Gardien vide = pas encore annoncé.
    """
    try:
        with connect() as c:
            rows = c.execute("SELECT game_date, away, home, team, goalie, status, "
                             "updated FROM goalies").fetchall()
    except sqlite3.OperationalError:
        return {}
    par_match = {}
    for g, a, h, team, nom, statut, maj in rows:
        par_match.setdefault((g, a, h), {})[team] = (nom, statut, maj)
    return par_match


STATUT_GARDIEN = {"Expected": "attendu", "Confirmed": "confirmé"}


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


def chiffres(df, colonnes):
    """Force des colonnes en numérique : None y devient une case vide.

    Sans ça, une colonne entièrement vide reste de type texte et Streamlit
    affiche le mot « None » dans chaque cellule.
    """
    for c in colonnes:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


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

def depot_github():
    """L'ancien dépôt : un seul pool, rangé dans le fichier picks.json du
    dépôt GitHub. Sert encore de source pour l'import vers Neon."""
    try:
        gh = st.secrets.get("github")
    except FileNotFoundError:
        gh = None
    if not gh:
        return None
    author = {"name": gh.get("author_name", "marekdoucet"),
              "email": gh.get("author_email",
                              "183759644+marekdoucet@users.noreply.github.com")}
    return pk.GitHubPicks(gh["repo"], gh["token"], gh.get("branch", "main"),
                          author=author)


def picks_backend(courriel):
    """Où vit le pool de la personne devant l'écran.

    Neon et connectée : son pool à elle, une ligne par courriel.
    Neon et anonyme  : rien du tout. Les pages d'analyse fonctionnent, les
                       pages personnelles invitent à se connecter — on ne
                       montre le pool de personne.
    Sans Neon        : l'ancien dépôt partagé, ou picks.json en local.
    """
    try:
        neon = st.secrets.get("neon")
    except FileNotFoundError:
        neon = None
    if neon:
        return (depot.PoolNeon(neon["url"], courriel) if courriel
                else depot.PoolAnonyme(pk.empty_state))
    return depot_github()


def connexion():
    """Appelée par le bouton. Streamlit masque l'erreur de st.login à l'écran
    (« redacted to prevent data leaks ») : le visiteur voit une page blanche et
    « Internal server error », et il faut aller fouiller le journal de Manage
    app. On l'attrape pour l'afficher en clair.
    """
    if not auth.authlib_present():
        st.session_state["_err_connexion"] = (
            "Le paquet Authlib est absent de l'environnement. Il est dans "
            "requirements.txt : fais « Reboot app » pour que Streamlit Cloud "
            "reconstruise l'environnement."
        )
        return
    try:
        st.login()
    except Exception as e:
        if auth.est_controle_streamlit(e):
            raise                       # c'est Streamlit qui pilote, pas un bogue
        st.session_state["_err_connexion"] = f"{type(e).__name__} : {e}"


def save_state(new_state, message):
    # Point de passage unique de toutes les écritures : c'est ici qu'on refuse
    # un anonyme, plutôt que devant chaque bouton. Les boutons sont désactivés
    # en plus, pour le dire avant le clic — mais c'est cette ligne qui protège.
    if not PEUT_ECRIRE:
        st.sidebar.error("Connecte-toi pour enregistrer.")
        return
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


PEUT_ECRIRE = auth.peut_ecrire(st.secrets, st.user)
MOI = auth.qui(st.user)

today = dt.datetime.now(cm.TZ).date()
remote = picks_backend(MOI)
state = remote.load() if remote else pk.load(PICKS_PATH)
picks, day_overrides = state["picks"], state["days"]
# Tour en cours : quand il ne reste qu'une personne en vie, le pool repart de
# la semaine courante et toutes les équipes redeviennent disponibles. Les picks
# des tours précédents restent dans picks.json, mais ne bloquent plus rien.
since = pk.last_reset(state["resets"], today)
tour = pk.this_round(picks, since)
# 8 semaines de base : survivre plus longtemps serait étonnant, et optimiser
# toute la saison ferait garder des équipes pour des semaines qu'on n'atteint
# jamais. Si le tour dépasse 8 semaines, l'horizon suit, une par semaine.
horizon = pk.horizon(since, today)
overrides = tuple(sorted(day_overrides.items()))   # clé de cache
all_days = pick_days_for(VERSION, overrides) if VERSION else {}
this_monday = op.week_start(today)
from_date, used_set, provisional = pk.planning(picks, today, since)
# Le pick de la semaine en cours. Defini ici plutot que dans page_pick :
# page_pool le lit aussi, et st.navigation n'execute QUE la page ouverte.
# Quand il etait local a page_pick, ouvrir « Le pool » levait
# « NameError: name 'current' is not defined ».
current = next((p for p in picks if p["week"] == this_monday.isoformat()), None)
used = tuple(sorted(used_set))

RES = resultats(VERSION, overrides) if VERSION else {}
# Même règle pour moi que pour les autres : le pick perdu élimine, et le
# bouton de la barre latérale reste prioritaire.
_moi = {"picks": {p["week"]: p["team"] for p in picks},
        "out": state["out"], "force": state.get("force")}
_out, _sem_out, _origine_out = pk.statut(_moi, RES, since)
out = (dt.date.fromisoformat(_sem_out) if _out and _sem_out
       else (this_monday if _out else None))

with st.sidebar:
    if auth.configuree(st.secrets):
        if PEUT_ECRIRE:
            st.caption(f"Connecté : {auth.nom(st.user)}")
            st.button("Se déconnecter", width="stretch", on_click=st.logout)
        else:
            st.button("Se connecter avec Google", type="primary",
                      width="stretch", on_click=connexion)
            if st.session_state.get("_err_connexion"):
                st.error(st.session_state["_err_connexion"])
            st.caption("Tu peux tout consulter sans compte. "
                       "La connexion sert à enregistrer.")

        # Où va ce qu'on enregistre. Affiché parce qu'une erreur de stockage
        # est invisible autrement : on croit avoir sauvegardé, et non.
        if isinstance(remote, depot.PoolNeon):
            st.caption("Ton pool est enregistré dans la base Neon.")
        elif isinstance(remote, depot.PoolAnonyme):
            st.caption("Connecte-toi pour avoir ton propre pool.")
        elif remote:
            st.caption(f"Pool partagé, dans GitHub ({remote.repo})")

        st.divider()

    st.header("Mes picks")
    if not tour:
        st.caption("Aucun pick dans ce tour.")
    for p in tour:
        col1, col2 = st.columns([4, 1])
        col1.markdown(f"**{p['team']}** — semaine du {p['week']}")
        if col2.button("✕", key=f"del-{p['week']}", help="Retirer ce pick",
                       disabled=not PEUT_ECRIRE):
            save_picks(pk.remove(picks, dt.date.fromisoformat(p["week"])),
                       f"Pick retiré : {p['team']} (semaine du {p['week']})")

    st.subheader("Tour en cours")
    archive = len(picks) - len(tour)
    if since:
        st.caption(f"Reparti la semaine du {since}."
                   + (f" {archive} pick(s) des tours précédents en archive."
                      if archive else ""))
    else:
        st.caption("Premier tour de la saison.")
    with st.popover("Faire repartir le pool", width="stretch"):
        st.markdown("Quand il ne reste **qu'une personne en vie**, le pool "
                    "recommence à partir de la semaine en cours : toutes les "
                    "équipes redeviennent disponibles.")
        st.caption("Rien n'est effacé — les picks des tours précédents restent "
                   "dans le fichier et l'opération est annulable.")
        depart = st.date_input("Repartir à partir de la semaine du",
                               value=this_monday, format="YYYY-MM-DD")
        lundi = op.week_start(depart)
        if lundi != depart:
            st.caption(f"Ramené au lundi de cette semaine : {lundi}.")
        if st.button("Confirmer le redépart", type="primary", width="stretch",
                     disabled=not PEUT_ECRIRE):
            save_state(pk.reset(state, lundi),
                       f"Pool reparti à la semaine du {lundi}")
    if since:
        if st.button(f"Annuler le redépart du {since}", width="stretch",
                     disabled=not PEUT_ECRIRE):
            save_state(pk.undo_reset(state, since),
                       f"Redépart du {since} annulé")
    # Plus de bouton « Je suis éliminé » : le site le déduit du résultat de
    # mon pick. Reste l'annulation, pour les cas qu'aucun match ne dira.
    if out:
        st.caption(f"Éliminé depuis la semaine du {out}"
                   + (" (déduit du résultat)" if _origine_out == "auto" else "")
                   + ".")
        if st.button("Je suis encore en vie", width="stretch",
                     help="Passe outre : règle maison, erreur, litige",
                     disabled=not PEUT_ECRIRE):
            save_state(pk.back_in(state), "Élimination annulée")

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
            if st.form_submit_button("Enregistrer la journée",
                                     disabled=not PEUT_ECRIRE):
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

v = VERSION
snaps = snapshots(v) if v else []
if not snaps:
    st.error("Aucune donnée. Lance d'abord `python daily.py`.")
    st.stop()

if out:
    # Coupé ici, avant plan_for : l'optimisation est de loin le plus gros
    # calcul de la page, et il n'y a plus rien à planifier une fois éliminé.
    with ui.panel("elimine", "Tu es éliminé",
                  f"Depuis la semaine du {out}. Aucun plan n'est calculé "
                  f"jusqu'au prochain redépart."):
        st.markdown(
            "Le pool recommence quand il ne reste **qu'une personne en vie** : "
            "toutes les équipes redeviennent alors disponibles, et le plan "
            "repart de la semaine en cours.")
        st.markdown("Dans la barre latérale : **Faire repartir le pool** quand "
                    "ce moment arrive, ou **Je suis encore en vie** si c'est "
                    "une fausse manœuvre.")
        if tour:
            st.caption("Tes picks de ce tour : "
                       + ", ".join(f"{p['team']} (sem. du {p['week']})" for p in tour))
    st.stop()

plan, exp_weeks, snapshot = plan_for(v, from_date, used, overrides, horizon)
hist = history(v)
latest = hist[hist.snapshot == snapshot]
counts = latest.groupby("source").size()
STATUT.caption(
    f"Données du {snapshot} · "
    + " · ".join(f"{SOURCES[s]} : {counts.get(s, 0)} matchs" for s in SOURCES if s != "consensus")
    + f" · {len(used)} équipe(s) utilisée(s)"
)

options = options_for(v, from_date, used, overrides, horizon)
planned = {o.team: m for m, o in plan if o}   # équipe → semaine où le plan l'utilise


def best_later(team, after):
    """Meilleur match de l'équipe après la semaine `after` : (lundi, Option) ou None."""
    later = [(m, w[team]) for m, w in options.items() if m > after and team in w]
    return max(later, key=lambda x: x[1].p, default=None)
team_form = forms(v)


# ── Pick de la semaine ────────────────────────────────────────────────────

def page_pick():
    # Un seul endroit pour choisir, ici. Le formulaire qui doublonnait dans la
    # barre latérale a été retiré : il y avait deux façons d'enregistrer le
    # même pick, avec le risque de ne plus savoir laquelle faisait foi.
    choisi = None            # l'équipe montrée dans « Enregistrer mon pick »
    if current and not provisional:
        st.success(f"Pick verrouillé pour cette semaine : **{current['team']}**. "
                   f"Le plan reprend la semaine du {from_date}.")
    elif this_monday in options:
        week_opts = sorted(options[this_monday].values(), key=lambda o: -o.p)
        teams_ = [o.team for o in week_opts]
        # Ce que le site retient : le pick déjà enregistré s'il y en a un,
        # sinon la recommandation. C'est CE choix qui se verrouille vendredi
        # minuit, qu'on y retouche ou non.
        retenu = (provisional or {}).get("team")
        if retenu is None and plan and plan[0][1] is not None:
            retenu = plan[0][1].team
        idx = teams_.index(retenu) if retenu in teams_ else 0
        labels = [f"{o.team} {match_label(o)} — {o.p:.1%}" for o in week_opts]
        with ui.panel("choisir", "Enregistrer mon pick",
                      f"Modifiable jusqu'au "
                      f"{fr_day(pk.pick_deadline(today).isoformat())} à minuit. "
                      f"Passé ce délai, c'est l'équipe affichée ici qui est "
                      f"retenue."):
            # Hors formulaire, exprès : le menu relance l'affichage à chaque
            # changement, donc la carte dessous montre tout de suite l'équipe
            # qu'on est en train de considérer, avant même d'enregistrer.
            new = st.selectbox(f"Équipe pour la fin de semaine du "
                               f"{fr_weekend(this_monday)}", range(len(week_opts)),
                               index=idx, format_func=lambda i, lb=labels: lb[i])
            choisi = week_opts[new]
            deja = provisional and provisional["team"] == choisi.team
            st.caption("C'est ton pick enregistré." if deja
                       else ("Pas encore enregistré — clique pour le confirmer."
                             if provisional else
                             "Rien d'enregistré pour cette semaine."))
            if st.button("Remplacer mon pick" if provisional
                         else "Enregistrer mon pick",
                         type="primary", width="stretch",
                         disabled=bool(deja) or not PEUT_ECRIRE):
                save_picks(pk.add(picks, this_monday, choisi.team, choisi.game_date,
                                  since=since),
                           f"Pick {'modifié' if provisional else 'enregistré'} : "
                           f"{choisi.team} (semaine du {this_monday})")
        # La carte de l'équipe choisie. C'est elle qui doit donner sa couleur
        # au fond de la page, pas la recommandation : le site reflète MON
        # choix. Comme ui.hero() pose la lueur lui-même et que la
        # recommandation est dessinée après, on repose la bonne teinte à la
        # fin (voir plus bas) — une feuille de style plus tardive l'emporte.
        ui.hero(choisi.team,
                "Mon pick" + (" · enregistré" if deja else " · à confirmer"),
                f"contre {ui.name(choisi.opponent)} · "
                + ("à domicile" if choisi.home else "à l'étranger")
                + f" · {fr_day(choisi.game_date)}",
                choisi.p, players(v).get(choisi.team))

    if not plan or plan[0][1] is None:
        st.warning("Aucun match disponible pour la prochaine semaine.")
    else:
        monday, best = plan[0]
        game = latest[(latest.game_date == best.game_date)
                      & ((latest.away == best.team) | (latest.home == best.team))]
        by_src = dict(zip(game.source, win_prob(game, best.team)))
        # On compare avec l'équipe AFFICHÉE au-dessus, pas avec celle qui est
        # enregistrée : sans pick sauvegardé, le menu montre déjà la
        # recommandation, et la carte apparaissait deux fois de suite.
        if choisi is None or best.team != choisi.team or monday != this_monday:
            ui.hero(best.team,
                    f"Pick recommandé · fin de semaine du {fr_weekend(monday)}",
                    f"contre {ui.name(best.opponent)} · "
                    + ("à domicile" if best.home else "à l'étranger")
                    + f" · {fr_day(best.game_date)}",
                    best.p,
                    players(v).get(best.team))
        if choisi is not None:
            # ui.hero() pose la lueur pour chaque carte qu'il dessine ; sans
            # ceci, la dernière dessinée — la recommandation — l'emporterait.
            # Une feuille de style plus tardive gagne, donc on repose la teinte
            # du choix ici, à la fin.
            ui.backdrop((players(v).get(choisi.team) or {}).get("color"))
        # La couleur suit la source, jamais son rang : on indexe dans SOURCES,
        # pas dans la liste filtrée, sinon une source absente repeint les autres.
        # Toutes les sources, même absentes : une case vide dit « pas encore de
        # cote », alors qu'une pastille manquante ne dit rien du tout.
        ui.chips([(SOURCES[s], by_src.get(s)) for s in SOURCES], COLORS)
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
        # Blessures du match de l'équipe MONTRÉE comme mon pick (le choix du
        # menu s'il y en a un cette semaine, sinon la recommandation) — pas
        # celle des pastilles ci-dessus, qui suivent toujours la recommandation.
        cible = choisi if (choisi is not None and monday == this_monday) else best
        bl = blesses(v)
        visiteur, local = ((cible.opponent, cible.team) if cible.home
                           else (cible.team, cible.opponent))
        gd = gardiens(v).get((cible.game_date, visiteur, local), {})
        if bl or gd:
            with ui.panel("avant-match", f"Avant le match · {cible.team} contre {cible.opponent}",
                          "Information seulement : les modèles en tiennent déjà "
                          "compte, ce n'est pas ajouté aux probabilités. Un "
                          "gardien titulaire pèse plus que n'importe quel attaquant."):
                if gd:
                    st.markdown("**Gardiens**")
                    for equipe in (cible.team, cible.opponent):
                        nom, statut, _maj = gd.get(equipe, ("", "", ""))
                        st.markdown(
                            f"{ui.name(equipe)} : **{nom}** "
                            f"({STATUT_GARDIEN.get(statut, statut.lower() or 'annoncé')})"
                            if nom else f"{ui.name(equipe)} : pas encore annoncé")
                    maj = next((m for _n, _s, m in gd.values() if m), "")
                    st.caption("Source : RotoWire, via Puckcast"
                               + (f" · mis à jour {maj}" if maj else "")
                               + ". « Attendu » n'est pas une certitude : le "
                                 "titulaire se confirme en général le jour du match.")
                if bl:
                    st.markdown("**Blessures**")
                for equipe in (cible.team, cible.opponent) if bl else ():
                    liste = bl.get(equipe, [])
                    if not liste:
                        st.caption(f"{ui.name(equipe)} : aucune blessure déclarée.")
                        continue
                    st.markdown(f"**{ui.name(equipe)}** — {len(liste)} blessé(s)")
                    st.dataframe(pd.DataFrame(
                        [{"Joueur": j, "Poste": p, "Statut": s, "Blessure": b}
                         for j, p, s, b in liste]),
                        hide_index=True, width="stretch")
        # `include` force l'évaluation de l'équipe choisie même si elle n'est
        # pas dans les meilleures : sans ça, son espérance serait introuvable.
        # Popularité chez les adversaires : une équipe que tout le monde prend
        # ne démarque pas. Renseignée dans la section « Le pool ».
        pris = pk.popularity(state, monday, since, RES) if monday == this_monday else {}
        # Ce que rapporte le fait de se démarquer : survivre ET voir tomber
        # tous les adversaires en vie. Suivre le troupeau donne exactement 0.
        rivaux = (pk.pool_picks(state, monday, since, RES)
                  if monday == this_monday else {})
        vivants = pk.pool_alive(state, since, RES) if monday == this_monday else []
        # Sans TOUS les picks des adversaires en vie, la colonne serait
        # trompeuse : elle mesure la chance qu'ils tombent tous.
        manquants = len(vivants) - len(rivaux)
        p_semaine = {t: o.p for t, o in options.get(monday, {}).items()}
        alts = alternatives_for(v, from_date, used, overrides, horizon,
                                (choisi.team,) if choisi else ())
        top_e = alts[0][1]

        # L'espérance affichée suit MON pick, pas la recommandation : si je
        # choisis autre chose, le chiffre doit dire ce que CE choix rapporte.
        mien = next((e for o, e in alts if choisi and o.team == choisi.team), None)
        exp_montree = mien if mien is not None else exp_weeks
        vedette = choisi or best
        ui.tiles([
            ("Espérance", f"{exp_montree:.2f} sem.",
             f"survécues sur {len(plan)}"
             + ("" if mien is None else " avec ce pick")),
            ("Coût du choix",
             "—" if mien is None else f"{top_e - mien:+.2f} sem.",
             "d'espérance vs le meilleur choix" if mien is not None
             else "aucun pick choisi"),
            ("Match", "Domicile" if vedette.home else "Visiteur",
             f"contre {vedette.opponent} · {fr_day(vedette.game_date)}"),
            ("Série en cours", team_form[vedette.team].streak_label or "aucune",
             f"de {vedette.team}"),
        ] + ([("Adversaires sur ce pick", str(pris.get(vedette.team, 0)),
               f"sur {len(pk.pool_alive(state, since, RES))} en vie")]
             if pk.pool(state) else []))
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
                    reasons.append(f"{ui.inline(o.team)} : {o.p:.0%} cette semaine, mais "
                                   f"{later_match} → le plan la garde pour ce match.")
                else:
                    reasons.append(f"{ui.inline(o.team)} : {o.p:.0%} cette semaine ; le plan la "
                                   f"garde pour {later_match}, une semaine où peu "
                                   f"d'équipes ont un bon match.")
            elif not pm:
                reasons.append(f"{ui.inline(o.team)} : {o.p:.0%} cette semaine, mais une autre "
                               f"équipe fait mieux chaque semaine où elle joue → hors plan.")
            if len(reasons) == 4:
                break
        with ui.panel("pourquoi", "Pourquoi pas une autre équipe ?",
                      "Le plan est optimisé sur toute la saison d'un coup : une "
                      "équipe forte cette semaine peut être gardée pour une semaine "
                      "où son match est encore meilleur, ou où aucune autre équipe "
                      "ne fait mieux."):
            st.markdown("\n".join(f"- {x}" for x in reasons), unsafe_allow_html=True)

        rows = []
        for o, e in alts:
            later = best_later(o.team, monday)
            pm = planned.get(o.team)
            seule = pk.alone_odds(o.team, o.p, rivaux, p_semaine, len(vivants))
            rows.append({
                "": ui.logo(o.team),
                "Équipe": o.team, "Pris par": pris.get(o.team, 0),
                # Colonne omise si elle n'est pas calculable : une colonne
                # entièrement vide n'apprend rien et Streamlit y écrit « None ».
                **({} if seule is None else {"Seule debout": 100 * seule}),
                "Match cette semaine": match_label(o),
                "Probabilité": 100 * o.p,
                "Meilleur match plus tard": (
                    f"{later[1].p:.1%} {'vs' if later[1].home else '@'} "
                    f"{later[1].opponent} ({fr_date(later[0])})" if later else "—"),
                "Au plan": "cette semaine" if pm == monday else (
                    f"sem. du {fr_date(pm)}" if pm else "hors plan"),
                "Espérance": e, "Coût": top_e - e,
                "Série": team_form[o.team].streak_label,
            })
        with ui.panel("choix", "Tous les choix possibles cette semaine",
                      "« Coût » = espérance perdue par rapport au meilleur "
                      "choix. « Seule debout » = ce que ce choix rapporte en "
                      "échange : la chance de survivre pendant que tous les "
                      "adversaires tombent. L'arbitrage se lit entre ces deux "
                      "colonnes."):
            if manquants > 0:
                st.info(
                    f"La colonne **« Seule debout »** apparaîtra quand les "
                    f"{manquants} adversaire(s) en vie sur {len(vivants)} qui "
                    f"n'ont pas encore de pick saisi seront renseignés. Elle "
                    f"mesure la chance qu'ils tombent **tous** : en ignorer un "
                    f"la rendrait bien trop optimiste. Ça se remplit dans "
                    f"« Le pool ».")
            st.dataframe(chiffres(pd.DataFrame(rows),
                                  ["Seule debout", "Probabilité", "Espérance",
                                   "Coût", "Pris par"]),
                         hide_index=True, width="stretch",
                         column_config={
                             "": st.column_config.ImageColumn("", width="small"),
                             "Pris par": st.column_config.NumberColumn(
                                 "Pris par", format="%d",
                                 help="Adversaires en vie qui prennent cette "
                                      "équipe cette semaine"),
                             "Seule debout": st.column_config.NumberColumn(
                                 format="%.1f %%",
                                 help="Chance de survivre ET de voir tomber "
                                      "tous les adversaires en vie. Une équipe "
                                      "qu'un adversaire prend aussi donne 0 : "
                                      "on ne peut pas se démarquer en la "
                                      "prenant."),
                             "Probabilité": PCT,
                             "Espérance": st.column_config.NumberColumn(
                                 format=SEM,
                                 help="Semaines survécues attendues en prenant "
                                      "cette équipe, puis le meilleur plan"),
                             "Coût": st.column_config.NumberColumn(
                                 format=SEM,
                                 help="Semaines d'espérance perdues par rapport "
                                      "au meilleur choix"),
                         })


# ── Plan complet ──────────────────────────────────────────────────────────

def page_plan():
    rows, alive = [], 1.0
    for monday, o in plan:
        alive *= o.p if o else 0.0
        rows.append({
            "": ui.logo(o.team) if o else "",
            "Semaine du": str(monday),
            "Journée": fr_day(o.game_date) if o else "—", "Équipe": o.team if o else "—",
            "Match": match_label(o) if o else "aucune équipe disponible",
            "Probabilité": 100 * o.p if o else 0.0, "Survie cumulée": 100 * alive,
            "Source": o.source if o else "",
        })
    jouees = [(o.team, o.p, m) for m, o in plan if o]
    faible = min(jouees, key=lambda x: x[1]) if jouees else ("—", 0.0, from_date)
    skipped = [m for m, d in all_days.items() if not d.days and m >= op.week_start(from_date)]
    if skipped:
        st.info("Semaine(s) sautée(s), aucun match la fin de semaine : "
                + ", ".join(f"fin de semaine du {fr_weekend(m)}" for m in skipped))
    ui.tiles([
        ("Espérance", f"{exp_weeks:.2f} sem.",
         f"survécues sur {len(plan)}"),
        ("Semaines planifiées", str(len(plan)), "jusqu'à la fin de la saison"),
        # Le maillon faible plutôt que la survie cumulée : le produit de 27
        # probabilités tend vers zéro et n'apprend rien ; la semaine la plus
        # risquée, elle, dit où le plan peut casser.
        ("Semaine la plus risquée", f"{100 * faible[1]:.0f} %",
         f"{faible[0]} · semaine du {fr_date(faible[2])}"),
        ("Équipes déjà prises", str(len(used)), "retirées du plan"),
    ])
    st.caption("« estimé » = au-delà des 49 jours collectés, probabilité tirée du "
               "modèle de force des équipes. Le plan est recalculé chaque jour.")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 height=min(38 + 35 * len(rows), 1000), column_config={
                     "": st.column_config.ImageColumn("", width="small"),
                     "Probabilité": PCT,
                     "Survie cumulée": st.column_config.ProgressColumn(
                         format="%.1f %%", min_value=0.0, max_value=100.0),
                 })


# ── Carte des matchups ────────────────────────────────────────────────────

# Rampe séquentielle bleue de la palette de référence, du plus sombre au plus
# clair : sur fond sombre, c'est le pas sombre qui doit se fondre dans la
# surface pour dire « proche de zéro ». L'ordre inverse de la version fond
# clair, mêmes valeurs.
BLUES = ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"]

def page_carte():
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
    heat = alt.Chart(cells).mark_rect(cornerRadius=3, stroke="#1a1a19", strokeWidth=2).encode(
        x=x, y=y,
        color=alt.Color("Probabilité:Q", title="Probabilité",
                        scale=alt.Scale(domain=[0.45, 0.85], range=BLUES, clamp=True),
                        legend=alt.Legend(format="%", orient="bottom")),
        tooltip=["Équipe:N", "Semaine:O", "Match:N",
                 alt.Tooltip("Probabilité:Q", format=".1%"), "Au plan:N"],
    )
    # Encadré des picks du plan : blanc, pas la couleur de la surface. Il
    # était sombre du temps du fond clair, où il ressortait ; sur fond sombre
    # il devenait invisible sur les cases de faible probabilité.
    ring = alt.Chart(cells[cells["Au plan"]]).mark_rect(
        fill=None, stroke="#ffffff", strokeWidth=2, cornerRadius=3).encode(x=x, y=y)
    text = alt.Chart(cells[cells["Au plan"]]).mark_text(fontSize=11, fontWeight="bold").encode(
        x=x, y=y, text="Valeur:N",
        # La rampe va du sombre (faible) au clair (fort) : l'encre s'inverse
        # donc par rapport à la version fond clair, sinon le texte des cases
        # faibles est sombre sur sombre.
        color=alt.condition("datum.Probabilité > 0.7", alt.value("#11110f"),
                            alt.value("#ffffff")))
    st.altair_chart((heat + ring + text).properties(height=24 * len(order) + 40),
                    width="stretch")
    st.caption("Équipes triées par semaine prévue au plan ; celles hors plan à la fin. "
               "Survole une case pour voir le match.")


# ── Évolution des probabilités ────────────────────────────────────────────

def page_evolution():
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

def page_forme():
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

def page_precision():
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

    # ── Comment se calcule l'espérance ────────────────────────────────────
    # Construit sur les vraies semaines du plan : un exemple invente
    # n'apprendrait pas ou le chiffre affiche en haut du site vient.
    with ui.panel("esperance", "Comment se calcule l'espérance",
                  "Le chiffre affiché partout sur le site : le nombre de "
                  "semaines que le plan devrait survivre en moyenne."):
        st.markdown(
            "Dans un survivor, **une seule défaite élimine**. Être encore en vie "
            "à la semaine 3 demande donc de gagner les semaines 1, 2 **et** 3 : "
            "les probabilités se multiplient, elles ne s'additionnent pas.")
        st.markdown(
            "L'espérance additionne, pour chaque semaine, la probabilité d'être "
            "encore en vie à ce moment-là :")
        st.latex(r"E = p_1 + p_1 p_2 + p_1 p_2 p_3 + \dots")

        montre = [(m, o) for m, o in plan if o][:4]
        if montre:
            lignes, vivant, cumul = [], 1.0, 0.0
            for i, (m, o) in enumerate(montre, 1):
                vivant *= o.p
                cumul += vivant
                lignes.append({
                    "Semaine": f"{i} — {fr_date(m)}", "Équipe": o.team,
                    "Probabilité de gagner": 100 * o.p,
                    "Encore en vie après": 100 * vivant,
                    "Espérance cumulée": cumul,
                })
            st.markdown(f"Tes {len(montre)} premières semaines, en vrai :")
            st.dataframe(pd.DataFrame(lignes), hide_index=True, width="stretch",
                         column_config={
                             "Probabilité de gagner": PCT,
                             "Encore en vie après": PCT,
                             "Espérance cumulée": st.column_config.NumberColumn(
                                 format=SEM),
                         })
            # Trois décimales : avec deux, le produit affiché ne retombait pas sur
            # le pourcentage du tableau, ce qui avait l'air d'une erreur de calcul.
            detail = " × ".join(f"{o.p:.3f}" for _m, o in montre)
            st.caption(
                f"Colonne « encore en vie » : le produit des probabilités depuis "
                f"le début ({detail} pour la dernière ligne). La colonne de droite "
                f"les additionne — c'est l'espérance. En continuant sur les "
                f"{len(plan)} semaines du plan, elle atteint **{exp_weeks:.2f}**.")
        st.markdown(
            "**Pourquoi ça change les décisions.** Une semaine proche pèse plus "
            "lourd qu'une semaine lointaine : si je suis éliminé en semaine 2, "
            "les semaines 10 à 27 ne rapportent rien, quelle que soit leur "
            "qualité. L'optimiseur ne cherche donc pas la meilleure équipe "
            "chaque semaine prise isolément — il peut garder une équipe forte "
            "pour une semaine où rien d'autre ne tient la route, parce que ça "
            "fait monter le total.")
        st.caption(
            "C'est aussi ce que mesure la colonne « Coût » dans les choix de la "
            "semaine : l'espérance perdue en prenant cette équipe-là plutôt que "
            "la meilleure.")


# ── Depuis hier ───────────────────────────────────────────────────────────

def page_depuis_hier():
    if len(snaps) < 2:
        st.info("Il faut au moins deux collectes pour comparer. Reviens après la "
                "prochaine collecte quotidienne.")
    else:
        prev = snaps[1]
        # snapshot= nommé : un argument positionnel de plus a déjà atterri
        # dans `horizon` une fois, ce qui plantait sur un découpage.
        prev_plan, prev_exp, _ = plan_for(v, from_date, used, overrides,
                                          horizon, snapshot=prev)
        st.caption(f"Comparaison entre la collecte du {prev} et celle du {snapshot}.")

        c1, c2 = st.columns(2)
        now_pick = plan[0][1].team if plan and plan[0][1] else "—"
        old_pick = prev_plan[0][1].team if prev_plan and prev_plan[0][1] else "—"
        c1.metric("Pick de la semaine", now_pick)
        c1.caption("Inchangé" if now_pick == old_pick else f"Avant : {old_pick}")
        c2.metric("Espérance", f"{exp_weeks:.2f} sem.",
                  f"{exp_weeks - prev_exp:+.2f} sem.")

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


# ── Le pool ───────────────────────────────────────────────────────────────

def page_pool():
    semaine = this_monday.isoformat()
    joueurs = pk.pool(state)
    proba = {t: o.p for t, o in options.get(this_monday, {}).items()}
    pris = pk.popularity(state, this_monday, since, RES)
    mon_pick = (provisional or {}).get("team") or (current or {}).get("team")

    ui.tiles([
        # Compté ici plutôt qu'avec survivors() : `out` tient déjà compte de
        # mon élimination déduite des résultats, ce que l'état seul ignore.
        ("Survivants", str((0 if out else 1) + len(pk.pool_alive(state, since, RES))),
         "moi comprise" if not out else "je suis éliminée"),
        ("Adversaires en vie", str(len(pk.pool_alive(state, since, RES))),
         f"sur {len(joueurs)} inscrits"),
        ("Équipes prises", str(len(pris)),
         "par les adversaires cette semaine"),
        ("Mon pick", mon_pick or "—",
         f"pris par {pris.get(mon_pick, 0)} autre(s)" if mon_pick else "à choisir"),
    ])

    with ui.panel("registre", "Qui joue quoi cette semaine",
                  "Une ligne par adversaire — ne t'ajoute pas, tu es déjà "
                  "comptée à part. Le statut se déduit tout seul des "
                  "résultats : inutile de suivre qui tombe. Mets « out » ou "
                  "« in » seulement pour ce qu'aucun match ne dira — une "
                  "cotisation impayée, une règle maison."):
        def verdict(j):
            elimine, sem, origine = pk.statut(j, RES, since)
            if not elimine:
                return "en vie" if origine == "manuel" else "en vie"
            return f"éliminé sem. du {sem}" if sem else "éliminé"

        # Choix de la semaine à remplir, en dehors du formulaire pour que le
        # tableau se reconstruise tout de suite quand on change — sinon il
        # faudrait cliquer « Enregistrer » juste pour voir une autre semaine.
        # Séparé de `semaine` (toujours la semaine en cours, this_monday) :
        # le potentiel restant plus bas dépend de `semaine` pour planifier à
        # partir de la semaine SUIVANTE, et doit rester sur la vraie semaine
        # courante même quand on vient corriger un pick oublié.
        semaines_dispo = pk.semaines_tour(since, this_monday)
        semaine_edit = st.selectbox(
            "Semaine à remplir", semaines_dispo, format_func=fr_weekend,
            help="Pour rattraper un pick oublié la semaine d'avant.")
        semaine_edit_iso = semaine_edit.isoformat()
        proba_edit = ({t: o.p for t, o in options.get(semaine_edit, {}).items()}
                      if semaine_edit == this_monday else {})

        lignes = [{
            "Joueur": nom,
            "Pick": pk.pick_registre(j, semaine_edit_iso, RES, since),
            "Statut": j.get("force") or pk.AUTO,
            "Réel": verdict(j),
            "Probabilité": 100 * proba_edit[j["picks"][semaine_edit_iso]]
                           if j["picks"].get(semaine_edit_iso) in proba_edit else None,
            "Équipes déjà prises": ", ".join(sorted(pk.pool_used(state, nom, since))),
        } for nom, j in sorted(joueurs.items())]
        # Dans un formulaire, modifier une case ne reexecute pas le script.
        # Sans lui, chaque frappe relancait tout — relecture de Neon, calcul
        # des probabilites, reconstruction de la grille — et le curseur
        # sautait hors de la case en cours de saisie.
        #
        # Contrepartie assumee : « Reel » et « Probabilite » se calculent,
        # donc elles ne se mettent a jour qu'a l'enregistrement. On saisit
        # seize noms bien plus souvent qu'on ne regarde une probabilite
        # bouger en direct.
        #
        # Probabilite n'est calculable que pour la semaine en cours : les
        # options passees ne couvrent que l'horizon futur, pas l'historique.
        # La colonne reste donc vide pour une semaine passee, sans fausser
        # quoi que ce soit — ce n'est pas l'objectif de ce rattrapage.
        #
        # La cle varie avec la semaine choisie : sinon Streamlit garde l'etat
        # du tableau de la semaine precedemment affichee au lieu de reprendre
        # les picks de la nouvelle semaine.
        with st.form(f"registre_pool_{semaine_edit_iso}", border=False):
            edite = st.data_editor(
                chiffres(pd.DataFrame(lignes,
                                      columns=["Joueur", "Pick", "Statut", "Réel",
                                               "Probabilité", "Équipes déjà prises"]),
                         ["Probabilité"]),
                key=f"registre-{semaine_edit_iso}", num_rows="dynamic",
                hide_index=True, width="stretch",
                column_config={
                    "Pick": st.column_config.SelectboxColumn(
                        f"Pick du {fr_weekend(semaine_edit)}",
                        options=[pk.ELIMINE] + sorted(cm.TEAMS), required=False,
                        help="« Éliminé » se met tout seul après la semaine "
                             "où quelqu'un sort : rien à remplir."),
                    "Statut": st.column_config.SelectboxColumn(
                        "Statut", options=[pk.AUTO, pk.DEHORS, pk.DEDANS],
                        required=False,
                        help="auto = déduit des résultats ; out = éliminé quoi "
                             "qu'en disent les matchs ; in = maintenu en vie"),
                    "Réel": st.column_config.TextColumn(
                        "Réel", disabled=True,
                        help="Ce que le site retient, une fois le manuel appliqué"),
                    "Probabilité": st.column_config.NumberColumn(
                        format="%.1f %%", disabled=True,
                        help="Calculée, pas modifiable — disponible seulement "
                             "pour la semaine en cours"),
                    "Équipes déjà prises": st.column_config.TextColumn(disabled=True),
                })
            if st.form_submit_button("Enregistrer le registre",
                                     type="primary", width="stretch",
                                     disabled=not PEUT_ECRIRE):
                # « Éliminé » n'est qu'un affichage : jamais écrit dans
                # picks.json comme si c'était une équipe.
                records = edite.to_dict("records")
                for r in records:
                    if r.get("Pick") == pk.ELIMINE:
                        r["Pick"] = ""
                nouveau = pk.merge_pool_week(joueurs, records, semaine_edit_iso)
                save_state(pk.set_pool(state, nouveau),
                           f"Registre du pool ({len(nouveau)} joueur(s))")

    if pris:
        with ui.panel("popularite", "Popularité des équipes cette semaine",
                      "Une équipe que beaucoup prennent ne te démarque pas : si "
                      "elle gagne, vous restez tous en vie. Si elle perd, vous "
                      "tombez tous. C'est en prenant autre chose qu'on finit "
                      "seul debout."):
            rangs = sorted(pris.items(), key=lambda kv: (-kv[1], kv[0]))
            st.dataframe(pd.DataFrame([{
                "": ui.logo(t), "Équipe": t, "Adversaires": n,
                "Probabilité": 100 * proba[t] if t in proba else None,
                "Aussi mon pick": "oui" if t == mon_pick else "",
            } for t, n in rangs]), hide_index=True, width="stretch",
                column_config={"": st.column_config.ImageColumn("", width="small"),
                               "Probabilité": PCT})
    elif joueurs:
        st.caption("Aucun pick saisi pour cette semaine.")

    # ── Potentiel restant ─────────────────────────────────────────────────
    # Une équipe brûlée ne revient pas. Deux personnes à égalité aujourd'hui
    # ne valent donc pas la même chose : celle qui a dépensé ses gros clubs a
    # moins de marge pour la suite. On remesure, pour chacune, le meilleur
    # plan encore atteignable avec ce qu'il lui reste.
    # Le pick de la semaine est DÉJÀ JOUÉ pour chacun : son plan futur commence
    # donc la semaine suivante, pas celle-ci. Planifier à partir de cette
    # semaine leur attribuait une équipe qu'ils ne peuvent plus prendre, ce qui
    # gonflait leur potentiel et inversait le classement.
    suivante = this_monday + dt.timedelta(days=7)
    horizon_suite = max(1, horizon - 1)

    def evalue(brulees, equipe):
        """(potentiel après cette semaine, survie totale).

        Survie totale = p(gagner cette semaine) × (1 + potentiel après), la
        même formule que pour comparer les choix d'une semaine : il faut passer
        la semaine avant de profiter de ce qu'on a gardé.
        """
        apres = plan_for(v, suivante, tuple(sorted(brulees)), overrides,
                         horizon_suite)[1]
        p = proba.get(equipe)
        return apres, (None if p is None else p * (1 + apres))

    mes_brulees = pk.my_used(picks, since)
    _, ma_survie = evalue(mes_brulees, mon_pick)

    gens = [("Moi", mes_brulees, not out, mon_pick)]
    gens += [(nom, pk.pool_used(state, nom, since),
              nom in pk.pool_alive(state, since, RES),
              joueurs[nom]["picks"].get(semaine))
             for nom in sorted(joueurs)]

    lignes = []
    for nom, brulees, vivant, equipe in gens:
        apres, total = evalue(brulees, equipe)
        lignes.append({
            # Barré pour une personne éliminée : sinon son nom se mélange
            # visuellement à ceux encore en vie dans la même colonne.
            "Joueur": nom if vivant else ui.barre(nom),
            "En vie": "oui" if vivant else "non",
            "Pick": equipe or "—",
            "Gagne cette semaine": 100 * proba[equipe] if equipe in proba else None,
            "Potentiel après": apres,
            "Survie totale": total,
            "Écart vs moi": (None if total is None or ma_survie is None
                             else total - ma_survie),
            "Brûlées": ", ".join(sorted(brulees)) or "—",
        })
    # Les éliminés tout en bas, quel que soit leur potentiel restant : un
    # grand « Survie totale » théorique n'y change rien, ils ne sont plus en
    # course. Chaque groupe reste trié par survie totale, comme avant.
    lignes.sort(key=lambda r: (r["En vie"] == "non",
                               -(r["Survie totale"] if r["Survie totale"]
                                 is not None else -1), r["Joueur"]))

    with ui.panel("potentiel", "Chances de survie de chacun",
                  "Deux chiffres à ne pas confondre : ce qu'on garde pour plus "
                  "tard, et ce qu'on vaut une fois le risque de cette semaine "
                  "pris en compte."):
        st.dataframe(chiffres(pd.DataFrame(lignes),
                              ["Gagne cette semaine", "Potentiel après",
                               "Survie totale", "Écart vs moi"]),
                     hide_index=True, width="stretch",
                     column_config={
                         "Gagne cette semaine": PCT,
                         "Potentiel après": st.column_config.NumberColumn(
                             format=SEM,
                             help="Meilleur plan atteignable À PARTIR DE LA "
                                  "SEMAINE PROCHAINE, avec ses équipes "
                                  "restantes. Garder un gros club le fait "
                                  "monter."),
                         "Survie totale": st.column_config.NumberColumn(
                             format=SEM,
                             help="p(gagner cette semaine) × (1 + potentiel "
                                  "après). Il faut passer la semaine avant de "
                                  "profiter de ce qu'on a gardé."),
                         "Écart vs moi": st.column_config.NumberColumn(
                             format=SEM_SIGNE,
                             help="Positif = mieux placé que moi"),
                     })
        st.markdown(
            "**Garder un gros club vaut quelque chose — mais moins qu'on "
            "pense.** Prendre une équipe faible pour se réserver Colorado fait "
            "bien monter la colonne « Potentiel après ». Il faut pourtant "
            "survivre à cette semaine-là d'abord, et un pile ou face coûte "
            "presque toujours plus que la marge gagnée.")
        st.caption(
            "Le pick de la semaine est déjà joué : le potentiel se calcule "
            "donc à partir de la semaine suivante. Une personne sans pick "
            "saisi n'a pas de survie totale — remplis sa ligne dans le "
            "registre au-dessus.")



# ── Navigation ─────────────────────────────────────────────────────────────
# st.navigation plutôt qu'une rangée d'onglets ou un bouton radio : ça donne
# une vraie navigation de site — des liens groupés dans la barre latérale,
# une URL par section (donc le bouton Retour du navigateur fonctionne), et le
# chevron de Streamlit ouvre et ferme le panneau.
# Comme avec le menu précédent, une seule section s'exécute par affichage,
# contrairement à st.tabs qui les exécutait toutes.

st.navigation({
    "Le pick": [
        st.Page(page_pick, title="Pick de la semaine",
                icon=":material/sports_hockey:", url_path="pick", default=True),
        st.Page(page_plan, title="Plan complet",
                icon=":material/calendar_month:", url_path="plan"),
        st.Page(page_pool, title="Le pool",
                icon=":material/groups:", url_path="pool"),
    ],
    "Analyse": [
        st.Page(page_carte, title="Carte des matchups",
                icon=":material/grid_on:", url_path="carte"),
        st.Page(page_evolution, title="Évolution des probabilités",
                icon=":material/show_chart:", url_path="evolution"),
        st.Page(page_forme, title="Équipes en forme",
                icon=":material/trending_up:", url_path="forme"),
    ],
    "Fiabilité": [
        st.Page(page_precision, title="Précision des sources",
                icon=":material/target:", url_path="precision"),
        st.Page(page_depuis_hier, title="Depuis hier",
                icon=":material/update:", url_path="depuis-hier"),
    ],
}, expanded=True).run()
