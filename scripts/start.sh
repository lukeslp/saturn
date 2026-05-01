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
# Single worker so background job state (runner._JOBS) is shared across
# all request handlers. Threads give us concurrent reads. If this ever
# becomes a bottleneck, move _JOBS to SQLite or a shared memory store.
WORKERS="${SATURN_WORKERS:-1}"
THREADS="${SATURN_THREADS:-8}"

cd "$APP_DIR"
source venv/bin/activate
export PYTHONPATH="/home/coolhand/shared:${PYTHONPATH:-}"
# Trust X-Forwarded-Prefix from Caddy so url_for() prepends /saturn behind the proxy.
export SATURN_TRUST_FORWARDED_PREFIX=1

# Public-instance posture: the form defaults to stats-only. If a visitor wants
# a plain-language summary they paste their own API key (BYOK). Wiping these
# guarantees a request without a key can't fall through to whatever happens
# to be in the server's environment or shared/config/API_KEYS.md.
unset ANTHROPIC_API_KEY OPENAI_API_KEY GROQ_API_KEY GEMINI_API_KEY \
      MISTRAL_API_KEY COHERE_API_KEY XAI_API_KEY PERPLEXITY_API_KEY HF_TOKEN
export SATURN_LLM_DISABLE_CONFIG_MANAGER=1

mkdir -p "$FINDINGS_DIR"

exec gunicorn \
    --bind "${HOST}:${PORT}" \
    --workers "$WORKERS" --threads "$THREADS" \
    --access-logfile - --error-logfile - \
    "saturn.viewer.app:create_app(findings_dir='${FINDINGS_DIR}')"
