#!/usr/bin/env bash
# Run the project's local Python (LOCAL_PYTHON from config.env) from the project root,
# with config.env and secrets.env exported (notifications read them from the environment).
#   bash deploy/py.sh collect.py --mission <nom> --dry-run -v
#   bash deploy/py.sh -m dealhound.cli doctor
#   bash deploy/py.sh -m dealhound.notify discover|test
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
set -a
# shellcheck source=/dev/null
[ -f config.env ] && source config.env
# shellcheck source=/dev/null
[ -f secrets.env ] && source secrets.env
set +a
PY="${LOCAL_PYTHON:-}"
for candidate in "$PY" "$HOME/.venvs/dealhound/bin/python" "$ROOT/.venv/bin/python"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then exec "$candidate" "$@"; fi
done
echo "ERROR: no Python with dealHound requirements found (set LOCAL_PYTHON in config.env)" >&2
exit 1
