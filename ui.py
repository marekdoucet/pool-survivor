"""
Couche présentation : thème sombre, logos d'équipes, carte du pick.

Séparé de app.py pour que la logique du pool reste lisible. Rien ici ne
calcule : ce module ne fait que mettre en forme des valeurs déjà décidées
ailleurs. Les classes CSS sont toutes préfixées « ps- » et ne visent que nos
propres éléments — on ne dépend donc pas des noms de classes internes de
Streamlit, qui changent d'une version à l'autre.
"""

import html

import streamlit as st

# Logos officiels de la LNH, en SVG (donc nets à n'importe quelle taille).
# Le tricode est celui déjà utilisé partout dans les données (COL, MTL, VGK…).
# Variante « dark » = celle conçue POUR un fond sombre : sur les équipes au
# logo noir, le noir y est remplacé par la couleur claire (BOS passe au or).
LOGO_URL = "https://assets.nhle.com/logos/nhl/svg/{tri}_dark.svg"

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


def logo(tri):
    """URL du logo SVG de l'équipe, version fond sombre."""
    return LOGO_URL.format(tri=tri)


def name(tri):
    """Nom complet de l'équipe ; le tricode si on ne le connaît pas."""
    return TEAMS.get(tri, tri)


# @import plutôt que theme.fontFaces : l'URL de l'API Google Fonts est stable,
# alors que les URL de fichiers .woff2 qu'elle sert peuvent changer.
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&display=swap');

.ps-hero {
  display: flex; align-items: center; gap: 1.75rem; flex-wrap: wrap;
  background: linear-gradient(135deg, #242422 0%, #1d1d1b 100%);
  border: 1px solid #33332f; border-left: 4px solid #3987e5;
  border-radius: 0.9rem; padding: 1.5rem 1.75rem; margin: 0.5rem 0 1.25rem;
}
.ps-hero img { width: 104px; height: 104px; flex: none; }
.ps-hero-body { flex: 1 1 260px; min-width: 0; }
.ps-kicker {
  font-size: 0.72rem; font-weight: 600; letter-spacing: 0.14em;
  text-transform: uppercase; color: #c3c2b7; margin-bottom: 0.35rem;
}
/* Sélecteur composé (deux classes) et non « .ps-team » seul : Streamlit
   cible les h2 de ses blocs markdown, ce qui bat une classe unique et
   ramenait la police à Source Sans. Deux classes reprennent la main sans
   avoir à sortir le !important. */
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

@media (max-width: 640px) {
  .ps-hero { gap: 1rem; padding: 1.15rem; }
  .ps-hero img { width: 68px; height: 68px; }
  .ps-team { font-size: 2rem; }
  .ps-hero-num { text-align: left; }
  .ps-pct { font-size: 3rem; }
}
/* ── Carte de hockey ──────────────────────────────────────────────────────
   Proportions d'une vraie carte (5:7), cadre clair, logo en filigrane et
   joueur détouré par-dessus. Fond neutre volontairement : les couleurs de
   marque des 32 équipes ne sont pas dans la palette validée, et certaines
   passeraient sous le seuil de contraste. Le logo porte l'identité. */
.ps-card {
  flex: none; width: 190px; aspect-ratio: 5 / 7; border-radius: 0.7rem;
  padding: 5px; background: linear-gradient(160deg, #4a4a45 0%, #2a2a27 55%, #3a3a35 100%);
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.45);
}
.ps-card-inner {
  position: relative; height: 100%; border-radius: 0.45rem; overflow: hidden;
  background: radial-gradient(circle at 50% 22%, #2f3f57 0%, #1d2430 60%, #15181f 100%);
}
.ps-card-logo {
  position: absolute; top: 6%; left: 50%; transform: translateX(-50%);
  width: 78%; opacity: 0.22;
}
.ps-card-player {
  position: absolute; bottom: 30px; left: 50%; transform: translateX(-50%);
  width: 104%; max-width: none;
}
.ps-card-plate {
  position: absolute; left: 0; right: 0; bottom: 0; padding: 5px 8px;
  background: rgba(10, 10, 9, 0.82); backdrop-filter: blur(2px);
  font-family: 'Barlow Condensed', system-ui, sans-serif;
  font-size: 0.92rem; font-weight: 600; letter-spacing: 0.02em;
  color: #ffffff; text-align: center; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.ps-card-plate .ps-pts { color: #c3c2b7; font-weight: 500; }

/* Carte sans joueur connu : le logo reprend toute la place. */
.ps-card-inner.ps-nolog .ps-card-logo {
  top: 50%; transform: translate(-50%, -50%); opacity: 1; width: 66%;
}

/* Source sans cote : la pastille reste, en retrait. Dire « pas encore de
   cote » vaut mieux qu'une pastille absente, qui ne dit rien. */
.ps-chip.ps-vide { opacity: 0.55; }
.ps-chip.ps-vide .ps-chip-val { color: #c3c2b7; font-weight: 500; }

</style>
"""


def inject_css():
    """À appeler une fois, juste après st.set_page_config."""
    st.markdown(CSS, unsafe_allow_html=True)


def hero(tri, kicker, matchup, pct, player=None):
    """Carte du pick : carte de hockey à gauche, équipe et probabilité à droite.

    `player` : dict (first_name, last_name, points, headshot) ou None. Sans
    joueur connu, la carte montre simplement le logo en grand.
    """
    if player:
        plate = (f'<div class="ps-card-plate">{html.escape(player["first_name"])} '
                 f'{html.escape(player["last_name"])} '
                 f'<span class="ps-pts">{player["points"]} pts</span></div>')
        photo = (f'<img class="ps-card-player" src="{html.escape(player["headshot"])}" '
                 f'alt="" loading="lazy">')
        klass = "ps-card-inner"
    else:
        plate, photo, klass = "", "", "ps-card-inner ps-nolog"
    st.markdown(
        f'<div class="ps-hero">'
        f'<div class="ps-card"><div class="{klass}">'
        f'<img class="ps-card-logo" src="{logo(tri)}" '
        f'alt="Logo des {html.escape(name(tri))}">{photo}{plate}'
        f'</div></div>'
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
    """Une pastille par (nom de source, probabilité). `None` = pas de cote.

    La couleur est décorative : le nom de la source est toujours écrit, jamais
    porté par la couleur seule.
    """
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
