# Notifications Telegram

L'analyse, sur le serveur, peut envoyer les meilleurs deals sur Telegram. `/dealhound
wizard telegram` déroule ces étapes.

## 1. Créer le bot

Dans Telegram, écrire à **@BotFather**, envoyer `/newbot`, choisir un nom et un
identifiant. BotFather donne un **token** (`123456789:AA…`) : c'est un secret, qui permet
d'envoyer des messages au nom du bot.

## 2. Mettre le token dans `secrets.env`

À la racine du projet :

```bash
cp secrets.example.env secrets.env && chmod 600 secrets.env
```

Puis coller le token dans `secrets.env` (`TELEGRAM_BOT_TOKEN="…"`). Ce fichier est
ignoré par git ; `deploy.sh push` le copie sur le serveur dans
`~/.config/dealhound/secrets.env`, lisible par ton seul compte. Ne pas le mettre dans
`config.env` et ne pas le coller dans une conversation.

## 3. Trouver les chats à notifier

Chaque personne à notifier envoie `/start` au bot. Pour un groupe : ajouter le bot au
groupe et y écrire un message. Puis :

```bash
bash deploy/py.sh -m dealhound.notify discover
```

Reporter le ou les identifiants voulus dans `config.env`, séparés par des espaces :
`NOTIFY_TELEGRAM_CHAT_IDS="123456789 -1001234567890"`.

## 4. Choisir quand notifier

`NOTIFY_MODE` dans `config.env` :

| Mode | Quand | Usage |
|---|---|---|
| `top` (défaut) | Une annonce notée pendant l'analyse entre dans le top `NOTIFY_TOP_N` (défaut 10) | Usage courant : peu de messages, seulement ce qui compte |
| `analysis` | À chaque analyse qui a noté au moins une annonce : synthèse + top 5 | Régler les critères de notation : on voit tout de suite l'effet |
| `new_deal` | À chaque analyse : toutes les annonces notées | Très bavard |
| `off` | Jamais | |

Une mission peut avoir son propre mode dans `mission.yaml` : `notify: {mode: analysis}`.
Pas d'annonce nouvelle : pas d'analyse, donc pas de message, quel que soit le mode.
En mode `top`, une annonce qui remonte dans le classement parce qu'une autre a disparu ne
déclenche pas de message : seules les annonces notées pendant l'analyse comptent.

## 5. Tester et déployer

```bash
bash deploy/py.sh -m dealhound.notify test   # envoi depuis le PC
bash deploy/deploy.sh push                   # envoie secrets.env et la config au serveur
bash deploy/deploy.sh notify-test            # envoi depuis le serveur
```

Un échec d'envoi n'interrompt jamais l'analyse : il est seulement loggé
(`deploy.sh logs`).
