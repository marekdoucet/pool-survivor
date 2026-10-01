"""
Vérifications statiques sur app.py, sans faire tourner Streamlit.

app.py n'est pas testable comme un module ordinaire : l'importer exécute la
page. Mais on peut l'analyser, et une classe de bogues mérite ce filet.
"""

import builtins
import io
import symtable
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app.py"


def noms_non_definis(source):
    """Les noms qu'une fonction lit et qui n'existent nulle part.

    symtable dit, pour chaque fonction, quels symboles sont « globaux » — donc
    cherchés en dehors d'elle. Si un de ces noms n'est ni au niveau module ni
    dans les builtins, l'appel lèvera NameError à l'exécution.
    """
    haut = symtable.symtable(source, "app.py", "exec")
    connus = set(haut.get_identifiers()) | set(dir(builtins))

    def visite(table, chemin=""):
        trouves = []
        for enfant in table.get_children():
            nom = f"{chemin}{enfant.get_name()}"
            if enfant.get_type() == "function":
                trouves += [(nom, s.get_name()) for s in enfant.get_symbols()
                            if s.is_global() and s.is_referenced()
                            and s.get_name() not in connus]
            trouves += visite(enfant, nom + ".")
        return trouves

    return visite(haut)


def test_aucune_page_ne_lit_une_variable_dune_autre():
    """Le bogue du 1er octobre : « current » était local à page_pick, mais
    page_pool le lisait. Comme st.navigation n'exécute QUE la page ouverte,
    ouvrir « Le pool » levait NameError — et aucun test ne pouvait le voir,
    puisque app.py ne s'importe pas.
    """
    manquants = noms_non_definis(io.open(APP, encoding="utf-8").read())
    assert not manquants, "\n".join(
        f"{fonction} lit « {nom} », qui n'existe pas au niveau module"
        for fonction, nom in manquants)


def test_le_garde_fou_attrape_vraiment_ce_bogue():
    """Un test qui passe ne prouve rien tant qu'on ne l'a pas vu échouer. On
    réinjecte le bogue exact et on vérifie qu'il est signalé."""
    source = io.open(APP, encoding="utf-8").read()
    ligne = ('current = next((p for p in picks '
             'if p["week"] == this_monday.isoformat()), None)')
    assert ligne + "\n" in source, "la ligne a changé, mets ce test à jour"

    casse = source.replace(ligne + "\n", "", 1)
    casse = casse.replace("def page_pick():\n",
                          "def page_pick():\n    " + ligne + "\n", 1)
    assert ("page_pool", "current") in noms_non_definis(casse)
