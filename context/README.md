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
| `profil.md`        | Qui est l'utilisateur : identité, lieu (+ coordonnées), foyer, situation socio-professionnelle, finances. |
| `preferences.md`   | Préférences de recherche : ce qu'il cherche en général, arbitrage prix/qualité, état, marques, vendeurs, urgence, ce qu'est un bon deal pour lui. |
| `achats.md`        | Règles d'achat générales : prix, paiement, vendeurs, signaux d'alerte. |
| `communication.md` | Comment lui présenter les résultats.                           |

Ces fichiers sont remplis par l'entretien de profil de `/dealhound wizard` (ou seul :
`/dealhound wizard profil`), qui pose des questions jusqu'à avoir assez de contexte.

## Règles d'écriture

- Faits seulement, courts, en français. Marquer `[à vérifier]` ce qui n'est pas confirmé
  ou déduit, `[non renseigné]` ce que l'utilisateur a préféré ne pas dire.
- Finances : ordres de grandeur et habitudes, jamais de montants exacts ni d'identifiants
  (comptes, pièces d'identité), ni d'adresse postale exacte.
- Pas de détails propres à une recherche ici : ils vont dans la mission.
- **Privé** : tout `context/` sauf ce README est ignoré par git (dépôt public). Les
  fichiers ne sont copiés que sur les serveurs de l'utilisateur, par `deploy.sh push`.
