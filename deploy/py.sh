#!/usr/bin/env bash
# Run the project's local Python (LOCAL_PYTHON from config.env) from the project root.
#   bash deploy/py.sh collect.py --mission <nom> --dry-run -v
#   bash deploy/py.sh -m dealhound.cli doctor
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY=""
if [ -f config.env ]; then
  # shellcheck source=/dev/null
  source config.env
  PY="${LOCAL_PYTHON:-}"
fi
for candidate in "$PY" "$HOME/.venvs/dealhound/bin/python" "$ROOT/.venv/bin/python"; do
  if [ -n "$candidate" ] && [ -x "$candidate" ]; then exec "$candidate" "$@"; fi
done
echo "ERROR: no Python with dealHound requirements found (set LOCAL_PYTHON in config.env)" >&2
exit 1
