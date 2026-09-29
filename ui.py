"""
Couche présentation : thème sombre, logos d'équipes, carte du pick.

Séparé de app.py pour que la logique du pool reste lisible. Rien ici ne
calcule : ce module ne fait que mettre en forme des valeurs décidées ailleurs.
Les classes CSS sont toutes préfixées « ps- » et les règles qui risquent
d'entrer en conflit avec Streamlit utilisent deux classes : on ne dépend donc
pas de ses noms internes, qui changent d'une version à l'autre.
"""

import html

import altair as alt
import streamlit as st

# Logos officiels de la LNH, en SVG (nets à n'importe quelle taille).
# « dark » = variante conçue POUR un fond sombre (sur les logos noirs, le noir
# y devient la couleur claire : BOS passe au or) ; « light » pour fond clair.
LOGO_URL = "https://assets.nhle.com/logos/nhl/svg/{tri}_{mode}.svg"

POSITIONS = {"C": "Centre", "L": "Ailier gauche", "R": "Ailier droit",
             "D": "Défenseur", "G": "Gardien"}

# Noms français, convention des médias d'ici (« Avalanche du Colorado »).
TEAMS = {
    "ANA": "Ducks d'Anaheim",          "BOS": "Bruins de Boston",
    "BUF": "Sabres de Buffalo",        "CAR": "Hurricanes de la Caroline",
    "CBJ": "Blue Jackets de Columbus", "CGY": "Flames de Calgary",
    "CHI": "Blackhawks de Chicago",    "COL": "Avalanche du Colorado",
    "DAL": "Stars de Dallas",          "DET": "Red Wings de Détroit",
    "EDM": "Oilers d'Edmonton",        "FLA": "Panthers de la Floride",
    "LAK": "Kings de Los Angeles",     "MIN": "Wild du Minnesota",
    "MTL": "Canadiens de Montréal",    "NJD": "Devils du New Jersey",
    "NSH": "Predators de Nashville",   "NYI": "Islanders de New York",
    "NYR": "Rangers de New York",      "OTT": "Sénateurs d'Ottawa",
    "PHI": "Flyers de Philadelphie",   "PIT": "Penguins de Pittsburgh",
    "SEA": "Kraken de Seattle",        "SJS": "Sharks de San Jose",
    "STL": "Blues de St. Louis",       "TBL": "Lightning de Tampa Bay",
    "TOR": "Maple Leafs de Toronto",   "UTA": "Mammoth de l'Utah",
    "VAN": "Canucks de Vancouver",     "VGK": "Golden Knights de Vegas",
    "WPG": "Jets de Winnipeg",         "WSH": "Capitals de Washington",
}


def logo(tri, mode="dark"):
    return LOGO_URL.format(tri=tri, mode=mode)


def name(tri):
    """Nom complet de l'équipe ; le tricode si on ne le connaît pas."""
    return TEAMS.get(tri, tri)


def rgba(bg, alpha):
    """'#236192' + 0.4 → 'rgba(35,97,146,0.4)'.

    Calculé ici plutôt qu'avec color-mix() en CSS : une teinte d'équipe arrive
    sous forme de texte depuis players.csv, et rgba() marche partout.
    """
    h = bg.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def ink(bg):
    """Encre noire ou blanche sur `bg`, selon le meilleur contraste.

    Calculé (luminance WCAG) et non choisi à l'œil : l'or des Bruins réclame
    du noir, le marine des Leafs du blanc, et ni l'un ni l'autre ne se devine.
    """
    h = bg.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    chan = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        chan.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    lum = 0.2126 * chan[0] + 0.7152 * chan[1] + 0.0722 * chan[2]
    # contraste avec le noir : (L+0.05)/0.05 ; avec le blanc : 1.05/(L+0.05)
    return "#11110f" if (lum + 0.05) / 0.05 > 1.05 / (lum + 0.05) else "#ffffff"


# @import plutôt que theme.fontFaces : l'URL de l'API Google Fonts est stable,
# alors que les URL de fichiers .woff2 qu'elle sert peuvent changer.
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&display=swap');

/* Fond de page : un dégradé plutôt qu'un noir plat, avec une lueur froide en
   haut. La page du pick remplace ensuite cette lueur par la couleur de
   l'équipe (voir backdrop()). Les valeurs restent sur des surfaces opaques :
   le dégradé habille le vide entre les panneaux, jamais les données. */
:root { --ps-glow: rgba(57, 135, 229, 0.22); }
[data-testid="stApp"] {
  background:
    radial-gradient(1100px 620px at 74% -12%, var(--ps-glow), transparent 62%),
    radial-gradient(760px 520px at 8% 4%, rgba(144, 133, 233, 0.10), transparent 60%),
    linear-gradient(176deg, #1d1d23 0%, #17171b 42%, #131316 100%);
  background-attachment: fixed;
}
[data-testid="stSidebarContent"] {
  background: linear-gradient(190deg, #111116 0%, #0c0c0f 100%);
}
/* La barre du haut de Streamlit est opaque par défaut et coupait le dégradé. */
[data-testid="stHeader"] { background: transparent; }

/* Panneau : la surface sur laquelle poser un bloc de contenu. Translucide
   pour laisser passer le dégradé, assez opaque pour que le texte garde son
   contraste.
   Cible : la classe « st-key-… » que Streamlit ajoute quand on donne une clé
   à un conteneur. C'est documenté et stable, contrairement aux classes
   « st-emotion-cache-… » qui changent d'une version à l'autre. */
[class*="st-key-ps-panel"] {
  background: rgba(35, 35, 42, 0.62) !important;
  border: 1px solid #34343d !important;
  border-radius: 1rem !important;
  padding: 1.2rem 1.4rem !important;
  backdrop-filter: blur(10px);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.3);
}
.ps-panel-title {
  font-size: 0.72rem; font-weight: 600; letter-spacing: 0.14em;
  text-transform: uppercase; color: #c3c2b7;
  margin: 0 0 0.15rem; display: flex; align-items: center; gap: 0.5rem;
}
.ps-panel-note { color: #9b9a90; font-size: 0.86rem; margin-bottom: 0.4rem; }

/* Tuiles : un chiffre qui compte, pas une phrase. */
.ps-tiles {
  display: flex; gap: 0.75rem; flex-wrap: wrap; margin: 0.2rem 0 0.9rem;
}
.ps-tile {
  flex: 1 1 150px; min-width: 130px;
  background: rgba(16, 16, 20, 0.5); border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 0.85rem; padding: 0.8rem 1rem;
}
.ps-tile-label {
  font-size: 0.68rem; letter-spacing: 0.1em; text-transform: uppercase;
  color: #9b9a90; margin-bottom: 0.25rem;
}
.ps-tile-value {
  font-family: 'Barlow Condensed', system-ui, sans-serif;
  font-size: 2.1rem; font-weight: 700; line-height: 1; color: #ffffff;
}
.ps-tile-sub { font-size: 0.78rem; color: #9b9a90; margin-top: 0.2rem; }
.ps-tile-accent { border-left: 3px solid var(--ps-accent, #3987e5); }

/* Tout graphique et tout tableau reçoit la même surface, sans qu'on ait à
   toucher aux sept sections : :has() cible le conteneur d'élément qui en
   contient un. Les blocs déjà posés dans un panneau sont neutralisés juste
   après, sinon on empilerait deux cadres. */
[data-testid="stElementContainer"]:has(> [data-testid="stVegaLiteChart"]),
[data-testid="stElementContainer"]:has(> [data-testid="stDataFrame"]),
[data-testid="stElementContainer"]:has(> [data-testid="stDataFrameResizable"]) {
  background: rgba(35, 35, 42, 0.62); border: 1px solid #34343d;
  border-radius: 1rem; padding: 1rem 1.1rem; margin-bottom: 0.9rem;
  backdrop-filter: blur(10px);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.3);
}
[class*="st-key-ps-panel"] [data-testid="stElementContainer"]:has(> [data-testid="stVegaLiteChart"]),
[class*="st-key-ps-panel"] [data-testid="stElementContainer"]:has(> [data-testid="stDataFrame"]),
[class*="st-key-ps-panel"] [data-testid="stElementContainer"]:has(> [data-testid="stDataFrameResizable"]) {
  background: none; border: 0; border-radius: 0; padding: 0;
  backdrop-filter: none; box-shadow: none;
}

/* Les titres de section (#### en markdown) prennent l'allure des libellés de
   panneau, pour que tout le site parle la même langue. */
[data-testid="stMarkdownContainer"] h4 {
  font-size: 0.78rem !important; font-weight: 600; letter-spacing: 0.13em;
  text-transform: uppercase; color: #c3c2b7; margin: 1.4rem 0 0.3rem;
}

/* Une équipe dans une phrase : logo + tricode, alignés sur la ligne de base. */
.ps-team-inline {
  display: inline-flex; align-items: center; gap: 0.35rem;
  vertical-align: -0.28em;
}
.ps-team-inline img { width: 22px; height: 22px; }

/* Bandeau du pick : la photo d'action déborde à droite et se fond dans le
   panneau, une lueur à la couleur de l'équipe derrière. La photo de fond est
   floutée et désaturée pour rester une texture — c'est la carte, nette, qui
   porte l'information. Tout le texte reste sur la moitié gauche, au-dessus
   d'un voile opaque : aucune valeur ne se lit par-dessus une image. */
.ps-hero {
  position: relative; overflow: hidden; isolation: isolate;
  display: flex; align-items: center; gap: 1.9rem; flex-wrap: wrap;
  background: linear-gradient(135deg, #26262e 0%, #1b1b21 55%, #17171c 100%);
  border: 1px solid #34343d; border-radius: 1.1rem;
  padding: 1.6rem 1.9rem; margin: 0.5rem 0 1.25rem;
  box-shadow: 0 18px 46px rgba(0, 0, 0, 0.45);
}
.ps-hero::before {          /* photo d'action, débordante et fondue */
  content: ""; position: absolute; inset: -14% -4% -14% 30%; z-index: -2;
  background-image: var(--ps-shot); background-size: cover;
  background-position: 46% 14%;
  filter: blur(2px) saturate(0.85) contrast(1.05);
  opacity: 0.6;
  -webkit-mask-image: linear-gradient(to right, transparent 4%, #000 58%);
  mask-image: linear-gradient(to right, transparent 4%, #000 58%);
}
.ps-hero::after {           /* lueur à la couleur de l'équipe */
  content: ""; position: absolute; inset: 0; z-index: -1; pointer-events: none;
  background: radial-gradient(760px 300px at 72% -30%, var(--ps-glow), transparent 68%);
}
.ps-hero-body { flex: 1 1 260px; min-width: 0; position: relative; }
/* Carte qui prolonge le panneau juste au-dessus : les deux se lisent comme un
   seul bloc, au lieu de deux cadres empilés. */
.ps-hero.ps-hero-suite {
  margin-top: -0.9rem; border-top-left-radius: 0.25rem;
  border-top-right-radius: 0.25rem;
}
.ps-kicker {
  font-size: 0.72rem; font-weight: 600; letter-spacing: 0.14em;
  text-transform: uppercase; color: #c3c2b7; margin-bottom: 0.35rem;
}
.ps-hero .ps-team {
  font-family: 'Barlow Condensed', system-ui, sans-serif;
  font-size: 3.1rem; font-weight: 700; line-height: 1.02;
  letter-spacing: 0.01em; color: #ffffff; margin: 0;
}
.ps-matchup { color: #c3c2b7; font-size: 0.95rem; margin-top: 0.3rem; }
/* Le nombre tombe sur la partie éclairée de la photo : il lui faut sa propre
   surface, sinon le contraste dépend du maillot du joueur. */
.ps-hero-num {
  flex: none; text-align: right; min-width: 148px; position: relative;
  background: rgba(16, 16, 20, 0.62); backdrop-filter: blur(10px);
  border: 1px solid rgba(255, 255, 255, 0.10); border-radius: 0.9rem;
  padding: 0.85rem 1.1rem 0.75rem;
}
.ps-hero .ps-pct {
  font-family: 'Barlow Condensed', system-ui, sans-serif;
  font-size: 4.2rem; font-weight: 700; line-height: 0.95; color: #ffffff;
}
.ps-hero .ps-pct span { font-size: 2rem; color: #c3c2b7; margin-left: 0.1rem; }
.ps-pct-label {
  font-size: 0.72rem; letter-spacing: 0.1em; text-transform: uppercase;
  color: #c3c2b7;
}

/* Carte de hockey : proportions d'une vraie carte (5:7), cadre argenté,
   photo d'action pleine carte, bandeau de nom en bas.
   La photo source est en 16:9 avec le joueur au centre, donc « cover » sur un
   cadre portrait ne rogne que les côtés et jamais la tête. */
.ps-card {
  flex: none; width: 202px; aspect-ratio: 5 / 7; padding: 7px;
  border-radius: 0.55rem;
  background: linear-gradient(150deg, #ffffff 0%, #d6d9dd 26%, #f6f7f8 48%,
                              #bfc3c8 74%, #eef0f2 100%);
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.5);
}
.ps-card-inner {
  position: relative; height: 100%; border-radius: 0.28rem;
  overflow: hidden; background: #0e0e0d;
}
/* Deux classes, et « height » forcée : Streamlit impose height:auto à toutes
   les images de ses blocs markdown, ce qui laissait la photo occuper le haut
   de la carte et une bande noire en dessous.
   La photo d'action sert de DÉCOR : floutée et assombrie. Le joueur, lui, est
   le portrait détouré posé par-dessus. Cadrer la photo d'action sur le joueur
   était impossible — elles ne sont pas centrées sur lui (sur celle de
   McDavid, c'est le gardien adverse qui occupe le centre). */
.ps-card-inner .ps-card-shot {
  position: absolute; inset: 0; width: 100%; height: 100% !important;
  object-fit: cover; object-position: 50% 30%;
  filter: blur(3px) brightness(0.55) saturate(0.9);
  transform: scale(1.06);          /* masque le bord flou */
}
/* max-width: none — Streamlit borne toutes ses images à 100 %, ce qui rendait
   tout élargissement du portrait sans effet. */
.ps-card-inner .ps-card-face {
  position: absolute; bottom: 16px; left: 50%;
  transform: translateX(-50%);
  width: 124%; max-width: none !important; height: auto !important;
  filter: drop-shadow(0 4px 10px rgba(0, 0, 0, 0.55));
}
.ps-card-inner .ps-card-logo {
  position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
  width: 66%; height: auto;
}
.ps-card-plate {
  position: absolute; left: 0; right: 0; bottom: 0;
  background: linear-gradient(#fdfdfd, #e4e6e9); padding: 5px 6px 6px;
  box-shadow: 0 -1px 0 rgba(0, 0, 0, 0.18);
}
.ps-plate-name {
  font-family: 'Barlow Condensed', system-ui, sans-serif;
  font-size: 0.95rem; font-weight: 700; letter-spacing: 0.03em;
  text-transform: uppercase; color: #15151a; text-align: center;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ps-plate-row { display: flex; align-items: center; gap: 4px; margin-top: 3px; }
.ps-plate-chip {
  flex: 1 1 0; min-width: 0; border-radius: 2px; padding: 2px;
  font-family: 'Barlow Condensed', system-ui, sans-serif;
  font-size: 0.6rem; font-weight: 600; letter-spacing: 0.07em;
  text-transform: uppercase; text-align: center;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ps-plate-badge {
  flex: none; width: 27px; height: 27px; border-radius: 50%; background: #ffffff;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.18);
}
.ps-plate-badge img { width: 21px; height: 21px; }

/* Une pastille par source. La couleur est décorative : le nom de la source
   est toujours écrit, jamais porté par la couleur seule. */
.ps-chips { display: flex; gap: 0.6rem; flex-wrap: wrap; margin-bottom: 1.1rem; }
.ps-chip {
  display: flex; align-items: baseline; gap: 0.45rem;
  background: rgba(36, 36, 42, 0.72); border: 1px solid #34343d;
  border-radius: 999px; padding: 0.45rem 0.95rem;
  backdrop-filter: blur(8px);
}
.ps-dot { width: 9px; height: 9px; border-radius: 50%; flex: none;
          align-self: center; }
.ps-chip-name { font-size: 0.82rem; color: #c3c2b7; }
.ps-chip-val { font-size: 0.95rem; font-weight: 600; color: #ffffff; }
/* Source sans cote : la pastille reste, en retrait. « Pas encore de cote »
   dit quelque chose ; une pastille absente ne dit rien. */
.ps-chip.ps-vide { opacity: 0.55; }
.ps-chip.ps-vide .ps-chip-val { color: #c3c2b7; font-weight: 500; }

@media (max-width: 640px) {
  .ps-hero { gap: 1.1rem; padding: 1.15rem; }
  .ps-card { width: 150px; }
  .ps-hero .ps-team { font-size: 2rem; }
  .ps-hero-num { text-align: left; }
  .ps-hero .ps-pct { font-size: 3rem; }
}
</style>
"""


SURFACE = "#1a1a19"        # même valeur que backgroundColor dans config.toml
ENCRE = "#ffffff"
ENCRE_2 = "#c3c2b7"
GRILLE = "#2e2e36"


def altair_theme():
    """Habillage commun des graphiques : grille discrète, axes en retrait.

    Ne touche PAS aux couleurs des séries : elles viennent de
    theme.chartCategoricalColors dans config.toml, c'est-à-dire de la palette
    validée. Un thème Altair qui redéfinirait `range` entrerait en conflit
    avec elle et casserait les garanties de lisibilité pour le daltonisme.
    """
    @alt.theme.register("pool-survivor", enable=True)
    def _theme():
        axe = {
            "labelColor": ENCRE_2, "titleColor": ENCRE_2,
            "labelFontSize": 12, "titleFontSize": 12, "titleFontWeight": 500,
            "gridColor": GRILLE, "gridOpacity": 0.7, "gridWidth": 1,
            "domainColor": GRILLE, "tickColor": GRILLE,
            "labelPadding": 6, "titlePadding": 10,
        }
        return {"config": {
            # transparent : c'est le panneau vitré qui fournit la surface
            "background": "transparent",
            "view": {"stroke": None, "continuousWidth": 400, "continuousHeight": 300},
            "axis": axe,
            "axisX": {"grid": False},        # la grille verticale n'apporte rien
            "axisY": {"domain": False, "ticks": False},
            "legend": {
                "labelColor": ENCRE_2, "titleColor": ENCRE_2,
                "labelFontSize": 12, "titleFontSize": 11,
                "symbolType": "circle", "symbolSize": 90,
                "orient": "bottom", "direction": "horizontal",
                "offset": 12, "padding": 4, "titlePadding": 6,
            },
            "title": {"color": ENCRE, "fontSize": 14, "fontWeight": 600,
                      "anchor": "start", "offset": 10},
            "text": {"color": ENCRE_2},
            "line": {"strokeWidth": 2},
            "point": {"size": 70, "filled": True},
            "rect": {"cornerRadius": 3},
        }}


def inject_css():
    """À appeler une fois, juste après st.set_page_config."""
    st.markdown(CSS, unsafe_allow_html=True)
    altair_theme()


def backdrop(color):
    """Teinte la lueur du fond de page avec la couleur de l'équipe."""
    if color:
        st.markdown(f"<style>:root{{--ps-glow:{rgba(color, 0.26)}}}</style>",
                    unsafe_allow_html=True)


_cles = {}


def panel(cle, titre=None, note=None):
    """Conteneur vitré : `with ui.panel("plan", "Titre"):`.

    La clé sert à la fois d'identité pour Streamlit et de prise CSS. Elle doit
    être unique dans un affichage ; un compteur dédoublonne au besoin, sinon
    Streamlit lève une erreur de clé répétée.
    """
    n = _cles[cle] = _cles.get(cle, 0) + 1
    box = st.container(border=True, key=f"ps-panel-{cle}-{n}")
    if titre:
        box.markdown(f'<div class="ps-panel-title">{html.escape(titre)}</div>',
                     unsafe_allow_html=True)
    if note:
        box.markdown(f'<div class="ps-panel-note">{html.escape(note)}</div>',
                     unsafe_allow_html=True)
    return box


def tiles(items):
    """Rangée de tuiles. `items` : (libellé, valeur, précision, couleur|None)."""
    out = ['<div class="ps-tiles">']
    for libelle, valeur, *reste in items:
        sub = reste[0] if reste else ""
        coul = reste[1] if len(reste) > 1 else None
        style = f' style="--ps-accent:{coul}"' if coul else ""
        klass = "ps-tile ps-tile-accent" if coul else "ps-tile"
        out.append(
            f'<div class="{klass}"{style}>'
            f'<div class="ps-tile-label">{html.escape(str(libelle))}</div>'
            f'<div class="ps-tile-value">{html.escape(str(valeur))}</div>'
            + (f'<div class="ps-tile-sub">{html.escape(str(sub))}</div>' if sub else "")
            + '</div>')
    out.append("</div>")
    st.markdown("".join(out), unsafe_allow_html=True)


def inline(tri):
    """Logo + tricode, à glisser dans une phrase en markdown."""
    return (f'<span class="ps-team-inline"><img src="{logo(tri)}" alt="">'
            f'<b>{html.escape(tri)}</b></span>')


def _carte(tri, player):
    """La carte de hockey seule. Sans joueur connu : le logo en grand."""
    if not player or not player.get("action"):
        return (f'<div class="ps-card"><div class="ps-card-inner">'
                f'<img class="ps-card-logo" src="{logo(tri)}" '
                f'alt="Logo des {html.escape(name(tri))}"></div></div>')
    coul = player.get("color") or "#3a3a35"
    poste = POSITIONS.get(player.get("position", ""), player.get("position", ""))
    joueur = f'{player["first_name"]} {player["last_name"]}'
    chip = (f'class="ps-plate-chip" '
            f'style="background:{html.escape(coul)};color:{ink(coul)}"')
    return (
        f'<div class="ps-card"><div class="ps-card-inner">'
        f'<img class="ps-card-shot" src="{html.escape(player["action"])}" '
        f'alt="" loading="lazy">'
        + (f'<img class="ps-card-face" src="{html.escape(player["headshot"])}" '
           f'alt="{html.escape(joueur)}" loading="lazy">'
           if player.get("headshot") else "")
        + f'<div class="ps-card-plate">'
        f'<div class="ps-plate-name">{html.escape(joueur)}</div>'
        f'<div class="ps-plate-row">'
        f'<span {chip}>{html.escape(poste)}</span>'
        f'<span class="ps-plate-badge">'
        f'<img src="{logo(tri, "light")}" alt=""></span>'
        f'<span {chip}>{player["points"]} pts</span>'
        f'</div></div></div></div>')


def hero(tri, kicker, matchup, pct, player=None, glow=True, suite=False):
    """Carte de hockey à gauche, équipe et probabilité de victoire à droite.

    `glow`  : teinte aussi le fond de la page. Une seule carte par affichage
              doit le faire, sinon la dernière dessinée l'emporte sans qu'on
              l'ait décidé.
    `suite` : la carte prolonge le panneau juste au-dessus (pas d'espace entre
              les deux), pour qu'ils se lisent comme un seul bloc.
    """
    coul = (player or {}).get("color") or "#3987e5"
    shot = (player or {}).get("action") or ""
    # url('...') en guillemets SIMPLES : l'attribut style est délimité par des
    # guillemets doubles, et en mettre à l'intérieur le refermait — la variable
    # arrivait vide et la photo de fond ne s'affichait jamais.
    style = (f'--ps-glow:{rgba(coul, 0.34)};'
             + (f"--ps-shot:url('{html.escape(shot, quote=True)}');" if shot else ""))
    if glow:
        backdrop(coul)
    classe = "ps-hero ps-hero-suite" if suite else "ps-hero"
    st.markdown(
        f'<div class="{classe}" style="{style}">{_carte(tri, player)}'
        f'<div class="ps-hero-body">'
        f'<div class="ps-kicker">{html.escape(kicker)}</div>'
        f'<h2 class="ps-team">{html.escape(name(tri))}</h2>'
        f'<div class="ps-matchup">{html.escape(matchup)}</div>'
        f'</div>'
        f'<div class="ps-hero-num">'
        f'<div class="ps-pct">{pct * 100:.1f}<span>%</span></div>'
        f'<div class="ps-pct-label">de chances de gagner</div>'
        f'</div></div>',
        unsafe_allow_html=True)


def chips(pairs, colors):
    """Une pastille par (nom de source, probabilité). `None` = pas de cote."""
    out = ['<div class="ps-chips">']
    for i, (label, p) in enumerate(pairs):
        vide = " ps-vide" if p is None else ""
        val = "pas encore de cote" if p is None else f"{p * 100:.1f} %"
        out.append(
            f'<div class="ps-chip{vide}">'
            f'<span class="ps-dot" style="background:{colors[i % len(colors)]}"></span>'
            f'<span class="ps-chip-name">{html.escape(label)}</span>'
            f'<span class="ps-chip-val">{val}</span>'
            f'</div>')
    out.append('</div>')
    st.markdown("".join(out), unsafe_allow_html=True)
