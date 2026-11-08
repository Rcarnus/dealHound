---
name: deploy-remote
description: Deploy, update, monitor or stop dealHound searches (missions) on the remote server (Raspberry Pi) over SSH. Use when the user wants to deploy a search remotely, push code or a mission to the server, check what runs there, trigger a collection, read remote logs, fetch remote deals.md, or retire a remote search.
---

# Déployer dealHound sur le serveur distant

Tout passe par `deploy/deploy.sh` (bash, à lancer depuis Linux/WSL). Cible et horaires :
`deploy/config.env` (`DEPLOY_HOST`, `DEPLOY_DIR`, `COLLECT_ON_CALENDAR`,
`ANALYZE_ON_CALENDAR`). Surcharge locale non versionnée : `deploy/config.local.env`.

## Lancer le script

- Sous Linux / WSL, depuis la racine du projet : `bash deploy/deploy.sh <commande>`
- Depuis Windows (PowerShell) : `wsl --cd "<chemin Windows du projet>" -- bash deploy/deploy.sh <commande>`
  (ne pas passer par `bash -c '...'` : les espaces du chemin OneDrive cassent les guillemets).
- SSH par clé uniquement (BatchMode). C'est la clé **WSL** qui doit être autorisée sur le
  serveur, pas la clé Windows.

| Commande                  | Effet                                                           | Écrit sur le serveur ? |
|---------------------------|-----------------------------------------------------------------|---|
| `check`                   | Accès SSH et prérequis (python ≥ 3.10, venv, rsync, systemd user, linger, claude) | non |
| `push [mission...]`       | Code + `context/` + missions données ; venv ; timers systemd     | oui |
| `status`                  | Timers, dernier passage, état de chaque mission                  | non |
| `run`                     | Lance une collecte immédiatement et affiche le log              | oui (state) |
| `logs [n]`                | Dernières lignes du journal                                      | non |
| `pull <mission>`          | Rapatrie `deals.md` et `state.json` dans `.remote/<mission>/`     | non |
| `retire <mission>`        | Arrête une mission : déplacée dans `missions/.archive/` (état gardé) | oui |

`push` ne touche jamais l'état distant (`state.json`, `deals.md`) : le serveur est maître
de ses données. Le code et `context/` sont maîtres côté local (`--delete`).

## Procédure : déployer une nouvelle recherche

1. **Vérifier la mission en local** : `missions/<nom>/mission.yaml` existe, et ses
   filtres ont été validés avec `python collect.py --mission <nom> --dry-run -v`
   (le nombre d'annonces est cohérent, pas d'erreur de source).
2. **Confirmer avec l'utilisateur** : mission, serveur, fréquence de collecte. Un
   déploiement lance des requêtes régulières vers les sites depuis son réseau.
3. `check` : corriger les `FAIL` avant d'aller plus loin. Signaler les `warn` (linger
   notamment : sans linger, les timers s'arrêtent sans session ouverte ; le corriger
   demande `sudo loginctl enable-linger <user>`, à faire par l'utilisateur).
4. `push <nom>` (on peut en donner plusieurs).
5. `run`, puis `status` : vérifier que la mission a bien collecté (`collected`, nombre
   d'annonces) et que le log ne montre pas d'erreur (403 DataDome notamment).
6. Résumer à l'utilisateur : ce qui tourne, à quelle fréquence, comment consulter le
   résultat (`pull <nom>`).

## Autres cas

- **Mettre à jour le code** après une modification : `push` sans argument (les missions
  déjà présentes continuent de tourner).
- **Modifier une mission** déjà déployée : modifier en local, `--dry-run`, puis
  `push <nom>`. L'état distant est conservé ; si les filtres ont beaucoup changé, des
  annonces passeront en `gone` à la collecte suivante, c'est attendu.
- **Arrêter une recherche** : `retire <nom>` (réversible : le dossier est archivé, pas
  supprimé). Confirmer avec l'utilisateur avant.
- **Diagnostiquer** : `status`, `logs 100`. Un 403 répété = DataDome : espacer les
  collectes (`COLLECT_ON_CALENDAR`), ne pas insister.

## Règles

- Ne jamais supprimer de données distantes ; `retire` archive. Aucune commande `rm` sur
  le serveur hors de ce script sans accord explicite.
- Ne pas modifier la configuration système du serveur (sudo, paquets, linger, clés SSH,
  `authorized_keys`) : donner la commande à l'utilisateur.
- `context/*.md` et tout `missions/` contiennent des données personnelles (ignorés par
  git, dépôt public) : ils sont copiés sur le serveur de l'utilisateur, nulle part
  ailleurs. Ne pas nommer une vraie mission dans un fichier versionné ni un message de
  commit.
