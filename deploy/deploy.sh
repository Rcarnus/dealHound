#!/usr/bin/env bash
# Deploy dealHound to a remote Linux host (Raspberry Pi) over SSH, run it with
# systemd user timers. Run from Linux/WSL. Configuration: deploy/config.env.
#
#   deploy.sh check                 verify SSH access and remote prerequisites (read-only)
#   deploy.sh push [mission...]     sync code + context/, install venv and timers,
#                                   and sync the given missions (mission.yaml, context.md)
#   deploy.sh status                timers, last runs, missions present on the server
#   deploy.sh run                   trigger a collection now and show its log
#   deploy.sh logs [n]              last n journal lines (default 50)
#   deploy.sh pull <mission>        fetch remote deals.md + state.json into .remote/<mission>/
#   deploy.sh retire <mission>      stop a mission: move it to missions/.archive/ on the server
#
# Remote state (state.json, deals.md) is never overwritten by push.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
# shellcheck source=/dev/null
source "$HERE/config.env"
# shellcheck source=/dev/null
[ -f "$HERE/config.local.env" ] && source "$HERE/config.local.env"

: "${DEPLOY_HOST:?DEPLOY_HOST not set in deploy/config.env}"
DEPLOY_DIR="${DEPLOY_DIR:-dealhound}"
COLLECT_ON_CALENDAR="${COLLECT_ON_CALENDAR:-*:0/30}"
ANALYZE_ON_CALENDAR="${ANALYZE_ON_CALENDAR:-*-*-* 08,13,19:00}"

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10)
CODE_PATHS=(dealhound collect.py analyze.py prompts requirements.txt CLAUDE.md context)

rsh() { ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" "$@"; }
# rscript [args...] < script : run a bash script on the remote with safely quoted args
# (ssh joins its arguments into one string re-parsed by the remote shell).
rscript() { ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" "bash -s -- $(printf '%q ' "$@")"; }
rsync_to() { rsync -az -e "ssh ${SSH_OPTS[*]}" "$@"; }
die() { echo "ERROR: $*" >&2; exit 1; }
need_mission() { [ -n "${1:-}" ] || die "mission name required"; }
valid_name() { [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid mission name: $1"; }

cmd_check() {
  echo "== $DEPLOY_HOST"
  rsh true 2>/dev/null || die "SSH key login to $DEPLOY_HOST failed. Authorize your key: ssh-copy-id $DEPLOY_HOST"
  rscript <<'EOS'
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
if command -v claude >/dev/null || [ -x "$HOME/.local/bin/claude" ]; then ok "claude cli"
else warn "claude cli not found (needed by analyze.py only)"; fi
ok "disk: $(df -h "$HOME" | awk 'NR==2 {print $4 " free"}'), ram: $(free -m | awk '/Mem:/ {print $7 " MB available"}')"
exit $FAILED
EOS
}

install_remote() {
  rscript "$DEPLOY_DIR" "$COLLECT_ON_CALENDAR" "$ANALYZE_ON_CALENDAR" <<'EOS'
set -euo pipefail
DIR="$1"; COLLECT_CAL="$2"; ANALYZE_CAL="$3"
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
unit() {  # unit <name> <script> <on_calendar> <description>
  cat > "$UD/dealhound-$1.service" <<EOF
[Unit]
Description=dealHound: $4

[Service]
Type=oneshot
WorkingDirectory=%h/$DIR
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=%h/$DIR/.venv/bin/python $2
Nice=10
TimeoutStartSec=30min
EOF
  cat > "$UD/dealhound-$1.timer" <<EOF
[Unit]
Description=dealHound: $4 (timer)

[Timer]
OnCalendar=$3
RandomizedDelaySec=5min
Persistent=true

[Install]
WantedBy=timers.target
EOF
}
unit collect collect.py "$COLLECT_CAL" "collect listings"
TIMERS=(dealhound-collect.timer)
if [ -f analyze.py ]; then
  unit analyze analyze.py "$ANALYZE_CAL" "analyze listings with claude"
  TIMERS+=(dealhound-analyze.timer)
fi
systemctl --user daemon-reload
systemctl --user enable --now "${TIMERS[@]}" >/dev/null
echo "timers enabled: ${TIMERS[*]}"
EOS
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
    [ -f "missions/$m/context.md" ] && files+=("missions/$m/context.md")
    rsh mkdir -p "$DEPLOY_DIR/missions/$m"
    rsync_to "${files[@]}" "$DEPLOY_HOST:$DEPLOY_DIR/missions/$m/"
    echo "mission pushed: $m"
  done

  install_remote
}

cmd_status() {
  rscript "$DEPLOY_DIR" <<'EOS'
cd "$HOME/$1" 2>/dev/null || { echo "not installed in ~/$1"; exit 1; }
echo "== timers"; systemctl --user list-timers 'dealhound-*' --all --no-pager | head -n -2
echo; echo "== last collect run"
systemctl --user show dealhound-collect.service -p Result -p ExecMainExitTimestamp -p ExecMainStatus --no-pager
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
  echo "running collection on $DEPLOY_HOST (blocks until done)..."
  local rc=0
  rsh systemctl --user start dealhound-collect.service || rc=$?
  rsh "journalctl --user-unit dealhound-collect.service -n 20 --no-pager -o cat" || true
  return $rc
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
  status) cmd_status ;;
  run)    cmd_run ;;
  logs)   shift; cmd_logs "$@" ;;
  pull)   shift; cmd_pull "$@" ;;
  retire) shift; cmd_retire "$@" ;;
  *) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
