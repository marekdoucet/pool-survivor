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
            "optimize", "store", "picks", "page", "ui")   # page avant ui, qui s'en sert


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

# Langue du visiteur. Gardée dans st.session_state (une par visiteur) et
# initialisée depuis l'adresse : la vitrine anglaise ouvre l'app avec
# ?lang=en. Doit précéder tout texte affiché, d'où sa place tout en haut.
if "lang" not in st.session_state:
    st.session_state["lang"] = "en" if st.query_params.get("lang") == "en" else "fr"
tr = ui.t
EN = ui.langue() == "en"

SOURCES = {"consensus": "Consensus", "market": tr("Casinos", "Sportsbooks"),
           "kalshi": "Kalshi", "moneypuck": "MoneyPuck", "dimers": "Dimers",
           "puckcast": "Puckcast"}
# Palette catégorielle de référence (8 couleurs), dans un ordre fixe :
# la couleur suit la source, ou l'équipe (au plus 8 équipes comparées à la fois).
# Pas calibrés pour fond sombre — mêmes teintes et même ordre que la version
# fond clair, mais re-choisis pour la surface sombre, pas éclaircis au hasard.
TEAM_COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500",
               "#d55181", "#008300", "#9085e9", "#e66767"]
COLORS = TEAM_COLORS[:len(SOURCES)]

st.set_page_config(page_title=tr("Pool survivor NHL", "NHL Survivor Pool"),
                   page_icon="🏒", layout="wide")
ui.inject_css()

# Le titre est dessiné ici, avant toute préparation de données. Sans ça,
# l'écran reste vide pendant que la base se reconstruit, que picks.json est
# lu sur GitHub et que le plan s'optimise — sur Streamlit Cloud, un réveil
# après inactivité prend des minutes, et un écran blanc ne se distingue pas
# d'une panne. Mieux vaut montrer quelque chose qui vit.
def basculer_langue():
    st.session_state["lang"] = "fr" if EN else "en"


_titre, _bouton = st.columns([6, 1], vertical_alignment="center")
_titre.title(tr("🏒 Pool survivor NHL 2026-27", "🏒 NHL Survivor Pool 2026-27"))
_bouton.button("FR" if EN else "ENG", key="langue", on_click=basculer_langue,
               help=tr("Switch to English", "Passer en français"), width="stretch")
# Dans l'adresse aussi : un lien copié s'ouvre dans la même langue.
if EN:
    st.query_params["lang"] = "en"
elif "lang" in st.query_params:
    del st.query_params["lang"]
STATUT = st.empty()
STATUT.caption(tr("Chargement des données…", "Loading data…"))

PCT = st.column_config.NumberColumn(format="%.1f %%")   # valeurs déjà × 100
# L'espérance n'est PAS un pourcentage : c'est un nombre de semaines survécues.
# Sans unité, à côté de colonnes en %, 2.34 se lit de travers.
SEM = tr("%.2f sem.", "%.2f wk")
SEM_SIGNE = tr("%+.2f sem.", "%+.2f wk")
SEMAINES = tr("sem.", "wk")             # même unité, dans le texte


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
    with st.spinner(tr("Reconstruction de la base depuis data/…",
                       "Rebuilding the database from data/…")):
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
def premiers_matchs(version):
    """{date ISO: début du premier match de ce jour, en heure de l'Est}."""
    with connect() as c:
        rows = c.execute("SELECT game_date, MIN(start_utc) FROM schedule "
                         "GROUP BY game_date").fetchall()
    return {g: dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(cm.TZ)
            for g, s in rows if s}


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


STATUT_GARDIEN = ({"Expected": "expected", "Confirmed": "confirmed"} if EN
                  else {"Expected": "attendu", "Confirmed": "confirmé"})


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


MOIS = (["Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.",
         "Oct.", "Nov.", "Dec."] if EN else
        ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.",
         "oct.", "nov.", "déc."])


JOURS = (["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
          "Sunday"] if EN else
         ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"])


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
    """'3 oct.' ; en anglais 'Oct. 3'."""
    return f"{MOIS[d.month - 1]} {d.day}" if EN else f"{d.day} {MOIS[d.month - 1]}"


def fr_weekend(monday):
    """Lundi → '19-20 déc.' (ou '31 oct.-1 nov.') ; 'Dec. 19-20' en anglais."""
    sat, sun = monday + dt.timedelta(days=5), monday + dt.timedelta(days=6)
    if sat.month == sun.month:
        return (f"{MOIS[sun.month - 1]} {sat.day}-{sun.day}" if EN
                else f"{sat.day}-{sun.day} {MOIS[sun.month - 1]}")
    return f"{fr_date(sat)}-{fr_date(sun)}"


def fr_day(iso):
    """'2026-10-03' → 'samedi 3 oct.' ; 'Saturday Oct. 3' en anglais."""
    d = dt.date.fromisoformat(iso)
    return f"{JOURS[d.weekday()]} {fr_date(d)}"


def heure(moment):
    """20 h 30 ; 8:30 PM en anglais."""
    if EN:
        return f"{(moment.hour - 1) % 12 + 1}:{moment.minute:02d} "                f"{'AM' if moment.hour < 12 else 'PM'}"
    return f"{moment.hour} h {moment.minute:02d}"


def serie(team_form_):
    """« V3 » / « D2 » ; « W3 » / « L2 » en anglais."""
    s = team_form_.streak_label
    return s.replace("V", "W").replace("D", "L") if EN else s


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
        st.session_state["_err_connexion"] = tr(
            "Le paquet Authlib est absent de l'environnement. Il est dans "
            "requirements.txt : fais « Reboot app » pour que Streamlit Cloud "
            "reconstruise l'environnement.",
            "The Authlib package is missing from the environment. It is in "
            "requirements.txt: use \"Reboot app\" so Streamlit Cloud rebuilds "
            "the environment."
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
        st.sidebar.error(tr("Connecte-toi pour enregistrer.", "Sign in to save."))
        return
    try:
        if remote:
            remote.save(new_state, message)
        else:
            pk.save(new_state, PICKS_PATH)
    except Exception as e:   # conflit, jeton expiré, réseau…
        st.sidebar.error(tr(f"Non enregistré : {e}", f"Not saved: {e}"))
        return
    st.rerun()


def erreur_pick(e):
    """Le refus de picks.add, dans la langue du visiteur.

    picks.py reste en français (c'est de la logique, pas de l'affichage) : on
    traduit ses deux messages connus et on laisse passer tout autre tel quel.
    """
    msg = str(e)
    if EN:
        msg = (msg.replace(" a déjà été utilisée dans ce tour",
                           " has already been used this round")
                  .replace("équipe inconnue : ", "unknown team: "))
    return msg


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
# Le plan vise la fin probable du tour (voir pk.horizon). Sans redépart, le
# tour a commencé avec la saison : sa première semaine vient du calendrier.
_premiers = premiers_matchs(VERSION) if VERSION else {}
_debut_saison = (op.week_start(dt.date.fromisoformat(min(_premiers)))
                 if _premiers else None)
horizon = pk.horizon(since or _debut_saison, today)
overrides = tuple(sorted(day_overrides.items()))   # clé de cache
all_days = pick_days_for(VERSION, overrides) if VERSION else {}
this_monday = op.week_start(today)
# Le verrou du pool : 30 min avant le premier match de la journée de pick de
# CETTE semaine — elle suit le choix forcé dans la barre latérale s'il y en a
# un. Une heure précise, comparée à l'heure actuelle, pas à la date.
_jour_pick = all_days.get(this_monday)
VERROU = pk.verrou(this_monday, _jour_pick.days if _jour_pick else (),
                   premiers_matchs(VERSION))
OUVERT = dt.datetime.now(cm.TZ) < VERROU
from_date, used_set, provisional = pk.planning(picks, today, since, ouvert=OUVERT)
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
            st.caption(tr(f"Connecté : {auth.nom(st.user)}",
                          f"Signed in: {auth.nom(st.user)}"))
            st.button(tr("Se déconnecter", "Sign out"), width="stretch",
                      on_click=st.logout)
        else:
            st.button(tr("Se connecter avec Google", "Sign in with Google"),
                      type="primary", width="stretch", on_click=connexion)
            if st.session_state.get("_err_connexion"):
                st.error(st.session_state["_err_connexion"])
            st.caption(tr("Tu peux tout consulter sans compte. "
                          "La connexion sert à enregistrer.",
                          "You can browse everything without an account. "
                          "Signing in lets you save."))

        # Où va ce qu'on enregistre. Affiché parce qu'une erreur de stockage
        # est invisible autrement : on croit avoir sauvegardé, et non.
        if isinstance(remote, depot.PoolNeon):
            st.caption(tr("Ton pool est enregistré dans la base Neon.",
                          "Your pool is saved in the Neon database."))
        elif isinstance(remote, depot.PoolAnonyme):
            st.caption(tr("Connecte-toi pour avoir ton propre pool.",
                          "Sign in to get your own pool."))
        elif remote:
            st.caption(tr(f"Pool partagé, dans GitHub ({remote.repo})",
                          f"Shared pool, in GitHub ({remote.repo})"))

        st.divider()

    st.header(tr("Mes picks", "My picks"))
    if not tour:
        st.caption(tr("Aucun pick dans ce tour.", "No picks this round."))
    # Par date, la plus ancienne en haut, celle de cette semaine en bas. Sans
    # tri, la liste suivait l'ordre d'enregistrement — et corriger un pick
    # passé le retire puis le rajoute à la fin, sous celui de cette semaine.
    for p in sorted(tour, key=lambda p: p["week"]):
        col1, col2 = st.columns([4, 1])
        # La fin de semaine (« 26-27 sept. ») plutôt que le lundi ISO : c'est
        # la forme du reste du site, le jour où le pick se joue, et elle tient
        # sur une ligne — « semaine du 2026-09-28 » coupait en deux.
        fds = fr_weekend(dt.date.fromisoformat(p["week"]))
        col1.markdown(f"**{p['team']}** — {fds}")
        # Une confirmation : le ✕ supprimait d'un seul clic. Or supprimer un
        # pick passé rend l'équipe de nouveau disponible au plan, et peut même
        # remettre en vie si ce pick avait perdu. Un simple bouton qui fait
        # apparaître la question, pas un st.popover : celui-ci ajoute sa propre
        # flèche « ouvrir » au libellé, illisible dans une colonne si étroite.
        demande = f"del-demande-{p['week']}"
        if col2.button("✕", key=f"del-{p['week']}",
                       help=tr("Retirer ce pick", "Remove this pick"),
                       disabled=not PEUT_ECRIRE):
            st.session_state[demande] = True
        if st.session_state.get(demande):
            st.caption(tr(f"Supprimer {p['team']}, fin de semaine du {fds} ? "
                          f"L'équipe redevient disponible dans le plan.",
                          f"Remove {p['team']}, weekend of {fds}? "
                          f"The team becomes available again in the plan."))
            oui, non = st.columns(2)
            if oui.button(tr("Supprimer", "Remove"), type="primary",
                          key=f"del-ok-{p['week']}"):
                st.session_state.pop(demande, None)
                save_picks(pk.remove(picks, dt.date.fromisoformat(p["week"])),
                           tr(f"Pick retiré : {p['team']} ({fds})",
                              f"Pick removed: {p['team']} ({fds})"))
            if non.button(tr("Garder", "Keep"), key=f"del-non-{p['week']}"):
                st.session_state.pop(demande, None)
                st.rerun()

    # Corriger ou remettre un pick d'une semaine passée. Un menu, pas une page :
    # « Enregistrer mon pick » ne connaît que la semaine en cours, donc un pick
    # passé supprimé ou oublié ne pouvait plus être saisi nulle part.
    passees = pk.semaines_tour(since, this_monday)[1:]
    if passees:
        with st.popover(tr("Ajouter ou corriger un pick passé",
                           "Add or fix a past pick"), width="stretch",
                        disabled=not PEUT_ECRIRE):
            sem = st.selectbox(tr("Fin de semaine", "Weekend"), passees,
                               key="corr-sem", format_func=fr_weekend)
            actuel = next((p["team"] for p in picks if p["week"] == sem.isoformat()), None)
            equipes = sorted(cm.TEAMS)
            eq = st.selectbox(tr("Équipe", "Team"), equipes, key=f"corr-eq-{sem}",
                              index=equipes.index(actuel) if actuel in equipes else None,
                              placeholder=tr("Choisir une équipe", "Choose a team"))
            st.caption(tr(f"Enregistré actuellement : {actuel or 'rien'}.",
                          f"Currently saved: {actuel or 'nothing'}."))
            if st.button(tr("Enregistrer", "Save"), type="primary", key="corr-ok",
                         disabled=eq is None or eq == actuel):
                # Mêmes règles que partout : une équipe une seule fois par tour.
                try:
                    nouveaux = pk.add(picks, sem, eq, since=since)
                except ValueError as e:
                    st.error(erreur_pick(e))
                else:
                    save_picks(nouveaux,
                               tr(f"Pick corrigé : {eq} ({fr_weekend(sem)})"
                                  + (f", remplace {actuel}" if actuel else ""),
                                  f"Pick fixed: {eq} ({fr_weekend(sem)})"
                                  + (f", replaces {actuel}" if actuel else "")))

    # Annuler la dernière modification — pick, registre, redépart, n'importe
    # quoi passé par save_state. Seulement avec Neon : c'est là que vit
    # l'historique. `derniere` est lu par load(), sans requête de plus.
    derniere = getattr(remote, "derniere", None)
    if derniere and hasattr(remote, "annuler"):
        message, quand = derniere
        with st.popover(tr("↶ Annuler la dernière modification",
                           "↶ Undo the last change"), width="stretch",
                        disabled=not PEUT_ECRIRE):
            st.write(tr(f"Revenir à l'état d'avant : « {message} »",
                        f"Go back to the state before: \"{message}\""))
            if isinstance(quand, dt.datetime):
                q = quand.astimezone(cm.TZ)
                st.caption(tr(f"Modification faite le {fr_day(q.date().isoformat())} "
                              f"à {heure(q)}. Tu peux annuler plusieurs "
                              f"fois de suite pour remonter plus loin.",
                              f"Change made on {fr_day(q.date().isoformat())} "
                              f"at {heure(q)}. You can undo several times "
                              f"in a row to go further back."))
            if st.button(tr("Annuler cette modification", "Undo this change"),
                         type="primary", key="annuler-ok"):
                try:
                    remote.annuler()
                except Exception as e:   # réseau, base indisponible…
                    st.error(tr(f"Pas annulé : {e}", f"Not undone: {e}"))
                else:
                    st.rerun()

    st.subheader(tr("Tour en cours", "Current round"))
    archive = len(picks) - len(tour)
    if since:
        st.caption(tr(f"Reparti la semaine du {since}."
                      + (f" {archive} pick(s) des tours précédents en archive."
                         if archive else ""),
                      f"Restarted the week of {since}."
                      + (f" {archive} pick(s) from earlier rounds archived."
                         if archive else "")))
    else:
        st.caption(tr("Premier tour de la saison.", "First round of the season."))
    with st.popover(tr("Faire repartir le pool", "Restart the pool"), width="stretch"):
        st.markdown(tr("Quand il ne reste **qu'une personne en vie**, le pool "
                       "recommence à partir de la semaine en cours : toutes les "
                       "équipes redeviennent disponibles.",
                       "When **only one person is left alive**, the pool "
                       "starts over from the current week: every team becomes "
                       "available again."))
        st.caption(tr("Rien n'est effacé — les picks des tours précédents restent "
                      "dans le fichier et l'opération est annulable.",
                      "Nothing is deleted — picks from earlier rounds are kept "
                      "and the restart can be undone."))
        depart = st.date_input(tr("Repartir à partir de la semaine du",
                                  "Restart from the week of"),
                               value=this_monday, format="YYYY-MM-DD")
        lundi = op.week_start(depart)
        if lundi != depart:
            st.caption(tr(f"Ramené au lundi de cette semaine : {lundi}.",
                          f"Moved to the Monday of that week: {lundi}."))
        if st.button(tr("Confirmer le redépart", "Confirm the restart"),
                     type="primary", width="stretch", disabled=not PEUT_ECRIRE):
            save_state(pk.reset(state, lundi),
                       tr(f"Pool reparti à la semaine du {lundi}",
                          f"Pool restarted the week of {lundi}"))
    if since:
        if st.button(tr(f"Annuler le redépart du {since}",
                        f"Undo the restart of {since}"), width="stretch",
                     disabled=not PEUT_ECRIRE):
            save_state(pk.undo_reset(state, since),
                       tr(f"Redépart du {since} annulé",
                          f"Restart of {since} undone"))
    # Plus de bouton « Je suis éliminé » : le site le déduit du résultat de
    # mon pick. Reste l'annulation, pour les cas qu'aucun match ne dira.
    if out:
        st.caption(tr(f"Éliminé depuis la semaine du {out}"
                      + (" (déduit du résultat)" if _origine_out == "auto" else "")
                      + ".",
                      f"Eliminated since the week of {out}"
                      + (" (from the result)" if _origine_out == "auto" else "")
                      + "."))
        if st.button(tr("Je suis encore en vie", "I'm still alive"), width="stretch",
                     help=tr("Passe outre : règle maison, erreur, litige",
                             "Override: house rule, mistake, dispute"),
                     disabled=not PEUT_ECRIRE):
            save_state(pk.back_in(state),
                       tr("Élimination annulée", "Elimination undone"))

    st.subheader(tr("Journée de pick", "Pick day"))
    ties = [m for m, d in all_days.items() if d.how == "égalité" and m >= op.week_start(today)]
    st.caption(tr("Par défaut : la journée de fin de semaine qui a le plus de matchs. "
                  "Change-la ici si l'organisateur en décide autrement.",
                  "By default: the weekend day with the most games. "
                  "Change it here if the organizer decides otherwise."))
    if ties:
        st.warning(tr("Égalité samedi/dimanche à confirmer : "
                      + ", ".join(f"fin de semaine du {fr_weekend(m)}" for m in ties)
                      + ". En attendant, les deux jours sont permis.",
                      "Saturday/Sunday tie to confirm: "
                      + ", ".join(f"weekend of {fr_weekend(m)}" for m in ties)
                      + ". Until then, both days are allowed."))
    future = [m for m in all_days if m >= op.week_start(today)]
    if future:
        with st.form("jour"):
            wk = st.selectbox(
                tr("Semaine du", "Week of"), future,
                format_func=lambda m: f"{fr_date(m)} — " + (
                    tr(" ou ", " or ").join(fr_day(d) for d in all_days[m].days)
                    or tr("aucun match", "no games"))
                    + (tr(" (choisi)", " (chosen)") if all_days[m].how == "choisi" else ""))
            pd_ = all_days[wk]
            sat = (wk + dt.timedelta(days=5)).isoformat()
            sun = (wk + dt.timedelta(days=6)).isoformat()
            choices = {tr("Automatique (le plus de matchs)",
                          "Automatic (most games)"): None,
                       tr(f"{fr_day(sat)} ({pd_.sat_games} matchs)",
                          f"{fr_day(sat)} ({pd_.sat_games} games)"): sat,
                       tr(f"{fr_day(sun)} ({pd_.sun_games} matchs)",
                          f"{fr_day(sun)} ({pd_.sun_games} games)"): sun}
            choice = st.radio(tr("Journée", "Day"), list(choices))
            if st.form_submit_button(tr("Enregistrer la journée", "Save the day"),
                                     disabled=not PEUT_ECRIRE):
                day = choices[choice]
                new_days = pk.set_day(day_overrides, wk,
                                      dt.date.fromisoformat(day) if day else None)
                save_state({**state, "days": new_days},
                           tr(f"Journée de pick, semaine du {wk} : "
                              + (fr_day(day) if day else "automatique"),
                              f"Pick day, week of {wk}: "
                              + (fr_day(day) if day else "automatic")))
        chosen = {m: d for m, d in all_days.items() if d.how == "choisi" and m in future}
        if chosen:
            st.caption(tr("Journées choisies : ", "Chosen days: ") + ", ".join(
                tr(f"sem. du {fr_date(m)} → {fr_day(d.days[0])}",
                   f"week of {fr_date(m)} → {fr_day(d.days[0])}")
                for m, d in chosen.items()))


# ── En-tête ───────────────────────────────────────────────────────────────

v = VERSION
snaps = snapshots(v) if v else []
if not snaps:
    st.error(tr("Aucune donnée. Lance d'abord `python daily.py`.",
                "No data. Run `python daily.py` first."))
    st.stop()

if out:
    # Coupé ici, avant plan_for : l'optimisation est de loin le plus gros
    # calcul de la page, et il n'y a plus rien à planifier une fois éliminé.
    with ui.panel("elimine", tr("Tu es éliminé", "You are eliminated"),
                  tr(f"Depuis la semaine du {out}. Aucun plan n'est calculé "
                     f"jusqu'au prochain redépart.",
                     f"Since the week of {out}. No plan is calculated "
                     f"until the next restart.")):
        st.markdown(tr(
            "Le pool recommence quand il ne reste **qu'une personne en vie** : "
            "toutes les équipes redeviennent alors disponibles, et le plan "
            "repart de la semaine en cours.",
            "The pool starts over when **only one person is left alive**: "
            "every team becomes available again, and the plan restarts from "
            "the current week."))
        st.markdown(tr("Dans la barre latérale : **Faire repartir le pool** quand "
                       "ce moment arrive, ou **Je suis encore en vie** si c'est "
                       "une fausse manœuvre.",
                       "In the sidebar: **Restart the pool** when that moment "
                       "comes, or **I'm still alive** if this is a mistake."))
        if tour:
            st.caption(tr("Tes picks de ce tour : ", "Your picks this round: ")
                       + ", ".join(tr(f"{p['team']} (sem. du {p['week']})",
                                      f"{p['team']} (week of {p['week']})")
                                   for p in tour))
    st.stop()

plan, exp_weeks, snapshot = plan_for(v, from_date, used, overrides, horizon)
hist = history(v)
latest = hist[hist.snapshot == snapshot]
counts = latest.groupby("source").size()
STATUT.caption(
    tr(f"Données du {snapshot} · ", f"Data from {snapshot} · ")
    + " · ".join(tr(f"{SOURCES[s]} : {counts.get(s, 0)} matchs",
                    f"{SOURCES[s]}: {counts.get(s, 0)} games")
                 for s in SOURCES if s != "consensus")
    + tr(f" · {len(used)} équipe(s) utilisée(s)", f" · {len(used)} team(s) used")
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
        st.success(tr(f"Pick verrouillé pour cette semaine : **{current['team']}**. "
                      f"Le plan reprend la semaine du {from_date}.",
                      f"Pick locked for this week: **{current['team']}**. "
                      f"The plan resumes the week of {from_date}."))
    elif this_monday in options:
        week_opts = sorted(options[this_monday].values(), key=lambda o: -o.p)
        teams_ = [o.team for o in week_opts]
        # Ce que le site retient : le pick déjà enregistré s'il y en a un,
        # sinon la recommandation. C'est CE choix qui se verrouille au verrou
        # (30 min avant le premier match de la journée de pick), qu'on y
        # retouche ou non.
        retenu = (provisional or {}).get("team")
        if retenu is None and plan and plan[0][1] is not None:
            retenu = plan[0][1].team
        idx = teams_.index(retenu) if retenu in teams_ else 0
        labels = [f"{o.team} {match_label(o)} — {o.p:.1%}" for o in week_opts]
        with ui.panel("choisir", tr("Enregistrer mon pick", "Save my pick"),
                      tr(f"Modifiable jusqu'au "
                         f"{fr_day(VERROU.date().isoformat())} à "
                         f"{heure(VERROU)} (heure de l'Est), "
                         f"30 minutes avant le premier match de la journée de pick. "
                         f"Passé ce délai, c'est l'équipe affichée ici qui est "
                         f"retenue.",
                         f"Can be changed until "
                         f"{fr_day(VERROU.date().isoformat())} at "
                         f"{heure(VERROU)} (Eastern time), "
                         f"30 minutes before the first game of the pick day. "
                         f"After that, the team shown here is the one that "
                         f"counts.")):
            # Hors formulaire, exprès : le menu relance l'affichage à chaque
            # changement, donc la carte dessous montre tout de suite l'équipe
            # qu'on est en train de considérer, avant même d'enregistrer.
            new = st.selectbox(tr(f"Équipe pour la fin de semaine du "
                                  f"{fr_weekend(this_monday)}",
                                  f"Team for the weekend of "
                                  f"{fr_weekend(this_monday)}"), range(len(week_opts)),
                               index=idx, format_func=lambda i, lb=labels: lb[i])
            choisi = week_opts[new]
            deja = provisional and provisional["team"] == choisi.team
            st.caption(tr("C'est ton pick enregistré.", "This is your saved pick.")
                       if deja
                       else (tr("Pas encore enregistré — clique pour le confirmer.",
                                "Not saved yet — click to confirm it.")
                             if provisional else
                             tr("Rien d'enregistré pour cette semaine.",
                                "Nothing saved for this week.")))
            if st.button(tr("Remplacer mon pick", "Replace my pick") if provisional
                         else tr("Enregistrer mon pick", "Save my pick"),
                         type="primary", width="stretch",
                         disabled=bool(deja) or not PEUT_ECRIRE):
                save_picks(pk.add(picks, this_monday, choisi.team, choisi.game_date,
                                  since=since),
                           tr(f"Pick {'modifié' if provisional else 'enregistré'} : "
                              f"{choisi.team} ({fr_weekend(this_monday)})",
                              f"Pick {'changed' if provisional else 'saved'}: "
                              f"{choisi.team} ({fr_weekend(this_monday)})"))
        # La carte de l'équipe choisie. C'est elle qui doit donner sa couleur
        # au fond de la page, pas la recommandation : le site reflète MON
        # choix. Comme ui.hero() pose la lueur lui-même et que la
        # recommandation est dessinée après, on repose la bonne teinte à la
        # fin (voir plus bas) — une feuille de style plus tardive l'emporte.
        ui.hero(choisi.team,
                tr("Mon pick", "My pick") + (tr(" · enregistré", " · saved") if deja
                                             else tr(" · à confirmer", " · to confirm")),
                tr(f"contre {ui.name(choisi.opponent)} · ",
                   f"vs {ui.name(choisi.opponent)} · ")
                + (tr("à domicile", "at home") if choisi.home
                   else tr("à l'étranger", "on the road"))
                + f" · {fr_day(choisi.game_date)}",
                choisi.p, players(v).get(choisi.team))

    if not plan or plan[0][1] is None:
        st.warning(tr("Aucun match disponible pour la prochaine semaine.",
                      "No game available for next week."))
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
                    tr(f"Pick recommandé · fin de semaine du {fr_weekend(monday)}",
                       f"Recommended pick · weekend of {fr_weekend(monday)}"),
                    tr(f"contre {ui.name(best.opponent)} · ",
                       f"vs {ui.name(best.opponent)} · ")
                    + (tr("à domicile", "at home") if best.home
                       else tr("à l'étranger", "on the road"))
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
            txt = tr(" ou ", " or ").join(fr_day(d) for d in pd_week.days)
            is_sat = pd_week.days[0] == (monday + dt.timedelta(days=5)).isoformat()
            n_games = pd_week.sat_games if is_sat else pd_week.sun_games
            if pd_week.how == "égalité":
                st.warning(tr(f"Journée de pick : **{txt}** — égalité "
                              f"({pd_week.sat_games} matchs chacun), à confirmer dans la "
                              f"barre latérale.",
                              f"Pick day: **{txt}** — tie "
                              f"({pd_week.sat_games} games each), to confirm in the "
                              f"sidebar."))
            else:
                st.info(tr(f"Journée de pick : **{txt}** ({n_games} matchs)"
                           + (" — choisie par toi" if pd_week.how == "choisi" else ""),
                           f"Pick day: **{txt}** ({n_games} games)"
                           + (" — chosen by you" if pd_week.how == "choisi" else "")))
        # Blessures du match de l'équipe MONTRÉE comme mon pick (le choix du
        # menu s'il y en a un cette semaine, sinon la recommandation) — pas
        # celle des pastilles ci-dessus, qui suivent toujours la recommandation.
        cible = choisi if (choisi is not None and monday == this_monday) else best
        bl = blesses(v)
        visiteur, local = ((cible.opponent, cible.team) if cible.home
                           else (cible.team, cible.opponent))
        gd = gardiens(v).get((cible.game_date, visiteur, local), {})
        if bl or gd:
            with ui.panel("avant-match",
                          tr(f"Avant le match · {cible.team} contre {cible.opponent}",
                             f"Before the game · {cible.team} vs {cible.opponent}"),
                          tr("Information seulement : les modèles en tiennent déjà "
                             "compte, ce n'est pas ajouté aux probabilités. Un "
                             "gardien titulaire pèse plus que n'importe quel attaquant.",
                             "For information only: the models already account "
                             "for it, it is not added to the probabilities. A "
                             "starting goalie matters more than any forward.")):
                if gd:
                    st.markdown(tr("**Gardiens**", "**Goalies**"))
                    for equipe in (cible.team, cible.opponent):
                        nom, statut, _maj = gd.get(equipe, ("", "", ""))
                        st.markdown(
                            f"{ui.name(equipe)}{tr(' :', ':')} **{nom}** "
                            f"({STATUT_GARDIEN.get(statut, statut.lower() or tr('annoncé', 'announced'))})"
                            if nom else tr(f"{ui.name(equipe)} : pas encore annoncé",
                                           f"{ui.name(equipe)}: not announced yet"))
                    maj = next((m for _n, _s, m in gd.values() if m), "")
                    st.caption(tr("Source : RotoWire, via Puckcast"
                                  + (f" · mis à jour {maj}" if maj else "")
                                  + ". « Attendu » n'est pas une certitude : le "
                                    "titulaire se confirme en général le jour du match.",
                                  "Source: RotoWire, via Puckcast"
                                  + (f" · updated {maj}" if maj else "")
                                  + ". \"Expected\" is not a certainty: the "
                                    "starter is usually confirmed on game day."))
                else:
                    # Puckcast ne publie les gardiens que le jour du match.
                    st.markdown(tr(f"**Gardiens** : annoncés le jour du match "
                                   f"({fr_day(cible.game_date)}).",
                                   f"**Goalies**: announced on game day "
                                   f"({fr_day(cible.game_date)})."))
                if bl:
                    st.markdown(tr("**Blessures**", "**Injuries**"))
                for equipe in (cible.team, cible.opponent) if bl else ():
                    liste = bl.get(equipe, [])
                    if not liste:
                        st.caption(tr(f"{ui.name(equipe)} : aucune blessure déclarée.",
                                      f"{ui.name(equipe)}: no injuries reported."))
                        continue
                    st.markdown(tr(f"**{ui.name(equipe)}** — {len(liste)} blessé(s)",
                                   f"**{ui.name(equipe)}** — {len(liste)} injured"))
                    st.dataframe(pd.DataFrame(
                        [{tr("Joueur", "Player"): j, tr("Poste", "Pos."): p,
                          tr("Statut", "Status"): s, tr("Blessure", "Injury"): b}
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
            (tr("Espérance", "Expected weeks"), f"{exp_montree:.2f} {SEMAINES}",
             tr(f"survécues sur {len(plan)}", f"survived out of {len(plan)}")
             + ("" if mien is None else tr(" avec ce pick", " with this pick"))),
            (tr("Coût du choix", "Cost of the pick"),
             "—" if mien is None else f"{top_e - mien:+.2f} {SEMAINES}",
             tr("d'espérance vs le meilleur choix", "expected weeks vs the best pick")
             if mien is not None
             else tr("aucun pick choisi", "no pick chosen")),
            (tr("Match", "Game"), tr("Domicile", "Home") if vedette.home
             else tr("Visiteur", "Away"),
             tr(f"contre {vedette.opponent} · {fr_day(vedette.game_date)}",
                f"vs {vedette.opponent} · {fr_day(vedette.game_date)}")),
            (tr("Série en cours", "Current streak"),
             serie(team_form[vedette.team]) or tr("aucune", "none"),
             tr(f"de {vedette.team}", f"for {vedette.team}")),
        ] + ([(tr("Adversaires sur ce pick", "Opponents on this pick"),
               str(pris.get(vedette.team, 0)),
               tr(f"sur {len(pk.pool_alive(state, since, RES))} en vie",
                  f"out of {len(pk.pool_alive(state, since, RES))} alive"))]
             if pk.pool(state) else []))
        reasons = []
        for o, _e in alts:
            if o.team == best.team:
                continue
            pm = planned.get(o.team)
            if pm and pm != monday:
                po = options[pm][o.team]
                later_match = tr(f"{po.p:.0%} {'vs' if po.home else '@'} {po.opponent} "
                                 f"la semaine du {fr_date(pm)}",
                                 f"{po.p:.0%} {'vs' if po.home else '@'} {po.opponent} "
                                 f"the week of {fr_date(pm)}")
                if po.p >= o.p:
                    reasons.append(tr(
                        f"{ui.inline(o.team)} : {o.p:.0%} cette semaine, mais "
                        f"{later_match} → le plan la garde pour ce match.",
                        f"{ui.inline(o.team)}: {o.p:.0%} this week, but "
                        f"{later_match} → the plan saves it for that game."))
                else:
                    reasons.append(tr(
                        f"{ui.inline(o.team)} : {o.p:.0%} cette semaine ; le plan la "
                        f"garde pour {later_match}, une semaine où peu "
                        f"d'équipes ont un bon match.",
                        f"{ui.inline(o.team)}: {o.p:.0%} this week; the plan "
                        f"saves it for {later_match}, a week when few "
                        f"teams have a good game."))
            elif not pm:
                reasons.append(tr(
                    f"{ui.inline(o.team)} : {o.p:.0%} cette semaine, mais une autre "
                    f"équipe fait mieux chaque semaine où elle joue → hors plan.",
                    f"{ui.inline(o.team)}: {o.p:.0%} this week, but another "
                    f"team does better every week it plays → not in the plan."))
            if len(reasons) == 4:
                break
        with ui.panel("pourquoi", tr("Pourquoi pas une autre équipe ?",
                                     "Why not another team?"),
                      tr("Le plan est optimisé jusqu'à la fin probable du pool, "
                         "environ 10 semaines après son début, d'un coup : une "
                         "équipe forte cette semaine peut être gardée pour une semaine "
                         "où son match est encore meilleur, ou où aucune autre équipe "
                         "ne fait mieux.",
                         "The plan is optimized all at once up to the pool's likely "
                         "end, about 10 weeks after it starts: a "
                         "strong team this week may be saved for a week when its "
                         "game is even better, or when no other team does "
                         "better.")):
            st.markdown("\n".join(f"- {x}" for x in reasons), unsafe_allow_html=True)

        # Noms de colonnes : ils servent aussi de clés à chiffres() et à
        # column_config, d'où une seule définition par langue.
        PRIS, SEULE, PROBA, ESP, COUT = (
            tr("Pris par", "Picked by"), tr("Seule debout", "Last one standing"),
            tr("Probabilité", "Probability"), tr("Espérance", "Expected weeks"),
            tr("Coût", "Cost"))
        rows = []
        for o, e in alts:
            later = best_later(o.team, monday)
            pm = planned.get(o.team)
            seule = pk.alone_odds(o.team, o.p, rivaux, p_semaine, len(vivants))
            rows.append({
                "": ui.logo(o.team),
                tr("Équipe", "Team"): o.team, PRIS: pris.get(o.team, 0),
                # Colonne omise si elle n'est pas calculable : une colonne
                # entièrement vide n'apprend rien et Streamlit y écrit « None ».
                **({} if seule is None else {SEULE: 100 * seule}),
                tr("Match cette semaine", "Game this week"): match_label(o),
                PROBA: 100 * o.p,
                tr("Meilleur match plus tard", "Best game later"): (
                    f"{later[1].p:.1%} {'vs' if later[1].home else '@'} "
                    f"{later[1].opponent} ({fr_date(later[0])})" if later else "—"),
                tr("Au plan", "In the plan"):
                    tr("cette semaine", "this week") if pm == monday else (
                        tr(f"sem. du {fr_date(pm)}", f"week of {fr_date(pm)}")
                        if pm else tr("hors plan", "not in plan")),
                ESP: e, COUT: top_e - e,
                tr("Série", "Streak"): serie(team_form[o.team]),
            })
        with ui.panel("choix", tr("Tous les choix possibles cette semaine",
                                  "Every possible pick this week"),
                      tr("« Coût » = espérance perdue par rapport au meilleur "
                         "choix. « Seule debout » = ce que ce choix rapporte en "
                         "échange : la chance de survivre pendant que tous les "
                         "adversaires tombent. L'arbitrage se lit entre ces deux "
                         "colonnes.",
                         "\"Cost\" = expected weeks lost compared to the best "
                         "pick. \"Last one standing\" = what this pick gives in "
                         "return: the chance of surviving while every opponent "
                         "falls. The trade-off is read between those two "
                         "columns.")):
            if manquants > 0:
                st.info(tr(
                    f"La colonne **« Seule debout »** apparaîtra quand les "
                    f"{manquants} adversaire(s) en vie sur {len(vivants)} qui "
                    f"n'ont pas encore de pick saisi seront renseignés. Elle "
                    f"mesure la chance qu'ils tombent **tous** : en ignorer un "
                    f"la rendrait bien trop optimiste. Ça se remplit dans "
                    f"« Le pool ».",
                    f"The **\"Last one standing\"** column will appear once the "
                    f"{manquants} opponent(s) alive out of {len(vivants)} who "
                    f"have no pick entered yet are filled in. It measures the "
                    f"chance that they **all** fall: leaving one out would make "
                    f"it far too optimistic. Fill it in under \"The pool\"."))
            st.dataframe(chiffres(pd.DataFrame(rows), [SEULE, PROBA, ESP, COUT, PRIS]),
                         hide_index=True, width="stretch",
                         column_config={
                             "": st.column_config.ImageColumn("", width="small"),
                             PRIS: st.column_config.NumberColumn(
                                 PRIS, format="%d",
                                 help=tr("Adversaires en vie qui prennent cette "
                                         "équipe cette semaine",
                                         "Opponents still alive who pick this "
                                         "team this week")),
                             SEULE: st.column_config.NumberColumn(
                                 format="%.1f %%",
                                 help=tr("Chance de survivre ET de voir tomber "
                                         "tous les adversaires en vie. Une équipe "
                                         "qu'un adversaire prend aussi donne 0 : "
                                         "on ne peut pas se démarquer en la "
                                         "prenant.",
                                         "Chance of surviving AND seeing every "
                                         "opponent still alive fall. A team an "
                                         "opponent also picks gives 0: you can't "
                                         "stand out by picking it.")),
                             PROBA: PCT,
                             ESP: st.column_config.NumberColumn(
                                 format=SEM,
                                 help=tr("Semaines survécues attendues en prenant "
                                         "cette équipe, puis le meilleur plan",
                                         "Weeks expected to be survived by taking "
                                         "this team, then the best plan")),
                             COUT: st.column_config.NumberColumn(
                                 format=SEM,
                                 help=tr("Semaines d'espérance perdues par rapport "
                                         "au meilleur choix",
                                         "Expected weeks lost compared to the "
                                         "best pick")),
                         })


# ── Plan complet ──────────────────────────────────────────────────────────

def page_plan():
    PROBA, SURVIE = tr("Probabilité", "Probability"), tr("Survie cumulée",
                                                         "Cumulative survival")
    rows, alive = [], 1.0
    for monday, o in plan:
        alive *= o.p if o else 0.0
        rows.append({
            "": ui.logo(o.team) if o else "",
            tr("Semaine du", "Week of"): str(monday),
            tr("Journée", "Day"): fr_day(o.game_date) if o else "—",
            tr("Équipe", "Team"): o.team if o else "—",
            tr("Match", "Game"): match_label(o) if o
                else tr("aucune équipe disponible", "no team available"),
            PROBA: 100 * o.p if o else 0.0, SURVIE: 100 * alive,
            # « estimé » vient d'optimize.py : une valeur, traduite à l'affichage.
            "Source": (tr(o.source, "estimated") if o.source == "estimé"
                       else o.source) if o else "",
        })
    jouees = [(o.team, o.p, m) for m, o in plan if o]
    faible = min(jouees, key=lambda x: x[1]) if jouees else ("—", 0.0, from_date)
    skipped = [m for m, d in all_days.items() if not d.days and m >= op.week_start(from_date)]
    if skipped:
        st.info(tr("Semaine(s) sautée(s), aucun match la fin de semaine : ",
                   "Skipped week(s), no games on the weekend: ")
                + ", ".join(tr(f"fin de semaine du {fr_weekend(m)}",
                               f"weekend of {fr_weekend(m)}") for m in skipped))
    ui.tiles([
        (tr("Espérance", "Expected weeks"), f"{exp_weeks:.2f} {SEMAINES}",
         tr(f"survécues sur {len(plan)}", f"survived out of {len(plan)}")),
        (tr("Semaines planifiées", "Weeks planned"), str(len(plan)),
         tr("jusqu'à la fin probable du pool", "until the pool's likely end")),
        # Le maillon faible plutôt que la survie cumulée : le produit de 27
        # probabilités tend vers zéro et n'apprend rien ; la semaine la plus
        # risquée, elle, dit où le plan peut casser.
        (tr("Semaine la plus risquée", "Riskiest week"),
         tr(f"{100 * faible[1]:.0f} %", f"{100 * faible[1]:.0f}%"),
         tr(f"{faible[0]} · semaine du {fr_date(faible[2])}",
            f"{faible[0]} · week of {fr_date(faible[2])}")),
        (tr("Équipes déjà prises", "Teams already used"), str(len(used)),
         tr("retirées du plan", "removed from the plan")),
    ])
    st.caption(tr("« estimé » = au-delà des 49 jours collectés, probabilité tirée du "
                  "modèle de force des équipes. Le plan est recalculé chaque jour.",
                  "\"estimated\" = beyond the 49 days collected, probability taken "
                  "from the team strength model. The plan is recalculated every day."))
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 height=min(38 + 35 * len(rows), 1000), column_config={
                     "": st.column_config.ImageColumn("", width="small"),
                     PROBA: PCT,
                     SURVIE: st.column_config.ProgressColumn(
                         format="%.1f %%", min_value=0.0, max_value=100.0),
                 })


# ── Carte des matchups ────────────────────────────────────────────────────

# Rampe séquentielle bleue de la palette de référence, du plus sombre au plus
# clair : sur fond sombre, c'est le pas sombre qui doit se fondre dans la
# surface pour dire « proche de zéro ». L'ordre inverse de la version fond
# clair, mêmes valeurs.
BLUES = ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"]

def page_carte():
    st.caption(tr("Chaque case : la probabilité de victoire du **meilleur match** de "
                  "l'équipe à la journée de pick de la semaine (vide = elle ne joue pas "
                  "ce jour-là). Les cases "
                  "encadrées sont les picks du plan. Une équipe se lit de gauche à "
                  "droite : on voit quand elle vaut le plus.",
                  "Each cell: the win probability of the team's **best game** on "
                  "that week's pick day (empty = it doesn't play that day). Outlined "
                  "cells are the plan's picks. Read a team from left to right: you "
                  "see when it is worth the most."))
    mondays_all = list(options)
    n_weeks = st.slider(tr("Semaines affichées", "Weeks shown"), 4, len(mondays_all),
                        min(12, len(mondays_all)))
    shown = mondays_all[:n_weeks]
    labels = [fr_date(m) for m in shown]
    order = sorted(cm.TEAMS - set(used),
                   key=lambda t: (planned.get(t) or dt.date.max, t))
    cells = pd.DataFrame([{
        "Équipe": t, "Semaine": fr_date(m), "Probabilité": o.p,
        "Match": f"{'vs' if o.home else '@'} {o.opponent} — {o.game_date}",
        "Au plan": planned.get(t) == m, "Valeur": f"{100 * o.p:.0f}",
    } for m in shown for t, o in options[m].items()])

    # Les champs gardent leur nom français : seuls les titres affichés suivent
    # la langue. « Au plan » est un booléen ; on montre un texte à la place.
    cells["Au plan (texte)"] = cells["Au plan"].map(
        {True: tr("oui", "yes"), False: tr("non", "no")})
    x = alt.X("Semaine:O", sort=labels, title=tr("Semaine du", "Week of"),
              axis=alt.Axis(orient="top", labelAngle=0))
    y = alt.Y("Équipe:N", sort=order, title=None)
    heat = alt.Chart(cells).mark_rect(cornerRadius=3, stroke="#1a1a19", strokeWidth=2).encode(
        x=x, y=y,
        color=alt.Color("Probabilité:Q", title=tr("Probabilité", "Probability"),
                        scale=alt.Scale(domain=[0.45, 0.85], range=BLUES, clamp=True),
                        legend=alt.Legend(format="%", orient="bottom")),
        tooltip=[alt.Tooltip("Équipe:N", title=tr("Équipe", "Team")),
                 alt.Tooltip("Semaine:O", title=tr("Semaine", "Week")),
                 alt.Tooltip("Match:N", title=tr("Match", "Game")),
                 alt.Tooltip("Probabilité:Q", title=tr("Probabilité", "Probability"),
                             format=".1%"),
                 alt.Tooltip("Au plan (texte):N", title=tr("Au plan", "In the plan"))],
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
    st.caption(tr("Équipes triées par semaine prévue au plan ; celles hors plan à la fin. "
                  "Survole une case pour voir le match.",
                  "Teams sorted by their planned week; those not in the plan come "
                  "last. Hover over a cell to see the game."))


# ── Évolution des probabilités ────────────────────────────────────────────

def page_evolution():
    default_team = plan[0][1].team if plan and plan[0][1] else sorted(cm.TEAMS)[0]
    teams_sorted = sorted(cm.TEAMS)
    c1, c2 = st.columns(2)
    team = c1.selectbox(tr("Équipe", "Team"), teams_sorted,
                        index=teams_sorted.index(default_team))
    team_games = (latest[((latest.away == team) | (latest.home == team))
                         & (latest.source == "consensus")]
                  .sort_values("game_date"))
    if team_games.empty:
        st.info(tr("Aucun match à venir pour cette équipe dans les données collectées.",
                   "No upcoming game for this team in the collected data."))
    else:
        labels = {
            f"{r.game_date} — " + (f"@ {r.home}" if r.away == team else f"vs {r.away}"):
            (r.game_date, r.away, r.home) for r in team_games.itertuples()
        }
        default_game = next((i for i, (g, *_x) in enumerate(labels.values())
                             if plan and plan[0][1] and g == plan[0][1].game_date
                             and team == plan[0][1].team), 0)
        choice = c2.selectbox(tr("Match", "Game"), list(labels), index=default_game)
        g, a, h = labels[choice]
        series = hist[(hist.game_date == g) & (hist.away == a) & (hist.home == h)
                      & hist.source.isin(SOURCES)].copy()
        series["Probabilité"] = win_prob(series, team)
        series["Source"] = series.source.map(SOURCES)
        series["Collecte"] = pd.to_datetime(series.snapshot)
        present = [s for s in SOURCES if s in set(series.source)]

        base = alt.Chart(series).encode(
            x=alt.X("Collecte:T", title=tr("Date de collecte", "Collection date"),
                    axis=alt.Axis(format=tr("%d/%m", "%m/%d"),
                                  tickCount={"interval": "day", "step": 1})),
            y=alt.Y("Probabilité:Q", title=tr(f"Probabilité que {team} gagne",
                                              f"Probability that {team} wins"),
                    axis=alt.Axis(format="%"), scale=alt.Scale(zero=False)),
            color=alt.Color("Source:N", title="Source",
                            scale=alt.Scale(domain=[SOURCES[s] for s in present],
                                            range=[COLORS[list(SOURCES).index(s)]
                                                   for s in present])),
            tooltip=[alt.Tooltip("Source:N"),
                     alt.Tooltip("Collecte:T", title=tr("Collecte", "Collected"),
                                 format="%Y-%m-%d"),
                     alt.Tooltip("Probabilité:Q", title=tr("Probabilité", "Probability"),
                                 format=".1%")],
        )
        chart = (base.mark_line(strokeWidth=2) + base.mark_point(size=64, filled=True)
                 ).properties(height=360)
        st.altair_chart(chart, width="stretch")
        if series.snapshot.nunique() < 2:
            st.caption(tr("Une seule collecte pour l'instant : les courbes apparaîtront "
                          "au fil des collectes quotidiennes.",
                          "Only one collection so far: the lines will appear "
                          "as daily collections come in."))
        with st.expander(tr("Voir les données", "See the data")):
            proba = tr("Probabilité", "Probability")
            st.dataframe(series[["snapshot", "Source", "Probabilité"]]
                         .assign(Probabilité=lambda d: 100 * d.Probabilité)
                         .rename(columns={"snapshot": tr("Collecte", "Collected"),
                                          "Probabilité": proba}),
                         hide_index=True, column_config={proba: PCT})


# ── Équipes en forme ──────────────────────────────────────────────────────

def page_forme():
    played = any(f.results for f in team_form.values())
    hist_r = strength_hist(v)
    snaps_r = sorted(hist_r.snapshot.unique())
    first = hist_r[hist_r.snapshot == snaps_r[0]].set_index("team").rating
    last = hist_r[hist_r.snapshot == snaps_r[-1]].set_index("team").rating
    st.caption(tr("« Force » = chances de battre une équipe moyenne en terrain neutre, "
                  "selon le consensus de chaque collecte. Une série de victoires est déjà "
                  "en bonne partie intégrée dans les cotes : cet onglet aide à comprendre "
                  "le plan, pas à le contredire.",
                  "\"Strength\" = chance of beating an average team on neutral ice, "
                  "according to each collection's consensus. A winning streak is "
                  "already largely priced into the odds: this tab helps understand "
                  "the plan, not contradict it."))
    EQUIPE, SERIE, FICHE, DISPO = (tr("Équipe", "Team"), tr("Série", "Streak"),
                                   tr("Fiche", "Record"), tr("Disponible", "Available"))
    DEJA, OUI = tr("Déjà utilisée", "Already used"), tr("Oui", "Yes")

    # Séries en cours
    st.subheader(tr("Séries de victoires", "Winning streaks"))
    if not played:
        st.info(tr("Aucun match joué pour l'instant : les séries apparaîtront après les "
                   "premiers matchs (saison dès le 29 septembre).",
                   "No games played yet: streaks will appear after the first "
                   "games (season starts September 29)."))
    else:
        n_min = st.slider(tr("Victoires d'affilée, au moins", "Wins in a row, at least"),
                          2, 10, 4)
        hot = sorted((f for f in team_form.values() if f.streak >= n_min),
                     key=lambda f: -f.streak)
        if not hot:
            best = max(team_form.values(), key=lambda f: f.streak)
            st.caption(tr(f"Aucune équipe sur une série de {n_min} victoires ou plus "
                          f"(meilleure série actuelle : {best.team}, {serie(best)}).",
                          f"No team on a streak of {n_min} wins or more "
                          f"(best current streak: {best.team}, {serie(best)})."))
        else:
            force = tr("Force actuelle", "Current strength")
            st.dataframe(pd.DataFrame([{
                EQUIPE: f.team, SERIE: serie(f), FICHE: f"{f.wins}-{f.losses}",
                tr("10 derniers", "Last 10"): f.last10,
                force: 100 * last.get(f.team, 0.5),
                DISPO: DEJA if f.team in used else OUI,
            } for f in hot]), hide_index=True, width="stretch",
                column_config={force: PCT})

    # Progression de la force
    st.subheader(tr("Progression : projection initiale vs maintenant",
                    "Progress: initial projection vs now"))
    rise = (last - first).sort_values(ascending=False)
    default = [t for t in rise.index if t not in used and rise[t] >= 0.01][:5]
    if not default:   # pas encore de vraie progression : les plus fortes
        default = [t for t in last.sort_values(ascending=False).index if t not in used][:5]
    chosen = st.multiselect(tr("Équipes (8 au maximum)", "Teams (8 at most)"),
                            sorted(cm.TEAMS), default=default, max_selections=8,
                            help=tr("Par défaut : les équipes pas encore utilisées qui ont "
                                    "le plus progressé depuis la première collecte.",
                                    "By default: the teams not used yet that have "
                                    "improved the most since the first collection."))
    if chosen:
        data = hist_r[hist_r.team.isin(chosen)].assign(
            Collecte=lambda d: pd.to_datetime(d.snapshot), Force=lambda d: d.rating,
            Équipe=lambda d: d.team)
        color = alt.Color("Équipe:N", scale=alt.Scale(domain=chosen,
                                                      range=TEAM_COLORS[:len(chosen)]),
                          legend=alt.Legend(orient="bottom", title=None))
        base = alt.Chart(data).encode(
            x=alt.X("Collecte:T", title=tr("Date de collecte", "Collection date"),
                    axis=alt.Axis(format=tr("%d/%m", "%m/%d"),
                                  tickCount={"interval": "day", "step": 1})),
            y=alt.Y("Force:Q", title=tr("Force", "Strength"), axis=alt.Axis(format="%"),
                    scale=alt.Scale(zero=False)),
            color=color,
            tooltip=[alt.Tooltip("Équipe:N", title=EQUIPE),
                     alt.Tooltip("Collecte:T", title=tr("Collecte", "Collected"),
                                 format="%Y-%m-%d"),
                     alt.Tooltip("Force:Q", title=tr("Force", "Strength"), format=".1%")],
        )
        labels = alt.Chart(data[data.snapshot == snaps_r[-1]]).mark_text(
            align="left", dx=8, fontSize=12).encode(
            x="Collecte:T", y="Force:Q", text="Équipe:N", color=color)
        chart = base.mark_line(strokeWidth=2) + base.mark_point(size=64, filled=True)
        if len(chosen) <= 4:   # au-delà, les étiquettes se chevauchent : légende seule
            chart += labels
        st.altair_chart(chart.properties(height=380), width="stretch")
        if len(snaps_r) < 2:
            st.caption(tr("Une seule collecte pour l'instant : la progression se dessinera "
                          "au fil des jours.",
                          "Only one collection so far: progress will show up over "
                          "the coming days."))
        initiale, maintenant, ecart = (tr("Projection initiale", "Initial projection"),
                                       tr("Maintenant", "Now"),
                                       tr("Écart (points)", "Change (points)"))
        st.dataframe(pd.DataFrame([{
            EQUIPE: t, initiale: 100 * first[t], maintenant: 100 * last[t],
            ecart: 100 * (last[t] - first[t]),
            FICHE: f"{team_form[t].wins}-{team_form[t].losses}",
            SERIE: serie(team_form[t]),
            DISPO: DEJA if t in used else OUI,
        } for t in chosen]), hide_index=True, width="stretch", column_config={
            initiale: PCT, maintenant: PCT,
            ecart: st.column_config.NumberColumn(format="%+.1f")})

    # Victoires réelles vs attendues
    st.subheader(tr("Victoires réelles vs attendues", "Actual vs expected wins"))
    ev = expected(v)
    if not ev:
        st.info(tr("Disponible après les premiers matchs joués.",
                   "Available after the first games are played."))
    else:
        table = pd.DataFrame([{
            "Équipe": t, "Matchs": len(g), "Victoires": sum(w for *_x, w in g),
            "Attendues": sum(p for _d, _o, p, _w in g),
        } for t, g in ev.items()]).assign(Écart=lambda d: d.Victoires - d.Attendues)
        table = table.sort_values("Écart", ascending=False)
        st.caption(tr("« Attendues » = somme des probabilités d'avant-match. Un écart "
                      "positif : l'équipe gagne plus que prévu (forme réelle ou chance).",
                      "\"Expected\" = sum of the pre-game probabilities. A positive "
                      "gap: the team wins more than expected (real form or luck)."))
        c1, c2 = st.columns([2, 3])
        # Calculs sur les noms français ci-dessus ; seuls les en-têtes affichés
        # suivent la langue.
        attendues, ecart_v = tr("Attendues", "Expected"), tr("Écart", "Gap")
        c1.dataframe(table.rename(columns={
            "Équipe": EQUIPE, "Matchs": tr("Matchs", "Games"),
            "Victoires": tr("Victoires", "Wins"), "Attendues": attendues,
            "Écart": ecart_v}), hide_index=True, width="stretch", column_config={
            attendues: st.column_config.NumberColumn(format="%.1f"),
            ecart_v: st.column_config.NumberColumn(format="%+.1f")})
        pick_t = c2.selectbox(tr("Détail pour", "Details for"), list(table.Équipe))
        games = ev[pick_t]
        reelles, attendues_l = tr("Réelles", "Actual"), tr("Attendues", "Expected")
        cum = pd.DataFrame({"Match": range(1, len(games) + 1),
                            reelles: pd.Series([w for *_x, w in games]).cumsum(),
                            attendues_l: pd.Series([p for _d, _o, p, _w in games]).cumsum()})
        long = cum.melt("Match", var_name="Série", value_name="Victoires")
        c2.altair_chart(alt.Chart(long).mark_line(strokeWidth=2, point=True).encode(
            x=alt.X("Match:Q", title=tr("Matchs joués", "Games played"),
                    axis=alt.Axis(tickMinStep=1)),
            y=alt.Y("Victoires:Q", title=tr(f"Victoires cumulées de {pick_t}",
                                            f"Cumulative wins for {pick_t}")),
            color=alt.Color("Série:N", scale=alt.Scale(domain=[reelles, attendues_l],
                                                       range=COLORS[:2]),
                            legend=alt.Legend(orient="bottom", title=None)),
            tooltip=[alt.Tooltip("Match:Q", title=tr("Match", "Game")),
                     alt.Tooltip("Série:N", title=tr("Série", "Series")),
                     alt.Tooltip("Victoires:Q", title=tr("Victoires", "Wins"),
                                 format=".1f")],
        ).properties(height=320), width="stretch")


# ── Précision des sources ─────────────────────────────────────────────────

def page_precision():
    st.caption(tr("Poids actuels dans le consensus : ", "Current weights in the consensus: ")
               + " · ".join(f"{SOURCES[s]} {w:.0%}" for s, w in co.WEIGHTS.items())
               + tr(". Quand une source n'a pas de probabilité pour un match, les poids "
                    "des autres sont répartis entre elles.",
                    ". When a source has no probability for a game, its weight is "
                    "split among the others."))
    acc = [row for row in accuracy(v) if row[1]]
    if not acc:
        st.info(tr("Disponible après les premiers matchs joués : chaque source sera "
                   "jugée sur ses probabilités d'avant-match.",
                   "Available after the first games are played: each source will "
                   "be judged on its pre-game probabilities."))
    else:
        brier_t, favoris, brier_ct, casinos_t = (
            tr("Score de Brier", "Brier score"), tr("Favoris gagnants", "Favorites won"),
            tr("Brier (matchs cotés)", "Brier (games with odds)"),
            tr("Casinos, mêmes matchs", "Sportsbooks, same games"))
        st.dataframe(pd.DataFrame([{
            "Source": SOURCES[s], tr("Matchs jugés", "Games judged"): n, brier_t: brier,
            favoris: None if hits is None else 100 * hits,
            brier_ct: brier_c, casinos_t: brier_m,
        } for s, n, brier, hits, brier_c, brier_m in sorted(acc, key=lambda r: r[2])]),
            hide_index=True, width="stretch", column_config={
                brier_t: st.column_config.NumberColumn(format="%.4f"),
                favoris: PCT,
                brier_ct: st.column_config.NumberColumn(format="%.4f"),
                casinos_t: st.column_config.NumberColumn(format="%.4f"),
            })
        st.caption(tr(
            "**Score de Brier** : moyenne de (probabilité − résultat)². 0,25 = pile ou "
            "face ; plus c'est bas, mieux c'est. Les sources ne couvrent pas les mêmes "
            "matchs (Dimers : la veille seulement ; casinos : quelques jours avant) : "
            "pour une comparaison juste, regarde les deux dernières colonnes, calculées "
            "sur les seuls matchs cotés par les casinos. Il faut environ 100 matchs "
            "avant de tirer des conclusions ; ensuite, on pourra ajuster les poids.",
            "**Brier score**: average of (probability − result)². 0.25 = a coin "
            "flip; lower is better. The sources don't cover the same games "
            "(Dimers: the day before only; sportsbooks: a few days ahead): for a "
            "fair comparison, look at the last two columns, computed only on games "
            "the sportsbooks priced. It takes about 100 games before drawing "
            "conclusions; after that, the weights can be adjusted."))

    # ── Comment se calcule l'espérance ────────────────────────────────────
    # Construit sur les vraies semaines du plan : un exemple invente
    # n'apprendrait pas ou le chiffre affiche en haut du site vient.
    with ui.panel("esperance", tr("Comment se calcule l'espérance",
                                  "How expected weeks are calculated"),
                  tr("Le chiffre affiché partout sur le site : le nombre de "
                     "semaines que le plan devrait survivre en moyenne.",
                     "The number shown all over the site: how many weeks the "
                     "plan should survive on average.")):
        st.markdown(tr(
            "Dans un survivor, **une seule défaite élimine**. Être encore en vie "
            "à la semaine 3 demande donc de gagner les semaines 1, 2 **et** 3 : "
            "les probabilités se multiplient, elles ne s'additionnent pas.",
            "In a survivor pool, **a single loss eliminates you**. Being alive "
            "in week 3 therefore means winning weeks 1, 2 **and** 3: the "
            "probabilities multiply, they don't add up."))
        st.markdown(tr(
            "L'espérance additionne, pour chaque semaine, la probabilité d'être "
            "encore en vie à ce moment-là :",
            "Expected weeks add up, for each week, the probability of still "
            "being alive at that point:"))
        st.latex(r"E = p_1 + p_1 p_2 + p_1 p_2 p_3 + \dots")

        montre = [(m, o) for m, o in plan if o][:4]
        gagner, en_vie, cumulee = (tr("Probabilité de gagner", "Win probability"),
                                   tr("Encore en vie après", "Still alive after"),
                                   tr("Espérance cumulée", "Cumulative expected weeks"))
        if montre:
            lignes, vivant, cumul = [], 1.0, 0.0
            for i, (m, o) in enumerate(montre, 1):
                vivant *= o.p
                cumul += vivant
                lignes.append({
                    tr("Semaine", "Week"): f"{i} — {fr_date(m)}",
                    tr("Équipe", "Team"): o.team,
                    gagner: 100 * o.p,
                    en_vie: 100 * vivant,
                    cumulee: cumul,
                })
            st.markdown(tr(f"Tes {len(montre)} premières semaines, en vrai :",
                           f"Your first {len(montre)} weeks, for real:"))
            st.dataframe(pd.DataFrame(lignes), hide_index=True, width="stretch",
                         column_config={
                             gagner: PCT,
                             en_vie: PCT,
                             cumulee: st.column_config.NumberColumn(format=SEM),
                         })
            # Trois décimales : avec deux, le produit affiché ne retombait pas sur
            # le pourcentage du tableau, ce qui avait l'air d'une erreur de calcul.
            detail = " × ".join(f"{o.p:.3f}" for _m, o in montre)
            st.caption(tr(
                f"Colonne « encore en vie » : le produit des probabilités depuis "
                f"le début ({detail} pour la dernière ligne). La colonne de droite "
                f"les additionne — c'est l'espérance. En continuant sur les "
                f"{len(plan)} semaines du plan, elle atteint **{exp_weeks:.2f}**.",
                f"\"Still alive\" column: the product of the probabilities from "
                f"the start ({detail} for the last row). The right-hand column "
                f"adds them up — that is the expected weeks. Carried over the "
                f"plan's {len(plan)} weeks, it reaches **{exp_weeks:.2f}**."))
        st.markdown(tr(
            "**Pourquoi ça change les décisions.** Une semaine proche pèse plus "
            "lourd qu'une semaine lointaine : si je suis éliminé en semaine 2, "
            "les semaines 10 à 27 ne rapportent rien, quelle que soit leur "
            "qualité. L'optimiseur ne cherche donc pas la meilleure équipe "
            "chaque semaine prise isolément — il peut garder une équipe forte "
            "pour une semaine où rien d'autre ne tient la route, parce que ça "
            "fait monter le total.",
            "**Why it changes decisions.** A near week weighs more than a "
            "distant one: if I'm eliminated in week 2, weeks 10 to 27 are worth "
            "nothing, however good they are. So the optimizer doesn't look for "
            "the best team each week on its own — it can save a strong team for "
            "a week when nothing else holds up, because that raises the total."))
        st.caption(tr(
            "C'est aussi ce que mesure la colonne « Coût » dans les choix de la "
            "semaine : l'espérance perdue en prenant cette équipe-là plutôt que "
            "la meilleure.",
            "This is also what the \"Cost\" column measures in the week's "
            "picks: the expected weeks lost by taking that team rather than "
            "the best one."))


# ── Depuis hier ───────────────────────────────────────────────────────────

def page_depuis_hier():
    if len(snaps) < 2:
        st.info(tr("Il faut au moins deux collectes pour comparer. Reviens après la "
                   "prochaine collecte quotidienne.",
                   "At least two collections are needed to compare. Come back "
                   "after the next daily collection."))
    else:
        prev = snaps[1]
        # snapshot= nommé : un argument positionnel de plus a déjà atterri
        # dans `horizon` une fois, ce qui plantait sur un découpage.
        prev_plan, prev_exp, _ = plan_for(v, from_date, used, overrides,
                                          horizon, snapshot=prev)
        st.caption(tr(f"Comparaison entre la collecte du {prev} et celle du {snapshot}.",
                      f"Comparing the {prev} collection with the {snapshot} one."))

        c1, c2 = st.columns(2)
        now_pick = plan[0][1].team if plan and plan[0][1] else "—"
        old_pick = prev_plan[0][1].team if prev_plan and prev_plan[0][1] else "—"
        c1.metric(tr("Pick de la semaine", "Pick of the week"), now_pick)
        c1.caption(tr("Inchangé", "Unchanged") if now_pick == old_pick
                   else tr(f"Avant : {old_pick}", f"Before: {old_pick}"))
        c2.metric(tr("Espérance", "Expected weeks"), f"{exp_weeks:.2f} {SEMAINES}",
                  f"{exp_weeks - prev_exp:+.2f} {SEMAINES}")

        avant, maintenant = tr("Avant", "Before"), tr("Maintenant", "Now")
        old = {m: o.team for m, o in prev_plan if o}
        changes = [{tr("Semaine du", "Week of"): str(m), avant: old.get(m, "—"),
                    maintenant: o.team}
                   for m, o in plan if o and old.get(m) != o.team]
        st.markdown(tr("#### Changements dans le plan", "#### Changes in the plan"))
        if changes:
            st.dataframe(pd.DataFrame(changes), hide_index=True)
        else:
            st.caption(tr("Aucun changement.", "No changes."))

        st.markdown(tr("#### Plus gros mouvements de probabilité (consensus)",
                       "#### Biggest probability moves (consensus)"))
        cons = hist[hist.source == "consensus"]
        a = cons[cons.snapshot == snapshot].set_index(["game_date", "away", "home"]).p_away
        b = cons[cons.snapshot == prev].set_index(["game_date", "away", "home"]).p_away
        moves = (a - b).dropna()
        moves = moves.reindex(moves.abs().sort_values(ascending=False).index).head(10)
        if moves.empty:
            st.caption(tr("Aucun match commun entre les deux collectes.",
                          "No game in common between the two collections."))
        else:
            hausse = tr("Hausse (points)", "Rise (points)")
            rows = []
            for (g, away, home), d in moves.items():
                team, sign = (away, 1) if d > 0 else (home, -1)
                before = b[(g, away, home)] if sign > 0 else 1 - b[(g, away, home)]
                rows.append({tr("Match", "Game"): f"{away} @ {home}", "Date": g,
                             tr("En hausse", "Rising"): team,
                             avant: 100 * before, maintenant: 100 * (before + abs(d)),
                             hausse: 100 * abs(d)})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", column_config={
                avant: PCT, maintenant: PCT,
                hausse: st.column_config.NumberColumn(format="+%.1f"),
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
        (tr("Survivants", "Survivors"),
         str((0 if out else 1) + len(pk.pool_alive(state, since, RES))),
         tr("moi comprise", "including me") if not out
         else tr("je suis éliminée", "I'm eliminated")),
        (tr("Adversaires en vie", "Opponents alive"),
         str(len(pk.pool_alive(state, since, RES))),
         tr(f"sur {len(joueurs)} inscrits", f"out of {len(joueurs)} entered")),
        (tr("Équipes prises", "Teams picked"), str(len(pris)),
         tr("par les adversaires cette semaine", "by opponents this week")),
        (tr("Mon pick", "My pick"), mon_pick or "—",
         tr(f"pris par {pris.get(mon_pick, 0)} autre(s)",
            f"picked by {pris.get(mon_pick, 0)} other(s)") if mon_pick
         else tr("à choisir", "to choose")),
    ])

    # « Éliminé » est une valeur de picks.py (jamais enregistrée) : on l'affiche
    # dans la langue du visiteur et on reconnaît les deux à l'enregistrement.
    elimine_txt = tr(pk.ELIMINE, "Eliminated")

    with ui.panel("registre", tr("Qui joue quoi cette semaine",
                                 "Who's picking what this week"),
                  tr("Une ligne par adversaire — ne t'ajoute pas, tu es déjà "
                     "comptée à part. Le statut se déduit tout seul des "
                     "résultats : inutile de suivre qui tombe. Mets « out » ou "
                     "« in » seulement pour ce qu'aucun match ne dira — une "
                     "cotisation impayée, une règle maison.",
                     "One row per opponent — don't add yourself, you are "
                     "already counted separately. The status follows from the "
                     "results on its own: no need to track who falls. Set "
                     "\"out\" or \"in\" only for what no game will tell — an "
                     "unpaid entry fee, a house rule.")):
        def verdict(j):
            elimine, sem, origine = pk.statut(j, RES, since)
            if not elimine:
                return tr("en vie", "alive")
            return (tr(f"éliminé sem. du {sem}", f"eliminated week of {sem}") if sem
                    else tr("éliminé", "eliminated"))

        # Choix de la semaine à remplir, en dehors du formulaire pour que le
        # tableau se reconstruise tout de suite quand on change — sinon il
        # faudrait cliquer « Enregistrer » juste pour voir une autre semaine.
        # Séparé de `semaine` (toujours la semaine en cours, this_monday) :
        # le potentiel restant plus bas dépend de `semaine` pour planifier à
        # partir de la semaine SUIVANTE, et doit rester sur la vraie semaine
        # courante même quand on vient corriger un pick oublié.
        semaines_dispo = pk.semaines_tour(since, this_monday)
        semaine_edit = st.selectbox(
            tr("Semaine à remplir", "Week to fill in"), semaines_dispo,
            format_func=fr_weekend,
            help=tr("Pour rattraper un pick oublié la semaine d'avant.",
                    "To catch up on a pick missed the week before."))
        semaine_edit_iso = semaine_edit.isoformat()
        proba_edit = ({t: o.p for t, o in options.get(semaine_edit, {}).items()}
                      if semaine_edit == this_monday else {})

        lignes = [{
            "Joueur": nom,
            "Pick": (elimine_txt if (pr := pk.pick_registre(j, semaine_edit_iso, RES, since))
                     == pk.ELIMINE else pr),
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
                # Les clés restent en français : merge_pool_week les lit.
                # Seuls les en-têtes affichés suivent la langue.
                column_config={
                    "Joueur": st.column_config.TextColumn(tr("Joueur", "Player")),
                    "Pick": st.column_config.SelectboxColumn(
                        tr(f"Pick du {fr_weekend(semaine_edit)}",
                           f"Pick for {fr_weekend(semaine_edit)}"),
                        options=[elimine_txt] + sorted(cm.TEAMS), required=False,
                        help=tr("« Éliminé » se met tout seul après la semaine "
                                "où quelqu'un sort : rien à remplir.",
                                "\"Eliminated\" fills in by itself after the week "
                                "someone goes out: nothing to enter.")),
                    "Statut": st.column_config.SelectboxColumn(
                        tr("Statut", "Status"), options=[pk.AUTO, pk.DEHORS, pk.DEDANS],
                        required=False,
                        help=tr("auto = déduit des résultats ; out = éliminé quoi "
                                "qu'en disent les matchs ; in = maintenu en vie",
                                "auto = from the results; out = eliminated "
                                "whatever the games say; in = kept alive")),
                    "Réel": st.column_config.TextColumn(
                        tr("Réel", "Actual"), disabled=True,
                        help=tr("Ce que le site retient, une fois le manuel appliqué",
                                "What the site keeps, once the manual status "
                                "is applied")),
                    "Probabilité": st.column_config.NumberColumn(
                        tr("Probabilité", "Probability"),
                        format="%.1f %%", disabled=True,
                        help=tr("Calculée, pas modifiable — disponible seulement "
                                "pour la semaine en cours",
                                "Calculated, not editable — only available "
                                "for the current week")),
                    "Équipes déjà prises": st.column_config.TextColumn(
                        tr("Équipes déjà prises", "Teams already used"),
                        disabled=True),
                })
            if st.form_submit_button(tr("Enregistrer le registre", "Save the registry"),
                                     type="primary", width="stretch",
                                     disabled=not PEUT_ECRIRE):
                # « Éliminé » n'est qu'un affichage : jamais écrit dans
                # picks.json comme si c'était une équipe — dans aucune langue.
                records = edite.to_dict("records")
                for r in records:
                    if r.get("Pick") in (pk.ELIMINE, elimine_txt):
                        r["Pick"] = ""
                nouveau = pk.merge_pool_week(joueurs, records, semaine_edit_iso)
                save_state(pk.set_pool(state, nouveau),
                           tr(f"Registre du pool ({len(nouveau)} joueur(s))",
                              f"Pool registry ({len(nouveau)} player(s))"))

    if pris:
        with ui.panel("popularite", tr("Popularité des équipes cette semaine",
                                       "Team popularity this week"),
                      tr("Une équipe que beaucoup prennent ne te démarque pas : si "
                         "elle gagne, vous restez tous en vie. Si elle perd, vous "
                         "tombez tous. C'est en prenant autre chose qu'on finit "
                         "seul debout.",
                         "A team many people pick doesn't set you apart: if it "
                         "wins, you all stay alive. If it loses, you all fall. "
                         "Picking something else is how you end up the last "
                         "one standing.")):
            rangs = sorted(pris.items(), key=lambda kv: (-kv[1], kv[0]))
            proba_c = tr("Probabilité", "Probability")
            st.dataframe(pd.DataFrame([{
                "": ui.logo(t), tr("Équipe", "Team"): t, tr("Adversaires", "Opponents"): n,
                proba_c: 100 * proba[t] if t in proba else None,
                tr("Aussi mon pick", "Also my pick"): tr("oui", "yes") if t == mon_pick else "",
            } for t, n in rangs]), hide_index=True, width="stretch",
                column_config={"": st.column_config.ImageColumn("", width="small"),
                               proba_c: PCT})
    elif joueurs:
        st.caption(tr("Aucun pick saisi pour cette semaine.",
                      "No picks entered for this week."))

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

    gens = [(tr("Moi", "Me"), mes_brulees, not out, mon_pick)]
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
            # Valeur affichée ; le tri plus bas compare au même tr().
            "En vie": tr("oui", "yes") if vivant else tr("non", "no"),
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
    lignes.sort(key=lambda r: (r["En vie"] == tr("non", "no"),
                               -(r["Survie totale"] if r["Survie totale"]
                                 is not None else -1), r["Joueur"]))

    with ui.panel("potentiel", tr("Chances de survie de chacun",
                                  "Everyone's survival chances"),
                  tr("Deux chiffres à ne pas confondre : ce qu'on garde pour plus "
                     "tard, et ce qu'on vaut une fois le risque de cette semaine "
                     "pris en compte.",
                     "Two numbers not to confuse: what you keep for later, and "
                     "what you are worth once this week's risk is counted.")):
        # Clés françaises pour le tri ci-dessus ; en-têtes dans la langue
        # du visiteur, posés ici seulement.
        st.dataframe(chiffres(pd.DataFrame(lignes),
                              ["Gagne cette semaine", "Potentiel après",
                               "Survie totale", "Écart vs moi"]),
                     hide_index=True, width="stretch",
                     column_config={
                         "Joueur": st.column_config.TextColumn(tr("Joueur", "Player")),
                         "En vie": st.column_config.TextColumn(tr("En vie", "Alive")),
                         "Pick": st.column_config.TextColumn("Pick"),
                         "Gagne cette semaine": st.column_config.NumberColumn(
                             tr("Gagne cette semaine", "Wins this week"),
                             format="%.1f %%"),
                         "Potentiel après": st.column_config.NumberColumn(
                             tr("Potentiel après", "Potential after"),
                             format=SEM,
                             help=tr("Meilleur plan atteignable À PARTIR DE LA "
                                     "SEMAINE PROCHAINE, avec ses équipes "
                                     "restantes. Garder un gros club le fait "
                                     "monter.",
                                     "Best plan reachable STARTING NEXT WEEK, "
                                     "with the teams left. Saving a strong team "
                                     "raises it.")),
                         "Survie totale": st.column_config.NumberColumn(
                             tr("Survie totale", "Total survival"),
                             format=SEM,
                             help=tr("p(gagner cette semaine) × (1 + potentiel "
                                     "après). Il faut passer la semaine avant de "
                                     "profiter de ce qu'on a gardé.",
                                     "p(win this week) × (1 + potential after). "
                                     "You have to survive the week before "
                                     "benefiting from what you saved.")),
                         "Écart vs moi": st.column_config.NumberColumn(
                             tr("Écart vs moi", "Gap vs me"),
                             format=SEM_SIGNE,
                             help=tr("Positif = mieux placé que moi",
                                     "Positive = better placed than me")),
                         "Brûlées": st.column_config.TextColumn(
                             tr("Brûlées", "Used up")),
                     })
        st.markdown(tr(
            "**Garder un gros club vaut quelque chose — mais moins qu'on "
            "pense.** Prendre une équipe faible pour se réserver Colorado fait "
            "bien monter la colonne « Potentiel après ». Il faut pourtant "
            "survivre à cette semaine-là d'abord, et un pile ou face coûte "
            "presque toujours plus que la marge gagnée.",
            "**Saving a strong team is worth something — but less than you "
            "think.** Taking a weak team to save Colorado does raise the "
            "\"Potential after\" column. But you have to survive that week "
            "first, and a coin flip almost always costs more than the margin "
            "gained."))
        st.caption(tr(
            "Le pick de la semaine est déjà joué : le potentiel se calcule "
            "donc à partir de la semaine suivante. Une personne sans pick "
            "saisi n'a pas de survie totale — remplis sa ligne dans le "
            "registre au-dessus.",
            "This week's pick is already played: the potential is therefore "
            "calculated from next week. Someone with no pick entered has no "
            "total survival — fill in their row in the registry above."))



# ── Navigation ─────────────────────────────────────────────────────────────
# st.navigation plutôt qu'une rangée d'onglets ou un bouton radio : ça donne
# une vraie navigation de site — des liens groupés dans la barre latérale,
# une URL par section (donc le bouton Retour du navigateur fonctionne), et le
# chevron de Streamlit ouvre et ferme le panneau.
# Comme avec le menu précédent, une seule section s'exécute par affichage,
# contrairement à st.tabs qui les exécutait toutes.

# Les url_path ne se traduisent pas : un lien partagé doit marcher dans les
# deux langues.
st.navigation({
    tr("Le pick", "The pick"): [
        st.Page(page_pick, title=tr("Pick de la semaine", "Pick of the week"),
                icon=":material/sports_hockey:", url_path="pick", default=True),
        st.Page(page_plan, title=tr("Plan complet", "Full plan"),
                icon=":material/calendar_month:", url_path="plan"),
        st.Page(page_pool, title=tr("Le pool", "The pool"),
                icon=":material/groups:", url_path="pool"),
    ],
    tr("Analyse", "Analysis"): [
        st.Page(page_carte, title=tr("Carte des matchups", "Matchup map"),
                icon=":material/grid_on:", url_path="carte"),
        st.Page(page_evolution, title=tr("Évolution des probabilités",
                                         "Probability trends"),
                icon=":material/show_chart:", url_path="evolution"),
        st.Page(page_forme, title=tr("Équipes en forme", "Teams on fire"),
                icon=":material/trending_up:", url_path="forme"),
    ],
    tr("Fiabilité", "Reliability"): [
        st.Page(page_precision, title=tr("Précision des sources", "Source accuracy"),
                icon=":material/target:", url_path="precision"),
        st.Page(page_depuis_hier, title=tr("Depuis hier", "Since yesterday"),
                icon=":material/update:", url_path="depuis-hier"),
    ],
}, expanded=True).run()
