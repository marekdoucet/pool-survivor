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


def authlib_present():
    """La connexion native de Streamlit est une dépendance optionnelle.

    Sans le paquet Authlib, st.login lève StreamlitMissingAuthlibError — et
    Streamlit masque le message à l'écran (« redacted to prevent data leaks »),
    ce qui donne une page blanche et « Internal server error ». On vérifie donc
    avant de cliquer, pour pouvoir le dire en clair.
    """
    try:
        import authlib          # noqa: F401
        return True
    except ImportError:
        return False


# Exceptions par lesquelles Streamlit pilote son propre flot : les attraper
# casserait la page au lieu de la rafraîchir.
_CONTROLE = ("RerunException", "StopException", "RerunData")


def est_controle_streamlit(exc):
    return type(exc).__name__ in _CONTROLE
