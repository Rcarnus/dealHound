# dealHound

Chasseur de bonnes affaires sur les sites d'annonces. dealHound surveille des recherches
précises (« missions ») sur leboncoin, garde l'historique des annonces (nouvelles, baisses
de prix, disparues) dans un fichier Markdown lisible, et tournera en continu sur un
Raspberry Pi. L'analyse automatique des annonces par Claude (score, red flags, synthèse)
est la prochaine étape.

| Fonction                              | Statut      |
|---------------------------------------|-------------|
| Source leboncoin                      | fait        |
| Collecte + historique + `deals.md`    | fait        |
| Déploiement sur le Pi (systemd timers)| fait        |
| Analyse par `claude -p`               | à faire     |
| Notifications téléphone               | à faire     |
| Autres sources (La Centrale…)         | à faire     |

## Installation (développement, WSL)

Prérequis : Linux ou WSL, Python ≥ 3.10.

```bash
sudo apt install python3-venv rsync
python3 -m venv ~/.venvs/dealhound
~/.venvs/dealhound/bin/pip install -r requirements.txt
```

Sous WSL, garder le venv **hors du dossier OneDrive** (lent, et OneDrive essaierait de le
synchroniser). Depuis PowerShell, lancer les commandes avec
`wsl --cd "<chemin du projet>" -- <commande>`.

Dans la suite, `python` désigne `~/.venvs/dealhound/bin/python`.

## Créer une recherche

Une recherche = un dossier `missions/<nom>/`. **Tout `missions/` est ignoré par git** :
les vraies recherches restent privées. Seul l'exemple `examples/mission/` est versionné.

```
missions/<nom>/
├── mission.yaml   # critères (obligatoire)
├── context.md     # contexte libre pour l'analyse (optionnel, privé)
├── state.json     # état, généré : ne pas éditer
└── deals.md       # résultat, généré : à lire
```

1. Partir de l'exemple commenté :

   ```bash
   mkdir -p missions/<nom> && cp examples/mission/* missions/<nom>/
   ```

2. Remplir `mission.yaml` :
   - `filters` : filtres durs communs (`query`, `price_min`, `price_max`, `location` avec
     `city`, `lat`, `lng`, `radius_km`).
   - `sources.leboncoin` : `category` (`"2"` voitures, `"30"` instruments…), `max_pages`,
     et pour aller plus loin `enums` / `ranges` (marque, modèle, carburant, année,
     kilométrage… : exemple voiture en commentaire dans `examples/mission/mission.yaml`).
     Les clés possibles sont listées dans la docstring de `build_search_kwargs`
     (`dealhound/sources/leboncoin.py`).
   - `analysis` : critères souples (`must_have`, `nice_to_have`, `red_flags`) pour l'analyse.
3. Optionnel : `context.md` (gabarit fourni) pour tout ce que l'analyse doit savoir sur
   cette recherche (méthode de calcul, annonces déjà vues, pièges du modèle…). Sinon,
   le supprimer.
4. Tester sans rien écrire :

   ```bash
   python collect.py --mission <nom> --dry-run -v
   ```

Avec Claude Code dans ce dossier, on peut simplement décrire la recherche voulue : il lit
`context/` (profil, règles d'achat) et rédige la mission.

## Collecter et lire les résultats

```bash
python collect.py                    # toutes les missions
python collect.py --mission <nom>    # une seule
```

Ouvrir `missions/<nom>/deals.md` : annonces à analyser, baisses de prix (↓), annonces
disparues sur les 30 derniers jours. Le fichier n'est réécrit que si quelque chose change.

Test rapide d'une recherche leboncoin, sans mission :

```bash
python -m dealhound.sources.leboncoin "fender stratocaster" --price-max 800 --category 30
```

## Déployer sur le serveur (Raspberry Pi)

Configuration : `deploy/config.env` (hôte SSH, dossier, fréquences). L'accès SSH doit
se faire par clé **depuis WSL** (`ssh-copy-id <user>@<hôte>` depuis WSL).

```bash
bash deploy/deploy.sh check              # prérequis du serveur (lecture seule)
bash deploy/deploy.sh push <nom>         # code + contexte + mission, venv, timers
bash deploy/deploy.sh run                # collecte immédiate
bash deploy/deploy.sh status             # timers, dernier passage, état des missions
bash deploy/deploy.sh logs 100           # journal
bash deploy/deploy.sh pull <nom>         # rapatrie deals.md dans .remote/<nom>/
bash deploy/deploy.sh retire <nom>       # arrête la mission (archivée, rien n'est supprimé)
```

Avec Claude Code, le skill `deploy-remote` fait la même chose sur demande
(« déploie la recherche <nom> sur le Pi », « qu'est-ce qui tourne sur le serveur ? »).

Sur le serveur, la collecte tourne toutes les 30 minutes via un timer systemd utilisateur.
Pour qu'elle continue sans session ouverte, activer le *linger* une fois :
`sudo loginctl enable-linger <user>`.

## Contexte utilisateur

`context/` décrit l'utilisateur et ses règles générales (`profil.md`, `achats.md`,
`communication.md`) ; voir `context/README.md`. Il sert à l'analyse automatique et à
Claude Code quand on crée une recherche. Ces fichiers et les `context.md` des missions
contiennent des données personnelles : ils sont ignorés par git et copiés sur le serveur
par `deploy.sh push`.

## Avertissements

- leboncoin n'a pas d'API publique et protège son API interne (DataDome). dealHound
  utilise la lib communautaire non officielle [`lbc`](https://github.com/etienne-hd/lbc),
  ce qui est probablement contraire aux CGU de leboncoin : usage personnel, faible volume,
  pauses entre requêtes. En cas de blocages (403) répétés, espacer les collectes.
- dealHound aide à repérer des annonces ; il ne remplace ni la vérification de
  l'annonce, ni celle du vendeur.

Documentation technique (architecture, décisions, conventions) : `CLAUDE.md`.
