"""
Qui a le droit de modifier le pool.

L'app est publique et indexée. La lecture reste donc ouverte à tout le monde :
un visiteur venu de Google qui tombe sur un mur de connexion repart, et c'est
tout le canal de la vitrine qu'on perdrait. Seules les écritures demandent une
identité.

Les fonctions reçoivent `secrets` et `user` en argument plutôt que d'aller
chercher `st.secrets` et `st.user` elles-mêmes : c'est ce qui les rend
testables sans faire tourner Streamlit.
"""


def configuree(secrets):
    """La connexion est-elle branchée ?

    Sans section [auth], on est en développement local : pas de fournisseur
    OIDC, donc personne ne peut se connecter, et personne d'autre que toi n'a
    accès à la machine. On laisse alors écrire, sinon l'app serait inutilisable
    hors ligne. En production la section existe, donc la porte se referme.
    """
    try:
        return "auth" in secrets
    except Exception:       # secrets.toml absent ou illisible
        return False


def connecte(user):
    """Y a-t-il quelqu'un d'identifié ?"""
    try:
        return bool(user.is_logged_in)
    except Exception:       # sans [auth], st.user lève plutôt que de mentir
        return False


def peut_ecrire(secrets, user):
    return True if not configuree(secrets) else connecte(user)


def qui(user):
    """Le courriel de la personne connectée, ou None."""
    if not connecte(user):
        return None
    return getattr(user, "email", None) or None


def nom(user):
    """Son prénom si Google le donne, sinon son courriel, sinon rien."""
    if not connecte(user):
        return None
    for champ in ("given_name", "name", "email"):
        valeur = getattr(user, champ, None)
        if valeur:
            return valeur
    return None
