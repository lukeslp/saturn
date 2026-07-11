#!/usr/bin/env bash
# Saturn viewer launcher for dr.eamer.dev (sm-managed, port 5043).
#
# Reads findings JSON files out of /home/coolhand/saturn-findings/.
# Backing venv: /home/coolhand/projects/saturn/saturn/venv (installed with [web,llm]).

set -euo pipefail

APP_DIR="/home/coolhand/projects/saturn/saturn"
FINDINGS_DIR="${SATURN_FINDINGS_DIR:-/home/coolhand/saturn-findings}"
LEGACY_ARCHIVE_DIR="${SATURN_LEGACY_ARCHIVE_DIR:-/home/coolhand/www/dr.eamer.dev/saturn/view}"
PORT="${SATURN_PORT:-5043}"
HOST="${SATURN_HOST:-127.0.0.1}"
# Single worker so background job state (runner._JOBS) is shared across
# all request handlers. Threads give us concurrent reads. If this ever
# becomes a bottleneck, move _JOBS to SQLite or a shared memory store.
WORKERS="${SATURN_WORKERS:-1}"
THREADS="${SATURN_THREADS:-8}"

cd "$APP_DIR"
source venv/bin/activate
# Trust X-Forwarded-Prefix from Caddy so url_for() prepends /saturn behind the proxy.
export SATURN_TRUST_FORWARDED_PREFIX=1
export SATURN_LEGACY_ARCHIVE_DIR="$LEGACY_ARCHIVE_DIR"

# Demo posture: public requests attempt the LLM pass against Opus. The service
# manager must inject ANTHROPIC_API_KEY into this process environment; secrets
# never belong in this script or the repository. If the key is absent, Saturn
# fails open and still returns the deterministic analysis without model insight.
# Override SATURN_DEFAULT_LLM only together with that provider's standard key.
export SATURN_DEFAULT_LLM="${SATURN_DEFAULT_LLM:-anthropic:claude-opus-4-7}"
# Demo posture: anonymous uploads silently use the server's keys. Without
# this flag, public submissions are forced into BYOK mode (or fail). For a
# self-hosted private instance, leave this unset so visitors can't drain
# the operator's quota.
export SATURN_PUBLIC_KEYS="${SATURN_PUBLIC_KEYS:-1}"

mkdir -p "$FINDINGS_DIR"

exec gunicorn \
    --bind "${HOST}:${PORT}" \
    --workers "$WORKERS" --threads "$THREADS" \
    --access-logfile - --error-logfile - \
    "saturn.viewer.app:create_app(findings_dir='${FINDINGS_DIR}')"
