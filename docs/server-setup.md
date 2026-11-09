# Préparer un serveur dealHound

Le serveur fait tourner la recherche (toutes les `SEARCH_INTERVAL_MIN` minutes) et
l'analyse par Claude (toutes les `ANALYSIS_INTERVAL_MIN` minutes). `/dealhound wizard`
déroule ces étapes et vérifie chacune. Les commandes `sudo` et les connexions
interactives sont à lancer par toi : Claude ne les fait pas à ta place.

Commandes locales : sous Linux/WSL, depuis la racine du projet.

## 1. Machine

- Linux 64 bits (Raspberry Pi OS 64 bits ou Debian/Ubuntu, `aarch64` ou `x86_64`) :
  `curl_cffi`, utilisé pour leboncoin, n'a pas de paquet précompilé pour l'ARM 32 bits.
- Un compte utilisateur normal (pas root), joignable en SSH sur le réseau local.
- ~1 Go de RAM libre et quelques centaines de Mo de disque suffisent.

## 2. Accès SSH par clé, depuis WSL

Le script de déploiement tourne dans WSL : c'est la clé **WSL** qui doit être autorisée,
pas celle de Windows.

```bash
ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519      # dans WSL
ssh-copy-id <user>@<hôte>                              # demande le mot de passe une fois
ssh -o BatchMode=yes <user>@<hôte> true && echo ok     # doit afficher ok
```

## 3. Paquets et réglages système (sur le serveur)

```bash
sudo apt update && sudo apt install -y python3 python3-venv rsync curl
sudo loginctl enable-linger $USER          # timers actifs sans session ouverte
sudo usermod -aG systemd-journal $USER     # lecture des logs (inutile si déjà dans adm)
```

## 4. Installer Claude Code (sur le serveur)

```bash
curl -fsSL https://claude.ai/install.sh | bash   # installe ~/.local/bin/claude
~/.local/bin/claude --version
```

## 5. Authentifier Claude Code

L'analyse appelle `claude -p` sans interaction : le serveur doit être authentifié une
fois pour toutes.

**Option A, compte Claude (abonnement)**, depuis ton PC :

```bash
ssh -t <user>@<hôte> '~/.local/bin/claude'
```

Dans Claude Code, taper `/login`, ouvrir le lien affiché dans le navigateur du PC, coller
le code obtenu, puis quitter (`/exit`). Les identifiants restent dans
`~/.claude/.credentials.json` sur le serveur.

**Option B, clé API (facturation à l'usage)** : sur le serveur, la mettre dans
l'environnement des services utilisateur, lisible par toi seul :

```bash
mkdir -p ~/.config/environment.d && chmod 700 ~/.config/environment.d
echo 'ANTHROPIC_API_KEY=<ta clé>' > ~/.config/environment.d/claude.conf
chmod 600 ~/.config/environment.d/claude.conf
systemctl --user daemon-reexec
```

Ne jamais mettre la clé dans le projet ni dans `config.env`.

## 6. Vérifier

```bash
bash deploy/deploy.sh check          # prérequis : tout doit être « ok »
bash deploy/deploy.sh test-claude    # vrai appel claude -p minuscule (~0,03 $)
```

## 7. Installer dealHound et tester de bout en bout

```bash
bash deploy/deploy.sh push           # code, venv, timers (sans mission)
bash deploy/deploy.sh status         # deux timers : recherche et analyse
```

Puis créer et déployer une recherche avec `/dealhound new`. `deploy.sh run` et
`deploy.sh run analyze` lancent une recherche ou une analyse immédiatement.

## Coût de l'analyse

Chaque analyse ne note que les nouvelles annonces et les baisses de prix, par lots
d'environ 12. Sans nouveauté, il n'y a pas d'appel. Le coût dépend du modèle
(`ANALYSIS_MODEL` dans `config.env`, vide = modèle par défaut de Claude Code) et de la
taille du contexte de la mission. `deploy.sh logs` affiche le coût de chaque lot.
