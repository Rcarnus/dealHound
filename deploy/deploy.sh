#!/usr/bin/env bash
# Deploy dealHound to remote Linux hosts (Raspberry Pi) over SSH, run it with
# systemd user timers. Run from Linux/WSL. Configuration: config.env at the project
# root (template: config.example.env). Usually driven by the /dealhound skill.
#
#   deploy.sh [--host H] check              SSH access and remote prerequisites (read-only)
#   deploy.sh [--host H] push [mission...]  sync code + context/, install venv and timers,
#                                           and sync the given missions (mission.yaml, scoring.md, context.md, blacklist.yaml)
#   deploy.sh list                          missions on every configured host (JSON lines)
#   deploy.sh [--host H] results <mission> [n]   best n deals of a remote mission (JSON)
#   deploy.sh [--host H] status             timers, last runs, missions present on the server
#   deploy.sh [--host H] run [collect|analyze]   run a collection (default) or an analysis now
#   deploy.sh [--host H] test-claude        tiny claude -p call on the server (auth check, ~0.03 $)
#   deploy.sh [--host H] notify-test        send a Telegram test message from the server
#   deploy.sh [--host H] notify-preview <mission>  send the current top N (compact) to Telegram
#   deploy.sh [--host H] logs [n]           last n journal lines (default 50)
#   deploy.sh [--host H] pull <mission>     fetch remote deals.md + state.json into .remote/<mission>/
#   deploy.sh [--host H] retire <mission>   stop a mission: move it to missions/.archive/ on the server
#
# --host defaults to DEPLOY_HOST. Remote state (state.json, deals.md) is never
# overwritten by push.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
CONFIG="${DEALHOUND_CONFIG:-$ROOT/config.env}"
die() { echo "ERROR: $*" >&2; exit 1; }

HOST_OVERRIDE=""
if [ "${1:-}" = "--host" ]; then
  [ -n "${2:-}" ] || die "--host needs a value"
  HOST_OVERRIDE="$2"; shift 2
fi

[ -f "$CONFIG" ] || die "no config file ($CONFIG): run '/dealhound wizard' in Claude Code, or cp config.example.env config.env"
# shellcheck source=/dev/null
source "$CONFIG"

: "${DEPLOY_HOST:?DEPLOY_HOST not set in $CONFIG}"
[ -n "$HOST_OVERRIDE" ] && DEPLOY_HOST="$HOST_OVERRIDE"
EXTRA_HOSTS="${EXTRA_HOSTS:-}"
DEPLOY_DIR="${DEPLOY_DIR:-dealhound}"
SEARCH_INTERVAL_MIN="${SEARCH_INTERVAL_MIN:-30}"
ANALYSIS_INTERVAL_MIN="${ANALYSIS_INTERVAL_MIN:-180}"
ANALYSIS_MODEL="${ANALYSIS_MODEL:-}"
[[ "$DEPLOY_DIR" =~ ^[A-Za-z0-9._/-]+$ ]] || die "invalid DEPLOY_DIR: $DEPLOY_DIR"
for v in SEARCH_INTERVAL_MIN ANALYSIS_INTERVAL_MIN; do
  [[ "${!v}" =~ ^[0-9]+$ ]] && [ "${!v}" -ge 5 ] || die "$v must be a number of minutes >= 5 (got '${!v}')"
done
[[ "$ANALYSIS_MODEL" =~ ^[A-Za-z0-9._-]*$ ]] || die "invalid ANALYSIS_MODEL: $ANALYSIS_MODEL"
NOTIFY_TELEGRAM_CHAT_IDS="${NOTIFY_TELEGRAM_CHAT_IDS:-}"
NOTIFY_MODE="${NOTIFY_MODE:-top}"
NOTIFY_TOP_N="${NOTIFY_TOP_N:-10}"
[[ "$NOTIFY_TELEGRAM_CHAT_IDS" =~ ^(-?[0-9]+( +-?[0-9]+)*)?$ ]] || die "NOTIFY_TELEGRAM_CHAT_IDS: numeric chat ids, space-separated"
[[ "$NOTIFY_MODE" =~ ^(top|top10|analysis|each_analysis|new_deal|new|off|none)$ ]] || die "invalid NOTIFY_MODE: $NOTIFY_MODE"
[[ "$NOTIFY_TOP_N" =~ ^[0-9]+$ ]] && [ "$NOTIFY_TOP_N" -ge 1 ] || die "NOTIFY_TOP_N must be a number >= 1"
SECRETS="$ROOT/secrets.env"
REMOTE_SECRETS='.config/dealhound/secrets.env'   # relative to the remote $HOME

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10)
CODE_PATHS=(dealhound collect.py analyze.py prompts requirements.txt CLAUDE.md context)

rsh() { ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" "$@"; }
# rscript [args...] < script : run a bash script on the remote with safely quoted args
# (ssh joins its arguments into one string re-parsed by the remote shell).
rscript() { ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" "bash -s -- $(printf '%q ' "$@")"; }
rsync_to() { rsync -az -e "ssh ${SSH_OPTS[*]}" "$@"; }
need_mission() { [ -n "${1:-}" ] || die "mission name required"; }
valid_name() { [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid mission name: $1"; }
local_version() { git -C "$ROOT" describe --always --dirty 2>/dev/null || echo unknown; }

cmd_check() {
  echo "== $DEPLOY_HOST"
  rsh true 2>/dev/null || die "SSH key login to $DEPLOY_HOST failed. Authorize your key: ssh-copy-id $DEPLOY_HOST"
  echo "  info  local code version: $(local_version)"
  rscript "$DEPLOY_DIR" <<'EOS'
ok() { echo "  ok    $*"; }; ko() { echo "  FAIL  $*"; FAILED=1; }; warn() { echo "  warn  $*"; }
FAILED=0
. /etc/os-release 2>/dev/null && ok "os: ${PRETTY_NAME:-?} ($(uname -m))"
case "$(uname -m)" in aarch64|x86_64) ;; *) warn "arch $(uname -m): curl_cffi wheels may be missing (64-bit OS recommended)";; esac
if command -v python3 >/dev/null && python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  ok "python $(python3 -c 'import platform; print(platform.python_version())')"
else ko "python3 >= 3.10 missing"; fi
python3 -c 'import venv, ensurepip' 2>/dev/null && ok "python venv" || ko "python venv missing: sudo apt install python3-venv"
command -v rsync >/dev/null && ok "rsync" || ko "rsync missing: sudo apt install rsync"
systemctl --user show-environment >/dev/null 2>&1 && ok "systemd user session" || ko "systemd --user unavailable"
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" = yes ]; then ok "linger enabled"
else warn "linger disabled: timers stop when you log out. Fix: sudo loginctl enable-linger $USER"; fi
export PATH="$HOME/.local/bin:$PATH"
if command -v claude >/dev/null; then
  ok "claude cli $(claude --version 2>/dev/null | head -1)"
  if [ -f "$HOME/.claude/.credentials.json" ] || [ -n "${ANTHROPIC_API_KEY:-}${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    ok "claude credentials present (validate with: deploy.sh test-claude)"
  else ko "claude not authenticated: see docs/server-setup.md (claude login over ssh -t)"; fi
else ko "claude cli missing (needed by the analysis): see docs/server-setup.md"; fi
ok "disk: $(df -h "$HOME" | awk 'NR==2 {print $4 " free"}'), ram: $(free -m | awk '/Mem:/ {print $7 " MB available"}')"
if [ -f "$HOME/$1/.deployed" ]; then ok "dealhound installed in ~/$1, version $(cat "$HOME/$1/.deployed")"
else warn "dealhound not installed in ~/$1 yet (deploy.sh push)"; fi
exit $FAILED
EOS
}

install_remote() {
  rscript "$DEPLOY_DIR" "$SEARCH_INTERVAL_MIN" "$ANALYSIS_INTERVAL_MIN" "$ANALYSIS_MODEL" \
          "$NOTIFY_TELEGRAM_CHAT_IDS" "$NOTIFY_MODE" "$NOTIFY_TOP_N" "$REMOTE_SECRETS" <<'EOS'
set -euo pipefail
DIR="$1"; SEARCH_MIN="$2"; ANALYSIS_MIN="$3"; MODEL="$4"
CHATS="$5"; NOTIFY="$6"; TOP_N="$7"; SECRETS_REL="$8"
cd "$HOME/$DIR"
mkdir -p missions

# venv, reinstalled only when requirements.txt changes
[ -x .venv/bin/python ] || python3 -m venv .venv
sha=$(sha256sum requirements.txt | cut -d' ' -f1)
if [ "$(cat .venv/.req.sha 2>/dev/null)" != "$sha" ]; then
  echo "installing python dependencies..."
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  echo "$sha" > .venv/.req.sha
fi

UD="$HOME/.config/systemd/user"
mkdir -p "$UD"
unit() {  # unit <name> <script> <interval_min> <first_run_min> <timeout_min> <description>
  local jitter=$(( $3 / 10 )); [ "$jitter" -gt 5 ] && jitter=5; [ "$jitter" -lt 1 ] && jitter=1
  cat > "$UD/dealhound-$1.service" <<EOF
[Unit]
Description=dealHound: $6

[Service]
Type=oneshot
WorkingDirectory=%h/$DIR
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=DEALHOUND_ANALYSIS_MODEL=$MODEL
Environment="NOTIFY_TELEGRAM_CHAT_IDS=$CHATS" NOTIFY_MODE=$NOTIFY NOTIFY_TOP_N=$TOP_N
EnvironmentFile=-%h/$SECRETS_REL
ExecStart=%h/$DIR/.venv/bin/python $2
Nice=10
TimeoutStartSec=$5min
EOF
  # Fixed interval: first run <first_run_min> after (re)start, then every <interval_min>
  # after the previous run.
  cat > "$UD/dealhound-$1.timer" <<EOF
[Unit]
Description=dealHound: $6 (every $3 min)

[Timer]
OnActiveSec=$4min
OnUnitActiveSec=$3min
RandomizedDelaySec=${jitter}min
AccuracySec=1min

[Install]
WantedBy=timers.target
EOF
}
unit collect collect.py "$SEARCH_MIN" 2 30 "search the sources"
unit analyze analyze.py "$ANALYSIS_MIN" 10 60 "analyze listings with claude"
TIMERS=(dealhound-collect.timer dealhound-analyze.timer)
systemctl --user daemon-reload
systemctl --user enable "${TIMERS[@]}" >/dev/null 2>&1
systemctl --user restart "${TIMERS[@]}"   # apply new intervals
echo "timers: search every ${SEARCH_MIN} min, analysis every ${ANALYSIS_MIN} min (model: ${MODEL:-default})"
n_chats=$(wc -w <<< "$CHATS")
secrets_state=$([ -f "$HOME/$SECRETS_REL" ] && echo "secrets ok" || echo "no secrets file")
echo "notify: mode $NOTIFY, top $TOP_N, $n_chats chat(s), $secrets_state"
EOS
}

# secrets.env -> ~/.config/dealhound/secrets.env on the server, readable by its owner only.
push_secrets() {
  [ -f "$SECRETS" ] || { echo "no secrets.env: notifications disabled on the server"; return 0; }
  rsh "umask 077 && mkdir -p \"\$HOME/$(dirname "$REMOTE_SECRETS")\""
  rsync -a --chmod=F600 -e "ssh ${SSH_OPTS[*]}" "$SECRETS" "$DEPLOY_HOST:$REMOTE_SECRETS"
  echo "secrets pushed (mode 600)"
}

# run_notify <args...> : python -m dealhound.notify <args> on the server, with its secrets
# and the local notification settings.
run_notify() {
  [ -n "$NOTIFY_TELEGRAM_CHAT_IDS" ] || die "NOTIFY_TELEGRAM_CHAT_IDS is empty in config.env"
  rscript "$DEPLOY_DIR" "$REMOTE_SECRETS" "$NOTIFY_TELEGRAM_CHAT_IDS" "$NOTIFY_MODE" "$NOTIFY_TOP_N" "$@" <<'EOS'
set -euo pipefail
cd "$HOME/$1"
[ -f "$HOME/$2" ] || { echo "FAIL no ~/$2 on the server: run deploy.sh push"; exit 1; }
set -a; . "$HOME/$2"; set +a
chats="$3"; mode="$4"; top="$5"; shift 5
NOTIFY_TELEGRAM_CHAT_IDS="$chats" NOTIFY_MODE="$mode" NOTIFY_TOP_N="$top" .venv/bin/python -m dealhound.notify "$@"
EOS
}

cmd_notify_test() { run_notify test; }

cmd_notify_preview() {
  need_mission "${1:-}"; valid_name "$1"
  run_notify preview "$1" --send
}

cmd_push() {
  cd "$ROOT"
  for m in "$@"; do
    valid_name "$m"
    [ -f "missions/$m/mission.yaml" ] || die "unknown local mission: $m"
  done
  cmd_check >/dev/null || die "remote prerequisites not met, run: deploy.sh check"

  local paths=()
  for p in "${CODE_PATHS[@]}"; do [ -e "$p" ] && paths+=("$p"); done
  rsh mkdir -p "$DEPLOY_DIR/missions"
  echo "syncing code: ${paths[*]}"
  rsync_to --delete --exclude '__pycache__' --exclude '*.pyc' "${paths[@]}" "$DEPLOY_HOST:$DEPLOY_DIR/"

  for m in "$@"; do
    local files=("missions/$m/mission.yaml")
    for f in context.md scoring.md blacklist.yaml; do
      [ -f "missions/$m/$f" ] && files+=("missions/$m/$f")
    done
    rsh mkdir -p "$DEPLOY_DIR/missions/$m"
    rsync_to "${files[@]}" "$DEPLOY_HOST:$DEPLOY_DIR/missions/$m/"
    echo "mission pushed: $m"
  done

  push_secrets
  install_remote
  local ver; ver="$(local_version)"
  rsh "echo '$ver $(date +%Y-%m-%dT%H:%M:%S%z)' > \"\$HOME/$DEPLOY_DIR/.deployed\""
  echo "deployed version: $ver"
}

# One JSON line per host: {"host": ..., "ok": true, "missions": [...]} or an error.
cmd_list() {
  local h out
  for h in $DEPLOY_HOST $EXTRA_HOSTS; do
    if out="$(DEPLOY_HOST="$h" rsh "cd \"\$HOME/$DEPLOY_DIR\" 2>/dev/null && .venv/bin/python -m dealhound.cli list --json" 2>/dev/null)"; then
      printf '{"host": "%s", "default": %s, "ok": true, "missions": %s}\n' \
        "$h" "$([ "$h" = "$DEPLOY_HOST" ] && echo true || echo false)" "$out"
    else
      printf '{"host": "%s", "default": %s, "ok": false, "error": "unreachable, or dealhound not installed in ~/%s"}\n' \
        "$h" "$([ "$h" = "$DEPLOY_HOST" ] && echo true || echo false)" "$DEPLOY_DIR"
    fi
  done
}

cmd_results() {
  need_mission "${1:-}"; valid_name "$1"
  local n="${2:-10}"
  [[ "$n" =~ ^[0-9]+$ ]] || die "results: count must be a number"
  rsh "cd \"\$HOME/$DEPLOY_DIR\" && .venv/bin/python -m dealhound.cli results '$1' -n $n --json"
}

cmd_status() {
  rscript "$DEPLOY_DIR" <<'EOS'
cd "$HOME/$1" 2>/dev/null || { echo "not installed in ~/$1"; exit 1; }
echo "== timers"; systemctl --user list-timers 'dealhound-*' --all --no-pager | head -n -2
for u in collect analyze; do
  echo; echo "== last $u run"
  systemctl --user show "dealhound-$u.service" -p Result -p ExecMainExitTimestamp -p ExecMainStatus --no-pager
done
echo; echo "== missions"
for d in missions/*/; do
  [ -f "$d/mission.yaml" ] || continue
  .venv/bin/python - "$d" <<'PY'
import json, sys, pathlib
d = pathlib.Path(sys.argv[1]); p = d / "state.json"
if not p.exists():
    print(f"  {d.name:<20} no collection yet"); sys.exit()
s = json.loads(p.read_text())
L = s["listings"].values()
n = lambda st: sum(1 for r in L if r["status"] == st)
print(f"  {d.name:<20} collected {s.get('collected_at')}  active {n('new') + n('analyzed')}  "
      f"to analyze {n('new')}  gone {n('gone')}")
PY
done
EOS
}

cmd_run() {
  local what="${1:-collect}"
  case "$what" in collect|analyze) ;; *) die "run: collect or analyze";; esac
  echo "running $what on $DEPLOY_HOST (blocks until done)..."
  local rc=0
  rsh systemctl --user start "dealhound-$what.service" || rc=$?
  rsh "journalctl --user-unit dealhound-$what.service -n 25 --no-pager -o cat" || true
  return $rc
}

cmd_test_claude() {
  rscript <<'EOS'
export PATH="$HOME/.local/bin:$PATH"
cd /tmp
SCHEMA='{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"]}'
out=$(echo 'Réponds ok=true.' | timeout 180 claude -p --output-format json --tools "" \
  --no-session-persistence --strict-mcp-config --setting-sources "" --json-schema "$SCHEMA" 2>&1) \
  || { echo "FAIL claude call: ${out:0:300}"; exit 1; }
python3 - "$out" <<'PY'
import json, sys
try:
    d = json.loads(sys.argv[1])
except ValueError:
    print("FAIL non-JSON output:", sys.argv[1][:300]); sys.exit(1)
ok = (d.get("structured_output") or {}).get("ok") is True and not d.get("is_error")
models = ", ".join((d.get("modelUsage") or {}).keys()) or "?"
print(("ok  " if ok else "FAIL") + f" claude -p structured output, model {models}, "
      f"cost {d.get('total_cost_usd', 0):.3f} $" + ("" if ok else f": {str(d.get('result'))[:200]}"))
sys.exit(0 if ok else 1)
PY
EOS
}

# User-unit logs live in the system journal on Debian (journalctl --user finds nothing);
# --user-unit reads them, provided the user is in the adm or systemd-journal group.
cmd_logs() {
  local n="${1:-50}"
  [[ "$n" =~ ^[0-9]+$ ]] || die "logs: line count must be a number"
  rsh "journalctl --user-unit 'dealhound-*' -n $n --no-pager"
}

cmd_pull() {
  need_mission "${1:-}"; valid_name "$1"
  mkdir -p "$ROOT/.remote/$1"
  rsync_to "$DEPLOY_HOST:$DEPLOY_DIR/missions/$1/deals.md" "$DEPLOY_HOST:$DEPLOY_DIR/missions/$1/state.json" "$ROOT/.remote/$1/"
  echo "pulled into .remote/$1/"
}

cmd_retire() {
  need_mission "${1:-}"; valid_name "$1"
  rscript "$DEPLOY_DIR" "$1" <<'EOS'
set -euo pipefail
cd "$HOME/$1/missions"
[ -d "$2" ] || { echo "no such remote mission: $2" >&2; exit 1; }
mkdir -p .archive
dest=".archive/$2-$(date +%Y%m%d-%H%M%S)"
mv "$2" "$dest"
echo "retired: missions/$2 -> missions/$dest (state kept)"
EOS
}

case "${1:-}" in
  check)  cmd_check ;;
  push)   shift; cmd_push "$@" ;;
  list)   cmd_list ;;
  results) shift; cmd_results "$@" ;;
  status) cmd_status ;;
  run)    shift; cmd_run "$@" ;;
  test-claude) cmd_test_claude ;;
  notify-test) cmd_notify_test ;;
  notify-preview) shift; cmd_notify_preview "$@" ;;
  logs)   shift; cmd_logs "$@" ;;
  pull)   shift; cmd_pull "$@" ;;
  retire) shift; cmd_retire "$@" ;;
  *) sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
