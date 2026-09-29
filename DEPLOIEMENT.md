# Déploiement (gratuit)

Tout ce qui suit est gratuit : dépôt privé GitHub, GitHub Actions (2 000 minutes
par mois pour un dépôt privé ; la collecte en utilise environ 120), The Odds API
(500 crédits par mois ; la collecte en utilise environ 120) et Streamlit Community Cloud.

## 1. Créer le dépôt GitHub (public)

1. Sur https://github.com/new : nom `pool-survivor`, **Public**, ne coche rien
   d'autre (pas de README, pas de .gitignore).
2. Dans PowerShell :
   ```
   cd C:\Users\Marek\pool-survivor
   git remote add origin https://github.com/marekdoucet/pool-survivor.git
   git push -u origin main
   ```
   La première fois, une fenêtre s'ouvre pour te connecter à GitHub.

Public, parce que le plan gratuit de Streamlit Community Cloud ne permet
qu'une seule app privée. Conséquences : `picks.json` et les données sont
visibles de tous, et toute personne qui a l'adresse du site peut enregistrer
ou retirer un pick. La clé The Odds API et le jeton GitHub restent secrets
(ils ne sont jamais dans le code). Les commits utilisent l'adresse
« noreply » de GitHub pour ne pas exposer le courriel.

## 2. Clé The Odds API → secret GitHub

Dépôt → **Settings → Secrets and variables → Actions → New repository secret**
- Name : `ODDS_API_KEY`
- Secret : ta clé

## 3. Tester la collecte automatique

Dépôt → onglet **Actions** → **Collecte quotidienne** → **Run workflow**.
Après 2-3 minutes : pastille verte et un nouveau commit « Collecte du … ».
Ensuite elle tourne seule deux fois par jour (7 h et 17 h, heure de l'Est ;
GitHub peut retarder une exécution planifiée de quelques dizaines de minutes).
Si une source échoue, la pastille est rouge et GitHub t'envoie un courriel,
mais ce qui a été collecté est quand même gardé.

## 4. Jeton pour enregistrer les picks depuis le site

Streamlit Cloud efface ses fichiers à chaque redémarrage : le site écrit donc
`picks.json` directement dans le dépôt, avec un jeton limité à ce seul dépôt.

https://github.com/settings/personal-access-tokens/new
- Token name : `pool-survivor-streamlit`
- Expiration : après la fin de la saison (ex. 31 mai 2027)
- Repository access : **Only select repositories** → `pool-survivor`
- Permissions → Repository permissions → **Contents : Read and write**
  (rien d'autre)

Copie le jeton (`github_pat_…`) : il ne sera plus affiché.

## 5. Streamlit Community Cloud

1. https://share.streamlit.io → connexion avec GitHub (autorise l'accès aux
   dépôts privés quand c'est demandé) → **Create app**.
2. Repository `marekdoucet/pool-survivor`, branch `main`, main file `app.py`.
3. **Advanced settings** :
   - Python version : 3.13
   - Secrets : colle le contenu de `.streamlit/secrets.toml.exemple` en
     remplaçant `token` par ton jeton.
4. **Deploy**.

Après le déploiement, vérifie dans les réglages de partage de l'app
(**Share**) qu'elle n'est visible que par toi.

L'app se met à jour toute seule après chaque collecte (nouveau commit).
Elle s'endort après quelques jours sans visite ; la première visite la
réveille en une trentaine de secondes.

Dans la barre latérale, sous « Mes picks », tu dois voir
« Sauvegardés dans GitHub (marekdoucet/pool-survivor) ». Enregistre un pick :
un commit « Pick : … » apparaît dans le dépôt.

## En local

```
.venv\Scripts\activate
git pull                 # récupère les collectes faites par GitHub Actions
streamlit run app.py
```

Les picks locaux vont dans `picks.json` ; fais `git pull` avant et
`git commit` + `git push` après si tu les modifies en local plutôt que
sur le site.
