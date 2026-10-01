"""
Page publique statique, en français et en anglais, pour être trouvée par les
moteurs de recherche.

L'app Streamlit n'est pas indexable : son HTML ne contient que 5 Ko de script,
le contenu arrive ensuite par websocket et aucun robot ne va jusque-là. On
écrit donc de vraies pages HTML, avec le texte dedans, régénérées à chaque
collecte et servies gratuitement par GitHub Pages depuis docs/.

    python page.py        # écrit docs/index.html, docs/en/index.html,
                          # docs/sitemap.xml et docs/robots.txt

Aucun appel d'API : tout vient de la base déjà construite depuis data/, et les
images sont servies par le CDN public de la LNH. Le nombre de visiteurs n'a
donc aucun effet sur les crédits The Odds API.

Le style reprend celui de l'app — carte de hockey, photo fondue, lueur aux
couleurs de l'équipe. Le CSS est recopié ici plutôt qu'importé de ui.py : ce
module-là dépend de Streamlit, qui n'a rien à faire dans la collecte
quotidienne. Les deux peuvent diverger sans dommage, ce sont deux publics
différents.

L'app reste l'outil privé où l'on décide ; ces pages sont la vitrine, et elles
ne montrent aucune donnée personnelle — ni picks, ni registre du pool.
"""

import csv
import html as _html
import io
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
APP = "https://pool-survivor-cheat-sheet.streamlit.app/"

# Jeton de validation Google Search Console (propriete « prefixe d'URL »).
# Il doit rester sur les DEUX pages : Google revalide periodiquement et
# retire la propriete si la balise disparait.
VERIF_GOOGLE = "Q1ZDOb5gaK_3URkxFVTvbZvTi-0Wf54jt3Wkxhm8cOE"
SAISON = "2026-27"
SEUIL_DISETTE = 0.65     # en dessous, aucune équipe ne vaut vraiment le coup
LOGO = "https://assets.nhle.com/logos/nhl/svg/{tri}_{mode}.svg"
JOUEURS = Path("data") / "players.csv"

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
POSTES = {
    "fr": {"C": "Centre", "L": "Ailier gauche", "R": "Ailier droit",
           "D": "Défenseur", "G": "Gardien"},
    "en": {"C": "Center", "L": "Left wing", "R": "Right wing",
           "D": "Defense", "G": "Goalie"},
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
        "contre": "contre", "a": "à", "chances": "de chances de gagner",
        "pts": "pts",
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
        "cta": "Ouvrir l'application",
        "cta_note": ("Le plan complet, la carte des matchups, la précision des "
                     "sources. Si l'application dort, le premier chargement "
                     "peut prendre une minute ou deux — ce n'est pas une "
                     "panne."),
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
        "contre": "vs", "a": "at", "chances": "win probability",
        "pts": "pts",
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
        "cta": "Open the app",
        "cta_note": ("The full plan, the matchup map, source accuracy. If the "
                     "app is asleep, the first load can take a minute or two — "
                     "it is not broken."),
        "autre_langue": "Version française",
        "pied": ("Sources: The Odds API, MoneyPuck, Dimers, Puckcast, the "
                 "public NHL API. Personal project, not affiliated with the "
                 "NHL. These numbers are estimates, not certainties."),
    },
}

POLICE = ("https://fonts.googleapis.com/css2?"
          "family=Barlow+Condensed:wght@600;700&display=swap")

CSS = """
:root{color-scheme:dark;--carte:#232329;--bord:#34343d;--encre:#fff;
--encre2:#c3c2b7;--accent:#3987e5}
*{box-sizing:border-box}
body{margin:0;color:var(--encre);
background:radial-gradient(1100px 620px at 74% -12%,var(--lueur),transparent 62%),
linear-gradient(176deg,#1d1d23,#131316) fixed;
font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif}
.page{max-width:860px;margin:0 auto;padding:2.5rem 1.2rem 4rem}
h1{font-size:2rem;line-height:1.15;margin:0 0 .3rem}
h2{font-size:1.15rem;margin:2.4rem 0 .6rem}
.maj{color:var(--encre2);font-size:.9rem;margin:0 0 2rem}
.lang{float:right;font-size:.9rem}

/* Bandeau : photo d'action fondue à droite, lueur aux couleurs de l'équipe.
   Le texte reste à gauche, sur la partie restée sombre : aucune valeur ne
   se lit par-dessus une image. */
.hero{position:relative;overflow:hidden;isolation:isolate;display:flex;
align-items:center;gap:1.8rem;flex-wrap:wrap;border:1px solid var(--bord);
border-radius:1.1rem;padding:1.5rem 1.8rem;margin:0 0 1.6rem;
background:linear-gradient(135deg,#26262e,#17171c);
box-shadow:0 18px 46px rgba(0,0,0,.45)}
.hero::before{content:"";position:absolute;inset:-14% -4% -14% 30%;z-index:-2;
background:var(--photo) center/cover;filter:blur(2px) saturate(.85);opacity:.6;
-webkit-mask-image:linear-gradient(to right,transparent 4%,#000 58%);
mask-image:linear-gradient(to right,transparent 4%,#000 58%)}
.hero-txt{flex:1 1 240px;min-width:0;position:relative}
.hero-eq{font-family:"Barlow Condensed",system-ui,sans-serif;font-size:3rem;
font-weight:700;line-height:1.02;margin:0}
.hero-de{color:var(--encre2);margin-top:.3rem}
.hero-pc{flex:none;text-align:right;min-width:150px;position:relative;
background:rgba(16,16,20,.62);backdrop-filter:blur(10px);
border:1px solid rgba(255,255,255,.1);border-radius:.9rem;padding:.8rem 1.1rem}
.hero-pc b{font-family:"Barlow Condensed",system-ui,sans-serif;font-size:3.4rem;
font-weight:700;line-height:.95;display:block}
.hero-pc span{font-size:.7rem;letter-spacing:.1em;text-transform:uppercase;
color:var(--encre2)}

/* Carte de hockey : proportions 5:7, cadre argenté, photo d'action en décor
   et portrait détouré par-dessus — les photos d'action ne sont pas cadrées
   sur le joueur, le portrait garantit qu'on voit le bon. */
.carte{flex:none;width:186px;aspect-ratio:5/7;padding:7px;border-radius:.55rem;
background:linear-gradient(150deg,#fff,#d6d9dd 26%,#f6f7f8 48%,#bfc3c8 74%,#eef0f2);
box-shadow:0 8px 22px rgba(0,0,0,.5)}
.carte-in{position:relative;height:100%;border-radius:.28rem;overflow:hidden;
background:#0e0e0d}
.carte-fond{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;
object-position:50% 30%;filter:blur(3px) brightness(.55)}
.carte-vis{position:absolute;bottom:16px;left:50%;transform:translateX(-50%);
width:124%;max-width:none;filter:drop-shadow(0 4px 10px rgba(0,0,0,.55))}
.carte-logo{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
width:66%}
.plaque{position:absolute;left:0;right:0;bottom:0;padding:5px 6px 6px;
background:linear-gradient(#fdfdfd,#e4e6e9)}
.plaque-nom{font-family:"Barlow Condensed",system-ui,sans-serif;font-size:.92rem;
font-weight:700;letter-spacing:.03em;text-transform:uppercase;color:#15151a;
text-align:center;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.plaque-l{display:flex;align-items:center;gap:4px;margin-top:3px}
.jeton{flex:1 1 0;min-width:0;border-radius:2px;padding:2px;
font-family:"Barlow Condensed",system-ui,sans-serif;font-size:.58rem;
font-weight:600;letter-spacing:.07em;text-transform:uppercase;text-align:center;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ecusson{flex:none;width:26px;height:26px;border-radius:50%;background:#fff;
display:flex;align-items:center;justify-content:center;
box-shadow:0 0 0 1px rgba(0,0,0,.18)}
.ecusson img{width:20px;height:20px}

table{width:100%;border-collapse:collapse;margin:.6rem 0 0;font-size:.95rem;
background:rgba(35,35,42,.62);border:1px solid var(--bord);border-radius:1rem;
overflow:hidden}
th,td{text-align:left;padding:.55rem .7rem;border-bottom:1px solid var(--bord)}
th{color:var(--encre2);font-weight:600;font-size:.82rem;text-transform:uppercase;
letter-spacing:.06em}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
td.eq{display:flex;align-items:center;gap:.5rem}
td.eq img{width:22px;height:22px;flex:none}
p{color:#ddd}
.cta{display:flex;align-items:center;gap:1rem;flex-wrap:wrap;
background:rgba(35,35,42,.62);border:1px solid var(--bord);border-radius:1rem;
padding:1rem 1.2rem;margin:0 0 1.6rem}
.cta a{flex:none;background:var(--accent);color:#fff;text-decoration:none;
font-weight:600;padding:.6rem 1.3rem;border-radius:.6rem}
.cta a:hover{filter:brightness(1.1)}
.cta span{flex:1 1 220px;color:var(--encre2);font-size:.88rem}
.note{color:var(--encre2);font-size:.9rem}
a{color:var(--accent)}
footer{margin-top:3rem;padding-top:1.2rem;border-top:1px solid var(--bord);
color:var(--encre2);font-size:.85rem}
@media(max-width:620px){.hero{gap:1.1rem;padding:1.2rem}.carte{width:140px}
.hero-eq{font-size:2rem}.hero-pc{text-align:left}.hero-pc b{font-size:2.6rem}}
"""

# Où vit chaque langue. Le français est la racine ; l'anglais un sous-dossier.
CHEMINS = {"fr": ("index.html", f"{BASE}/"),
           "en": ("en/index.html", f"{BASE}/en/")}

# La politique de confidentialité. Google l'exige pour publier l'écran de
# consentement OAuth en production : sans elle, seuls les comptes inscrits
# comme « utilisateurs de test » peuvent se connecter.
CONFID = {"fr": ("confidentialite.html", f"{BASE}/confidentialite.html"),
          "en": ("en/privacy.html", f"{BASE}/en/privacy.html")}

DEPOT = "https://github.com/marekdoucet/pool-survivor"

VIE_PRIVEE = {
    "fr": {
        "titre": "Politique de confidentialité",
        "desc": "Ce que Pool Survivor LNH recueille, pourquoi, et ce qu'il n'en fait pas.",
        "retour": "Retour au pick de la semaine",
        "intro": ("Pool Survivor LNH est un outil personnel, gratuit, sans "
                  "publicité et sans mouchard. Cette page dit exactement ce "
                  "qu'il recueille et ce qu'il en fait."),
        "sections": [
            ("Ce qui est recueilli", [
                "Quand tu te connectes avec Google, l'application reçoit ton "
                "<b>adresse courriel</b> et ton <b>prénom</b>. Rien d'autre : "
                "ni tes contacts, ni ton agenda, ni tes fichiers.",
                "Ce que tu saisis toi-même : les <b>noms des membres de ton "
                "pool</b>, leurs <b>choix d'équipe</b> et les corrections "
                "manuelles d'élimination.",
            ]),
            ("Ton mot de passe", [
                "L'application ne le voit jamais. C'est Google qui vérifie ton "
                "identité et qui ne renvoie qu'une confirmation signée. Il n'y "
                "a aucun mot de passe à stocker ici, donc aucun à perdre.",
            ]),
            ("Pourquoi ton courriel", [
                "Il sert uniquement de clé : c'est ce qui permet de te montrer "
                "ton pool et pas celui de quelqu'un d'autre. Il n'est jamais "
                "affiché aux autres personnes, jamais envoyé à qui que ce soit, "
                "et ne sert à aucun envoi de courriel.",
            ]),
            ("Où c'est rangé", [
                "Dans une base de données PostgreSQL hébergée par "
                "<a href='https://neon.com'>Neon</a>, accessible seulement par "
                "l'application. Les données publiques de hockey — calendrier, "
                "cotes, modèles — sont stockées séparément et ne contiennent "
                "rien de personnel.",
            ]),
            ("Ce qui n'est pas fait", [
                "Aucune publicité. Aucun outil de mesure d'audience. Aucun "
                "cookie de pistage. Rien n'est vendu, loué ni partagé avec un "
                "tiers. Les données ne servent à rien d'autre qu'à faire "
                "fonctionner ton pool.",
            ]),
            ("Effacer tes données", [
                "Demande-le et ta ligne est supprimée, avec tout ce qu'elle "
                "contient. La demande se fait par le "
                "<a href='" + DEPOT + "/issues'>dépôt du projet</a>.",
            ]),
        ],
    },
    "en": {
        "titre": "Privacy policy",
        "desc": "What NHL Pool Survivor collects, why, and what it never does with it.",
        "retour": "Back to this week's pick",
        "intro": ("NHL Pool Survivor is a personal, free tool with no ads and "
                  "no trackers. This page states exactly what it collects and "
                  "what it does with it."),
        "sections": [
            ("What is collected", [
                "When you sign in with Google, the app receives your "
                "<b>email address</b> and <b>first name</b>. Nothing else: not "
                "your contacts, not your calendar, not your files.",
                "What you type in yourself: the <b>names of your pool "
                "members</b>, their <b>team picks</b>, and any manual "
                "elimination overrides.",
            ]),
            ("Your password", [
                "The app never sees it. Google verifies your identity and "
                "returns only a signed confirmation. There is no password "
                "stored here, so there is none to lose.",
            ]),
            ("Why your email", [
                "It is used only as a key: it is what lets the app show you "
                "your pool rather than someone else's. It is never shown to "
                "other people, never sent to anyone, and never used to email "
                "you.",
            ]),
            ("Where it is kept", [
                "In a PostgreSQL database hosted by "
                "<a href='https://neon.com'>Neon</a>, reachable only by the "
                "app. Public hockey data — schedule, odds, models — is stored "
                "separately and holds nothing personal.",
            ]),
            ("What is never done", [
                "No advertising. No analytics. No tracking cookies. Nothing is "
                "sold, rented or shared with a third party. The data serves no "
                "purpose beyond running your pool.",
            ]),
            ("Deleting your data", [
                "Ask, and your row is deleted along with everything in it. "
                "Requests go through the "
                "<a href='" + DEPOT + "/issues'>project repository</a>.",
            ]),
        ],
    },
}


def nom(tri, lang):
    return TEAMS[lang].get(tri, tri)


def logo(tri, mode="dark"):
    return LOGO.format(tri=tri, mode=mode)


def rgba(hexa, alpha):
    """'#236192' + 0.3 → 'rgba(35,97,146,0.3)'."""
    h = hexa.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def encre(bg):
    """Noir ou blanc sur `bg`, selon le meilleur contraste (luminance WCAG)."""
    h = bg.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    ch = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        ch.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    lum = 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    return "#11110f" if (lum + 0.05) / 0.05 > 1.05 / (lum + 0.05) else "#fff"


def joueurs(chemin=JOUEURS):
    """tricode → meneur de l'équipe. Absent = carte réduite au logo."""
    chemin = Path(chemin)
    if not chemin.exists():
        return {}
    with io.open(chemin, encoding="utf-8") as f:
        return {r["team"]: r for r in csv.DictReader(f)}


def fr_date(d, lang):
    mois = MOIS[lang][d.month - 1]
    return f"{d.day} {mois}" if lang == "fr" else f"{mois} {d.day}"


def fr_jour(iso, lang):
    d = dt.date.fromisoformat(iso)
    return f"{JOURS[lang][d.weekday()]} {fr_date(d, lang)}"


def pourcent(p, lang):
    return f"{p * 100:.1f} %" if lang == "fr" else f"{p * 100:.1f}%"


def carte(tri, joueur, lang):
    """La carte de hockey. Sans joueur connu, le logo prend toute la place."""
    e = _html.escape
    if not joueur or not joueur.get("action"):
        return (f'<div class="carte"><div class="carte-in">'
                f'<img class="carte-logo" src="{logo(tri)}" alt="" loading="lazy">'
                f'</div></div>')
    coul = joueur.get("color") or "#3a3a35"
    poste = POSTES[lang].get(joueur.get("position", ""), joueur.get("position", ""))
    qui = f"{joueur['first_name']} {joueur['last_name']}"
    jeton = f'class="jeton" style="background:{e(coul)};color:{encre(coul)}"'
    visage = (f'<img class="carte-vis" src="{e(joueur["headshot"])}" '
              f'alt="{e(qui)}" loading="lazy">' if joueur.get("headshot") else "")
    return (
        f'<div class="carte"><div class="carte-in">'
        f'<img class="carte-fond" src="{e(joueur["action"])}" alt="" loading="lazy">'
        f'{visage}'
        f'<div class="plaque"><div class="plaque-nom">{e(qui)}</div>'
        f'<div class="plaque-l"><span {jeton}>{e(poste)}</span>'
        f'<span class="ecusson"><img src="{logo(tri, "light")}" alt="" '
        f'loading="lazy"></span>'
        f'<span {jeton}>{joueur["points"]} {T[lang]["pts"]}</span>'
        f'</div></div></div></div>')


def rendu(ctx, lang):
    """Le HTML complet d'une langue, à partir d'un dictionnaire simple.

    Fonction pure : c'est elle que testent les tests, sans base ni réseau.
    """
    e, t = _html.escape, T[lang]
    best = ctx["meilleur"]
    vedette = ctx["joueurs"].get(best["team"])
    eq = nom(best["team"], lang)
    titre = t["titre"].format(equipe=eq)
    desc = t["desc"].format(equipe=eq, p=pourcent(best["p"], lang),
                            adv=nom(best["opponent"], lang))
    autre = "en" if lang == "fr" else "fr"

    coul = (vedette or {}).get("color") or "#3987e5"
    photo = (f"url('{e((vedette or {}).get('action', ''))}')"
             if vedette and vedette.get("action") else "none")
    style_page = f"--lueur:{rgba(coul, 0.26)}"
    style_hero = f"--photo:{photo}"

    lignes = "\n".join(
        f'<tr><td class=eq><img src="{logo(o["team"])}" alt="" loading="lazy">'
        f'{e(nom(o["team"], lang))}</td>'
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
<html lang="{lang}" style="{style_page}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(titre)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{CHEMINS[lang][1]}">
<meta name="google-site-verification" content="{VERIF_GOOGLE}">
<link rel="alternate" hreflang="fr" href="{CHEMINS['fr'][1]}">
<link rel="alternate" hreflang="en" href="{CHEMINS['en'][1]}">
<link rel="alternate" hreflang="x-default" href="{CHEMINS['fr'][1]}">
<meta property="og:type" content="website">
<meta property="og:title" content="{e(titre)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{CHEMINS[lang][1]}">
<meta property="og:image" content="{e((vedette or {}).get('action', ''))}">
<meta property="og:locale" content="{'fr_CA' if lang == 'fr' else 'en_CA'}">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{POLICE}">
<style>{CSS}</style>
</head>
<body>
<main class="page">
<a class="lang" href="{CHEMINS[autre][1]}">{t['autre_langue']}</a>
<h1>{t['h1']}</h1>
<p class="maj">{t['maj'].format(date=e(ctx['maj'][lang]), saison=SAISON)}</p>

<div class="hero" style="{style_hero}">
{carte(best['team'], vedette, lang)}
<div class="hero-txt">
  <h2 class="hero-eq">{e(eq)}</h2>
  <div class="hero-de">{t['contre'] if best['home'] else t['a']}
  {e(nom(best['opponent'], lang))}, {e(ctx['jour_match'][lang])}</div>
</div>
<div class="hero-pc"><b>{pourcent(best['p'], lang)}</b>
<span>{t['chances']}</span></div>
</div>

<div class="cta">
<a href="{APP}" rel="noopener">{t['cta']}</a>
<span>{t['cta_note']}</span>
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


def confidentialite(lang, maj=None):
    """La page de politique de confidentialité, dans une langue.

    Fonction pure, comme rendu() : testable sans base ni réseau. Elle reprend
    la même feuille de style pour que la vitrine reste d'un seul tenant.
    """
    t = VIE_PRIVEE[lang]
    autre = "en" if lang == "fr" else "fr"
    corps = []
    for titre, paragraphes in t["sections"]:
        corps.append(f"<h2>{_html.escape(titre)}</h2>")
        # Les paragraphes contiennent du balisage voulu (<b>, <a>) : ils sont
        # écrits ici, pas saisis par un visiteur, donc pas échappés.
        corps += [f"<p>{p}</p>" for p in paragraphes]

    maj = maj or dt.date.today().isoformat()
    # --lueur est posee sur <html> par la page d'accueil, aux couleurs de
    # l'equipe du jour. Ici il n'y a pas d'equipe, mais le fond du body
    # l'utilise : sans valeur, le degrade devient invalide et la page
    # s'affiche en blanc. On met donc la couleur d'accent, en sourdine.
    return f"""<!doctype html>
<html lang="{lang}" style="--lueur:rgba(57,135,229,.16)">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_html.escape(t["titre"])} — Pool Survivor</title>
<meta name="description" content="{_html.escape(t["desc"])}">
<link rel="canonical" href="{CONFID[lang][1]}">
<link rel="alternate" hreflang="fr" href="{CONFID['fr'][1]}">
<link rel="alternate" hreflang="en" href="{CONFID['en'][1]}">
<link rel="alternate" hreflang="x-default" href="{CONFID['fr'][1]}">
<meta name="google-site-verification" content="{VERIF_GOOGLE}">
<style>{CSS}</style>
</head>
<body>
<main class="page">
<a class="lang" href="{CONFID[autre][1]}">{"English" if lang == "fr" else "Français"}</a>
<h1>{_html.escape(t["titre"])}</h1>
<p class="maj">{"Mise à jour" if lang == "fr" else "Last updated"} : {maj}</p>
<p>{t["intro"]}</p>
{chr(10).join(corps)}
<footer><a href="{CHEMINS[lang][1]}">{_html.escape(t["retour"])}</a></footer>
</main>
</body>
</html>
"""


def sitemap(maj):
    """Les quatre pages, pour que les robots les trouvent d'un coup.

    Les deux vitrines changent tous les jours ; les deux pages de
    confidentialité presque jamais. Le dire evite de faire repasser un robot
    pour rien.
    """
    quotidien = [(u, "daily") for _c, u in CHEMINS.values()]
    rare = [(u, "yearly") for _c, u in CONFID.values()]
    urls = "".join(
        f"<url><loc>{u}</loc><lastmod>{maj}</lastmod>"
        f"<changefreq>{freq}</changefreq></url>"
        for u, freq in quotidien + rare)
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
        "joueurs": joueurs(),
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
    fichiers.update({chemin: confidentialite(lang, maj)
                     for lang, (chemin, _u) in CONFID.items()})
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
