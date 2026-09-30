import auth


class Utilisateur:
    """Imite st.user. Sans [auth] configurée, Streamlit lève au lieu de
    répondre False — c'est le cas que le code doit encaisser."""

    def __init__(self, connecte=False, leve=False, **champs):
        self._connecte, self._leve = connecte, leve
        for k, v in champs.items():
            setattr(self, k, v)

    @property
    def is_logged_in(self):
        if self._leve:
            raise RuntimeError("auth non configurée")
        return self._connecte


SECRETS_PROD = {"github": {}, "auth": {}}
SECRETS_LOCAL = {"github": {}}


def test_en_local_sans_auth_on_peut_ecrire():
    """Sinon l'app serait inutilisable sur ta machine."""
    assert auth.peut_ecrire(SECRETS_LOCAL, Utilisateur(leve=True)) is True


def test_en_production_un_anonyme_ne_peut_pas_ecrire():
    """Le trou qu'on ferme : n'importe qui pouvait remplacer les picks."""
    assert auth.peut_ecrire(SECRETS_PROD, Utilisateur(connecte=False)) is False


def test_en_production_un_connecte_peut_ecrire():
    assert auth.peut_ecrire(SECRETS_PROD, Utilisateur(connecte=True)) is True


def test_des_secrets_illisibles_ne_font_pas_planter():
    class Casse:
        def __contains__(self, k):
            raise RuntimeError("illisible")

    assert auth.peut_ecrire(Casse(), Utilisateur(leve=True)) is True


def test_le_courriel_nest_lu_que_si_connecte():
    assert auth.qui(Utilisateur(connecte=True, email="a@b.ca")) == "a@b.ca"
    assert auth.qui(Utilisateur(connecte=False, email="a@b.ca")) is None
    assert auth.qui(Utilisateur(leve=True)) is None


def test_le_nom_prend_le_prenom_puis_retombe_sur_le_courriel():
    assert auth.nom(Utilisateur(connecte=True, given_name="Marek",
                                name="Marek D", email="a@b.ca")) == "Marek"
    assert auth.nom(Utilisateur(connecte=True, name="Marek D",
                                email="a@b.ca")) == "Marek D"
    assert auth.nom(Utilisateur(connecte=True, email="a@b.ca")) == "a@b.ca"
    assert auth.nom(Utilisateur(connecte=True)) is None


def test_un_courriel_vide_vaut_absent():
    """Google peut renvoyer une chaîne vide plutôt que rien du tout."""
    assert auth.qui(Utilisateur(connecte=True, email="")) is None
