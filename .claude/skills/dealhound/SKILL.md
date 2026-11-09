---
name: dealhound
description: Main interface to dealHound, the deal hunter on classified-ads sites. Use for anything about dealHound searches ("missions") - listing deployed searches and their server, showing the best deals of a search with their scores, creating and deploying a new search (search criteria + scoring criteria), stopping a search, checking or setting up the configuration and the server. Subcommands - list, results <id>, new, stop <id>, config, wizard.
argument-hint: "[list | results <id> | new [description] | stop <id> | config | wizard]"
---

# /dealhound

Arguments reçus : `$ARGUMENTS`. Le premier mot est la sous-commande (`wizzard` = `wizard`).
Répondre en français, en suivant `context/communication.md` : concis, tableaux, pas de
listes à rallonge.

## Vocabulaire : deux sortes de critères, à ne jamais mélanger

- **Critères de recherche** : les filtres envoyés aux sources de données (mots-clés,
  catégorie, prix min/max, zone + rayon, marque, année, km…). Dans `mission.yaml` :
  `filters` (communs) et `sources.<source>` (propres à la source). Ils décident de ce qui
  est **collecté**.
- **Critères de notation** : ce qui permet de **classer** les annonces collectées (prix,
  distance du domicile, version/variante, état, fiabilité du vendeur…). Dans
  `mission.yaml` : `scoring.criteria` (id, libellé, poids, barème) ; et en prose complète
  dans `missions/<id>/scoring.md`, le prompt de notation utilisé par l'analyse
  automatique sur le serveur.

## Sans argument : aide

Afficher ce tableau, puis s'arrêter (sans rien lancer, mais signaler en une ligne si
`config.env` est absent, avec la proposition du wizard) :

| Commande | Effet |
|---|---|
| `/dealhound list` | Recherches déployées : mission, serveur, description, critères de recherche et de notation, dernière collecte |
| `/dealhound results <id>` | Les 10 meilleurs deals d'une recherche, notés critère par critère |
| `/dealhound new [description]` | Crée une recherche (critères de recherche + de notation), la fait valider, puis la déploie |
| `/dealhound stop <id>` | Arrête une recherche sur son serveur (archivée, rien n'est supprimé) |
| `/dealhound config` | État des lieux de la configuration (local et serveurs) |
| `/dealhound wizard` | Configuration guidée, préparation du serveur comprise, puis vérification complète |

## Avant toute sous-commande (sauf aide et wizard) : config active

Le fichier de configuration est `config.env` à la racine (modèle : `config.example.env`,
ignoré par git). S'il est absent, ou si `DEPLOY_HOST` vaut encore l'exemple
(`user@192.168.1.10`) : le dire, et proposer le wizard avec AskUserQuestion
(« Lancer le wizard (Recommended) » / « Annuler »). Ne pas continuer sans config.

Variables : `DEPLOY_HOST` (serveur par défaut), `EXTRA_HOSTS`, `DEPLOY_DIR`,
`SEARCH_INTERVAL_MIN` (recherche, défaut 30), `ANALYSIS_INTERVAL_MIN` (analyse par
inférence, défaut 180), `ANALYSIS_MODEL` (vide = défaut de Claude Code), `LOCAL_PYTHON`.

## Lancer les scripts

Tout s'exécute **sous Linux** (WSL sur Windows), depuis la racine du projet :

| Besoin | Linux | Windows (PowerShell) |
|---|---|---|
| Script de déploiement | `bash deploy/deploy.sh <cmd>` | `wsl --cd "<racine du projet>" -- bash deploy/deploy.sh <cmd>` |
| Python local du projet | `bash deploy/py.sh <args>` | `wsl --cd "<racine du projet>" -- bash deploy/py.sh <args>` |

Ne pas passer par `bash -c '...'` depuis PowerShell (les guillemets sont mangés). Pour un
script ad hoc, l'écrire dans le scratchpad et le lancer avec `bash deploy/py.sh <chemin>`.

Commandes disponibles :
- `deploy.sh [--host H] check | push [mission...] | list | results <id> [n] | status |
  run [collect|analyze] | test-claude | logs [n] | pull <id> | retire <id>`
  (`--host` par défaut : `DEPLOY_HOST` ; `list` interroge `DEPLOY_HOST` et `EXTRA_HOSTS`).
- `py.sh -m dealhound.cli list|results <id>|doctor [--json]` (missions locales),
  `py.sh collect.py --mission <id> --dry-run -v` (test réel des critères de recherche,
  n'écrit rien), `py.sh analyze.py --mission <id> --dry-run` (affiche le prompt
  d'analyse complet, sans inférence).

Une mission (`<id>`) = un dossier `missions/<id>/`. **`missions/`, `config.env` et
`context/*.md` sont privés** (dépôt public) : ne jamais les commiter, ne jamais nommer une
vraie mission dans un fichier versionné ou un message de commit.

## list

1. `deploy.sh list` : une ligne JSON par serveur (`host`, `default`, `ok`, `missions`).
2. `py.sh -m dealhound.cli list --json` : missions locales, pour repérer celles qui ne
   sont déployées nulle part.
3. Tableau, une ligne par mission déployée :

   | Mission | Serveur | Recherche | Critères de recherche | Critères de notation | Dernière collecte | Annonces |
   |---|---|---|---|---|---|---|

   - Mission : `missions/<id>/mission.yaml`.
   - Recherche : description en une phrase.
   - Critères de recherche (`search_criteria`) : condensés (mots-clés, prix, zone + rayon,
     filtres marquants).
   - Critères de notation (`scoring_criteria`) : libellés avec leur poids
     (« Prix ×3, Distance ×1… »).
   - Annonces : actives / à analyser / disparues.
4. Puis une ligne pour les serveurs injoignables, une pour les missions locales non
   déployées (« `<id>` : en local seulement »), et un avertissement pour toute mission
   sans critères de notation (elle n'est pas analysée). Aucune mission : le dire et
   proposer `/dealhound new`.

## results <id>

1. Trouver le serveur de la mission avec `deploy.sh list`. Absente partout mais présente
   en local : le dire (proposer de la déployer).
2. Récupérer les résultats **depuis le serveur** :
   `deploy.sh --host <serveur> results <id> 10` (JSON : `search_criteria`,
   `scoring_criteria`, `synthesis`, `active`, `analyzed`, `pending`, `deals`).
3. Afficher dans cet ordre :
   - **Critères de recherche** : une ligne condensée (ce qui est collecté).
   - **Critères de notation** : petit tableau Critère | Poids | Barème (court).
   - **Synthèse** de la dernière analyse (`synthesis.text`, avec sa date), si présente.
   - **Tableau récapitulatif**, une colonne par critère de notation, dans l'ordre de
     `scoring_criteria` :

     | # | Score | <Critère 1> | <Critère 2> | … | Prix | Annonce | Lieu | Avis |
     |---|---|---|---|---|---|---|---|---|

     - Score : note globale /10 (moyenne pondérée), en gras ; « écartée » si
       `eliminatory`.
     - Colonnes critères : la note /10 de `scores[<id>]`.
     - Prix : `previous_price` barré s'il y a eu une baisse.
     - Annonce : `[titre](url)`.
     - Lieu : ville + `distance_km` (« à vol d'oiseau »).
     - Avis : `summary`, puis `red_flags` préfixés de ⚠.
   - Sous le tableau : les `questions` à poser pour les 3 premières annonces.
4. Annonces pas encore notées (`score` null) : les mettre après les annonces notées, avec
   « – » dans les colonnes de notation, et préciser combien attendent la prochaine
   analyse (`pending`, fréquence `ANALYSIS_INTERVAL_MIN`). `analysis_stale` = notée avant
   une baisse de prix : le signaler (« note à réviser »).

## new [description]

1. **Lire le contexte d'abord** : `context/profil.md`, `context/achats.md`,
   `context/communication.md`, les exemples `examples/mission/` (`mission.yaml`,
   `scoring.md`, `context.md`), et la docstring de `build_search_kwargs`
   (`dealhound/sources/leboncoin.py`). Les connaissances sur les filtres leboncoin sont
   dans CLAUDE.md (section leboncoin).
2. **Récolter le besoin**, à partir de `$ARGUMENTS` et en demandant ce qui manque
   (AskUserQuestion, 2-4 questions max par tour) :
   - pour les **critères de recherche** : quoi, budget **tout compris**, zone (défaut :
     domicile de `profil.md`), contraintes dures ;
   - pour les **critères de notation** : ce qui fait un bon deal pour lui ici (version /
     variante préférée, état, distance acceptable, type de vendeur, accessoires…) et ce
     qui compte le plus. Proposer une liste de 3 à 6 critères avec des poids (le prix et
     la distance du domicile presque toujours), et la lui faire ajuster.
3. **Choisir un `<id>`** court en kebab-case, absent de `missions/` et de `deploy.sh list`.
4. **Écrire** dans `missions/<id>/` :
   - `mission.yaml` : `filters` / `sources` (critères de recherche) et
     `scoring.criteria` (critères de notation : `id` slug, `label`, `weight`,
     `description` = barème qui dit ce que valent 0, 5 et 10).
   - `scoring.md` : **le prompt de notation complet**, autonome, en français : objectif
     de la recherche, ce qu'est un excellent / un mauvais deal, chaque critère de
     notation avec son barème détaillé et des exemples chiffrés, les red flags et les
     cas éliminatoires, les points d'attention propres au produit. Il doit suffire à un
     analyste qui ne connaît rien d'autre de la recherche (gabarit :
     `examples/mission/scoring.md`).
   - `context.md` s'il y a de la matière (prix du marché, pièges, annonces déjà vues).
5. **Tester la recherche en réel** : `py.sh collect.py --mission <id> --dry-run -v`. Si 0
   annonce ou des résultats hors sujet, ajuster les critères de recherche et relancer,
   3 essais au plus. Pour découvrir les clés d'attributs d'une catégorie, lancer une
   sonde ad hoc (scratchpad) qui affiche les attributs de quelques annonces.
6. **Vérifier le prompt d'analyse** : `py.sh analyze.py --mission <id> --dry-run` doit
   s'exécuter sans erreur (il n'y a pas encore d'annonces en local : c'est normal qu'il
   dise « nothing to analyze » ; valider au moins que la mission se charge).
7. **Review** : montrer, séparément, les critères de recherche (extrait YAML), les
   critères de notation (tableau Critère | Poids | Barème), un résumé de `scoring.md`,
   le nombre d'annonces trouvées et 5 exemples (prix, titre, lieu). Puis AskUserQuestion :
   « Valider et déployer sur `<DEPLOY_HOST>` (Recommended) » / « Modifier » /
   « Garder en local sans déployer ». Sur « Modifier » : appliquer, refaire 5 à 7.
8. **Déployer** sur le serveur par défaut (`DEPLOY_HOST`), ou sur celui que l'utilisateur
   a nommé (`--host`) : `deploy.sh push <id>`, puis `deploy.sh run` (collecte), puis
   proposer `deploy.sh run analyze` pour une première notation immédiate (sinon elle
   aura lieu au prochain passage du timer), puis `deploy.sh results <id> 5`.
9. Résumer : mission, serveur, fréquences (`SEARCH_INTERVAL_MIN`,
   `ANALYSIS_INTERVAL_MIN`), annonces collectées, comment voir les résultats
   (`/dealhound results <id>`).

## stop <id>

1. Trouver le serveur avec `deploy.sh list`. Mission introuvable : le dire et afficher
   la liste.
2. Confirmer (AskUserQuestion) : mission, serveur, et le fait que la mission est
   archivée sur le serveur (`missions/.archive/`, état conservé), pas supprimée.
3. `deploy.sh --host <serveur> retire <id>`, puis `deploy.sh list` pour vérifier.
4. Le dossier local `missions/<id>/` n'est pas touché ; le dire (on pourra la
   redéployer avec `deploy.sh push <id>`).

## config

Lecture seule, sauf accord de l'utilisateur pour une correction.

1. Config active (voir plus haut).
2. Local : `py.sh -m dealhound.cli doctor --json` (config et intervalles, Python,
   modules, `context/`, missions locales valides et notables).
3. Chaque serveur (`DEPLOY_HOST` et `EXTRA_HOSTS`) : `deploy.sh --host <h> check`
   (prérequis, linger, Claude Code installé et authentifié, version installée).
4. Comparer `local code version` et la version installée : si elles diffèrent, ou si le
   code n'est pas installé, proposer `deploy.sh --host <h> push` (met à jour le code et
   les intervalles des timers sans toucher les missions ni leur état).
5. Tableau récapitulatif : Élément | État (ok / à corriger / attention) | Action.
6. Corrections : appliquer seulement ce qui ne touche ni le système ni la sécurité
   (venv local, push du code). Pour le reste, renvoyer vers l'étape concernée de
   `docs/server-setup.md` avec la commande exacte.

## wizard

Configuration interactive, une étape à la fois, avec AskUserQuestion et des valeurs par
défaut tirées de la config existante (ou de `config.example.env`). Suivre
`docs/server-setup.md` pour tout ce qui concerne le serveur.

1. **Environnement local** :
   - Sur Windows, vérifier WSL (`wsl -l -v`).
   - `python3` ≥ 3.10 sous Linux/WSL.
   - Venv local (`~/.venvs/dealhound`, hors OneDrive) avec `requirements.txt`. S'il
     manque, le créer avec l'accord de l'utilisateur :
     `python3 -m venv ~/.venvs/dealhound && ~/.venvs/dealhound/bin/pip install -r requirements.txt`.
   - Si `python3-venv` manque, l'utilisateur lance `sudo apt install python3-venv`.
2. **Paramètres** :
   - Serveur par défaut (`user@hôte`), dossier d'installation (défaut `dealhound`),
     serveurs supplémentaires éventuels.
   - Intervalle de recherche en minutes (30 (Recommended), 60, 120).
   - Intervalle d'analyse en minutes (180 (Recommended), 360, 720).
   - Modèle d'analyse : défaut de Claude Code (Recommended) ou un modèle moins cher
     (ex. `sonnet`).
3. **Écrire `config.env`** au format de `config.example.env`. S'il existe déjà, montrer
   les changements et confirmer avant d'écraser.
4. **Préparer le serveur** (`docs/server-setup.md`), en vérifiant chaque étape avant de
   passer à la suivante :
   - SSH par clé depuis WSL : `ssh -o BatchMode=yes <hôte> true`. En échec,
     l'utilisateur lance `ssh-copy-id <hôte>` depuis WSL, puis on reteste.
   - `deploy.sh check`. Pour chaque `FAIL` ou `warn`, donner la commande de la doc
     (paquets, linger, journal) et attendre que l'utilisateur l'ait lancée.
   - Claude Code absent : donner la commande d'installation de la doc (étape 4) ;
     l'utilisateur la lance ou autorise explicitement à la lancer via SSH.
   - Claude Code non authentifié : guider l'option A (`ssh -t <hôte> '~/.local/bin/claude'`
     puis `/login`) ou B (clé API dans `~/.config/environment.d/`). C'est l'utilisateur
     qui s'authentifie ; ne jamais manipuler de clé ou de mot de passe à sa place.
   - `deploy.sh test-claude` : doit afficher `ok`.
5. **Contexte utilisateur** : si `context/profil.md`, `achats.md` ou `communication.md`
   manquent, poser quelques questions (prénom, ville, ce qu'il aime chercher, budget et
   habitudes d'achat) et les rédiger selon `context/README.md`. Pour la ville, noter des
   coordonnées approximatives marquées `[à vérifier]`.
6. **Installer et vérifier** : `deploy.sh push` (code + timers), `deploy.sh status`
   (deux timers aux bons intervalles), puis dérouler `config`.
7. Conclure : ce qui est prêt, ce qui reste à faire par l'utilisateur, et la suite
   (`/dealhound new`).

## Règles

- Ne jamais supprimer de données sur un serveur (`retire` archive). Pas de `rm` distant.
- Ne pas toucher à la configuration système ni à la sécurité (sudo, paquets, linger,
  `authorized_keys`, identifiants Claude) : donner les commandes.
- Confirmer avant tout déploiement ou arrêt. Un déploiement lance des requêtes
  régulières vers les sites depuis le réseau de l'utilisateur, et des analyses payantes :
  rester sobre (3 pages max par source, recherche ≥ 30 min, analyse ≥ 60 min).
- leboncoin via `lbc` (non officielle) : en cas de 403 répétés (DataDome), espacer, ne
  pas insister.
