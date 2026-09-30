"""
Page publique statique, en français et en anglais, pour être trouvée par les
moteurs de recherche.

L'app Streamlit n'est pas indexable : son HTML ne contient que 5 Ko de script,
le contenu arrive ensuite par websocket et aucun robot ne va jusque-là. On
écrit donc de vraies pages HTML, avec le texte dedans, régénérées à chaque
collecte et servies gratuitement par GitHub Pages depuis docs/.

    python page.py        # écrit docs/index.html, docs/en/index.html,
                          # docs/sitemap.xml et docs/robots.txt

Aucun appel d'API : tout vient de la base déjà construite depuis data/. Le
nombre de visiteurs n'a donc aucun effet sur les crédits The Odds API.

L'app reste l'outil privé où l'on décide ; ces pages sont la vitrine.
"""

import html as _html
import sqlite3
import datetime as dt
from pathlib import Path

import collect_moneypuck as cm
import optimize as op
import store

DOCS = Path("docs")
# Adresse servie par GitHub Pages sur un dépôt public. Un nom de domaine
# personnalisé pourra s'y substituer plus tard sans rien changer d'autre :
# l'indexation ne dépend pas du domaine, seulement d'une adresse publique.
BASE = "https://marekdoucet.github.io/pool-survivor"
SAISON = "2026-27"
SEUIL_DISETTE = 0.65     # en dessous, aucune équipe ne vaut vraiment le coup

MOIS = {
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"],
}
JOURS = {
    "fr": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
           "Sunday"],
}

TEAMS = {
    "fr": {
        "ANA": "Ducks d'Anaheim", "BOS": "Bruins de Boston",
        "BUF": "Sabres de Buffalo", "CAR": "Hurricanes de la Caroline",
        "CBJ": "Blue Jackets de Columbus", "CGY": "Flames de Calgary",
        "CHI": "Blackhawks de Chicago", "COL": "Avalanche du Colorado",
        "DAL": "Stars de Dallas", "DET": "Red Wings de Détroit",
        "EDM": "Oilers d'Edmonton", "FLA": "Panthers de la Floride",
        "LAK": "Kings de Los Angeles", "MIN": "Wild du Minnesota",
        "MTL": "Canadiens de Montréal", "NJD": "Devils du New Jersey",
        "NSH": "Predators de Nashville", "NYI": "Islanders de New York",
        "NYR": "Rangers de New York", "OTT": "Sénateurs d'Ottawa",
        "PHI": "Flyers de Philadelphie", "PIT": "Penguins de Pittsburgh",
        "SEA": "Kraken de Seattle", "SJS": "Sharks de San Jose",
        "STL": "Blues de St. Louis", "TBL": "Lightning de Tampa Bay",
        "TOR": "Maple Leafs de Toronto", "UTA": "Mammoth de l'Utah",
        "VAN": "Canucks de Vancouver", "VGK": "Golden Knights de Vegas",
        "WPG": "Jets de Winnipeg", "WSH": "Capitals de Washington",
    },
    "en": {
        "ANA": "Anaheim Ducks", "BOS": "Boston Bruins", "BUF": "Buffalo Sabres",
        "CAR": "Carolina Hurricanes", "CBJ": "Columbus Blue Jackets",
        "CGY": "Calgary Flames", "CHI": "Chicago Blackhawks",
        "COL": "Colorado Avalanche", "DAL": "Dallas Stars",
        "DET": "Detroit Red Wings", "EDM": "Edmonton Oilers",
        "FLA": "Florida Panthers", "LAK": "Los Angeles Kings",
        "MIN": "Minnesota Wild", "MTL": "Montreal Canadiens",
        "NJD": "New Jersey Devils", "NSH": "Nashville Predators",
        "NYI": "New York Islanders", "NYR": "New York Rangers",
        "OTT": "Ottawa Senators", "PHI": "Philadelphia Flyers",
        "PIT": "Pittsburgh Penguins", "SEA": "Seattle Kraken",
        "SJS": "San Jose Sharks", "STL": "St. Louis Blues",
        "TBL": "Tampa Bay Lightning", "TOR": "Toronto Maple Leafs",
        "UTA": "Utah Mammoth", "VAN": "Vancouver Canucks",
        "VGK": "Vegas Golden Knights", "WPG": "Winnipeg Jets",
        "WSH": "Washington Capitals",
    },
}

# Tous les textes des deux pages. Les expressions qu'on vise dans les
# recherches sont dans les titres et les en-têtes, là où elles comptent —
# pas répétées artificiellement dans le corps.
T = {
    "fr": {
        "h1": "Pool survivor hockey — le pick de la semaine",
        "titre": "Pool survivor LNH : {equipe} est le pick de la semaine",
        "desc": ("{equipe} à {p} contre {adv}. Le meilleur pick de la semaine "
                 "pour un pool survivor de hockey, recalculé deux fois par "
                 "jour à partir des cotes des casinos et de trois modèles "
                 "statistiques."),
        "maj": "Mis à jour le {date}. Saison {saison}.",
        "contre": "contre", "a": "à",
        "h_choix": "Tous les choix de la semaine du {semaine}",
        "th": ["Équipe", "Match", "Chances de gagner", "Espérance (sem.)"],
        "note": ("« Espérance » = nombre de semaines que le plan devrait "
                 "survivre en moyenne si l'on prend cette équipe maintenant, "
                 "puis le meilleur choix ensuite. Ce n'est pas un pourcentage."),
        "h_disette": "Les semaines à éviter",
        "p_disette": ("Une équipe brûlée ne revient jamais. Ces semaines "
                      "n'offrent aucun favori solide : mieux vaut y arriver "
                      "avec une grosse équipe encore disponible."),
        "disette": ("<b>semaine du {semaine}</b> — la meilleure équipe "
                    "disponible n'est qu'à {p}"),
        "h_methode": "Comment ce pick est calculé",
        "methode": [
            "Dans un pool survivor, une seule défaite élimine, et une équipe "
            "ne sert qu'une fois. Prendre le plus gros favori chaque semaine "
            "n'est donc pas la bonne stratégie : il faut garder les grosses "
            "équipes pour les semaines où rien d'autre ne tient la route.",
            "Ce site combine quatre sources — les cotes des casinos, "
            "MoneyPuck, Dimers et Puckcast — en un consensus pondéré, puis "
            "optimise le plan sur les huit prochaines semaines d'un coup. Il "
            "maximise l'espérance du nombre de semaines survécues, ce qui "
            "tient compte du fait qu'une élimination précoce annule toutes "
            "les semaines suivantes.",
            "Les données sont recollectées deux fois par jour, avant et après "
            "les matchs.",
        ],
        "autre_langue": "English version",
        "pied": ("Sources : The Odds API, MoneyPuck, Dimers, Puckcast, API "
                 "publique de la LNH. Projet personnel, sans lien avec la LNH. "
                 "Ces chiffres sont des estimations, pas des certitudes."),
    },
    "en": {
        "h1": "NHL survivor pool — this week's pick",
        "titre": "NHL survivor pool cheat sheet: {equipe} is this week's pick",
        "desc": ("{equipe} at {p} against {adv}. The best pick of the week for "
                 "an NHL survivor pool, recalculated twice a day from "
                 "sportsbook odds and three statistical models."),
        "maj": "Updated {date}. {saison} season.",
        "contre": "vs", "a": "at",
        "h_choix": "Every option for the week of {semaine}",
        "th": ["Team", "Game", "Win probability", "Expected weeks"],
        "note": ("\"Expected weeks\" = how many weeks the plan should survive "
                 "on average if you take this team now, then the best choice "
                 "after. It is not a percentage."),
        "h_disette": "Weeks to avoid",
        "p_disette": ("A team you burn never comes back. These weeks offer no "
                      "solid favourite: better to reach them with a strong "
                      "team still available."),
        "disette": ("<b>week of {semaine}</b> — the best available team is "
                    "only at {p}"),
        "h_methode": "How this pick is calculated",
        "methode": [
            "In a survivor pool a single loss eliminates you, and each team "
            "can only be used once. Taking the biggest favourite every week "
            "is therefore not the right strategy: the strong teams have to be "
            "saved for the weeks when nothing else holds up.",
            "This site blends four sources — sportsbook odds, MoneyPuck, "
            "Dimers and Puckcast — into a weighted consensus, then optimises "
            "the plan over the next eight weeks at once. It maximises the "
            "expected number of weeks survived, which accounts for the fact "
            "that an early elimination cancels every week after it.",
            "Data is collected twice a day, before and after the games.",
        ],
        "autre_langue": "Version française",
        "pied": ("Sources: The Odds API, MoneyPuck, Dimers, Puckcast, the "
                 "public NHL API. Personal project, not affiliated with the "
                 "NHL. These numbers are estimates, not certainties."),
    },
}

CSS = """
:root{color-scheme:dark;--bg:#17171b;--carte:#232329;--bord:#34343d;
--encre:#fff;--encre2:#c3c2b7;--accent:#3987e5}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(176deg,#1d1d23,#131316);color:var(--encre);
font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
.page{max-width:820px;margin:0 auto;padding:2.5rem 1.2rem 4rem}
h1{font-size:2rem;line-height:1.15;margin:0 0 .3rem}
h2{font-size:1.15rem;margin:2.4rem 0 .6rem}
.maj{color:var(--encre2);font-size:.9rem;margin:0 0 2rem}
.lang{float:right;font-size:.9rem}
.top{background:var(--carte);border:1px solid var(--bord);border-left:4px solid var(--accent);
border-radius:.9rem;padding:1.3rem 1.5rem;margin:0 0 1.5rem;overflow:hidden}
.top .eq{font-size:1.9rem;font-weight:700;line-height:1.1}
.top .pc{font-size:2.6rem;font-weight:700;float:right;margin-left:1rem}
.top .de{color:var(--encre2);margin-top:.3rem}
table{width:100%;border-collapse:collapse;margin:.6rem 0 0;font-size:.95rem}
th,td{text-align:left;padding:.55rem .6rem;border-bottom:1px solid var(--bord)}
th{color:var(--encre2);font-weight:600;font-size:.82rem;text-transform:uppercase;
letter-spacing:.06em}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
p{color:#ddd}
.note{color:var(--encre2);font-size:.9rem}
a{color:var(--accent)}
footer{margin-top:3rem;padding-top:1.2rem;border-top:1px solid var(--bord);
color:var(--encre2);font-size:.85rem}
@media(max-width:560px){.top .pc{float:none;display:block;margin:0 0 .4rem}}
"""

# Où vit chaque langue. Le français est la racine ; l'anglais un sous-dossier.
CHEMINS = {"fr": ("index.html", f"{BASE}/"),
           "en": ("en/index.html", f"{BASE}/en/")}


def nom(tri, lang):
    return TEAMS[lang].get(tri, tri)


def fr_date(d, lang):
    mois = MOIS[lang][d.month - 1]
    return f"{d.day} {mois}" if lang == "fr" else f"{mois} {d.day}"


def fr_jour(iso, lang):
    d = dt.date.fromisoformat(iso)
    return f"{JOURS[lang][d.weekday()]} {fr_date(d, lang)}"


def pourcent(p, lang):
    return f"{p * 100:.1f} %" if lang == "fr" else f"{p * 100:.1f}%"


def rendu(ctx, lang):
    """Le HTML complet d'une langue, à partir d'un dictionnaire simple.

    Fonction pure : c'est elle que testent les tests, sans base ni réseau.
    """
    e, t = _html.escape, T[lang]
    best = ctx["meilleur"]
    eq = nom(best["team"], lang)
    titre = t["titre"].format(equipe=eq)
    desc = t["desc"].format(equipe=eq, p=pourcent(best["p"], lang),
                            adv=nom(best["opponent"], lang))
    autre = "en" if lang == "fr" else "fr"

    lignes = "\n".join(
        f"<tr><td>{e(nom(o['team'], lang))}</td>"
        f"<td>{t['contre'] if o['home'] else t['a']} "
        f"{e(nom(o['opponent'], lang))}</td>"
        f"<td class=n>{pourcent(o['p'], lang)}</td>"
        f"<td class=n>{o['e']:.2f}</td></tr>"
        for o in ctx["choix"])

    creuses = "".join(
        "<li>" + t["disette"].format(semaine=e(s["semaine"][lang]),
                                     p=pourcent(s["p"], lang)) + "</li>"
        for s in ctx["disettes"])
    bloc_creuses = (f"<h2>{t['h_disette']}</h2><p>{t['p_disette']}</p>"
                    f"<ul>{creuses}</ul>" if creuses else "")
    methode = "".join(f"<p>{p}</p>" for p in t["methode"])
    th = "".join(f"<th{' class=n' if i >= 2 else ''}>{h}</th>"
                 for i, h in enumerate(t["th"]))

    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(titre)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{CHEMINS[lang][1]}">
<link rel="alternate" hreflang="fr" href="{CHEMINS['fr'][1]}">
<link rel="alternate" hreflang="en" href="{CHEMINS['en'][1]}">
<link rel="alternate" hreflang="x-default" href="{CHEMINS['fr'][1]}">
<meta property="og:type" content="website">
<meta property="og:title" content="{e(titre)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{CHEMINS[lang][1]}">
<meta property="og:locale" content="{'fr_CA' if lang == 'fr' else 'en_CA'}">
<style>{CSS}</style>
</head>
<body>
<main class="page">
<a class="lang" href="{CHEMINS[autre][1]}">{t['autre_langue']}</a>
<h1>{t['h1']}</h1>
<p class="maj">{t['maj'].format(date=e(ctx['maj'][lang]), saison=SAISON)}</p>

<div class="top">
  <span class="pc">{pourcent(best['p'], lang)}</span>
  <div class="eq">{e(eq)}</div>
  <div class="de">{t['contre'] if best['home'] else t['a']}
  {e(nom(best['opponent'], lang))}, {e(ctx['jour_match'][lang])}</div>
</div>

<h2>{t['h_choix'].format(semaine=e(ctx['semaine'][lang]))}</h2>
<table>
<thead><tr>{th}</tr></thead>
<tbody>
{lignes}
</tbody>
</table>
<p class="note">{t['note']}</p>

{bloc_creuses}

<h2>{t['h_methode']}</h2>
{methode}

<footer>{t['pied']}</footer>
</main>
</body>
</html>
"""


def sitemap(maj):
    """Les deux pages, pour que les robots les trouvent d'un coup."""
    urls = "".join(
        f"<url><loc>{u}</loc><lastmod>{maj}</lastmod>"
        f"<changefreq>daily</changefreq></url>"
        for _chemin, u in CHEMINS.values())
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{urls}</urlset>\n")


def robots():
    return f"User-agent: *\nAllow: /\nSitemap: {BASE}/sitemap.xml\n"


def contexte(conn, today=None):
    """Rassemble ce dont les pages ont besoin. Indépendant de la langue."""
    today = today or dt.datetime.now(cm.TZ).date()
    plan, _e, _snap = op.optimize(conn, today, set(), horizon=8)
    alts = op.alternatives(conn, today, set(), top=8, horizon=8)
    weeks, *_ = op.build_options(conn, today, set(), horizon=16)

    monday, best = plan[0]
    disettes = []
    for m in sorted(weeks):
        meilleure = max((o.p for o in weeks[m].values()), default=0)
        if meilleure < SEUIL_DISETTE:
            disettes.append({"semaine": {g: fr_date(m, g) for g in T},
                             "p": meilleure})
    return {
        "maj": {g: fr_jour(today.isoformat(), g) for g in T},
        "semaine": {g: fr_date(monday, g) for g in T},
        "jour_match": {g: fr_jour(best.game_date, g) for g in T},
        "meilleur": {"team": best.team, "opponent": best.opponent,
                     "home": best.home, "p": best.p,
                     "game_date": best.game_date},
        "choix": [{"team": o.team, "opponent": o.opponent, "home": o.home,
                   "p": o.p, "e": e} for o, e in alts],
        "disettes": disettes[:5],
    }


def ecrire(docs=DOCS, db_path=None, today=None):
    """Écrit les deux pages, le sitemap et le robots.txt.

    Retourne la liste des fichiers réellement modifiés — _write_if_changed
    évite de commiter des fichiers identiques à chaque collecte.
    """
    conn = sqlite3.connect(db_path or cm.DB_PATH)
    try:
        ctx = contexte(conn, today)
    finally:
        conn.close()
    docs = Path(docs)
    maj = (today or dt.datetime.now(cm.TZ).date()).isoformat()

    fichiers = {chemin: rendu(ctx, lang)
                for lang, (chemin, _u) in CHEMINS.items()}
    fichiers["sitemap.xml"] = sitemap(maj)
    fichiers["robots.txt"] = robots()

    modifies = []
    for chemin, contenu in fichiers.items():
        cible = docs / chemin
        if store._write_if_changed(cible, contenu.encode("utf-8")):
            modifies.append(cible)
    return modifies


if __name__ == "__main__":
    # Lancée seule, la page reconstruit la base depuis data/ : un fichier
    # présent mais vide ne suffit pas à conclure qu'elle est utilisable.
    print(f"Base reconstruite depuis {store.DATA_DIR}/ : {store.build_db()} lignes")
    m = ecrire()
    print(f"{len(m)} fichier(s) écrit(s) dans {DOCS}/"
          + ("".join(f"\n  {f}" for f in m) if m else " (rien n'a changé)"))
