# GoFluent Automation

Application Windows avec interface PyQt6 et Playwright pour parcourir des activités GoFluent.

> Utilise un compte auquel tu es autorisé à accéder. Le dépôt ne doit contenir aucun cookie, profil Chromium, résultat de compte ou contenu de cours. Les données locales sont exclues par `.gitignore`.

## Prérequis

- Windows 10 ou 11
- Python 3.10+ (Python 3.11 recommandé)
- Git si tu souhaites cloner ou publier le projet
- Compte GoFluent autorisé et connexion Internet

## Installation

Ouvre PowerShell dans le dossier du projet :

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

`requirements.txt` indique les versions de PyQt6 et Playwright utilisées par le projet. Playwright installe une version dédiée de Chromium.

## Démarrage et première connexion

```powershell
.\.venv\Scripts\python.exe main.py
```

1. Clique sur **Ouvrir GoFluent**.
2. Connecte-toi dans Chromium.
3. Ferme Chromium avec le même bouton.
4. Choisis les options puis clique sur **Démarrer**.

Le profil et la session restent dans `gofluent_profile/` sur ton ordinateur. Ne partage jamais ce dossier.

## Réglages

- **Niveau CECRL** : filtre minimum et maximum.
- **Nombre d’activités** : `0` signifie sans limite numérique.
- **Source des activités** : `Activités du tableau de bord` est le choix par défaut et n’envoie rien à l’IA. Choisis `Créer un entraînement avec GoFluent AI` pour activer le thème.
- **Catégorie / thème** : en mode GoFluent AI, choisis une suggestion ou écris une demande libre en français ou en anglais. La liste n’est pas exhaustive. Un thème est obligatoire avant le démarrage ; la génération via **Explore content** peut prendre jusqu’à 2 minutes. L’application repère les nouveaux liens et ouvre le résultat dont le titre correspond le mieux au thème.
- **Temps avant réponse** : 0 à 300 secondes, 20 secondes par défaut. Réglage mémorisé ; l’attente commence quand la question apparaît et s’interrompt avec **Arrêter**.

## Données et confidentialité

L’application crée `data/` au démarrage. Ce dossier peut contenir les résultats, des journaux et les données de cours reçues pendant l’utilisation. Il est exclu de Git, comme le profil Chromium, l’environnement Python, les fichiers `.env` et les caches.

La rotation garde au maximum 5 journaux d’exécution. Les plus anciens sont supprimés au début d’une nouvelle exécution. **Effacer l’affichage** vide seulement le journal visible dans l’application.

Avant de publier, vérifie `git status` et n’ajoute jamais les dossiers ignorés en utilisant `git add -f`.

## Publier sur GitHub

Crée d’abord un dépôt vide sur GitHub. Puis, dans PowerShell, depuis ce dossier, remplace `<URL_DU_DEPOT>` par l’URL affichée par GitHub :

```powershell
git init
git add .
git status
git commit -m "Initial commit"
git branch -M main
git remote add origin <URL_DU_DEPOT>
git push -u origin main
```

Vérifie que `git status` ne montre ni `data/`, ni `gofluent_profile/`, ni `.venv/`, ni fichier `.env`.

## Fonctionnement et limites

En mode tableau de bord, l’application lit les activités et les données de contenu chargées par GoFluent dans le compte connecté. Seul le mode GoFluent AI envoie le thème à **Explore content**. L’application attend les liens générés, puis traite les activités détectées. Elle utilise uniquement les réponses explicites qu’elle peut relier sans ambiguïté aux questions prises en charge. Elle s’arrête si une réponse ou la progression n’est pas vérifiable ; une activité n’est comptée comme terminée qu’après confirmation de l’écran final.

Les contenus de cours et les solutions ne sont pas inclus. L’application dépend de l’interface GoFluent, qui peut évoluer.

## Dépannage

- **Chromium introuvable** : exécute `\.venv\Scripts\python.exe -m playwright install chromium`.
- **Connexion expirée** : reconnecte-toi avec **Ouvrir GoFluent**, puis ferme Chromium avant de démarrer.
- **Erreur d’installation** : vérifie ta version de Python et relance les commandes d’installation ci-dessus.
