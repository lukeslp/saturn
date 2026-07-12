#!/usr/bin/env bash
# Saturn viewer launcher for dr.eamer.dev (managed service, port 5043).
#
# Reads findings JSON files out of /home/coolhand/saturn-findings/.
# Source and virtual environments are immutable, commit-addressed releases.

set -euo pipefail

SCRIPT_DIR="$(cd -P "$(dirname "$0")" && pwd)"
APP_DIR="$(cd -P "$SCRIPT_DIR/.." && pwd)"
DEPLOY_ROOT="${SATURN_DEPLOY_ROOT:-$(dirname "$(dirname "$APP_DIR")")}"
FINDINGS_DIR="${SATURN_FINDINGS_DIR:-/home/coolhand/saturn-findings}"
LEGACY_ARCHIVE_DIR="${SATURN_LEGACY_ARCHIVE_DIR:-/home/coolhand/www/dr.eamer.dev/saturn/view}"
PORT="${SATURN_PORT:-5043}"
HOST="${SATURN_HOST:-127.0.0.1}"
# Single worker so background job state (runner._JOBS) is shared across
# all request handlers. Threads give us concurrent reads. If this ever
# becomes a bottleneck, move _JOBS to SQLite or a shared memory store.
WORKERS="${SATURN_WORKERS:-1}"
THREADS="${SATURN_THREADS:-8}"

export SATURN_DEPLOY_MANIFEST="$APP_DIR/.saturn-deployment.json"
DEPLOY_COMMIT="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["commit"])' "$SATURN_DEPLOY_MANIFEST")"
VENV_DIR="$DEPLOY_ROOT/venvs/$DEPLOY_COMMIT"
python3 "$APP_DIR/saturn/deployment.py" verify "$APP_DIR"
test -x "$VENV_DIR/bin/python"
cd "$APP_DIR"
export PYTHONDONTWRITEBYTECODE=1
# Trust X-Forwarded-Prefix from Caddy so url_for() prepends /saturn behind the proxy.
export SATURN_TRUST_FORWARDED_PREFIX=1
export SATURN_LEGACY_ARCHIVE_DIR="$LEGACY_ARCHIVE_DIR"

# Demo posture: public requests attempt the language-model pass against Luna.
# The service must inject OPENAI_API_KEY into this process environment; secrets
# never belong in this script or the repository. If the key is absent, Saturn
# fails open and still returns the deterministic analysis without model insight.
# Override SATURN_DEFAULT_LLM only together with that provider's standard key.
export SATURN_DEFAULT_LLM="${SATURN_DEFAULT_LLM:-openai:gpt-5.6-luna}"
# Demo posture: anonymous uploads silently use the server's keys. Without
# this flag, public submissions are forced into BYOK mode (or fail). For a
# self-hosted private instance, leave this unset so visitors can't drain
# the operator's quota.
export SATURN_PUBLIC_KEYS="${SATURN_PUBLIC_KEYS:-1}"

mkdir -p "$FINDINGS_DIR"

exec "$VENV_DIR/bin/python" -m gunicorn \
    --bind "${HOST}:${PORT}" \
    --workers "$WORKERS" --threads "$THREADS" \
    --access-logfile - --error-logfile - \
    "saturn.viewer.app:create_app(findings_dir='${FINDINGS_DIR}')"
