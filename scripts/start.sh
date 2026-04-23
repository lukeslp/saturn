#!/usr/bin/env bash
# Saturn viewer launcher for dr.eamer.dev (sm-managed, port 5043).
#
# Reads findings JSON files out of /home/coolhand/saturn-findings/.
# Backing venv: /home/coolhand/projects/saturn/saturn/venv (installed with [web] extra).

set -euo pipefail

APP_DIR="/home/coolhand/projects/saturn/saturn"
FINDINGS_DIR="${SATURN_FINDINGS_DIR:-/home/coolhand/saturn-findings}"
PORT="${SATURN_PORT:-5043}"
HOST="${SATURN_HOST:-127.0.0.1}"
WORKERS="${SATURN_WORKERS:-2}"
THREADS="${SATURN_THREADS:-4}"

cd "$APP_DIR"
source venv/bin/activate
export PYTHONPATH="/home/coolhand/shared:${PYTHONPATH:-}"
# Trust X-Forwarded-Prefix from Caddy so url_for() prepends /saturn behind the proxy.
export SATURN_TRUST_FORWARDED_PREFIX=1

mkdir -p "$FINDINGS_DIR"

exec gunicorn \
    --bind "${HOST}:${PORT}" \
    --workers "$WORKERS" --threads "$THREADS" \
    --access-logfile - --error-logfile - \
    "saturn.viewer.app:create_app(findings_dir='${FINDINGS_DIR}')"
