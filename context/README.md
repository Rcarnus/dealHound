# Contexte utilisateur (général)

Ce dossier décrit l'utilisateur de dealHound et ses règles générales, valables pour
**toutes** les recherches. Ce qui est propre à une recherche va dans
`missions/<nom>/context.md`.

## Qui l'utilise

- **L'inférence automatisée** (`analyze.py`, via `claude -p`) : tous les fichiers `*.md`
  de ce dossier (sauf ce README) sont injectés dans le prompt d'analyse, avant le
  contexte de la mission.
- **Claude Code**, quand on crée une nouvelle recherche : lire ces fichiers avant de
  rédiger `mission.yaml` et `context.md` (lieu de départ, budget, critères habituels,
  red flags).

## Fichiers

| Fichier            | Contenu                                                        |
|--------------------|----------------------------------------------------------------|
| `profil.md`        | Qui est l'utilisateur : identité, lieu, foyer, situation pro et financière, goûts. |
| `achats.md`        | Règles d'achat générales : prix, paiement, vendeurs, signaux d'alerte. |
| `communication.md` | Comment lui présenter les résultats.                           |

## Règles d'écriture

- Faits seulement, courts, en français. Marquer `[à vérifier]` ce qui n'est pas confirmé.
- Pas de détails propres à une recherche ici : ils vont dans la mission.
- Données personnelles : ces fichiers sont ignorés par git et sont copiés sur le serveur
  par le skill de déploiement.
