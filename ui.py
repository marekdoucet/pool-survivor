"""
Couche présentation : thème sombre, logos d'équipes, carte du pick.

Séparé de app.py pour que la logique du pool reste lisible. Rien ici ne
calcule : ce module ne fait que mettre en forme des valeurs décidées ailleurs.
Les classes CSS sont toutes préfixées « ps- » et les règles qui risquent
d'entrer en conflit avec Streamlit utilisent deux classes : on ne dépend donc
pas de ses noms internes, qui changent d'une version à l'autre.
"""

import html

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

.ps-hero {
  display: flex; align-items: center; gap: 1.9rem; flex-wrap: wrap;
  background: linear-gradient(135deg, #242422 0%, #1d1d1b 100%);
  border: 1px solid #33332f; border-left: 4px solid #3987e5;
  border-radius: 0.9rem; padding: 1.5rem 1.75rem; margin: 0.5rem 0 1.25rem;
}
.ps-hero-body { flex: 1 1 260px; min-width: 0; }
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
.ps-hero-num { flex: none; text-align: right; min-width: 148px; }
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
   de la carte et une bande noire en dessous. */
.ps-card-inner .ps-card-shot {
  position: absolute; inset: 0; width: 100%; height: 100% !important;
  object-fit: cover; object-position: 50% 12%;
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
  background: #242422; border: 1px solid #33332f; border-radius: 999px;
  padding: 0.4rem 0.9rem;
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


def inject_css():
    """À appeler une fois, juste après st.set_page_config."""
    st.markdown(CSS, unsafe_allow_html=True)


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
        f'alt="{html.escape(joueur)}" loading="lazy">'
        f'<div class="ps-card-plate">'
        f'<div class="ps-plate-name">{html.escape(joueur)}</div>'
        f'<div class="ps-plate-row">'
        f'<span {chip}>{html.escape(poste)}</span>'
        f'<span class="ps-plate-badge">'
        f'<img src="{logo(tri, "light")}" alt=""></span>'
        f'<span {chip}>{player["points"]} pts</span>'
        f'</div></div></div></div>')


def hero(tri, kicker, matchup, pct, player=None):
    """Carte de hockey à gauche, équipe et probabilité de victoire à droite."""
    st.markdown(
        f'<div class="ps-hero">{_carte(tri, player)}'
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
