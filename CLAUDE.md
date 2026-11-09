# dealHound

Chasseur de bonnes affaires sur les sites d'annonces (leboncoin, lacentrale, etc.).
Le projet collecte régulièrement les annonces correspondant à des recherches précises,
puis utilise Claude (via `claude` CLI) pour analyser les annonces et remonter les
meilleurs deals à l'utilisateur.

> Statut (2026-11-09) : collecte leboncoin, analyse automatique par `claude -p` sur le
> serveur, `deals.md`, déploiement sur le Pi et skill `/dealhound` faits ; notifications
> à faire. Les points marqués **[À TRANCHER]** sont des choix de design pas encore
> validés. Mode d'emploi : `README.md` ; préparation d'un serveur : `docs/server-setup.md`.

## Deux sortes de critères : ne jamais les mélanger

- **Critères de recherche** : filtres envoyés aux sources (`filters` + `sources.<source>`
  dans `mission.yaml`). Ils décident de ce qui est **collecté**.
- **Critères de notation** : ce qui **classe** les annonces collectées (prix, distance du
  domicile, version/variante, état, vendeur…). `scoring.criteria` dans `mission.yaml`
  (id, label, weight, barème) + le **prompt de notation complet** `missions/<id>/scoring.md`,
  rédigé à la création de la recherche. L'analyse note chaque critère 0-10 ; la note
  globale est la moyenne pondérée, calculée par le code.

## Contexte utilisateur : à lire d'abord

`context/` décrit l'utilisateur (identité, domicile, foyer, finances) et ses règles
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
- **Deux boucles, deux rythmes, sur le serveur, à intervalle fixe** (pas de
  déclenchement par événement pour l'instant) :
  1. **Recherche / collecte** toutes les `SEARCH_INTERVAL_MIN` minutes (défaut 30) :
     Python déterministe, sans inférence.
  2. **Analyse** toutes les `ANALYSIS_INTERVAL_MIN` minutes (défaut 180) : `claude -p`
     note les nouvelles annonces et les baisses de prix selon les critères de notation,
     et rédige une synthèse.
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
├── config.example.env      # modèle de config ; la vraie (config.env) est ignorée par git
├── context/                # contexte utilisateur général (ignoré par git sauf README)
├── docs/server-setup.md    # préparer un serveur (paquets, Claude Code, auth, tests)
├── deploy/
│   ├── deploy.sh           # [--host H] check|push|list|results|status|run|test-claude|logs|pull|retire
│   └── py.sh               # lance le Python local (LOCAL_PYTHON) depuis la racine
├── .claude/skills/dealhound/SKILL.md   # skill /dealhound : interface principale
├── dealhound/
│   ├── analysis.py         # prompt, schéma JSON, appel claude -p, note pondérée
│   ├── cli.py              # list | results | doctor (JSON), local ou sur le serveur
│   ├── context.py          # assemble context/ + mission pour l'analyse
│   ├── geo.py              # distance domicile -> annonce (à vol d'oiseau)
│   ├── lock.py             # verrou commun collecte/analyse (.dealhound.lock)
│   ├── models.py           # Listing, Mission, Criterion (chargement de mission.yaml)
│   ├── store.py            # state.json : chargement, fusion, écriture atomique
│   ├── report.py           # rendu de deals.md à partir de l'état
│   ├── notify.py           # (à faire) notifications push
│   └── sources/
│       ├── __init__.py     # REGISTRY : nom dans mission.yaml -> source
│       ├── base.py         # interface : search(filters) -> SearchResult(listings, complete)
│       └── leboncoin.py
├── collect.py              # boucle de recherche (toutes les missions)
├── analyze.py              # boucle d'analyse via `claude -p`
├── prompts/
│   └── analyze.md          # prompt système générique de l'analyse
├── examples/mission/       # mission d'exemple commentée (seule mission versionnée)
└── missions/               # vraies recherches : ENTIÈREMENT ignoré par git
    └── <nom>/
        ├── mission.yaml    # critères de recherche + critères de notation
        ├── scoring.md      # prompt de notation complet (rédigé à la création)
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
| `lat`, `lng`    | coordonnées de l'annonce (pour la distance au domicile) |
| `status`        | `new`, `analyzed`, `gone` (annonce disparue)   |
| `analysis`      | `score` (global, pondéré), `scores` (par critère), `eliminatory`, `summary`, `red_flags`, `questions`, `analyzed_at`, `model` |

`state.json` porte aussi `synthesis` (`text`, `at`) : la synthèse de la dernière analyse.

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
- Verrou commun avec l'analyse (`dealhound/lock.py`, `.dealhound.lock`) : si une
  collecte ou une analyse tourne, la collecte saute ce passage.
- Pauses aléatoires entre deux requêtes (3–8 s entre sources, 2–5 s entre pages).
- Codes de sortie : 0 ok, 1 au moins une source ou mission en échec, 2 configuration
  invalide.

### Boucle d'analyse (`analyze.py`)

```bash
python analyze.py                            # toutes les missions
python analyze.py --mission <id> --limit 5   # au plus 5 annonces
python analyze.py --mission <id> --dry-run   # affiche prompt + schéma, sans inférence
```

Pour chaque mission ayant des critères de notation :
1. Sélectionne les annonces `new` (nouvelles, ou repassées en `new` après une baisse de
   prix). Aucune : pas d'appel.
2. Par lots de 12, construit le prompt (`dealhound/analysis.py`) : système
   `prompts/analyze.md` ; contexte utilisateur (`context/`) ; mission (description,
   critères de recherche, `context.md`) ; `scoring.md` ; tableau des critères de
   notation ; 5 meilleures annonces déjà notées (pour comparer) ; annonces du lot en JSON
   (attributs utiles, description tronquée à 2 000 caractères, `distance_km`).
3. `claude -p --output-format json --tools "" --no-session-persistence
   --strict-mcp-config --setting-sources "" --system-prompt … --json-schema …` : la
   réponse (`structured_output`) est validée par un schéma qui exige une note entière
   0-10 par critère.
4. Le code calcule la note globale (moyenne pondérée ; plafonnée à 2 si
   `eliminatory`), enregistre l'analyse, passe les annonces en `analyzed`, met à jour
   `synthesis`, écrit l'état après chaque lot et régénère `deals.md`. Une entrée
   invalide, ou une annonce dont le prix a changé entre-temps, reste `new` et sera
   retentée au passage suivant.
5. Verrou commun avec la collecte (attente si une collecte tourne).

Modèle : `--model`, sinon `$DEALHOUND_ANALYSIS_MODEL` (posé par l'unité systemd depuis
`ANALYSIS_MODEL`), sinon le défaut de Claude Code. Coût loggé par lot (testé le
2026-11-09 sur le Pi : un lot de 3 annonces, prompt d'environ 40 000 caractères, résultat
pertinent).

### Fichier `mission.yaml` (exemple)

Modèle commenté et versionné : `examples/mission/` (`mission.yaml`, `scoring.md`,
gabarit de `context.md`). Structure :

```yaml
name: exemple-guitare
description: >            # texte libre, repris dans deals.md et dans le prompt
  Je cherche une Fender Stratocaster...
filters:                  # critères de RECHERCHE communs à toutes les sources
  query: "fender stratocaster"
  price_min: 0
  price_max: 800
  location: {city: Lyon, lat: 45.764, lng: 4.8357, radius_km: 100}  # centre = domicile
sources:                  # sources activées + leurs critères de recherche propres
  leboncoin:
    category: "30"
    max_pages: 2
scoring:                  # critères de NOTATION (prompt complet : scoring.md)
  criteria:
    - {id: prix, label: Prix, weight: 3, description: "10 = … ; 5 = … ; 0 = …"}
    - {id: distance, label: Distance, weight: 1, description: "…"}
notify:
  min_score: 8
```

Les clés de filtre acceptées par chaque source sont documentées dans la docstring de
sa fonction de traduction (`build_search_kwargs` pour leboncoin).

### `deals.md`

Fichier généré, ne pas éditer : il est réécrit à chaque collecte/analyse (seulement si
son contenu change). Contenu : synthèse de la dernière analyse, critères de notation,
tableau des deals triés par note (note globale, une colonne par critère, prix, annonce,
lieu + distance, avis et red flags), annonces pas encore analysées, disparues récentes.
`/dealhound results` lit le même état via `cli.py results` sur le serveur.

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

**Interface principale : le skill `/dealhound`** (`.claude/skills/dealhound/SKILL.md`) :
`list`, `results <id>`, `new`, `stop <id>`, `config`, `wizard`. Il pilote
`deploy/deploy.sh` (serveurs) et `deploy/py.sh` (Python local), qui s'appuient sur
`dealhound/cli.py` pour les sorties JSON. Toute nouvelle fonctionnalité destinée à
l'utilisateur doit être exposée via ce skill.

Configuration : `config.env` à la racine (ignoré par git ; modèle `config.example.env`) :
`DEPLOY_HOST` (serveur par défaut), `EXTRA_HOSTS`, `DEPLOY_DIR`, `SEARCH_INTERVAL_MIN`
(défaut 30), `ANALYSIS_INTERVAL_MIN` (défaut 180), `ANALYSIS_MODEL` (vide = défaut de
Claude Code), `LOCAL_PYTHON`. Lu par `deploy.sh` (sourcé) et `cli.py doctor`. Le serveur
n'a pas de `config.env` : `push` traduit la config en unités systemd.
`push` écrit la version du code (`git describe`) dans `~/<DEPLOY_DIR>/.deployed` ;
`check` l'affiche à côté de la version locale.

- Serveur par défaut : celui de `DEPLOY_HOST` dans `config.env` (ne jamais écrire son
  adresse dans un fichier versionné). Actuellement un Raspberry Pi, Debian 13 aarch64,
  Python 3.13, `claude` installé. Installation dans `~/<DEPLOY_DIR>` avec son propre `.venv`.
- **systemd timers utilisateur, à intervalle fixe** (`OnActiveSec` + `OnUnitActiveSec`,
  délai aléatoire ≤ 5 min) : `dealhound-collect.timer` toutes les `SEARCH_INTERVAL_MIN`
  (1er passage 2 min après le push), `dealhound-analyze.timer` toutes les
  `ANALYSIS_INTERVAL_MIN` (1er passage 10 min après). `push` réécrit les unités et
  redémarre les timers (nouveaux intervalles appliqués). L'unité d'analyse pose
  `DEALHOUND_ANALYSIS_MODEL` et un `PATH` incluant `~/.local/bin` (claude).
- Claude Code sur le serveur : installé dans `~/.local/bin`, authentifié une fois
  (`docs/server-setup.md`). `check` vérifie présence et identifiants ; `test-claude` fait
  un vrai appel minuscule (~0,02 $). Vérifié le 2026-11-09 sur le Pi : modèle par défaut
  `claude-opus-5-5`, `structured_output` présent dans la sortie JSON.
- Logs : journald. Sur ce Debian, les logs des unités utilisateur sont dans le journal
  système : lire avec `journalctl --user-unit 'dealhound-*'` (`journalctl --user` ne
  trouve rien). L'utilisateur distant doit être dans le groupe `adm` ou `systemd-journal`.
- `push` synchronise le code et `context/` (local maître, `--delete`) et les missions
  données (`mission.yaml`, `scoring.md`, `context.md`), sans jamais toucher `state.json` /
  `deals.md` distants.
- **Linger** : sans lui, les timers ne tournent que tant que l'utilisateur distant a une
  session ouverte. `deploy.sh check` le signale ; à corriger par l'utilisateur :
  `sudo loginctl enable-linger <user>`. (Désactivé sur le Pi au 2026-11-08.)
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
- Mission : YAML structuré (critères de recherche + critères de notation) + prompt de
  notation en Markdown (`scoring.md`), tous deux rédigés par `/dealhound new`.
- Analyse sur le serveur, à intervalle fixe (pas de déclenchement par événement pour
  l'instant), sortie structurée validée par JSON Schema, note globale calculée par le
  code (moyenne pondérée), distance calculée par le code.

## Décisions ouvertes

- **[À TRANCHER] Canal de notification** : ntfy (simple, sans compte) ou Telegram (bot).
- **[À TRANCHER] Déclenchement de l'analyse par événement** (ex. juste après une collecte
  qui trouve du nouveau) plutôt qu'à intervalle fixe.
