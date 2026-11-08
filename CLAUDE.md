# dealHound

Chasseur de bonnes affaires sur les sites d'annonces (leboncoin, lacentrale, etc.).
Le projet collecte régulièrement les annonces correspondant à des recherches précises,
puis utilise Claude (via `claude` CLI) pour analyser les annonces et remonter les
meilleurs deals à l'utilisateur.

> Statut (2026-11-08) : collecte leboncoin, `deals.md` et déploiement sur le Pi faits ;
> analyse (`analyze.py`) et notifications à faire. Les points marqués **[À TRANCHER]**
> sont des choix de design pas encore validés. Mode d'emploi utilisateur : `README.md`.

## Contexte utilisateur : à lire d'abord

`context/` décrit l'utilisateur (Romain, L'Union près de Toulouse) et ses règles
générales : `profil.md`, `achats.md`, `communication.md`. **Avant de créer ou modifier
une recherche, lire ces fichiers** : lieu de départ et coordonnées, budget tout compris,
pas de négociation, red flags, façon de présenter les résultats. Ce qui est propre à une
recherche va dans `missions/<nom>/context.md`, pas dans `context/`.
`dealhound/context.py` (`build_context`) assemble les deux pour l'analyse.

## Principe

- **Un code, plusieurs missions.** Une mission = une recherche précise : « Fender
  Stratocaster < 800 € autour de Lyon », « Peugeot 308 diesel < 100 000 km < 12 000 € »,
  etc. Chaque mission vit dans son propre dossier `missions/<nom>/` (critères + données).
  Le même code traite toutes les missions.
- **Des sources de données** : un module par site d'annonces, qui interroge le site et
  renvoie les annonces dans un format commun.
- **Deux boucles, deux rythmes** :
  1. **Collecte** (fréquente, ex. toutes les 30 min) : script Python déterministe, sans
     LLM. Interroge les sources, déduplique, met à jour l'état de chaque mission.
  2. **Analyse** (rare, ex. 3 fois par jour) : appel à `claude -p` (mode headless) qui
     évalue les nouvelles annonces au regard des critères de la mission, les classe et
     rédige une synthèse.
- **Stockage local** : un état machine en JSON par mission (source de vérité), et un
  `deals.md` régénéré à partir de cet état pour la lecture humaine.
- **Notification** : les meilleurs deals (au-dessus d'un seuil de score) sont aussi
  poussés sur téléphone (ntfy ou Telegram).

## Contraintes d'environnement

- **Cible : Linux.** Développement sur un host Windows, tests sous **WSL**, déploiement
  final sur une autre machine, probablement un **Raspberry Pi** (ARM64, ressources
  limitées, stockage sur carte SD).
- **Rester lightweight** :
  - Python 3 + un minimum de dépendances (`httpx` ou `requests`, `pyyaml`…).
  - Pas de navigateur headless (Playwright/Selenium) : on n'y vient qu'en dernier
    recours, si une source est inaccessible en HTTP simple.
  - Pas de base de données serveur, pas de Docker obligatoire.
  - Planification via systemd timers (utilisateur).
  - Limiter les écritures disque (carte SD) : réécrire les fichiers seulement s'ils
    ont changé, logs avec rotation.
- Le code doit tourner à l'identique sous WSL et sur le Pi : chemins POSIX, pas de
  dépendance à Windows, fins de ligne LF.

## Architecture

```
dealHound/
├── CLAUDE.md
├── README.md               # mode d'emploi
├── requirements.txt        # lbc, pyyaml
├── context/                # contexte utilisateur général (ignoré par git sauf README)
├── deploy/
│   ├── config.env          # serveur cible, horaires des timers
│   └── deploy.sh           # check | push | status | run | logs | pull | retire
├── .claude/skills/deploy-remote/SKILL.md   # skill Claude Code de déploiement
├── dealhound/
│   ├── context.py          # assemble context/ + mission pour l'analyse
│   ├── models.py           # Listing, Mission (chargement de mission.yaml)
│   ├── store.py            # state.json : chargement, fusion, écriture atomique
│   ├── report.py           # rendu de deals.md à partir de l'état
│   ├── notify.py           # (à faire) notifications push
│   └── sources/
│       ├── __init__.py     # REGISTRY : nom dans mission.yaml -> source
│       ├── base.py         # interface : search(filters) -> SearchResult(listings, complete)
│       └── leboncoin.py
├── collect.py              # boucle de collecte (toutes les missions)   [fait]
├── analyze.py              # (à faire) boucle d'analyse via `claude -p`
├── prompts/
│   └── analyze.md          # (à faire) prompt d'analyse envoyé à Claude
├── examples/mission/       # mission d'exemple commentée (seule mission versionnée)
└── missions/               # vraies recherches : ENTIÈREMENT ignoré par git
    └── <nom>/
        ├── mission.yaml    # critères de la recherche
        ├── context.md      # (optionnel) contexte libre pour l'analyse : profil, budget,
        │                   #   méthode de calcul, annonces déjà vues, red flags
        ├── state.json      # état machine (source de vérité, ne pas éditer à la main)
        └── deals.md        # vue générée, lisible par l'utilisateur
```

### Modèle commun d'une annonce (`Listing`)

Chaque source normalise ses résultats vers ce format :

| Champ           | Description                                    |
|-----------------|------------------------------------------------|
| `id`            | identifiant unique `<source>:<id_annonce>`     |
| `source`        | `leboncoin`, `lacentrale`, …                   |
| `url`           | lien vers l'annonce                            |
| `title`         | titre                                          |
| `price`         | prix actuel en euros                           |
| `price_history` | liste `[date, prix]` pour détecter les baisses |
| `location`      | ville / code postal                            |
| `published`     | date de publication                            |
| `attributes`    | champs spécifiques (km, année, marque…)        |
| `description`   | texte de l'annonce (tronqué si besoin)         |
| `first_seen`    | première détection par dealHound               |
| `last_seen`     | dernière détection                             |
| `status`        | `new`, `analyzed`, `gone` (annonce disparue)   |
| `analysis`      | score, justification, red flags (rempli par Claude) |

### Boucle de collecte (`collect.py`)

```bash
python collect.py                        # toutes les missions
python collect.py --mission <nom>
python collect.py --dry-run -v           # interroge les sources, n'écrit rien
```

Pour chaque mission de `missions/` :
1. Lit `mission.yaml`.
2. Pour chaque source activée, appelle `search()` avec les filtres communs fusionnés
   avec les filtres propres à la source.
3. Fusionne avec `state.json` : ajoute les nouvelles annonces (`status: new`), met à
   jour `price_history` (une baisse de prix repasse l'annonce en `new` pour une nouvelle
   analyse), marque `gone` les annonces disparues, et repasse en actif une annonce qui
   réapparaît.
4. Écrit `state.json` (atomique) puis régénère `deals.md` seulement si son contenu change.

Règles :
- **« Disparue » seulement si la recherche est complète** (`SearchResult.complete`, la
  dernière page n'était pas pleine). Sinon une annonce juste sortie des `max_pages`
  premières pages serait marquée disparue à tort.
- Aucun appel LLM : rapide, gratuit, peut tourner souvent.
- Une source en erreur est loggée et n'empêche pas les autres de tourner. Ses annonces
  ne sont pas touchées.
- Verrou `fcntl` sur `.collect.lock` : un second lancement concurrent sort sans rien faire.
- Pauses aléatoires entre deux requêtes (3–8 s entre sources, 2–5 s entre pages).
- Codes de sortie : 0 ok, 1 au moins une source ou mission en échec, 2 configuration
  invalide.

### Boucle d'analyse (`analyze.py`)

Pour chaque mission :
1. Sélectionne les annonces à analyser (`new`, ou prix en baisse).
2. S'il n'y en a aucune, ne lance pas Claude (économie d'appels).
3. Appelle `claude -p` avec `prompts/analyze.md`, la mission (y compris les critères
   « souples » en langage naturel) et les annonces, en demandant une sortie JSON.
4. Claude attribue un score (0–10), justifie, signale les red flags (prix suspect,
   arnaque probable, annonce incomplète) et rédige une synthèse.
5. Enregistre l'analyse dans `state.json`, passe les annonces en `analyzed`,
   régénère `deals.md`, et envoie une notification pour les annonces au-dessus de
   `notify.min_score`.

Notes `claude` CLI :
- Mode non interactif : `claude -p --output-format json`, prompt passé sur stdin.
- Restreindre les outils (`--allowedTools`) : l'analyse n'a besoin d'aucun outil,
  toutes les données sont dans le prompt.
- Valider le JSON renvoyé ; en cas de sortie invalide, logger et réessayer au
  prochain passage plutôt que corrompre l'état.
- L'authentification Claude doit être configurée sur la machine cible (WSL, puis Pi).

### Fichier `mission.yaml` (exemple)

Modèle commenté et versionné : `examples/mission/` (`mission.yaml` + gabarit de
`context.md`). Structure :

```yaml
name: exemple-guitare
description: >            # texte libre, repris dans deals.md et dans le prompt
  Je cherche une Fender Stratocaster...
filters:                  # filtres durs communs à toutes les sources
  query: "fender stratocaster"
  price_min: 0
  price_max: 800
  location: {city: Lyon, lat: 45.764, lng: 4.8357, radius_km: 100}
sources:                  # sources activées + filtres propres (écrasent les communs)
  leboncoin:
    category: "30"
    max_pages: 2
analysis:                 # critères souples, évalués par Claude
  must_have: [...]
  nice_to_have: [...]
  red_flags: [...]
notify:
  min_score: 8
```

Les clés de filtre acceptées par chaque source sont documentées dans la docstring de
sa fonction de traduction (`build_search_kwargs` pour leboncoin).

### `deals.md`

Fichier généré, ne pas éditer : il est réécrit à chaque collecte/analyse. Contenu :
synthèse de la dernière analyse, puis tableau des deals triés par score (titre, prix,
lieu, score, lien), puis les annonces pas encore analysées, puis les disparues récentes.

### Missions : privées

- **Tout `missions/` est ignoré par git** : les vraies recherches (critères, budget,
  contexte, état) ne doivent jamais être commitées, le dépôt est public. Seul
  `examples/mission/` est versionné.
- Ne jamais nommer ni décrire une vraie mission dans un fichier versionné (ce fichier,
  README, skill, messages de commit) : utiliser `<nom>` ou l'exemple.
- Pour connaître les missions existantes : lister `missions/` en local, ou
  `deploy.sh status` sur le serveur. Lire leur `mission.yaml` et `context.md`.
- Créer une mission : copier `examples/mission/` dans `missions/<nom>/`, après avoir lu
  `context/`.
- `context/*.md` contient aussi des données personnelles (ignoré par git sauf README).
  `deploy.sh push` copie le contexte et les missions données sur le serveur.

## Sources de données

| Source      | Accès                                                        |
|-------------|--------------------------------------------------------------|
| leboncoin   | Pas d'API publique officielle. API interne utilisée par le site/app, protégée par un anti-bot. |
| lacentrale  | Pas d'API publique officielle. Même situation.               |

Approche : **HTTP léger d'abord** (appels directs aux API internes, ou bibliothèque
communautaire existante si elle est maintenue). On n'évalue un navigateur headless que
si une source est bloquée en HTTP simple.

### leboncoin

- Endpoint interne : `POST https://api.leboncoin.fr/finder/search` (corps JSON :
  `filters.category/keywords/ranges/location/enums`, `limit`, `offset`, `sort_by`).
- **Testé le 2026-11-08** : une requête HTTP simple (stdlib, User-Agent honnête) est
  rejetée en **403 par DataDome** (redirection captcha). Le HTTP « naïf » ne suffit pas.
- **Décision** : utiliser la lib communautaire **`lbc`** (github.com/etienne-hd/lbc,
  MIT, non officielle). Elle passe DataDome via `curl_cffi` (empreinte TLS de navigateur),
  un User-Agent d'app mobile et une visite préalable de la home pour les cookies.
  Contraire aux CGU de leboncoin ; usage strictement personnel et faible volume.
- Testé avec `lbc` 1.1.9 le 2026-11-08 depuis WSL : la recherche fonctionne (35 annonces).
- Implémentation : `lbc.Client.search()` pour le transport, puis notre `parse_ad()` sur
  `ad.raw` pour produire des `Listing`. `max_retries=2` au lieu de 5 par défaut.
- En cas de 403 persistant : espacer les requêtes, ne pas insister (risque de ban IP).
- Filtres serveur génériques via `enums` / `ranges` dans `mission.yaml` : les clés sont
  les clés d'attributs des annonces. Voitures (catégorie `"2"`), vérifié le 2026-11-08 :
  `u_car_brand` (`TOYOTA`), `u_car_model` (`TOYOTA_Corolla`), `fuel` (`"6"` hybride,
  `"8"` hybride rechargeable, `"2"` diesel), `vehicle_type` (`break`…), plages `regdate`,
  `mileage`.
- Attributs voiture utiles pour l'analyse : `car_price_min` / `car_price_max` /
  `car_price_positioning` (cote leboncoin), `vehicle_history_report_public_url`
  (rapport Autoviza), `u_car_version`, `issuance_date`, `vehicle_commercial_warranty_duration`,
  `store_name`, `owner_type` (`pro` / `private`, ajouté par notre parser). Les attributs
  sont stockés avec leur libellé (`"7803 km"`), pas la valeur brute.
- Code : `dealhound/sources/leboncoin.py`. Test manuel :
  `python3 -m dealhound.sources.leboncoin "fender stratocaster" --price-max 800`

Règles pour toutes les sources :
- Usage personnel, faible volume : rate limiting strict, délais entre requêtes,
  pas de collecte massive.
- Respecter les CGU des sites ; si une source bloque, ne pas chercher à contourner
  agressivement les protections.
- Gérer proprement les erreurs (blocage, changement de format) : logger et continuer
  avec les autres sources plutôt que planter.

## Déploiement et planification

Skill Claude Code : `.claude/skills/deploy-remote/SKILL.md` (procédure complète). Script :
`deploy/deploy.sh`, lancé depuis WSL ; configuration dans `deploy/config.env`.

- Serveur : `rom@192.168.5.225`, Raspberry Pi, Debian 13 aarch64, Python 3.13, `claude`
  installé. Installation dans `~/dealhound` avec son propre `.venv`.
- **systemd timers utilisateur** : `dealhound-collect.timer` (`*:0/30`, délai aléatoire
  ≤ 5 min, `Persistent=true`) ; `dealhound-analyze.timer` (8 h, 13 h, 19 h) installé
  automatiquement dès que `analyze.py` existe.
- Logs : journald. Sur ce Debian, les logs des unités utilisateur sont dans le journal
  système : lire avec `journalctl --user-unit 'dealhound-*'` (`journalctl --user` ne
  trouve rien). `rom` est dans le groupe `adm`.
- `push` synchronise le code et `context/` (local maître, `--delete`) et les missions
  données (`mission.yaml`, `context.md`), sans jamais toucher `state.json` / `deals.md`
  distants.
- **Linger désactivé** sur le Pi (vérifié le 2026-11-08) : les timers ne tournent que
  tant que `rom` a une session ouverte. À corriger par l'utilisateur :
  `sudo loginctl enable-linger rom`.
- Clé SSH : c'est la clé de **WSL** qui doit être autorisée (le script tourne dans WSL),
  pas celle de Windows.

## Commandes de dev

Les tests se lancent sous WSL (distro Ubuntu, Python 3.14). Prérequis une fois :
`sudo apt install python3.14-venv`, puis un venv **hors du dossier OneDrive** (un venv
sur `/mnt/c` est lent et OneDrive tenterait de le synchroniser) :
`python3 -m venv ~/.venvs/dealhound && ~/.venvs/dealhound/bin/pip install -r requirements.txt`.
Sur le Pi, un `.venv/` à la racine du projet convient (ignoré par git).

Depuis Windows/PowerShell, lancer une commande WSL dans le dossier du projet avec
`wsl --cd "<chemin Windows du projet>" -- <commande>` : passer des chemins avec espaces
dans `bash -c '...'` via PowerShell casse les guillemets.

```bash
~/.venvs/dealhound/bin/python -m dealhound.sources.leboncoin "fender stratocaster" --price-max 800 --category 30
```

## Conventions

- Langue : code et identifiants en anglais, docs et sorties utilisateur en français.
- Python 3.11+, typage simple, pas de framework.
- Pas de secrets dans le repo (tokens ntfy/Telegram, etc.) : variables d'environnement
  ou fichier `.env` ignoré par git.
- `missions/` (critères et données des vraies recherches) est entièrement ignoré par git.

## Décisions prises

- Un seul code, N missions dans `missions/<nom>/`.
- État en JSON par mission (source de vérité) + `deals.md` généré pour la lecture.
- Accès aux sources en HTTP léger d'abord ; navigateur headless en dernier recours.
- Notification push des meilleurs deals en plus de `deals.md`.
- leboncoin via la lib `lbc` (le HTTP simple est bloqué par DataDome).
- Planification par systemd timers utilisateur ; déploiement par SSH + rsync.
- Contexte utilisateur général dans `context/`, contexte de recherche dans la mission.

## Décisions ouvertes

- **[À TRANCHER] Canal de notification** : ntfy (simple, sans compte) ou Telegram (bot).
- **[À TRANCHER] Format de la mission** : YAML structuré (proposé ci-dessus) ou Markdown
  en langage naturel.
