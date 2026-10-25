#!/usr/bin/env bash
# Insight Studio - one command start on Linux / macOS / VPS
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

HOST="${HOST:-0.0.0.0}" PORT="${PORT:-5000}"
echo
echo "Insight Studio starting on http://${HOST}:${PORT}"
echo "Press CTRL+C to stop."
echo

if command -v gunicorn >/dev/null 2>&1; then
  exec gunicorn --workers 2 --bind "${HOST}:${PORT}" --timeout 180 wsgi:app
fi

# Fallback for Windows/Git Bash or environments without gunicorn
exec python -m waitress --host="${HOST}" --port="${PORT}" wsgi:app
