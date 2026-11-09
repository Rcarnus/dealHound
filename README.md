# dealHound

Chasseur de bonnes affaires sur les sites d'annonces. dealHound surveille des recherches
précises (« missions ») sur leboncoin depuis un serveur (Raspberry Pi), garde
l'historique des annonces (nouvelles, baisses de prix, disparues), et les fait noter
automatiquement par Claude selon tes critères pour remonter les meilleurs deals.

| Fonction                                         | Statut  |
|--------------------------------------------------|---------|
| Source leboncoin                                 | fait    |
| Recherche périodique + historique + `deals.md`   | fait    |
| Analyse périodique par `claude -p` sur le serveur | fait    |
| Déploiement sur serveur (systemd)                | fait    |
| Skill Claude Code `/dealhound`                   | fait    |
| Notifications téléphone                          | à faire |
| Autres sources (La Centrale…)                    | à faire |

## Comment ça marche

Chaque recherche a **deux sortes de critères** :

- **Critères de recherche** : les filtres envoyés aux sites (mots-clés, catégorie, prix,
  zone + rayon, marque, année, km…). Ils décident de ce qui est collecté.
- **Critères de notation** : ce qui fait un bon deal pour toi (prix, distance du
  domicile, version/variante, état, sérieux du vendeur…), chacun avec un poids. Ils
  servent à classer ce qui a été collecté.

Sur le serveur, deux tâches tournent à intervalle fixe : la **recherche** (toutes les 30
min par défaut, sans IA) et l'**analyse** (toutes les 3 h par défaut). L'analyse note
chaque nouvelle annonce, critère par critère, avec `claude -p`, à partir d'un prompt de
notation rédigé à la création de la recherche. Les notes, red flags, questions à poser
et une synthèse remontent dans `/dealhound results`.

## Utilisation : `/dealhound` dans Claude Code

dealHound se pilote depuis [Claude Code](https://claude.com/claude-code), ouvert dans ce
dossier. Pas besoin d'écrire les fichiers de mission à la main : on décrit ce qu'on
cherche, Claude rédige la mission, la teste, la fait valider puis la déploie.

| Commande | Effet |
|---|---|
| `/dealhound` | Liste des sous-commandes |
| `/dealhound wizard` | Configuration guidée (environnement local, intervalles, serveur : paquets, Claude Code, authentification, tests ; contexte utilisateur), puis vérification complète |
| `/dealhound config` | État des lieux de la configuration, en local et sur les serveurs |
| `/dealhound new [description]` | Crée une recherche à partir de ta description et de ton profil (`context/`) : critères de recherche, critères de notation et prompt de notation. La teste en réel, te la fait relire, puis la déploie sur le serveur par défaut |
| `/dealhound list` | Recherches déployées : mission, serveur, description, critères de recherche et de notation, dernière collecte |
| `/dealhound results <id>` | Récupère les résultats sur le serveur : tableau des 10 meilleurs deals avec la note de chaque critère, la distance, l'avis, les red flags et la synthèse |
| `/dealhound stop <id>` | Arrête une recherche (archivée sur le serveur, rien n'est supprimé) |

Exemple : `/dealhound new une Fender Stratocaster mexicaine, moins de 700 €, pas trop loin`.

Sans configuration active, `/dealhound` propose de lancer le wizard.

## Première installation

1. **Linux ou WSL** avec Python ≥ 3.10, `python3-venv` et `rsync`
   (`sudo apt install python3-venv rsync`).
2. Ouvrir Claude Code dans le dossier du projet et lancer `/dealhound wizard`. Il crée
   le venv local, écrit `config.env`, puis prépare le serveur pas à pas : accès SSH,
   paquets, *linger*, installation et authentification de Claude Code, appel de test,
   installation de dealHound.

Le détail de la préparation du serveur, à faire aussi à la main si besoin :
[docs/server-setup.md](docs/server-setup.md). Les commandes `sudo` et la connexion à
Claude Code sur le serveur sont à lancer par toi.

## Configuration

`config.env` à la racine, **ignoré par git**. Modèle commenté : `config.example.env`.

| Variable | Rôle |
|---|---|
| `DEPLOY_HOST` | Serveur par défaut (`user@hôte`) |
| `EXTRA_HOSTS` | Autres serveurs, affichés par `/dealhound list` |
| `DEPLOY_DIR` | Dossier d'installation sur le serveur (relatif au home) |
| `SEARCH_INTERVAL_MIN` | Minutes entre deux recherches sur les sources (défaut 30, sans inférence) |
| `ANALYSIS_INTERVAL_MIN` | Minutes entre deux analyses par inférence (défaut 180) |
| `ANALYSIS_MODEL` | Modèle de l'analyse (vide = défaut de Claude Code ; ex. `sonnet` pour réduire le coût) |
| `LOCAL_PYTHON` | Python local avec les dépendances, pour tester une mission avant de la déployer |

Après un changement d'intervalle ou de modèle, `bash deploy/deploy.sh push` met à jour
les timers du serveur.

## Données privées

Le dépôt est public ; restent en local (et sur tes serveurs) seulement :

- `missions/` : les vraies recherches (critères, contexte, état, résultats). Seul
  l'exemple `examples/mission/` est versionné.
- `context/*.md` : ton profil et tes règles d'achat, lus par l'analyse et par
  `/dealhound new`. Voir `context/README.md`.
- `config.env` : tes serveurs.

## Usage avancé : les scripts

Ce que fait le skill, à la main. Sous Linux/WSL, depuis la racine
(sous Windows : `wsl --cd "<chemin du projet>" -- <commande>`) :

```bash
# Python local du projet (LOCAL_PYTHON)
bash deploy/py.sh collect.py --mission <id> --dry-run -v   # teste les filtres, n'écrit rien
bash deploy/py.sh collect.py                               # collecte locale, toutes missions
bash deploy/py.sh analyze.py --mission <id> --dry-run      # affiche le prompt d'analyse, sans inférence
bash deploy/py.sh -m dealhound.cli list | results <id> | doctor
bash deploy/py.sh -m dealhound.sources.leboncoin "fender stratocaster" --price-max 800 --category 30

# Serveurs (--host H pour un autre que DEPLOY_HOST)
bash deploy/deploy.sh check              # prérequis du serveur (lecture seule)
bash deploy/deploy.sh push [<id>...]     # code + contexte + missions, venv, timers
bash deploy/deploy.sh list               # missions de tous les serveurs (JSON)
bash deploy/deploy.sh results <id> [n]   # meilleurs deals (JSON)
bash deploy/deploy.sh run [analyze]      # recherche (ou analyse) immédiate
bash deploy/deploy.sh test-claude        # vérifie claude -p sur le serveur (~0,02 $)
bash deploy/deploy.sh status | logs [n]
bash deploy/deploy.sh pull <id>          # rapatrie deals.md dans .remote/<id>/
bash deploy/deploy.sh retire <id>        # arrête la mission (archivée)
```

Créer une mission sans le skill : `mkdir -p missions/<id> && cp examples/mission/* missions/<id>/`,
puis éditer `mission.yaml` (critères de recherche : clés de filtre dans la docstring de
`build_search_kwargs`, `dealhound/sources/leboncoin.py` ; critères de notation :
section `scoring`) et `scoring.md` (prompt de notation complet).

## Avertissements

- leboncoin n'a pas d'API publique et protège son API interne (DataDome). dealHound
  utilise la lib communautaire non officielle [`lbc`](https://github.com/etienne-hd/lbc),
  ce qui est probablement contraire aux CGU de leboncoin : usage personnel, faible volume,
  pauses entre requêtes. En cas de blocages (403) répétés, espacer les collectes.
- dealHound aide à repérer des annonces ; il ne remplace ni la vérification de
  l'annonce, ni celle du vendeur.

Documentation technique (architecture, décisions, conventions) : `CLAUDE.md`.
