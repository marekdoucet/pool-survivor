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
</style>
"""


def inject_css():
    """À appeler une fois, juste après st.set_page_config."""
    st.markdown(CSS, unsafe_allow_html=True)


def hero(tri, kicker, matchup, pct):
    """Carte du pick : logo, nom de l'équipe, adversaire, probabilité."""
    st.markdown(
        f'<div class="ps-hero">'
        f'<img src="{logo(tri)}" alt="Logo des {html.escape(name(tri))}">'
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
    """Une pastille par (nom de source, probabilité), dans l'ordre reçu."""
    out = ['<div class="ps-chips">']
    for i, (label, p) in enumerate(pairs):
        out.append(
            f'<div class="ps-chip">'
            f'<span class="ps-dot" style="background:{colors[i % len(colors)]}"></span>'
            f'<span class="ps-chip-name">{html.escape(label)}</span>'
            f'<span class="ps-chip-val">{p * 100:.1f} %</span>'
            f'</div>')
    out.append('</div>')
    st.markdown("".join(out), unsafe_allow_html=True)
